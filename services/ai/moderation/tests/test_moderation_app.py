"""
End-to-end through FastAPI with the stub backend: every reply validates
against the contract, the gating path is exercised, and every failure mode
ends in HOLD -- never in a silent allow, never in a 500.
"""

import json
import time
from pathlib import Path

import pytest
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.moderation import ModerationAction, ModerationDecision, ModerationLabel
from fastapi.testclient import TestClient
from services.ai.moderation.app import create_app
from services.ai.moderation.engine import (
    STUB_FAIL_TOKEN,
    STUB_GARBAGE_TOKEN,
    STUB_UNSURE_TOKEN,
)
from services.ai.moderation.settings import Settings


def _settings(policy_path: Path, **overrides) -> Settings:
    base = {
        "backend": "stub",
        "model_path": "",
        "policy_path": policy_path,
        "hold_threshold": 0.70,
        "block_threshold": 0.85,
        "max_text_chars": 200,
        "max_concurrent": 1,
        "timeout_s": 2.0,
        "port": 8003,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(scope="module")
def client(sample_policy_path: Path) -> TestClient:
    with TestClient(create_app(_settings(sample_policy_path))) as c:
        yield c


def _moderate(client: TestClient, text: str) -> ModerationDecision:
    resp = client.post("/v1/moderate", json={"contract_version": "0.1.0", "text": text})
    assert resp.status_code == 200, resp.text
    return ModerationDecision.model_validate(resp.json())


def test_health_live_and_ready(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "live"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["backend"] == "stub"
    assert body["policy_version"] == "policy@2026-10-01"
    assert body["taxonomy_source"] == "document"
    assert body["has_exemplars"] is True
    assert body["exemplars"]["A"] == 2


def test_contract_fixture_request_gets_a_valid_decision(
    client: TestClient, contract_fixtures_dir: Path
) -> None:
    payload = json.loads((contract_fixtures_dir / "moderation_request.json").read_text("utf-8"))
    resp = client.post("/v1/moderate", json=payload)
    assert resp.status_code == 200
    d = ModerationDecision.model_validate(resp.json())
    assert d.policy_version == "policy@2026-10-01"
    assert d.model_version == "stub-keywords@0"


@pytest.mark.parametrize(
    ("text", "label", "action"),
    [
        (
            "Om Sai Ram. May everyone have a peaceful morning.",
            ModerationLabel.A_DEVOTIONAL,
            ModerationAction.ALLOW,
        ),
        (
            "Bhajans this Thursday at 6 pm in the main hall.",
            ModerationLabel.B_ORGANIZATIONAL,
            ModerationAction.ALLOW,
        ),
        (
            "I am selling my old scooter, good condition, 25,000 rupees.",
            ModerationLabel.C_PERSONAL,
            ModerationAction.NUDGE,
        ),
        (
            "The way the bhajans are conducted now is completely wrong.",
            ModerationLabel.D_DISPUTATIONAL,
            ModerationAction.HOLD,
        ),
        (
            "Your bank account has been blocked. Call now and share your OTP.",
            ModerationLabel.E_HARMFUL,
            ModerationAction.BLOCK,
        ),
        # Devotional idiom with violent words must be A, not E.
        (
            "I want to kill my ego completely. Swami, help me destroy this pride.",
            ModerationLabel.A_DEVOTIONAL,
            ModerationAction.ALLOW,
        ),
    ],
)
def test_stub_walks_every_category_through_the_gate(
    client: TestClient, text: str, label: ModerationLabel, action: ModerationAction
) -> None:
    d = _moderate(client, text)
    assert d.label is label
    assert d.action is action
    assert not d.degraded.active
    if action is ModerationAction.ALLOW:
        assert d.nudge_text is None
    else:
        assert d.nudge_text and d.nudge_language is not None


def test_low_confidence_is_held_not_allowed(client: TestClient) -> None:
    d = _moderate(client, f"Om Sai Ram, peaceful morning. {STUB_UNSURE_TOKEN}")
    assert d.label is ModerationLabel.A_DEVOTIONAL
    assert d.action is ModerationAction.HOLD
    assert d.confidence < 0.70
    assert "below hold threshold" in d.rationale


def test_backend_error_fails_closed(client: TestClient) -> None:
    d = _moderate(client, f"anything {STUB_FAIL_TOKEN}")
    assert d.action is ModerationAction.HOLD
    assert d.degraded.active
    assert "backend error" in d.degraded.detail


def test_unparseable_output_fails_closed(client: TestClient) -> None:
    d = _moderate(client, f"anything {STUB_GARBAGE_TOKEN}")
    assert d.action is ModerationAction.HOLD
    assert d.degraded.active
    assert "unparseable" in d.degraded.detail


def test_empty_text_is_rejected_not_classified(client: TestClient) -> None:
    resp = client.post("/v1/moderate", json={"contract_version": "0.1.0", "text": "   "})
    assert resp.status_code == 422
    err = PipelineError.model_validate(resp.json())
    assert err.stage == "moderate.validate"


def test_oversized_text_is_rejected(client: TestClient) -> None:
    resp = client.post("/v1/moderate", json={"contract_version": "0.1.0", "text": "x" * 201})
    assert resp.status_code == 422
    err = PipelineError.model_validate(resp.json())
    assert err.detail == {"max_text_chars": 200, "received": 201}


def test_not_ready_returns_503_pipeline_error(sample_policy_path: Path) -> None:
    app = create_app(_settings(sample_policy_path))
    # No `with` -> lifespan never ran -> policy/model not loaded.
    resp = TestClient(app).post("/v1/moderate", json={"contract_version": "0.1.0", "text": "hi"})
    assert resp.status_code == 503
    assert PipelineError.model_validate(resp.json()).code is ErrorCode.MODEL_LOAD_FAILED


def test_timeout_fails_closed_and_frees_the_slot_afterwards(sample_policy_path: Path) -> None:
    class SlowStub:
        model_version = "slow@0"

        def load(self) -> None:
            pass

        def complete(self, messages):
            time.sleep(0.5)
            return '{"label": "A", "confidence": 0.9, "rationale": "late"}'

    app = create_app(_settings(sample_policy_path, timeout_s=0.1))
    app.state.moderation.classifier = SlowStub()
    with TestClient(app) as c:
        d = _moderate(c, "slow one")
        assert d.action is ModerationAction.HOLD
        assert d.degraded.active
        assert "timeout" in d.degraded.detail

        # The single slot is still held by the late inference; a second
        # request can't get it within its own timeout and is HELD as busy.
        d2 = _moderate(c, "second")
        assert d2.action is ModerationAction.HOLD
        assert "busy" in d2.degraded.detail

        # Once the slow call really finishes, the slot comes back.
        time.sleep(0.6)
        assert app.state.moderation.slots.acquire(blocking=False)
        app.state.moderation.slots.release()
