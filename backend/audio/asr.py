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

# Some vendors sit behind edge protection that rejects the stdlib default
# User-Agent outright (Groq answers "403 error code: 1010" without it).
ASR_USER_AGENT = "pulse/1.0 (+https://github.com/drmohpioneer/pulse)"

# Sentinel language value meaning "let the model detect it per segment".
# Needed for code-switched Egyptian Arabic: a pinned language transliterates
# the other language into nonsense instead of transcribing it.
AUTO_LANGUAGE = "auto"


class AsrVendor(BaseModel):
    """A transcription backend Pulse can talk to, described as data.

    Adding a vendor is a table entry, not a new code path, as long as it speaks
    one of the two wire protocols below.
    """

    name: str
    base_url: str
    default_model: str
    api_key_envs: tuple[str, ...]
    protocol: str = "openai_compatible"


ASR_VENDORS: Mapping[str, AsrVendor] = {
    "openai": AsrVendor(
        name="openai",
        base_url="https://api.openai.com/v1/audio/transcriptions",
        default_model=DEFAULT_OPENAI_TRANSCRIPTION_MODEL,
        api_key_envs=("PULSE_ASR_API_KEY", "OPENAI_API_KEY"),
    ),
    "groq": AsrVendor(
        name="groq",
        base_url="https://api.groq.com/openai/v1/audio/transcriptions",
        default_model="whisper-large-v3-turbo",
        api_key_envs=("PULSE_ASR_API_KEY", "GROQ_API_KEY"),
    ),
    "elevenlabs": AsrVendor(
        name="elevenlabs",
        base_url="https://api.elevenlabs.io/v1/speech-to-text",
        default_model="scribe_v1",
        api_key_envs=("PULSE_ASR_API_KEY", "ELEVENLABS_API_KEY", "ELEVEN_API_KEY"),
        protocol="elevenlabs",
    ),
}


def resolve_vendor(name: str) -> AsrVendor:
    """Resolve a provider name to a vendor profile.

    An unknown name is treated as a custom OpenAI-compatible endpoint, so a
    self-hosted or not-yet-listed vendor only needs PULSE_ASR_BASE_URL +
    PULSE_ASR_MODEL + PULSE_ASR_API_KEY, with no code change.
    """
    vendor = ASR_VENDORS.get(name)
    if vendor is not None:
        return vendor
    base_url = os.getenv("PULSE_ASR_BASE_URL")
    if not base_url:
        raise TranscriptionProviderConfigurationError(
            f"Unsupported ASR provider: {name}. Known providers: "
            f"{', '.join(sorted(['fake', *ASR_VENDORS]))}. "
            "For any other OpenAI-compatible endpoint, also set PULSE_ASR_BASE_URL."
        )
    return AsrVendor(
        name=name,
        base_url=base_url,
        default_model=os.getenv("PULSE_ASR_MODEL") or DEFAULT_OPENAI_TRANSCRIPTION_MODEL,
        api_key_envs=("PULSE_ASR_API_KEY",),
    )


def _resolve_language() -> str:
    """Return the language to pin, or "" to let the vendor auto-detect."""
    configured = (os.getenv("PULSE_ASR_LANGUAGE") or DEFAULT_ASR_LANGUAGE).strip()
    if configured.casefold() in {AUTO_LANGUAGE, "detect", "multi"}:
        return ""
    return configured


def _resolve_api_key(vendor: AsrVendor) -> str | None:
    for env_name in vendor.api_key_envs:
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value
    return None


