"""
The moderation service: POST /v1/moderate against contracts/ai/moderation.py.

Request path: bound the input -> build the prompt from the policy document ->
ask the backend (serialised, with a timeout) -> parse strictly -> gate
(decide.py) -> stamp policy_version / model_version -> reply. Every failure
on that path ends in a HOLD with `degraded` set, never in a silent allow or a
silent drop.

Run:  PYTHONPATH=. uvicorn services.ai.moderation.app:app --port 8003
(from the repository root). See README.md next to this file.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import asynccontextmanager
from typing import Any

from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.moderation import ModerationDecision, ModerationRequest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from services.ai.moderation.classify import classify
from services.ai.moderation.decide import decide
from services.ai.moderation.engine import (
    Classifier,
    LlamaCppClassifier,
    StubClassifier,
)
from services.ai.moderation.policy import Policy, load_policy
from services.ai.moderation.settings import Settings

logger = logging.getLogger("services.ai.moderation.app")


def _pipeline_error(
    code: ErrorCode,
    message: str,
    stage: str,
    status_code: int,
    detail: dict[str, Any] | None = None,
) -> JSONResponse:
    error = PipelineError(code=code, message=message, stage=stage, detail=detail)
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


def _make_classifier(settings: Settings) -> Classifier:
    if settings.backend == "llama_cpp":
        return LlamaCppClassifier(model_path=settings.model_path)
    return StubClassifier()


class _State:
    """Everything the routes need, built once in the lifespan."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.policy: Policy | None = None
        self.classifier: Classifier = _make_classifier(settings)
        self.ready = False
        # One worker per allowed concurrent inference; the semaphore is what
        # actually bounds it (a request that can't get a slot within the
        # timeout is HELD, not queued forever).
        self.executor = ThreadPoolExecutor(max_workers=settings.max_concurrent)
        self.slots = threading.Semaphore(settings.max_concurrent)

    def start(self) -> None:
        self.policy = load_policy(self.settings.policy_path)
        self.classifier.load()
        self.ready = True
        counts = self.policy.exemplar_count()
        if not self.policy.has_exemplars:
            logger.warning(
                "policy %s has no exemplars for %s -- classifier is running on category "
                "descriptions alone; not fit for the pilot until the workshop output lands",
                self.policy.version,
                [k for k, v in counts.items() if v == 0],
            )
        logger.info(
            "moderation ready: backend=%s model=%s policy=%s exemplars=%s",
            self.settings.backend,
            self.classifier.model_version,
            self.policy.version,
            counts,
        )


def create_app(settings: Settings) -> FastAPI:
    state = _State(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.start()
        yield
        state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="SatSandesh AI — Moderation (stewardship classifier)",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.moderation = state  # tests reach in here

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        if not state.ready or state.policy is None:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "backend": settings.backend,
                "model_version": state.classifier.model_version,
                "policy_version": state.policy.version,
                "policy_path": str(state.policy.source_path),
                "taxonomy_source": state.policy.taxonomy_source,
                "notices_source": state.policy.notices_source,
                "exemplars": state.policy.exemplar_count(),
                "has_exemplars": state.policy.has_exemplars,
                "two_pass": settings.two_pass,
                "hold_threshold": settings.hold_threshold,
                "block_threshold": settings.block_threshold,
            },
        )

    # A plain `def`, not `async def`: FastAPI runs it in a worker thread, so
    # a 10-second CPU inference never freezes the event loop (and /health/live
    # keeps answering while it runs). Same point raised on PR #52.
    @app.post("/v1/moderate", response_model=None)
    def moderate(request: ModerationRequest) -> ModerationDecision | JSONResponse:
        if not state.ready or state.policy is None:
            return _pipeline_error(
                ErrorCode.MODEL_LOAD_FAILED,
                "moderation service is not ready (policy or model still loading)",
                stage="moderate.startup",
                status_code=503,
            )
        policy = state.policy

        text = request.text
        if not text or not text.strip():
            return _pipeline_error(
                ErrorCode.INTERNAL_ERROR,
                "text must not be empty",
                stage="moderate.validate",
                status_code=422,
            )
        if len(text) > settings.max_text_chars:
            return _pipeline_error(
                ErrorCode.INTERNAL_ERROR,
                f"text is {len(text)} characters; the limit is {settings.max_text_chars}",
                stage="moderate.validate",
                status_code=422,
                detail={"max_text_chars": settings.max_text_chars, "received": len(text)},
            )

        common = {
            "policy": policy,
            "model_version": state.classifier.model_version,
            "hold_threshold": settings.hold_threshold,
            "block_threshold": settings.block_threshold,
        }

        deadline = time.monotonic() + settings.timeout_s
        if not state.slots.acquire(timeout=settings.timeout_s):
            return decide(None, failure_detail="classifier busy; no slot within timeout", **common)

        # The whole classification -- pass 1, the gate, and the conditional
        # rationale pass -- runs as ONE unit of work, so it holds one slot
        # under one timeout. See classify.py for why the passes can't be
        # accounted for separately.
        future: Future[ModerationDecision] = state.executor.submit(
            classify,
            text,
            policy=policy,
            classifier=state.classifier,
            hold_threshold=settings.hold_threshold,
            block_threshold=settings.block_threshold,
            two_pass=settings.two_pass,
            rationale_max_tokens=settings.rationale_max_tokens,
        )
        # The slot is released when the inference actually finishes -- not
        # when we stop waiting for it. A timed-out inference still occupies
        # the CPU; letting the next request start would only thrash.
        future.add_done_callback(lambda _f: state.slots.release())

        try:
            # classify() handles backend and parse failures itself, always
            # returning a decision; only the timeout and a genuinely
            # unexpected error surface here.
            return future.result(timeout=max(0.0, deadline - time.monotonic()))
        except FutureTimeout:
            logger.warning("moderation timed out after %.1fs", settings.timeout_s)
            return decide(None, failure_detail=f"timeout after {settings.timeout_s:g}s", **common)
        except Exception as exc:
            logger.exception("unexpected moderation failure")
            return decide(None, failure_detail=f"unexpected: {type(exc).__name__}", **common)

    return app


app = create_app(Settings.from_env())
