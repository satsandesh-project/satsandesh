from pydantic import Field, field_validator

from contracts.chat.common import AudioFormat, VersionedModel, validate_media_uri

# Re-exported so a caller only needs `from contracts.chat.media import
# AudioFormat` when working with this surface, without also needing to know
# it's defined in common.py alongside MediaRef.
__all__ = ["AudioFormat", "MediaUploadOut"]


class MediaUploadOut(VersionedModel):
    """Response body for `POST /media` — what a client gets back after
    uploading a raw audio file, to embed as `MessageIn.media_ref` on the
    `POST /messages` (or `message.send`) call that follows. Same fields as
    `MediaRef` (common.py) plus `contract_version`, since this is a
    top-level wire payload and `MediaRef` itself deliberately isn't
    (DECISIONS.md #9) — not a MediaRef reused/nested, to avoid a payload
    whose only top-level field is another model, which every other route in
    this package avoids.

    `format` here is the format of what's now actually stored at `uri` —
    ordinarily what the client uploaded, but not guaranteed to be, since a
    storage backend may transcode before persisting (see DECISIONS.md #15).
    `duration_ms` is carried through from whatever the client declared on
    upload; this package does not decode audio to measure it itself.

    Same `uri`/`duration_ms` constraints as MediaRef, via the shared
    validate_media_uri — see MediaRef's own docstring (common.py) and
    DECISIONS.md #13."""

    uri: str
    format: AudioFormat
    duration_ms: int | None = Field(default=None, ge=0)

    @field_validator("uri")
    @classmethod
    def _uri_must_be_scheme_qualified(cls, value: str) -> str:
        return validate_media_uri(value)