class OpenAITranscriptionProvider:
    """OpenAI-compatible ASR adapter behind the provider-neutral interface.

    Serves every vendor speaking the OpenAI /audio/transcriptions wire format
    (OpenAI, Groq, and self-hosted gateways). The vendor supplies the endpoint,
    default model, and which env vars hold the key; the wire format is shared.
    """

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: OpenAITransport | None = None,
        vendor: AsrVendor | None = None,
    ) -> None:
        vendor = vendor or ASR_VENDORS["openai"]
        self._vendor = vendor
        self.provider_name = vendor.name
        self._api_key = api_key or _resolve_api_key(vendor)
        self._model = (
            model
            or os.getenv("PULSE_ASR_MODEL")
            or os.getenv("PULSE_OPENAI_TRANSCRIPTION_MODEL")
            or vendor.default_model
        )
        self._language = _resolve_language()
        self._prompt = os.getenv("PULSE_ASR_PROMPT") or DEFAULT_ASR_PROMPT
        self._base_url = (
            base_url
            or os.getenv("PULSE_ASR_BASE_URL")
            or os.getenv("PULSE_OPENAI_TRANSCRIPTION_URL")
            or vendor.base_url
        )
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PULSE_ASR_TIMEOUT_SECONDS", "20")
        )
        self._transport = transport or _openai_http_transport
        if not self._api_key:
            raise TranscriptionProviderConfigurationError(
                f"{vendor.name} ASR provider requires one of: "
                f"{', '.join(vendor.api_key_envs)}."
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
        except urllib.error.HTTPError as exc:
            # HTTPError is a URLError subclass, so without this branch every
            # rejected request collapsed into one opaque "request failed".
            raise TranscriptionProviderRuntimeError(
                f"{self.provider_name} ASR rejected the request "
                f"(HTTP {exc.code}): {_error_body_excerpt(exc)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise TranscriptionProviderRuntimeError(
                f"{self.provider_name} ASR request failed: {exc.reason}"
            ) from exc
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
                "vendor": self._vendor.name,
                "model": self._model,
                "language": self._language or AUTO_LANGUAGE,
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


class ElevenLabsTranscriptionProvider:
    """ElevenLabs Scribe adapter.

    Kept separate from the OpenAI-compatible provider because ElevenLabs
    differs on the wire: xi-api-key auth, model_id/language_code field names,
    and native speaker diarization.
    """

    provider_name = "elevenlabs"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
        vendor = ASR_VENDORS["elevenlabs"]
        self._vendor = vendor
        self._api_key = api_key or _resolve_api_key(vendor)
        self._model = model or os.getenv("PULSE_ASR_MODEL") or vendor.default_model
        self._language = _resolve_language()
        self._base_url = base_url or os.getenv("PULSE_ASR_BASE_URL") or vendor.base_url
        self._diarize = (os.getenv("PULSE_ASR_DIARIZE") or "").strip().casefold() in {
            "1",
            "true",
            "yes",
        }
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PULSE_ASR_TIMEOUT_SECONDS", "20")
        )
        self._transport = transport or _elevenlabs_http_transport
        if not self._api_key:
            raise TranscriptionProviderConfigurationError(
                f"elevenlabs ASR provider requires one of: {', '.join(vendor.api_key_envs)}."
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
                self._diarize,
            )
        except TimeoutError as exc:
            raise TranscriptionProviderRuntimeError("elevenlabs ASR request timed out.") from exc
        except urllib.error.HTTPError as exc:
            raise TranscriptionProviderRuntimeError(
                f"elevenlabs ASR rejected the request "
                f"(HTTP {exc.code}): {_error_body_excerpt(exc)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise TranscriptionProviderRuntimeError(
                f"elevenlabs ASR request failed: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise TranscriptionProviderRuntimeError("elevenlabs ASR audio read failed.") from exc

        text = str(raw.get("text") or "").strip()
        if not text:
            raise TranscriptionProviderRuntimeError(
                "elevenlabs ASR returned an empty transcript."
            )

        confidence = _bounded_confidence(raw.get("language_probability") or 0.85)
        speaker_label = _first_speaker_label(raw)
        return TranscriptChunkResult(
            session_id=request.session_id,
            sequence=request.sequence,
            text=text,
            confidence=confidence,
            started_at=timestamp,
            ended_at=timestamp + timedelta(milliseconds=duration_ms),
            language=_language_from_metadata(_short_language_code(raw.get("language_code"))),
            speaker_label=speaker_label,
            provider_name=self.provider_name,
            audio_reference=request.audio_reference,
            provider_metadata={
                "provider_mode": "configured_real_provider",
                "vendor": self._vendor.name,
                "model": self._model,
                "language": self._language or AUTO_LANGUAGE,
                "base_url": self._base_url,
                "diarize": self._diarize,
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
    vendor = resolve_vendor(configured_name)
    if vendor.protocol == "elevenlabs":
        return ElevenLabsTranscriptionProvider()
    return OpenAITranscriptionProvider(vendor=vendor)


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
    fields = {
        "model": model,
        "prompt": prompt,
        "response_format": "json",
    }
    # An empty language means auto-detect: the field has to be absent, because
    # the API rejects "auto" and pinning one language wrecks the other one.
    if language:
        fields["language"] = language
    body = _multipart_body(
        boundary=boundary,
        fields=fields,
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
            "User-Agent": ASR_USER_AGENT,
        },
    )
    with urllib.request.urlopen(http_request, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping):
        raise TranscriptionProviderRuntimeError("OpenAI ASR returned a non-object response.")
    return parsed


def _elevenlabs_http_transport(
    request: AudioTranscriptionRequest,
    audio_bytes: bytes,
    filename: str,
    model: str,
    url: str,
    api_key: str,
    timeout_seconds: float,
    language: str,
    diarize: bool,
) -> Mapping[str, Any]:
    boundary = f"pulse-{uuid4().hex}"
    fields = {"model_id": model, "diarize": "true" if diarize else "false"}
    if language:
        fields["language_code"] = language
    body = _multipart_body(
        boundary=boundary,
        fields=fields,
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
            "xi-api-key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": ASR_USER_AGENT,
        },
    )
    with urllib.request.urlopen(http_request, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping):
        raise TranscriptionProviderRuntimeError(
            "elevenlabs ASR returned a non-object response."
        )
    return parsed


def _error_body_excerpt(error: urllib.error.HTTPError) -> str:
    """Surface the vendor's own error text instead of swallowing it."""
    try:
        return error.read().decode("utf-8", errors="replace").strip()[:300] or error.reason
    except Exception:  # noqa: BLE001 - diagnostics must never mask the real error
        return str(error.reason)


def _short_language_code(value: object) -> str | None:
    """Map a vendor language code onto the TranscriptLanguage vocabulary."""
    if value is None:
        return None
    code = str(value).strip().casefold().replace("-", "_")
    if code.startswith("ar"):
        return TranscriptLanguage.EGYPTIAN_ARABIC.value
    if code.startswith("en"):
        return TranscriptLanguage.ENGLISH.value
    return code


def _first_speaker_label(raw: Mapping[str, Any]) -> str | None:
    words = raw.get("words")
    if not isinstance(words, list):
        return None
    for word in words:
        if isinstance(word, Mapping):
            speaker = _optional_text(word.get("speaker_id"))
            if speaker:
                return speaker
    return None


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
