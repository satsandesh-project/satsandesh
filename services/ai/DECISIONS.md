# Design decisions — `contracts/ai/` and `services/ai/mock/`

Decisions I made unilaterally while these were unblocked-but-unreviewed. Anything
here is a candidate for "why did you do it this way" in PR review — that's the
point of writing it down before the meeting, not after.

## 1. Language code standard: BCP-47, not any model's native code

`LanguageCode` uses BCP-47 primary subtags (`en`, `hi`, `te`) as the **wire**
contract. This is not what any of the four models natively speaks:

- faster-whisper uses its own short codes (mostly BCP-47-like, but not
  guaranteed for every language).
- IndicTrans2 uses FLORES-200 codes (`eng_Latn`, `hin_Deva`, `tel_Telu`).
- Indic-TTS/VITS uses per-voice model ids, not language codes at all.

So a per-model mapping table is genuinely required regardless of which standard
the contract picks — there is no standard all four models agree on. Given that,
the contract should speak the standard the *client* already knows (BCP-47 is
what HTTP `Accept-Language`, browsers, and most chat UIs use), not leak any one
model's internal vocabulary into the interface. The mapping
(`LanguageCode -> model-native code`) is Week 2 service-layer work, one function
per model family, not part of the contract. If IndicTrans2 is swapped for a
different MT model later, or Indic-TTS for a different TTS engine, the contract
does not change — only the mapping table does.

## 2. Enum, not a bare string, for language and every other closed set

`LanguageCode`, `AudioFormat`, `ModerationLabel`, `ModerationAction`,
`DegradedReason`, and `ErrorCode` are all closed Pydantic enums, never plain
`str` fields. A typo (`"telugu"` instead of `"te"`) fails validation at the
contract boundary instead of silently mis-routing a message three services
downstream. The cost is that adding a value later is a contract change, not a
config change — see Open Questions on how the team wants to handle that for
Tamil/Kannada.

## 3. Tamil and Kannada are not in the enum

Per the brief: stretch goals must not be assumed anywhere. The most literal way
to guarantee that is to make it impossible to construct a request or response
that claims Tamil/Kannada support — they are absent from `LanguageCode`, not
present-but-flagged. Downstream code that does `LanguageCode.TELUGU` etc. can't
accidentally reference a language that isn't shipped. Cost: adding `ta`/`kn`
later is additive for producers (old code still validates) but breaks any
consumer doing exhaustive `match`/`if-elif` chains over the enum without a
fallback branch — flagged in Open Questions.

## 4. Envelope is a standalone proposal, not a base class

`contracts/ai/envelope.py` exists so my own tests and the mock server can
round-trip a realistic message shape without a real gateway existing yet, but no
request/response model here imports it, subclasses it, or is validated against
it. The gateway owner defines the real envelope; my payloads are designed to
nest inside whatever that turns out to be. `test_payload_modules_do_not_import_envelope`
in `test_contracts_envelope.py` enforces this mechanically so it can't drift by
accident as the contracts evolve.

## 5. `contract_version` lives on every top-level payload, not on nested objects

`VersionedModel` (stamping `contract_version`) is the base for every
request/response/decision/error, but *not* for nested value objects
(`AudioRef`, `StageTiming`, `DegradedMode`, `RenderResult`). A `RenderResult`
inside a `RenderResponse` doesn't need its own independent version stamp — it's
never validated or transmitted on its own, only as part of its parent. Versioning
every nested object would be noise without adding any information a consumer
could act on differently.

## 6. `DegradedMode` is per-`RenderResult`, not just top-level on `RenderResponse`

`RenderRequest` fans out to multiple target languages in one call. If TTS is
skipped for Telugu under VRAM pressure while Hindi succeeds fully, a single
top-level `degraded` flag can't express that — the caller would have to guess
which language(s) are affected. Each `RenderResult` carries its own `degraded`;
`RenderResponse.degraded` is kept too, as a cheap "did anything in this batch
degrade" check for callers who don't need per-language granularity.

## 7. `RenderRequest.target_languages` dedupes silently instead of rejecting

Originally spec'd as reject-on-duplicate; changed to dedupe-preserving-order on
explicit instruction. Rationale: the caller assembling target languages for a
circle (e.g. from receiver profiles) may easily produce duplicates without it
being a caller bug worth failing the whole request over — two receivers who both
prefer Hindi is a normal case, not malformed input.

## 8. No confidence→action threshold enforced in the schema

`ModerationDecision.action` (`ALLOW`/`NUDGE`/`HOLD`/`BLOCK`) is independent of
`label` and `confidence` at the type level — nothing in the schema forces
`confidence < X` to imply `action == HOLD`. That threshold is a policy decision
tied to `policy_version`, which the brief says changes weekly; hardcoding it in
Pydantic validation would mean a schema change (and a `contract_version` bump)
every time the policy prompt is tuned. Enforcement belongs in the service layer,
versioned by `policy_version`, not in the wire contract.

## 9. `nudge_text`/`nudge_language` are separate fields from `rationale`

`rationale` is English, written for moderators/ops reading logs. `nudge_text` is
sender-facing and must be in the sender's own language — conflating the two
would force a choice between an English-only audit trail and an English-only
(unreadable) nudge for an elder user. Both fields default to `None` when no
nudge is produced (`ALLOW`/`BLOCK` decisions).

## 10. Audio is a reference (`AudioRef`), never inline bytes

Week 1 has no gateway and no blob storage. `AudioRef.uri` is an unconstrained
string today (a local file path in the mock server, `s3://...`-shaped in the
fixture examples) because there is no agreed storage layer yet to validate
against. This is intentionally loose — see Open Questions, this is the one most
likely to need a real format constraint once storage is decided.

## 11. Root-level files created out of necessity, not convention

Nothing existed before this work: no `CLAUDE.md`, no `contracts/` directory, no
packaging config anywhere in the repo. To keep `services/ai/` and `contracts/ai/`
testable completely standalone (per the brief) without creating any file inside
another member's future directory, `contracts/` and `services/` are left as
PEP 420 namespace packages (no `__init__.py` at those levels) and all tooling
config (`pyproject.toml`, `.gitignore`, `conftest.py`) lives inside `services/ai/`
only. No root-level `pyproject.toml`, `pytest.ini`, or `.gitignore` was created.
The one file touched outside my two owned directories was moving the repo itself
from `Documents/SatSandesh` to `Projects/SatSandesh` before any other member's
directories existed — flagged here for visibility, not something to defend in
review of the contracts themselves.
