"""
IndicTrans2 (indic -> English direction only) loading, warm-up, and
translation. See services/ai/mt/README.md.

Usage pattern (preprocess -> tokenize -> generate -> decode -> postprocess)
follows AI4Bharat's own documented example for `IndicProcessor` +
`AutoModelForSeq2SeqLM`, confirmed against the installed indictranstoolkit
1.1.1 package metadata before this module was written.

Language codes: the wire contract (contracts/ai/language.py) speaks BCP-47
(en/hi/te) deliberately -- none of the models in this pipeline agree on a
code scheme, so the mapping to IndicProcessor's FLORES-200-style codes
(hin_Deva, tel_Telu, eng_Latn) is kept here, in the service layer, not in the
contract.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from importlib.metadata import version as _pkg_version

import torch
from contracts.ai.language import LanguageCode
from IndicTransToolkit.processor import IndicProcessor
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

logger = logging.getLogger("services.ai.mt.engine")

ENGLISH_FLORES = "eng_Latn"

# Only hi/te are valid *source* languages for this indic-en model -- en is
# handled as passthrough one layer up (app.py), never sent through the model.
_SOURCE_FLORES: dict[LanguageCode, str] = {
    LanguageCode.HINDI: "hin_Deva",
    LanguageCode.TELUGU: "tel_Telu",
}


class UnsupportedSourceLanguageError(ValueError):
    """source_language has no FLORES-200 mapping for this indic-en model."""


def flores_code_for(language: LanguageCode) -> str:
    try:
        return _SOURCE_FLORES[language]
    except KeyError as exc:
        raise UnsupportedSourceLanguageError(
            f"{language!r} has no indic-en source mapping (supported: "
            f"{sorted(c.value for c in _SOURCE_FLORES)})"
        ) from exc


@dataclass
class TranslationResult:
    text: str
    preprocess_duration_ms: float
    inference_duration_ms: float
    postprocess_duration_ms: float

    @property
    def total_duration_ms(self) -> float:
        return self.preprocess_duration_ms + self.inference_duration_ms + self.postprocess_duration_ms


class MtEngine:
    """Owns exactly one IndicTrans2 indic-en model instance, loaded once at
    startup, plus the IndicProcessor pre/post-processing pipeline."""

    def __init__(self, model_name: str, device: str, num_beams: int, max_length: int) -> None:
        self._model_name = model_name
        self._requested_device = device
        self._num_beams = num_beams
        self._max_length = max_length
        self._device: str | None = None
        self._processor: IndicProcessor | None = None
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
        self._device = (
            ("cuda" if torch.cuda.is_available() else "cpu")
            if self._requested_device == "auto"
            else self._requested_device
        )

        start = time.perf_counter()
        self._processor = IndicProcessor(inference=True)
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_name, trust_remote_code=True)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(
            self._model_name, trust_remote_code=True
        ).to(self._device)
        self._model.eval()
        self.load_duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "MT model loaded: %s on %s (%.1f ms)",
            self.model_version,
            self._device,
            self.load_duration_ms,
        )

    def warm_up(self, text: str, source_language: LanguageCode) -> None:
        if self._model is None:
            raise RuntimeError("warm_up() called before load()")
        start = time.perf_counter()
        self.translate(text, source_language)
        self.warmup_duration_ms = (time.perf_counter() - start) * 1000
        logger.info("MT warm-up translation complete (%.1f ms)", self.warmup_duration_ms)

    def translate(self, text: str, source_language: LanguageCode) -> TranslationResult:
        if self._model is None or self._processor is None or self._tokenizer is None:
            raise RuntimeError("translate() called before load()")

        src_flores = flores_code_for(source_language)

        pre_start = time.perf_counter()
        batch = self._processor.preprocess_batch(
            [text], src_lang=src_flores, tgt_lang=ENGLISH_FLORES, visualize=False
        )
        tokenized = self._tokenizer(
            batch, padding="longest", truncation=True, max_length=self._max_length, return_tensors="pt"
        ).to(self._device)
        preprocess_duration_ms = (time.perf_counter() - pre_start) * 1000

        infer_start = time.perf_counter()
        with torch.inference_mode():
            generated = self._model.generate(
                **tokenized,
                num_beams=self._num_beams,
                num_return_sequences=1,
                max_length=self._max_length,
            )
        inference_duration_ms = (time.perf_counter() - infer_start) * 1000

        post_start = time.perf_counter()
        decoded = self._tokenizer.batch_decode(
            generated, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )
        outputs = self._processor.postprocess_batch(decoded, lang=ENGLISH_FLORES)
        postprocess_duration_ms = (time.perf_counter() - post_start) * 1000

        return TranslationResult(
            text=outputs[0],
            preprocess_duration_ms=preprocess_duration_ms,
            inference_duration_ms=inference_duration_ms,
            postprocess_duration_ms=postprocess_duration_ms,
        )
