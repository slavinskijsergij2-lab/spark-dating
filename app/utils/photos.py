"""Shared photo saving utility.

Compresses with PIL, then either:
  - Saves to PHOTO_DIR filesystem (if Railway Volume is mounted) → returns /photos/<name>
  - Falls back to base64 data URL (local dev without Volume)
"""
import base64
import io
import os
import uuid
from pathlib import Path

from PIL import Image, ImageOps

MAX_DIMENSION = (800, 800)
JPEG_QUALITY = 80


def _photo_dir_path() -> Path | None:
    d = os.getenv("PHOTO_DIR")
    if not d:
        return None
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_image_bytes(raw: bytes, prefix: str = "") -> str:
    """Compress raw image bytes and persist.

    Args:
        raw:    Raw image bytes (any PIL-supported format).
        prefix: Optional filename prefix, e.g. "chat_" or "profile_".

    Returns:
        URL string: "/photos/<name>.jpg" if Volume available, else base64 data URL.

    Raises:
        ValueError: if the bytes cannot be decoded as an image.
    """
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img.thumbnail(MAX_DIMENSION, Image.LANCZOS)
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=JPEG_QUALITY)
    except Exception as exc:
        raise ValueError(f"Cannot process image: {exc}") from exc

    data = buf.getvalue()
    photo_dir = _photo_dir_path()
    if photo_dir:
        filename = f"{prefix}{uuid.uuid4().hex}.jpg"
        (photo_dir / filename).write_bytes(data)
        return f"/photos/{filename}"

    return "data:image/jpeg;base64," + base64.b64encode(data).decode()
