"""How a stored voice note is handed to the AI services.

Two facts, both read from services/ai/ rather than assumed:

1. The real ASR resolves `AudioRef.uri` as a LOCAL FILESYSTEM PATH or a
   `file://` URI (services/ai/speech/app.py::_resolve_local_path). It does not
   understand the gateway's own `media:<id>` scheme -- handing it that gets a
   422 `AUDIO_FETCH_FAILED` for every note. The mock never opens the file,
   which is why this was invisible until the real services were read.
2. contracts/ai/common.AudioFormat has no `webm_opus`, which is what a
   browser actually records.

Neither is settled (both are M3's call -- see OPEN_QUESTIONS.md), so each has
a switch whose default preserves today's behaviour:

- `mount_root` (AI_AUDIO_MOUNT_ROOT): where the ASR service sees the media
  store. Set -> a `file://` URI the ASR can open. Unset -> `media:<id>`.
- `webm_as_ogg_opus` (AI_ACCEPT_WEBM_AS_OGG_OPUS): label WebM/Opus as
  ogg_opus. Off -> webm_opus is refused.

Plain functions over plain values, no settings object and no database, so
they test without either.
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Protocol

from contracts.ai.common import AudioFormat as AiAudioFormat
from contracts.ai.common import AudioRef

# contracts/chat's format enum and contracts/ai's don't fully agree: webm_opus
# is a real, storable format here with no counterpart there.
_AI_FORMAT_BY_CHAT_FORMAT: dict[str, AiAudioFormat] = {
    "wav_pcm16": AiAudioFormat.WAV_PCM16,
    "ogg_opus": AiAudioFormat.OGG_OPUS,
    "mp3": AiAudioFormat.MP3,
}


class UnsupportedAudioFormatError(ValueError):
    """No honest contracts.ai AudioFormat exists for this stored format. A
    retry cannot change that, so callers treat it as permanent."""


class _StoredMedia(Protocol):
    id: uuid.UUID
    format: str
    duration_ms: int | None


def stored_file_name(media_id: str) -> str:
    """The file name app/media_storage.py's LocalDiskMediaStorage writes for
    `media_id`. Restated here because the ASR service opens that exact file;
    tests/test_ai_audio.py checks it against what the store really writes."""
    return f"{media_id}.bin"


def ai_audio_format(chat_format: str, *, webm_as_ogg_opus: bool) -> AiAudioFormat:
    if chat_format == "webm_opus" and webm_as_ogg_opus:
        return AiAudioFormat.OGG_OPUS
    try:
        return _AI_FORMAT_BY_CHAT_FORMAT[chat_format]
    except KeyError:
        raise UnsupportedAudioFormatError(
            f"no contracts.ai AudioFormat for chat format {chat_format!r} "
            "(see app/ai_audio.py and OPEN_QUESTIONS.md)"
        ) from None


def ai_audio_ref(
    media: _StoredMedia, *, mount_root: str | None, webm_as_ogg_opus: bool
) -> AudioRef:
    fmt = ai_audio_format(media.format, webm_as_ogg_opus=webm_as_ogg_opus)
    if mount_root:
        root = PurePosixPath(mount_root)
        if not root.is_absolute():
            raise ValueError(
                f"AI_AUDIO_MOUNT_ROOT must be an absolute path, got {mount_root!r} "
                "(a relative one would resolve against the ASR process's cwd)"
            )
        uri = (root / stored_file_name(str(media.id))).as_uri()
    else:
        uri = f"media:{media.id}"
    return AudioRef(uri=uri, format=fmt, duration_ms=media.duration_ms)
