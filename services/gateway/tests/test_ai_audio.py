"""Week 7 Phase 2: how a stored voice note is handed to the AI services.

The real ASR services resolve `AudioRef.uri` as a LOCAL FILESYSTEM PATH or a
`file://` URI (services/ai/speech/app.py::_resolve_local_path) -- they do not
understand the gateway's own `media:<id>` scheme, so sending that gets a 422
`AUDIO_FETCH_FAILED` for every note. The mock never opens the file, which is
why this only surfaced when reading the real services.

Provisional: a shared volume (the ASR container mounts the media store at
AI_AUDIO_MOUNT_ROOT) is one of two ways to close the gap; the other is a
contract change that is M3's call. Unset, behaviour is unchanged.
"""

import uuid
from urllib.parse import unquote, urlparse

import pytest
from contracts.ai.common import AudioFormat

from app.ai_audio import (
    UnsupportedAudioFormatError,
    ai_audio_format,
    ai_audio_ref,
    stored_file_name,
)
from app.media_storage import LocalDiskMediaStorage


class _Media:
    def __init__(self, fmt="wav_pcm16", duration_ms=1500):
        self.id = uuid.uuid4()
        self.format = fmt
        self.duration_ms = duration_ms


def test_without_a_mount_root_the_uri_stays_the_gateways_own_scheme() -> None:
    m = _Media()
    ref = ai_audio_ref(m, mount_root=None, webm_as_ogg_opus=False)
    assert ref.uri == f"media:{m.id}"


def test_with_a_mount_root_the_uri_is_a_file_uri_the_asr_service_can_resolve() -> None:
    m = _Media()
    ref = ai_audio_ref(m, mount_root="/ai-media", webm_as_ogg_opus=False)
    assert ref.uri.startswith("file:///ai-media/")
    # Resolve it exactly the way the real ASR service does.
    assert unquote(urlparse(ref.uri).path) == f"/ai-media/{m.id}.bin"


def test_a_trailing_slash_on_the_mount_root_does_not_double_up() -> None:
    m = _Media()
    ref = ai_audio_ref(m, mount_root="/ai-media/", webm_as_ogg_opus=False)
    assert unquote(urlparse(ref.uri).path) == f"/ai-media/{m.id}.bin"


def test_a_relative_mount_root_is_rejected_loudly() -> None:
    # A relative path would silently resolve against the ASR process's cwd.
    with pytest.raises(ValueError, match="absolute"):
        ai_audio_ref(_Media(), mount_root="media", webm_as_ogg_opus=False)


def test_the_file_name_matches_what_the_media_store_actually_writes(tmp_path) -> None:
    """The ASR service opens the file the media store wrote. If the store's
    naming ever changes, this fails instead of every transcription."""
    storage = LocalDiskMediaStorage(str(tmp_path))
    media_id = uuid.uuid4()
    storage.put(str(media_id), b"bytes")
    written = [p.name for p in tmp_path.iterdir()]
    assert written == [stored_file_name(str(media_id))]


def test_the_reference_carries_format_and_duration() -> None:
    ref = ai_audio_ref(
        _Media("ogg_opus", 4200), mount_root="/ai-media", webm_as_ogg_opus=False
    )
    assert ref.format is AudioFormat.OGG_OPUS
    assert ref.duration_ms == 4200


@pytest.mark.parametrize(
    "chat_format,expected",
    [
        ("wav_pcm16", AudioFormat.WAV_PCM16),
        ("ogg_opus", AudioFormat.OGG_OPUS),
        ("mp3", AudioFormat.MP3),
    ],
)
def test_formats_both_contracts_know_map_one_to_one(chat_format, expected) -> None:
    assert ai_audio_format(chat_format, webm_as_ogg_opus=False) is expected


def test_webm_opus_is_refused_by_default() -> None:
    # contracts/ai has no webm_opus; labelling it ogg_opus would be a lie in
    # the request. Which resolution to use is M3's decision (OPEN_QUESTIONS #2).
    with pytest.raises(UnsupportedAudioFormatError, match="webm_opus"):
        ai_audio_format("webm_opus", webm_as_ogg_opus=False)


def test_webm_opus_can_be_explicitly_opted_in_as_ogg_opus() -> None:
    # ffmpeg sniffs the container from the bytes (checked against a real
    # WebM/Opus file), so the stopgap decodes -- but it is an opt-in label lie.
    assert ai_audio_format("webm_opus", webm_as_ogg_opus=True) is AudioFormat.OGG_OPUS


def test_an_unknown_format_is_refused_even_with_the_webm_switch() -> None:
    with pytest.raises(UnsupportedAudioFormatError):
        ai_audio_format("flac", webm_as_ogg_opus=True)
