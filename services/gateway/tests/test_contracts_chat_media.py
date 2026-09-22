import pytest
from contracts.chat.common import AudioFormat
from contracts.chat.media import MediaUploadOut
from pydantic import ValidationError


def test_media_upload_out_requires_uri_and_format() -> None:
    with pytest.raises(ValidationError):
        MediaUploadOut.model_validate({})
    out = MediaUploadOut(uri="media:abc123", format=AudioFormat.WEBM_OPUS)
    assert out.uri == "media:abc123"
    assert out.duration_ms is None


def test_media_upload_out_is_a_versioned_top_level_payload() -> None:
    # Unlike MediaRef (DECISIONS.md #9), this is a top-level response body,
    # so it carries contract_version.
    out = MediaUploadOut(uri="media:abc123", format=AudioFormat.MP3, duration_ms=1500)
    assert out.contract_version == "0.2.0"
