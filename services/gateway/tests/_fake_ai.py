"""A scriptable stand-in for the four AI services, for the orchestrator's
tests. Speaks the real contracts (contracts/ai/*) over httpx.MockTransport, so
the code under test goes through the real AiClient and real response parsing.

It is deliberately NOT services/ai/mock/: that mock returns canned values and
cannot be told "the moderation service is down" or "this transcript is
empty". Set attributes, or `errors[stage] = ...`, to script a scenario;
`calls` records every request in order.
"""

import json
import wave
from collections.abc import Callable
from pathlib import Path

import httpx
from contracts.ai.common import AudioFormat, AudioRef, DegradedMode, DegradedReason
from contracts.ai.moderation import (
    ModerationAction,
    ModerationDecision,
    ModerationLabel,
)
from contracts.ai.pivot import PivotResponse
from contracts.ai.render import RenderResponse, RenderResult
from contracts.ai.transcribe import TranscribeResponse

from app.ai_client import AiClient, AiStage


class FakeAi:
    def __init__(self, audio_dir: Path | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        # transcribe
        self.transcript = ("ఈ రోజు సత్సంగం ఎప్పుడు?", "te")
        # pivot
        self.pivot_text = "When is satsang today?"
        # moderate
        self.action = ModerationAction.ALLOW
        self.label = ModerationLabel.A_DEVOTIONAL
        self.degraded_verdict = False
        self.nudge_text: str | None = None
        # render
        self.tts_languages = {"hi", "te"}
        self.audio_dir = audio_dir
        self.write_audio_files = True
        # stage -> (status_code, json body) or an Exception to raise
        self.errors: dict[str, object] = {}
        # What the real speech service does with an undeclared note: it auto-detects, and answers 422
        # UNSUPPORTED_LANGUAGE when it guesses a language outside en/hi/te (staging, 2026-10-08: 'nn',
        # 'si', 'ur'). Set to the guessed code to reproduce; a request WITH a language_hint is fine,
        # unless `reject_even_with_hint` is also set.
        self.guess_unsupported: str | None = None
        self.reject_even_with_hint = False
        # called with the stage name at the moment a request arrives -- lets a test look at the
        # world (e.g. whether a database transaction is open) while "the service" is busy
        self.on_call: Callable[[str], None] | None = None
        self._wav_counter = 0

    # -- plumbing ---------------------------------------------------------------

    def client(self) -> AiClient:
        return AiClient(
            urls={stage: "http://ai.test" for stage in AiStage},
            http=httpx.Client(transport=httpx.MockTransport(self._handle)),
        )

    def stages(self) -> list[str]:
        return [stage for stage, _ in self.calls]

    def requests(self, stage: str) -> list[dict]:
        return [body for s, body in self.calls if s == stage]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        stage = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content)
        self.calls.append((stage, body))
        if self.on_call is not None:
            self.on_call(stage)
        if (
            stage == "transcribe"
            and self.guess_unsupported is not None
            and (body.get("language_hint") is None or self.reject_even_with_hint)
        ):
            return httpx.Response(
                422,
                json={
                    "code": "UNSUPPORTED_LANGUAGE",
                    "message": f"detected language {self.guess_unsupported!r} is not one of the "
                    "LanguageCode values this contract supports (en/hi/te)",
                },
            )
        scripted = self.errors.get(stage)
        if isinstance(scripted, Exception):
            raise scripted
        if scripted is not None:
            status, payload = scripted
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json=getattr(self, f"_{stage}")(body))

    # -- the four services ---------------------------------------------------------

    def _transcribe(self, body: dict) -> dict:
        text, language = self.transcript
        return TranscribeResponse(
            text=text,
            detected_language=language,
            model_version="fake-asr",
            duration_ms=10.0,
        ).model_dump(mode="json")

    def _pivot(self, body: dict) -> dict:
        return PivotResponse(
            pivot_text=self.pivot_text,
            source_language=body["source_language"],
            model_version="fake-mt",
            duration_ms=10.0,
        ).model_dump(mode="json")

    def _moderate(self, body: dict) -> dict:
        degraded = (
            DegradedMode(active=True, reason=DegradedReason.MODEL_FALLBACK, detail="timeout")
            if self.degraded_verdict
            else DegradedMode.ok()
        )
        return ModerationDecision(
            label=self.label,
            confidence=0.0 if self.degraded_verdict else 0.93,
            action=self.action,
            rationale="fake verdict",
            nudge_text=self.nudge_text,
            nudge_language="en" if self.nudge_text else None,
            policy_version="policy@fake",
            model_version="fake-moderation",
            degraded=degraded,
        ).model_dump(mode="json")

    def _render(self, body: dict) -> dict:
        results = []
        for language in body["target_languages"]:
            if language in self.tts_languages:
                results.append(self._spoken(body["pivot_text"], language))
            else:
                results.append(
                    RenderResult(
                        language=language,
                        text=body["pivot_text"],
                        audio=AudioRef(uri="", format=AudioFormat.WAV_PCM16, duration_ms=0),
                        model_version_translate="fake-render-mt",
                        model_version_tts="none",
                        duration_ms=1.0,
                        degraded=DegradedMode(
                            active=True, reason=DegradedReason.TEXT_ONLY, detail="no voice"
                        ),
                    )
                )
        return RenderResponse(results=results).model_dump(mode="json")

    def _spoken(self, pivot_text: str, language: str) -> RenderResult:
        self._wav_counter += 1
        name = f"render-{self._wav_counter}-{language}.wav"
        if self.audio_dir is not None and self.write_audio_files:
            with wave.open(str(self.audio_dir / name), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x01\x00" * 800)
        # A path as the RENDER container sees it -- not where the gateway
        # mounts the same directory. The gateway must use the file name only.
        return RenderResult(
            language=language,
            text=f"[{language}] {pivot_text}",
            audio=AudioRef(
                uri=f"file:///render/output/{name}",
                format=AudioFormat.WAV_PCM16,
                duration_ms=50,
                sample_rate_hz=16000,
            ),
            model_version_translate="fake-render-mt",
            model_version_tts=f"fake-tts:{language}",
            duration_ms=1.0,
        )
