"""
Render engines: IndicTrans2 English -> Indic translation and Piper TTS.
See services/ai/render/README.md.

Two independent engines, each loaded once at startup:
  * MtEngine  -- indictrans2-en-indic-dist-200M (eng_Latn -> hin_Deva / tel_Telu)
  * TtsEngine -- one Piper voice per language, downloaded from rhasspy/piper-voices
                 (ungated) on first start and cached under the voice dir.

The wire contract speaks BCP-47 (en/hi/te); the FLORES-200 codes IndicProcessor
needs are mapped here, in the service layer, same as services/ai/mt/engine.py.
"""

from __future__ import annotations

import logging
import time
import wave
from dataclasses import dataclass
from importlib.metadata import version as _pkg_version
from pathlib import Path

from contracts.ai.language import LanguageCode

logger = logging.getLogger("services.ai.render.engine")

ENGLISH_FLORES = "eng_Latn"

_TARGET_FLORES: dict[LanguageCode, str] = {
    LanguageCode.HINDI: "hin_Deva",
    LanguageCode.TELUGU: "tel_Telu",
}


class UnsupportedTargetLanguageError(ValueError):
    """No en-indic mapping / Piper voice for this target language."""


def flores_code_for(language: LanguageCode) -> str:
    try:
        return _TARGET_FLORES[language]
    except KeyError as exc:
        raise UnsupportedTargetLanguageError(
            f"{language.value!r} has no en-indic target mapping "
            f"(supported: {sorted(c.value for c in _TARGET_FLORES)})"
        ) from exc


@dataclass
class TranslationResult:
    text: str
    duration_ms: float  # preprocess + inference + postprocess


class MtEngine:
    """Owns one IndicTrans2 en-indic model instance."""

    def __init__(
        self, model_name: str, device: str, num_beams: int, max_length: int, hf_token: str
    ) -> None:
        self._model_name = model_name
        self._requested_device = device
        self._num_beams = num_beams
        self._max_length = max_length
        self._hf_token = hf_token
        self._device: str | None = None
        self._processor = None
        self._tokenizer = None
        self._model = None
        self.load_duration_ms: float | None = None
        self.warmup_duration_ms: float | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_version(self) -> str:
        return f"{self._model_name}@indictranstoolkit-{_pkg_version('indictranstoolkit')}"

    def load(self) -> None:
        import torch
        from IndicTransToolkit.processor import IndicProcessor
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self._device = (
            ("cuda" if torch.cuda.is_available() else "cpu")
            if self._requested_device == "auto"
            else self._requested_device
        )
        start = time.perf_counter()
        self._processor = IndicProcessor(inference=True)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self._model_name, trust_remote_code=True, token=self._hf_token
        )
        self._model = AutoModelForSeq2SeqLM.from_pretrained(
            self._model_name, trust_remote_code=True, token=self._hf_token
        ).to(self._device)
        self._model.eval()
        self.load_duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "MT model loaded: %s on %s (%.1f ms)",
            self.model_version,
            self._device,
            self.load_duration_ms,
        )

    def warm_up(self, text: str, target_language: LanguageCode) -> None:
        start = time.perf_counter()
        self.translate(text, target_language)
        self.warmup_duration_ms = (time.perf_counter() - start) * 1000
        logger.info("MT warm-up complete (%.1f ms)", self.warmup_duration_ms)

    def translate(self, text: str, target_language: LanguageCode) -> TranslationResult:
        if self._model is None or self._processor is None or self._tokenizer is None:
            raise RuntimeError("translate() called before load()")
        import torch

        tgt = flores_code_for(target_language)
        start = time.perf_counter()
        batch = self._processor.preprocess_batch(
            [text], src_lang=ENGLISH_FLORES, tgt_lang=tgt, visualize=False
        )
        tokenized = self._tokenizer(
            batch,
            padding="longest",
            truncation=True,
            max_length=self._max_length,
            return_tensors="pt",
        ).to(self._device)
        with torch.inference_mode():
            generated = self._model.generate(
                **tokenized,
                num_beams=self._num_beams,
                num_return_sequences=1,
                max_length=self._max_length,
            )
        decoded = self._tokenizer.batch_decode(
            generated, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )
        outputs = self._processor.postprocess_batch(decoded, lang=tgt)
        return TranslationResult(text=outputs[0], duration_ms=(time.perf_counter() - start) * 1000)


