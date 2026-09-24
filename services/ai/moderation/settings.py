"""
Typed startup configuration for the moderation service.

Same shape as services/ai/speech/settings.py: every value has a visible
default so `uvicorn services.ai.moderation.app:app` works from a clean
checkout, and a *present but invalid* override raises SettingsError at import
time rather than failing later inside the model loader.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_ALLOWED_BACKENDS = {"stub", "llama_cpp"}


class SettingsError(RuntimeError):
    """Missing or invalid services/ai/moderation configuration."""


def _env_float(name: str, default: str, lo: float, hi: float) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name}={raw!r} is not a number") from exc
    if not (lo <= value <= hi):
        raise SettingsError(f"{name} must be in [{lo}, {hi}], got {value}")
    return value


def _env_int(name: str, default: str, lo: int, hi: int) -> int:
    raw = os.environ.get(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name}={raw!r} is not an integer") from exc
    if not (lo <= value <= hi):
        raise SettingsError(f"{name} must be in [{lo}, {hi}], got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    backend: str
    model_path: str
    policy_path: Path
    hold_threshold: float
    block_threshold: float
    max_text_chars: int
    max_concurrent: int
    timeout_s: float
    two_pass: bool
    rationale_max_tokens: int
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        # `stub` is a deterministic keyword heuristic that runs with no model
        # weights -- for tests, the orchestrator's dev loop, and CI. It is
        # never the production backend; /health/ready says which one is live.
        backend = os.environ.get("MOD_BACKEND", "stub").strip()
        if backend not in _ALLOWED_BACKENDS:
            raise SettingsError(
                f"MOD_BACKEND={backend!r} is not one of {sorted(_ALLOWED_BACKENDS)}"
            )

        # Path to a GGUF file for the llama_cpp backend. The proposal's target
        # is Qwen2.5 3B Instruct at 4-bit (fits the 4 GB card, runs on CPU).
        # Only required when MOD_BACKEND=llama_cpp.
        model_path = os.environ.get("MOD_MODEL_PATH", "").strip()
        if backend == "llama_cpp" and not model_path:
            raise SettingsError("MOD_MODEL_PATH is required when MOD_BACKEND=llama_cpp")

        # The policy document *is* the prompt's source of truth: categories,
        # actions, exemplars, notices all come from docs/policy-taxonomy.md,
        # so a policy change is a docs PR the liaison approves, not a code
        # change (proposal section 11, "policy-as-code").
        policy_raw = os.environ.get("MOD_POLICY_PATH", "").strip()
        policy_path = Path(policy_raw) if policy_raw else _REPO_ROOT / "docs" / "policy-taxonomy.md"

        # Gating (see decide.py). Below hold_threshold nothing is ever
        # allowed, nudged or blocked on the model's word -- it is held for a
        # human. BLOCK additionally needs block_threshold, because a wrong
        # block is the most visible failure the product can have.
        hold_threshold = _env_float("MOD_HOLD_THRESHOLD", "0.70", 0.0, 1.0)
        block_threshold = _env_float("MOD_BLOCK_THRESHOLD", "0.85", 0.0, 1.0)
        if block_threshold < hold_threshold:
            raise SettingsError(
                f"MOD_BLOCK_THRESHOLD ({block_threshold}) must be >= "
                f"MOD_HOLD_THRESHOLD ({hold_threshold})"
            )

        # Bounded input (docs/security-checklist.md, Part A). A 30-second
        # voice note is a few hundred characters of pivot text; 4000 is
        # generous for a typed message and small enough that a prompt never
        # blows the model's context.
        max_text_chars = _env_int("MOD_MAX_TEXT_CHARS", "4000", 1, 100_000)

        # CPU inference is serialised: N requests degrade to a queue, not to
        # thrashing. 1 is right for a 4 GB laptop card / shared CPU.
        max_concurrent = _env_int("MOD_MAX_CONCURRENT", "1", 1, 64)

        # A classification that takes longer than this is treated as a model
        # failure and the message is HELD (fail closed to a human), never
        # dropped and never allowed by default.
        timeout_s = _env_float("MOD_TIMEOUT_S", "20", 0.1, 600)

        # Two-pass output (services/ai/moderation/classify.py): ask for
        # {label, confidence} first, and generate the moderator-facing
        # rationale only when the gated action is not ALLOW. On by default
        # -- it is the difference between ~10s and ~2-3s per message for
        # the 85-95% of traffic that auto-allows (README, "Measured").
        # Set MOD_TWO_PASS=0 to go back to one call per message.
        two_pass = os.environ.get("MOD_TWO_PASS", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        rationale_max_tokens = _env_int("MOD_RATIONALE_MAX_TOKENS", "80", 8, 512)

        # 8003: 8001 is the mock server, 8002 the real ASR service.
        port = _env_int("MOD_PORT", "8003", 1, 65535)

        return cls(
            backend=backend,
            model_path=model_path,
            policy_path=policy_path,
            hold_threshold=hold_threshold,
            block_threshold=block_threshold,
            max_text_chars=max_text_chars,
            max_concurrent=max_concurrent,
            timeout_s=timeout_s,
            two_pass=two_pass,
            rationale_max_tokens=rationale_max_tokens,
            port=port,
        )
