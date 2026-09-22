"""
Classifier backends. Each one takes chat messages and returns the model's raw
text reply; prompt.py parses it and decide.py gates it. The service never
trusts a backend's output shape -- see parse_model_output.

Two backends:

  StubClassifier   -- deterministic keyword heuristic, no weights. For tests,
                      CI, and the orchestrator's dev loop. Its verdicts are
                      shaped like a model's (label + confidence + rationale)
                      so the whole gating path is exercised. Never production.

  LlamaCppClassifier -- Qwen2.5 3B Instruct (or any chat GGUF) via llama.cpp
                      on CPU, temperature 0, JSON-constrained output. The
                      proposal's target for the 4 GB card. Imported lazily so
                      the package has no hard dependency on llama-cpp-python.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Protocol

logger = logging.getLogger("services.ai.moderation.engine")


class ClassifierError(RuntimeError):
    """The backend failed to produce any reply (load failure, runtime error)."""


class Classifier(Protocol):
    @property
    def model_version(self) -> str: ...

    def load(self) -> None: ...

    def complete(self, messages: list[dict[str, str]]) -> str:
        """Return the model's raw reply text for these chat messages."""
        ...


# --------------------------------------------------------------------------
# Stub
# --------------------------------------------------------------------------

STUB_FAIL_TOKEN = "[[stub:fail]]"  # makes the stub raise, to exercise fail-closed
STUB_GARBAGE_TOKEN = "[[stub:garbage]]"  # makes the stub reply with non-JSON
STUB_UNSURE_TOKEN = "[[stub:unsure]]"  # forces a low-confidence verdict

# (pattern, label, confidence, rationale). First match wins; order matters:
# devotional idioms with violent words are checked before the E patterns.
_STUB_RULES: list[tuple[re.Pattern[str], str, float, str]] = [
    (
        re.compile(r"\b(kill|destroy|die to)\b.*\b(ego|pride|self)\b"),
        "A",
        0.90,
        "Devotional idiom.",
    ),
    (
        re.compile(r"\b(otp|bank account|send (money|\d+)|share your otp|lottery)\b"),
        "E",
        0.95,
        "Scam pattern.",
    ),
    (
        re.compile(r"\b(useless|fool|idiot|shut up|regret it|know where you live)\b"),
        "E",
        0.92,
        "Abuse or threat.",
    ),
    (
        re.compile(r"\b(that caste|those people should|driven out)\b"),
        "E",
        0.93,
        "Hate toward a group.",
    ),
    (
        re.compile(r"\b(do not want to live|no point in anything|end my life)\b"),
        "E",
        0.97,
        "Person in distress; must reach a human.",
    ),
    (
        re.compile(r"\b(selling|for sale|per jar|rupees|chit fund|job opening|send cvs?)\b"),
        "C",
        0.86,
        "Commerce or personal business.",
    ),
    (
        re.compile(r"\b(divorce|heard that|such a shame|where did that money)\b"),
        "C",
        0.78,
        "Gossip.",
    ),
    (re.compile(r"\b(forward this to|do not break the chain)\b"), "C", 0.88, "Chain message."),
    (
        re.compile(
            r"\b(completely wrong|stupid|favouritism|ashamed|misleading everyone|never like this before|only true one)\b"
        ),
        "D",
        0.82,
        "Criticism or dispute.",
    ),
    (
        re.compile(r"\b(bhajans?|satsang|seva|reminder|volunteers?|schedule|\d{1,2}\s?(am|pm))\b"),
        "B",
        0.90,
        "Organisational or informational.",
    ),
    (
        re.compile(r"\b(om|pray|prayer|grateful|swami|gita|blessing|peaceful|namaskaram)\b"),
        "A",
        0.91,
        "Devotional.",
    ),
]


class StubClassifier:
    """No model. See module docstring. Replies in the same JSON shape a real
    model is asked for, so prompt.parse_model_output is exercised too."""

    model_version = "stub-keywords@0"

    def load(self) -> None:
        return None

    def complete(self, messages: list[dict[str, str]]) -> str:
        text = messages[-1]["content"]
        if STUB_FAIL_TOKEN in text:
            raise ClassifierError("stub asked to fail")
        if STUB_GARBAGE_TOKEN in text:
            return "I think this is probably fine? A maybe."
        lowered = text.lower()
        label, confidence, rationale = "A", 0.75, "No policy signal found; default devotional."
        for pattern, rule_label, rule_conf, rule_rationale in _STUB_RULES:
            if pattern.search(lowered):
                label, confidence, rationale = rule_label, rule_conf, rule_rationale
                break
        if STUB_UNSURE_TOKEN in text:
            confidence = 0.40
        return json.dumps(
            {"label": label, "confidence": confidence, "rationale": f"[stub] {rationale}"}
        )


# --------------------------------------------------------------------------
# llama.cpp
# --------------------------------------------------------------------------

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["A", "B", "C", "D", "E"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
    },
    "required": ["label", "confidence", "rationale"],
}


class LlamaCppClassifier:
    """CPU inference through llama-cpp-python. Not exercised by the test
    suite (no weights in the repo); the Week-6 bring-up runs it for real."""

    def __init__(self, model_path: str, n_threads: int = 4, n_ctx: int = 4096) -> None:
        self._model_path = model_path
        self._n_threads = n_threads
        self._n_ctx = n_ctx
        self._llm = None
        self.load_duration_ms: float | None = None

    @property
    def model_version(self) -> str:
        name = self._model_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return f"llama.cpp:{name}"

    def load(self) -> None:
        try:
            from llama_cpp import Llama  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ClassifierError(
                "MOD_BACKEND=llama_cpp but llama-cpp-python is not installed "
                "(pip install -e '.[moderation]')"
            ) from exc
        start = time.perf_counter()
        try:
            self._llm = Llama(
                model_path=self._model_path,
                n_ctx=self._n_ctx,
                n_threads=self._n_threads,
                verbose=False,
            )
        except Exception as exc:  # pragma: no cover - depends on the environment
            raise ClassifierError(f"could not load {self._model_path}: {exc}") from exc
        self.load_duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "moderation model loaded: %s (%.0f ms)", self.model_version, self.load_duration_ms
        )

    def complete(self, messages: list[dict[str, str]]) -> str:
        if self._llm is None:
            raise ClassifierError("complete() called before load()")
        try:
            result = self._llm.create_chat_completion(
                messages=messages,
                temperature=0.0,
                max_tokens=160,
                response_format={"type": "json_object", "schema": _JSON_SCHEMA},
            )
        except Exception as exc:  # pragma: no cover - depends on the environment
            raise ClassifierError(f"inference failed: {exc}") from exc
        return result["choices"][0]["message"]["content"] or ""
