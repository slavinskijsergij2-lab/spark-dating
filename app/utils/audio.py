"""Audio file storage utility.

Saves raw audio bytes to the same PHOTO_DIR Volume used for images,
falling back to base64 data URLs when no Volume is configured (local dev).
"""
import base64
import os
import uuid
from pathlib import Path

ALLOWED_AUDIO_MIMES = frozenset({
    "audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav",
})

_EXT_MAP: dict[str, str] = {
    "audio/webm": "webm",
    "audio/ogg":  "ogg",
    "audio/mp4":  "m4a",
    "audio/mpeg": "mp3",
    "audio/wav":  "wav",
}


def _audio_dir_path() -> Path | None:
    d = os.getenv("PHOTO_DIR")
    if not d:
        return None
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_audio_bytes(raw: bytes, mime: str = "audio/webm") -> str:
    """Persist *raw* audio bytes.

    Returns ``/photos/voice_<uuid>.<ext>`` if ``PHOTO_DIR`` is set,
    otherwise a ``data:<mime>;base64,…`` URL for local dev.
    """
    if mime not in ALLOWED_AUDIO_MIMES:
        mime = "audio/webm"
    ext = _EXT_MAP.get(mime, "webm")

    audio_dir = _audio_dir_path()
    if audio_dir:
        filename = f"voice_{uuid.uuid4().hex}.{ext}"
        (audio_dir / filename).write_bytes(raw)
        return f"/photos/{filename}"

    b64 = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{b64}"
