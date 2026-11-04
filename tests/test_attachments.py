"""Image attachments: type sniffing, size cap, content addressing, and the contextvar handoff."""

import base64

import pytest
from fixtures import cracked_band_png, solid_jpeg_like_bytes

from agentops import attachments
from agentops.attachments import AttachmentError, parse_image, sniff_image_type


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_sniffs_real_types():
    assert sniff_image_type(cracked_band_png()) == "image/png"
    assert sniff_image_type(solid_jpeg_like_bytes()) == "image/jpeg"
    assert sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_image_type(b"GIF89a") is None
    assert sniff_image_type(b"<svg onload=alert(1)>") is None


def test_declared_type_must_match_bytes():
    png = cracked_band_png()
    att = parse_image(b64(png), "image/png")
    assert att.mime_type == "image/png" and att.size == len(png) and len(att.sha256) == 64
    with pytest.raises(AttachmentError, match="does not match"):
        parse_image(b64(png), "image/jpeg")  # declared JPEG, actually PNG


@pytest.mark.parametrize("payload, msg", [
    (b64(b"<svg onload=alert(1)>"), "only PNG, JPEG or WebP"),
    (b64(b"%PDF-1.7 ..."), "only PNG, JPEG or WebP"),
    ("not*base64*", "not valid base64"),
    (b64(b""), "empty"),
])
def test_rejects_non_images(payload, msg):
    with pytest.raises(AttachmentError, match=msg):
        parse_image(payload, "image/png")


def test_size_cap_enforced():
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (attachments.MAX_IMAGE_BYTES + 1)
    with pytest.raises(AttachmentError, match="exceeds"):
        parse_image(b64(big), "image/png")


def test_save_is_content_addressed_and_idempotent(tmp_path):
    att = parse_image(b64(cracked_band_png()), "image/png")
    p1 = attachments.save(att, tmp_path)
    p2 = attachments.save(att, tmp_path)
    assert p1 == p2 and p1.name == f"{att.sha256}.png" and p1.read_bytes() == att.data
    assert len(list(tmp_path.iterdir())) == 1


def test_current_attachment_is_scoped():
    att = parse_image(b64(cracked_band_png()), "image/png")
    assert attachments.get_current() is None
    tok = attachments.set_current(att)
    try:
        assert attachments.get_current() is att
    finally:
        attachments.reset_current(tok)
    assert attachments.get_current() is None
