"""The gateway's client for the four AI services (transcribe, pivot,
moderate, render).

One job: turn "an HTTP call to a model server" into either a typed contract
response or an `AiCallError` that answers the only question the job worker
cares about -- is it worth retrying?

That needs care because the services fail in several different shapes, read
from their code (services/ai/*/app.py), not assumed:

  - not ready (still loading a model): 503 + a PipelineError body.
  - request this service can't serve: 422 + a PipelineError body
    (AUDIO_FETCH_FAILED, UNSUPPORTED_LANGUAGE, moderation's INTERNAL_ERROR).
  - a request that breaks the schema: FastAPI's DEFAULT 422
    `{"detail": [...]}` -- no service registers an exception handler, so this
    is NOT a PipelineError and must not be parsed as one.
  - an unexpected exception inside a service: a bare 500, text body.
  - the mock (services/ai/mock/) injects every ErrorCode as a 422, including
    the transient ones (TIMEOUT, OUT_OF_MEMORY), where a real service would
    answer 5xx -- so the code decides, not just the status.
  - `degraded` is data on a SUCCESSFUL 200 (render's per-language TTS_SKIPPED;
    moderation's fail-closed HOLD). That is a decision the orchestrator must
    see, so it is returned, never raised.

Sync (httpx.Client), because job handlers run in worker threads
(app/jobs.py). No retry loop of its own: retrying belongs to the job queue,
which counts attempts, backs off and dead-letters durably.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import Enum
from typing import TypeVar

import httpx
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.moderation import ModerationDecision, ModerationRequest
from contracts.ai.pivot import PivotRequest, PivotResponse
from contracts.ai.render import RenderRequest, RenderResponse
from contracts.ai.transcribe import TranscribeRequest, TranscribeResponse
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class AiStage(str, Enum):
    TRANSCRIBE = "transcribe"
    PIVOT = "pivot"
    MODERATE = "moderate"
    RENDER = "render"


_PATH_BY_STAGE: dict[AiStage, str] = {
    AiStage.TRANSCRIBE: "/v1/transcribe",
    AiStage.PIVOT: "/v1/pivot",
    AiStage.MODERATE: "/v1/moderate",
    AiStage.RENDER: "/v1/render",
}

# Fallbacks only; app/config.py's AI_*_TIMEOUT_S are what a deployment uses.
# Longer than each service's own limit (moderation gives up at 20s and answers
# HOLD -- a client that hung up first would turn that into a retry).
_DEFAULT_TIMEOUT_S: dict[AiStage, float] = {
    AiStage.TRANSCRIBE: 120.0,
    AiStage.PIVOT: 90.0,
    AiStage.MODERATE: 30.0,
    AiStage.RENDER: 180.0,
}

# A PipelineError code that describes the SERVICE's condition, not this
# request: worth another attempt later.
_TRANSIENT_CODES = frozenset(
    {ErrorCode.MODEL_LOAD_FAILED, ErrorCode.OUT_OF_MEMORY, ErrorCode.TIMEOUT}
)


class AiErrorKind(str, Enum):
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    NOT_READY = "not_ready"
    SERVER_ERROR = "server_error"
    REJECTED = "rejected"
    BAD_RESPONSE = "bad_response"


class AiCallError(Exception):
    """An AI-service call that did not produce a usable response.
    `retryable` is the whole point: True means the same request may succeed
    later (service down, loading, overloaded); False means it cannot, so the
    job should go dead now instead of burning its attempts."""

    def __init__(
        self,
        stage: AiStage,
        kind: AiErrorKind,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        code: ErrorCode | None = None,
    ) -> None:
        self.stage = stage
        self.kind = kind
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.code = code
        super().__init__(str(self))

    def __str__(self) -> str:
        parts = [f"{self.stage.value}: {self.kind.value}"]
        if self.status_code is not None:
            parts.append(f"HTTP {self.status_code}")
        if self.code is not None:
            parts.append(self.code.value)
        return f"{' / '.join(parts)} -- {self.message}"


def _pipeline_error(response: httpx.Response) -> PipelineError | None:
    """The body as a PipelineError, or None -- including for FastAPI's default
    `{"detail": [...]}`, which has no `code` and is a different thing."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or "code" not in body:
        return None
    try:
        return PipelineError.model_validate(body)
    except ValidationError:
        return None


def _classify(stage: AiStage, response: httpx.Response) -> AiCallError:
    status = response.status_code
    error = _pipeline_error(response)
    code = error.code if error else None

    transient_status = status >= 500 or status in (408, 429)
    retryable = transient_status or code in _TRANSIENT_CODES

    if status == 503 or code is ErrorCode.MODEL_LOAD_FAILED:
        kind = AiErrorKind.NOT_READY
    elif retryable:
        kind = AiErrorKind.SERVER_ERROR
    else:
        kind = AiErrorKind.REJECTED

    if error is not None:
        message = error.message
    else:
        message = f"HTTP {status} with no PipelineError body"
    return AiCallError(stage, kind, message, retryable=retryable, status_code=status, code=code)