@dataclass
class SynthesisResult:
    path: Path
    sample_rate_hz: int
    audio_duration_ms: int
    duration_ms: float  # wall-clock synthesis time


class TtsEngine:
    """Owns one loaded Piper voice per supported language."""

    PIPER_REPO = "rhasspy/piper-voices"

    def __init__(self, voices: dict[LanguageCode, str], voice_dir: Path) -> None:
        self._voice_ids = voices
        self._voice_dir = voice_dir
        self._voices: dict[LanguageCode, object] = {}
        self.load_durations_ms: dict[str, float] = {}
        self.warmup_durations_ms: dict[str, float] = {}

    @property
    def languages(self) -> set[LanguageCode]:
        return set(self._voices)

    @property
    def is_loaded(self) -> bool:
        return set(self._voices) == set(self._voice_ids)

    def model_version(self, language: LanguageCode) -> str:
        return f"piper:{self._voice_ids[language]}@piper-tts-{_pkg_version('piper-tts')}"

    @staticmethod
    def _repo_dir(voice_id: str) -> str:
        # te_IN-maya-medium -> te/te_IN/maya/medium
        locale, name, quality = voice_id.split("-")
        return f"{locale.split('_')[0]}/{locale}/{name}/{quality}"

    def _fetch(self, voice_id: str) -> Path:
        from huggingface_hub import hf_hub_download

        base = self._repo_dir(voice_id)
        self._voice_dir.mkdir(parents=True, exist_ok=True)
        for suffix in (".onnx.json", ".onnx"):
            hf_hub_download(
                self.PIPER_REPO,
                f"{base}/{voice_id}{suffix}",
                local_dir=self._voice_dir,
                token=False,  # rhasspy/piper-voices is public; never send the HF token there
            )
        return self._voice_dir / base / f"{voice_id}.onnx"

    def load(self) -> None:
        from piper import PiperVoice

        for language, voice_id in self._voice_ids.items():
            start = time.perf_counter()
            path = self._fetch(voice_id)
            self._voices[language] = PiperVoice.load(str(path))
            self.load_durations_ms[voice_id] = (time.perf_counter() - start) * 1000
            logger.info(
                "TTS voice loaded: %s (%.1f ms)", voice_id, self.load_durations_ms[voice_id]
            )

    def warm_up(self, texts: dict[LanguageCode, str], out_dir: Path) -> None:
        # First Piper synthesis is far slower than later ones (spike: 5.5 s vs 0.65 s),
        # so ready must not flip until each voice has synthesized once.
        for language, text in texts.items():
            path = out_dir / f".warmup_{language.value}.wav"
            start = time.perf_counter()
            self.synthesize(text, language, path)
            self.warmup_durations_ms[self._voice_ids[language]] = (
                time.perf_counter() - start
            ) * 1000
            path.unlink(missing_ok=True)

    def synthesize(self, text: str, language: LanguageCode, out_path: Path) -> SynthesisResult:
        voice = self._voices.get(language)
        if voice is None:
            raise UnsupportedTargetLanguageError(f"no Piper voice loaded for {language.value!r}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        with wave.open(str(out_path), "wb") as wav:
            voice.synthesize_wav(text, wav)  # type: ignore[attr-defined]
        elapsed = (time.perf_counter() - start) * 1000
        with wave.open(str(out_path), "rb") as wav:
            rate = wav.getframerate()
            duration_ms = round(wav.getnframes() / rate * 1000)
        return SynthesisResult(
            path=out_path, sample_rate_hz=rate, audio_duration_ms=duration_ms, duration_ms=elapsed
        )
