"""User-supplied image attachments (multimodal input).

Security posture
- The declared MIME type is never trusted: the type is sniffed from magic bytes
  and must match the declaration. Only PNG, JPEG and WebP are accepted.
- Size is capped before the bytes reach the model.
- Images are content-addressed (sha256). Nothing is written to disk unless a
  human confirms a ticket that references the image.
- Text visible inside an image is untrusted input, exactly like retrieved
  documents; the agent instruction says so, and every tool still enforces authz
  in backend code regardless of what the model was persuaded to do.

The attachment for the current turn is held in a contextvar set by the API
layer (like the active Trace), so tools can reference it without the model
being able to name, swap or forge an attachment.
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import hashlib
from dataclasses import dataclass
from pathlib import Path

MAX_IMAGE_BYTES = 4 * 1024 * 1024
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class AttachmentError(ValueError):
    pass


@dataclass(frozen=True)
class Attachment:
    mime_type: str
    data: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)

    def meta(self) -> dict:
        return {"sha256": self.sha256, "mime_type": self.mime_type, "bytes": self.size}


def sniff_image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def parse_image(data_base64: str, declared_mime: str) -> Attachment:
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError("image is not valid base64") from exc
    if not data:
        raise AttachmentError("image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise AttachmentError(f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    actual = sniff_image_type(data)
    if actual is None:
        raise AttachmentError("only PNG, JPEG or WebP images are accepted")
    if actual != declared_mime:
        raise AttachmentError(f"declared type {declared_mime} does not match file content ({actual})")
    return Attachment(mime_type=actual, data=data, sha256=hashlib.sha256(data).hexdigest())


def save(att: Attachment, directory: Path) -> Path:
    """Write the image under its content hash (idempotent). Called only on confirmed tickets."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{att.sha256}.{_EXT[att.mime_type]}"
    if not path.exists():
        path.write_bytes(att.data)
    return path


_current: contextvars.ContextVar[Attachment | None] = contextvars.ContextVar("agentops_attachment", default=None)


def set_current(att: Attachment | None) -> contextvars.Token:
    return _current.set(att)


def get_current() -> Attachment | None:
    return _current.get()


def reset_current(token: contextvars.Token) -> None:
    _current.reset(token)
