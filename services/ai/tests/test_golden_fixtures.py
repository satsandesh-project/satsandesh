"""
Golden fixtures are the Week-1 contract freeze made concrete: each committed JSON
file in fixtures/ is one canonical example of a payload. If a field is renamed,
retyped, or dropped without a version bump, this test fails in CI instead of the
drift being caught by memory at a later team meeting.
"""

import json
from pathlib import Path

import pytest
from contracts.ai.errors import PipelineError
from contracts.ai.moderation import ModerationDecision, ModerationRequest
from contracts.ai.pivot import PivotRequest, PivotResponse
from contracts.ai.render import RenderRequest, RenderResponse
from contracts.ai.transcribe import TranscribeRequest, TranscribeResponse
from pydantic import BaseModel

FIXTURES_DIR = Path(__file__).parent / "fixtures"

MODELS_BY_FIXTURE: dict[str, type[BaseModel]] = {
    "transcribe_request.json": TranscribeRequest,
    "transcribe_response.json": TranscribeResponse,
    "pivot_request.json": PivotRequest,
    "pivot_response.json": PivotResponse,
    "render_request.json": RenderRequest,
    "render_response.json": RenderResponse,
    "moderation_request.json": ModerationRequest,
    "moderation_decision.json": ModerationDecision,
    "pipeline_error.json": PipelineError,
}


@pytest.mark.parametrize("filename,model_cls", MODELS_BY_FIXTURE.items())
def test_golden_fixture_parses_and_round_trips(filename: str, model_cls: type[BaseModel]) -> None:
    fixture_path = FIXTURES_DIR / filename
    assert fixture_path.exists(), f"missing committed golden fixture: {filename}"
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    instance = model_cls.model_validate(raw)
    assert instance.model_dump(mode="json") == raw