class AiClient:
    def __init__(
        self,
        *,
        urls: Mapping[AiStage, str],
        timeouts: Mapping[AiStage, float] | None = None,
        transcribe_timeout_per_audio_s: float = 0.0,
        transcribe_timeout_max_s: float = 600.0,
        http: httpx.Client | None = None,
    ) -> None:
        missing = set(AiStage) - set(urls)
        if missing:
            raise ValueError(f"no URL for stage(s): {sorted(s.value for s in missing)}")
        self._urls = {stage: url.rstrip("/") for stage, url in urls.items()}
        self._timeouts = {**_DEFAULT_TIMEOUT_S, **(timeouts or {})}
        self._transcribe_per_audio_s = transcribe_timeout_per_audio_s
        self._transcribe_max_s = transcribe_timeout_max_s
        # trust_env=False: these are calls to our own services; a system proxy
        # setting must never be able to intercept them.
        self._http = http if http is not None else httpx.Client(trust_env=False)

    @classmethod
    def from_settings(cls, settings) -> AiClient:
        shared = settings.AI_SERVICE_URL
        urls = {
            AiStage.TRANSCRIBE: settings.AI_TRANSCRIBE_URL or shared,
            AiStage.PIVOT: settings.AI_PIVOT_URL or shared,
            AiStage.MODERATE: settings.AI_MODERATION_URL or shared,
            AiStage.RENDER: settings.AI_RENDER_URL or shared,
        }
        timeouts = {
            AiStage.TRANSCRIBE: settings.AI_TRANSCRIBE_TIMEOUT_S,
            AiStage.PIVOT: settings.AI_PIVOT_TIMEOUT_S,
            AiStage.MODERATE: settings.AI_MODERATION_TIMEOUT_S,
            AiStage.RENDER: settings.AI_RENDER_TIMEOUT_S,
        }
        return cls(
            urls=urls,
            timeouts=timeouts,
            transcribe_timeout_per_audio_s=settings.AI_TRANSCRIBE_TIMEOUT_PER_AUDIO_S,
            transcribe_timeout_max_s=settings.AI_TRANSCRIBE_TIMEOUT_MAX_S,
        )

    def url_for(self, stage: AiStage) -> str:
        return self._urls[stage]

    def timeout_for(self, stage: AiStage) -> float:
        return self._timeouts[stage]

    def transcribe_timeout_for(self, audio_duration_ms: int | None) -> float:
        """The ASR decode time grows with the note's length (and with how much text it emits), so a
        fixed timeout is right for one length only. The floor is `AI_TRANSCRIBE_TIMEOUT_S`; a note
        gets `AI_TRANSCRIBE_TIMEOUT_PER_AUDIO_S` seconds per second of audio, up to
        `AI_TRANSCRIBE_TIMEOUT_MAX_S`. The duration is the client's own declaration (it is not
        measured), which is why the cap exists: a client cannot buy a longer wait than the cap. An
        undeclared duration, or a per-audio factor of 0, gives the floor, as before."""
        floor = self._timeouts[AiStage.TRANSCRIBE]
        if not audio_duration_ms or self._transcribe_per_audio_s <= 0:
            return floor
        scaled = self._transcribe_per_audio_s * audio_duration_ms / 1000.0
        return max(floor, min(scaled, self._transcribe_max_s))

    def close(self) -> None:
        self._http.close()

    # -- the four stages ------------------------------------------------------

    def transcribe(self, request: TranscribeRequest) -> TranscribeResponse:
        timeout = self.transcribe_timeout_for(request.audio.duration_ms)
        return self._call(AiStage.TRANSCRIBE, request, TranscribeResponse, timeout=timeout)

    def pivot(self, request: PivotRequest) -> PivotResponse:
        return self._call(AiStage.PIVOT, request, PivotResponse)

    def moderate(self, request: ModerationRequest) -> ModerationDecision:
        return self._call(AiStage.MODERATE, request, ModerationDecision)

    def render(self, request: RenderRequest) -> RenderResponse:
        return self._call(AiStage.RENDER, request, RenderResponse)

    # -- plumbing -------------------------------------------------------------

    def _call(
        self,
        stage: AiStage,
        request: BaseModel,
        response_model: type[ResponseT],
        *,
        timeout: float | None = None,
    ) -> ResponseT:
        url = f"{self._urls[stage]}{_PATH_BY_STAGE[stage]}"
        timeout = self._timeouts[stage] if timeout is None else timeout
        try:
            response = self._http.post(url, json=request.model_dump(mode="json"), timeout=timeout)
        except httpx.TimeoutException as exc:
            raise AiCallError(
                stage,
                AiErrorKind.TIMEOUT,
                f"no answer from {url} within {timeout:g}s",
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise AiCallError(
                stage,
                AiErrorKind.UNREACHABLE,
                f"could not reach {url}: {type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc

        if response.status_code != 200:
            error = _classify(stage, response)
            logger.warning("AI call failed: %s", error)
            raise error

        try:
            return response_model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            # A 200 that is not valid JSON, or breaks the contract: the same
            # request will break it the same way, so retrying is pointless.
            raise AiCallError(
                stage,
                AiErrorKind.BAD_RESPONSE,
                f"{url} answered 200 but not with a valid {response_model.__name__}: "
                f"{type(exc).__name__}",
                retryable=False,
                status_code=response.status_code,
            ) from exc
