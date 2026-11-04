"""Synthetic image fixture, generated in pure Python (no PIL) so tests and evals stay dependency-free.

    python evals/fixtures.py            # writes data/samples/cracked_band.png

The picture is deliberately synthetic: a grey headband shape with a dark jagged
crack across it, labelled as such. It is NOT a photo of a real product. It gives
Gemini something real to look at in the multimodal demo and the tests a valid
PNG whose bytes we control.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

W, H = 320, 200


def _png(width: int, height: int, pixel) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def cracked_band_png() -> bytes:
    """A grey arc (headband) on white, with a jagged dark crack through it."""
    def pixel(x: int, y: int) -> tuple[int, int, int]:
        # headband: ring segment centred below the image
        cx, cy = W / 2, H + 40
        d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
        on_band = 150 <= d <= 200 and y < H - 20
        if not on_band:
            return (255, 255, 255)
        # crack: zig-zag line near the top centre
        zig = 6 if (y // 6) % 2 == 0 else -6
        if abs((x - W / 2) - zig - (y - 40) * 0.15) < 2.5 and 40 <= y <= 95:
            return (40, 30, 30)
        shade = 120 + int(40 * (d - 150) / 50)
        return (shade, shade, shade + 6)

    return _png(W, H, pixel)


def solid_jpeg_like_bytes() -> bytes:
    """Bytes that start like a JPEG but are not a full image: enough for type sniffing tests."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 64


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "data" / "samples" / "cracked_band.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(cracked_band_png())
    print(f"wrote {out} ({out.stat().st_size} bytes)", file=sys.stderr)
