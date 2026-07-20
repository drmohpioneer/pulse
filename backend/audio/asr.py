import os
import json
import mimetypes
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.audio.multimodal import TranscriptLanguage


class TranscriptionProviderConfigurationError(RuntimeError):
    """Raised when a configured transcription provider cannot run safely."""


class TranscriptionProviderRuntimeError(RuntimeError):
    """Raised when a configured transcription provider fails during a request."""


class AudioTranscriptionRequest(BaseModel):
    session_id: str
    sequence: int = Field(ge=1)
    audio_reference: str
    content_type: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    timestamp: datetime | None = None
    sample_rate_hz: int | None = Field(default=None, gt=0)
    channel_count: int | None = Field(default=None, gt=0)
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class TranscriptChunkResult(BaseModel):
    session_id: str
    sequence: int = Field(ge=1)
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    started_at: datetime
    ended_at: datetime
    language: TranscriptLanguage = TranscriptLanguage.UNKNOWN
    speaker_label: str | None = None
    provider_name: str
    audio_reference: str
    provider_metadata: Mapping[str, Any] = Field(default_factory=dict)


class TranscriptionProviderStatus(BaseModel):
    provider_name: str
    mode: str
    configured: bool
    available: bool
    fallback_provider_name: str | None = None
    error: str | None = None


class TranscriptionProvider(Protocol):
    provider_name: str

    def transcribe(
        self,
        request: AudioTranscriptionRequest,
    ) -> TranscriptChunkResult:
        """Transcribe one audio chunk into a provider-neutral transcript result."""
        ...


class FakeTranscriptionProvider:
    """Deterministic fake ASR for tests and quota-conscious demos."""

    provider_name = "fake"

    def transcribe(
        self,
        request: AudioTranscriptionRequest,
    ) -> TranscriptChunkResult:
        timestamp = request.timestamp or datetime.now(UTC)
        duration_ms = request.duration_ms if request.duration_ms is not None else 1000
        text = str(
            request.metadata.get("simulated_text")
            or request.metadata.get("transcript_text")
            or "inaudible audio chunk"
        )
        confidence = _bounded_confidence(request.metadata.get("simulated_confidence", 0.95))
        language = _language_from_metadata(request.metadata.get("language"))
        speaker_label = request.metadata.get("speaker_label")
        return TranscriptChunkResult(
            session_id=request.session_id,
            sequence=request.sequence,
            text=text,
            confidence=confidence,
            started_at=timestamp,
            ended_at=timestamp + timedelta(milliseconds=duration_ms),
            language=language,
            speaker_label=str(speaker_label) if speaker_label else None,
            provider_name=self.provider_name,
            audio_reference=request.audio_reference,
            provider_metadata={
                "deterministic_fake": True,
                "stored_audio": False,
                "provider_mode": "fake/demo",
            },
        )

    def status(self) -> TranscriptionProviderStatus:
        return TranscriptionProviderStatus(
            provider_name=self.provider_name,
            mode="fake/demo",
            configured=True,
            available=True,
        )


OpenAITransport = Callable[
    [AudioTranscriptionRequest, bytes, str, str, str, str, float, str, str],
    Mapping[str, Any],
]


DEFAULT_OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_ASR_LANGUAGE = "en"
DEFAULT_ASR_PROMPT = (
    "Cardiac arrest resuscitation. Terms: rhythm, VF, VT, asystole, "
    "PEA, shock delivered, epinephrine, amiodarone, 1 mg, ROSC, CPR."
)


