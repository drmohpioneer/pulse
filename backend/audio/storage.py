import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4


class LiveAudioStorageError(ValueError):
    """Raised when a live audio upload cannot be safely stored."""


@dataclass(frozen=True)
class StoredAudioChunk:
    session_id: str
    sequence: int
    content_type: str
    file_size_bytes: int
    storage_reference: str
    stored_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class LiveAudioStoragePolicy:
    allowed_content_types: frozenset[str] = frozenset(
        {
            "audio/webm",
            "audio/wav",
            "audio/wave",
            "audio/x-wav",
            "audio/mpeg",
            "audio/mp3",
            "audio/mp4",
            "audio/m4a",
        }
    )
    max_chunk_bytes: int = 5 * 1024 * 1024
    retention_seconds: int = 60 * 60

    @classmethod
    def from_env(cls) -> "LiveAudioStoragePolicy":
        max_chunk_bytes = int(os.getenv("PULSE_AUDIO_MAX_CHUNK_BYTES", str(cls.max_chunk_bytes)))
        retention_seconds = int(os.getenv("PULSE_AUDIO_RETENTION_SECONDS", str(cls.retention_seconds)))
        return cls(
            max_chunk_bytes=max_chunk_bytes,
            retention_seconds=retention_seconds,
        )


class LiveAudioChunkStore:
    """Temporary local storage for demo live audio chunks."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        policy: LiveAudioStoragePolicy | None = None,
    ) -> None:
        self.root = Path(
            root
            or os.getenv("PULSE_AUDIO_STORAGE_DIR")
            or Path(tempfile.gettempdir()) / "pulse_live_audio"
        )
        self.policy = policy or LiveAudioStoragePolicy.from_env()

    def store(
        self,
        *,
        session_id: str,
        sequence: int,
        content: bytes,
        content_type: str,
    ) -> StoredAudioChunk:
        normalized_type = content_type.split(";")[0].strip().casefold()
        if normalized_type not in self.policy.allowed_content_types:
            raise LiveAudioStorageError(f"unsupported audio content type: {content_type}")
        if len(content) > self.policy.max_chunk_bytes:
            raise LiveAudioStorageError("audio chunk exceeds maximum size")

        self.cleanup_expired()
        stored_at = datetime.now(UTC)
        expires_at = stored_at + timedelta(seconds=self.policy.retention_seconds)
        session_dir = self.root / _safe_path_part(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        suffix = _suffix_for_content_type(normalized_type)
        path = session_dir / f"{sequence:06d}-{uuid4().hex}{suffix}"
        path.write_bytes(content)
        return StoredAudioChunk(
            session_id=session_id,
            sequence=sequence,
            content_type=normalized_type,
            file_size_bytes=len(content),
            storage_reference=str(path),
            stored_at=stored_at,
            expires_at=expires_at,
        )

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - timedelta(
            seconds=self.policy.retention_seconds
        )
        removed = 0
        if not self.root.exists():
            return removed
        for path in self.root.glob("*/*"):
            if not path.is_file():
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified >= cutoff:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        for directory in self.root.glob("*"):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        return removed


def _safe_path_part(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return safe[:96] or "session"


def _suffix_for_content_type(content_type: str) -> str:
    if content_type in {"audio/wav", "audio/wave", "audio/x-wav"}:
        return ".wav"
    if content_type in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if content_type in {"audio/mp4", "audio/m4a"}:
        return ".m4a"
    return ".webm"