class OpenAITranscriptionProvider:
    """OpenAI-compatible ASR adapter behind the provider-neutral interface."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: OpenAITransport | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._model = (
            model
            or os.getenv("PULSE_OPENAI_TRANSCRIPTION_MODEL")
            or DEFAULT_OPENAI_TRANSCRIPTION_MODEL
        )
        self._language = os.getenv("PULSE_ASR_LANGUAGE") or DEFAULT_ASR_LANGUAGE
        self._prompt = os.getenv("PULSE_ASR_PROMPT") or DEFAULT_ASR_PROMPT
        self._base_url = (
            base_url
            or os.getenv("PULSE_OPENAI_TRANSCRIPTION_URL")
            or "https://api.openai.com/v1/audio/transcriptions"
        )
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PULSE_ASR_TIMEOUT_SECONDS", "20")
        )
        self._transport = transport or _openai_http_transport
        if not self._api_key:
            raise TranscriptionProviderConfigurationError(
                "OpenAI ASR provider requires OPENAI_API_KEY."
            )

    def transcribe(
        self,
        request: AudioTranscriptionRequest,
    ) -> TranscriptChunkResult:
        timestamp = request.timestamp or datetime.now(UTC)
        duration_ms = request.duration_ms if request.duration_ms is not None else 1000
        audio_bytes, filename = _audio_bytes_from_request(request)
        try:
            raw = self._transport(
                request,
                audio_bytes,
                filename,
                self._model,
                self._base_url,
                self._api_key,
                self._timeout_seconds,
                self._language,
                self._prompt,
            )
        except TimeoutError as exc:
            raise TranscriptionProviderRuntimeError("OpenAI ASR request timed out.") from exc
        except urllib.error.URLError as exc:
            raise TranscriptionProviderRuntimeError("OpenAI ASR request failed.") from exc
        except OSError as exc:
            raise TranscriptionProviderRuntimeError("OpenAI ASR audio read failed.") from exc

        text = str(raw.get("text") or "").strip()
        if not text:
            raise TranscriptionProviderRuntimeError("OpenAI ASR returned an empty transcript.")

        confidence = _bounded_confidence(
            raw.get("confidence")
            or raw.get("avg_logprob_confidence")
            or request.metadata.get("provider_confidence")
            or 0.85
        )
        started_at = _datetime_from_provider(raw.get("started_at")) or timestamp
        ended_at = (
            _datetime_from_provider(raw.get("ended_at"))
            or started_at + timedelta(milliseconds=duration_ms)
        )
        return TranscriptChunkResult(
            session_id=request.session_id,
            sequence=request.sequence,
            text=text,
            confidence=confidence,
            started_at=started_at,
            ended_at=ended_at,
            language=_language_from_metadata(raw.get("language")),
            speaker_label=_optional_text(raw.get("speaker_label")),
            provider_name=self.provider_name,
            audio_reference=request.audio_reference,
            provider_metadata={
                "provider_mode": "configured_real_provider",
                "model": self._model,
                "language": self._language,
                "base_url": self._base_url,
                "prompt_context_chars": len(self._prompt),
                "raw_fields": sorted(str(key) for key in raw.keys()),
            },
        )

    def status(self) -> TranscriptionProviderStatus:
        return TranscriptionProviderStatus(
            provider_name=self.provider_name,
            mode="configured_real_provider",
            configured=True,
            available=True,
        )


class UnavailableTranscriptionProvider:
    provider_name = "unavailable"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def transcribe(
        self,
        request: AudioTranscriptionRequest,
    ) -> TranscriptChunkResult:
        raise TranscriptionProviderConfigurationError(self._reason)

    def status(self) -> TranscriptionProviderStatus:
        return TranscriptionProviderStatus(
            provider_name=self.provider_name,
            mode="provider_error",
            configured=False,
            available=False,
            error=self._reason,
        )


class FallbackTranscriptionProvider:
    """Fail-closed remote config fallback that keeps demo sessions usable."""

    def __init__(
        self,
        *,
        requested_provider_name: str,
        reason: str,
        fallback: TranscriptionProvider | None = None,
    ) -> None:
        self._requested_provider_name = requested_provider_name
        self._reason = reason
        self._fallback = fallback or FakeTranscriptionProvider()
        self.provider_name = self._fallback.provider_name

    def transcribe(
        self,
        request: AudioTranscriptionRequest,
    ) -> TranscriptChunkResult:
        result = self._fallback.transcribe(request)
        metadata = dict(result.provider_metadata)
        metadata.update(
            {
                "configured_provider": self._requested_provider_name,
                "provider_error": self._reason,
                "fallback_provider": self._fallback.provider_name,
            }
        )
        return result.model_copy(update={"provider_metadata": metadata})

    def status(self) -> TranscriptionProviderStatus:
        return TranscriptionProviderStatus(
            provider_name=self._requested_provider_name,
            mode="provider_error_fallback",
            configured=False,
            available=True,
            fallback_provider_name=self._fallback.provider_name,
            error=self._reason,
        )


def build_transcription_provider(
    provider_name: str | None = None,
) -> TranscriptionProvider:
    configured_name = (provider_name or os.getenv("PULSE_ASR_PROVIDER") or "fake").casefold()
    if configured_name == "fake":
        return FakeTranscriptionProvider()
    if configured_name == "openai":
        return OpenAITranscriptionProvider()
    raise TranscriptionProviderConfigurationError(
        f"Unsupported ASR provider: {configured_name}"
    )


def configured_transcription_provider() -> TranscriptionProvider:
    configured_name = os.getenv("PULSE_ASR_PROVIDER")
    try:
        return build_transcription_provider()
    except TranscriptionProviderConfigurationError as exc:
        if configured_name and configured_name.casefold() != "fake":
            return FallbackTranscriptionProvider(
                requested_provider_name=configured_name.casefold(),
                reason=str(exc),
            )
        return UnavailableTranscriptionProvider(str(exc))


def provider_status(provider: TranscriptionProvider) -> TranscriptionProviderStatus:
    status = getattr(provider, "status", None)
    if callable(status):
        return status()
    return TranscriptionProviderStatus(
        provider_name=provider.provider_name,
        mode="unknown",
        configured=True,
        available=True,
    )


def _bounded_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return max(0.0, min(1.0, confidence))


def _language_from_metadata(value: object) -> TranscriptLanguage:
    if value is None:
        return TranscriptLanguage.UNKNOWN
    try:
        return TranscriptLanguage(str(value))
    except ValueError:
        return TranscriptLanguage.UNKNOWN


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _datetime_from_provider(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _audio_bytes_from_request(request: AudioTranscriptionRequest) -> tuple[bytes, str]:
    raw_path = request.metadata.get("audio_file_path") or request.audio_reference
    path = Path(str(raw_path)).expanduser()
    if not path.exists() or not path.is_file():
        raise TranscriptionProviderConfigurationError(
            "OpenAI ASR requires audio_reference or metadata.audio_file_path to be a readable local file."
        )
    return path.read_bytes(), path.name


def _openai_http_transport(
    request: AudioTranscriptionRequest,
    audio_bytes: bytes,
    filename: str,
    model: str,
    url: str,
    api_key: str,
    timeout_seconds: float,
    language: str,
    prompt: str,
) -> Mapping[str, Any]:
    boundary = f"pulse-{uuid4().hex}"
    body = _multipart_body(
        boundary=boundary,
        fields={
            "model": model,
            "language": language,
            "prompt": prompt,
            "response_format": "json",
        },
        files={
            "file": (
                filename,
                audio_bytes,
                request.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
            ),
        },
    )
    http_request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(http_request, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping):
        raise TranscriptionProviderRuntimeError("OpenAI ASR returned a non-object response.")
    return parsed


def _multipart_body(
    *,
    boundary: str,
    fields: Mapping[str, str],
    files: Mapping[str, tuple[str, bytes, str]],
) -> bytes:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    for name, (filename, content, content_type) in files.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)
