# Open questions for the Week-1 contract meeting

Things I could not settle unilaterally because they depend on decisions other
members own, or on team agreement about tradeoffs. Contracts ship as a proposal
against these; nothing below blocks Part A being usable from Week 2, but all of
it affects the real shape once settled.

1. **Who owns the real envelope, and what does it actually look like?**
   `contracts/ai/envelope.py` is my placeholder guess (`{type, id, ts, payload}`)
   so I could test standalone. The gateway owner needs to confirm the real shape
   — field names, whether `type` is a flat string or namespaced, whether there's
   a `correlation_id` or `reply_to` for routing responses back through the
   FastAPI/WebSocket backbone. My payload models don't depend on it either way,
   but the `MessageType` enum values I invented (`ai.transcribe.request`, etc.)
   are only useful if the team converges on something compatible.

2. **Where does audio actually live?** `AudioRef.uri` is an unconstrained
   string today because there's no gateway or blob storage in Week 1. Once
   someone owns storage (local disk for dev? S3-compatible object storage?
   something else?), `AudioRef` should probably get a stricter shape — either a
   validated URI scheme, or splitting into distinct `LocalAudioRef` /
   `RemoteAudioRef` types if the two need different handling. I did not want to
   guess a storage layer that isn't mine to decide.

3. **How do we add Tamil/Kannada later without breaking consumers?**
   `LanguageCode` is a closed enum by design (see DECISIONS.md #3). Adding a
   new member is backward-compatible for anyone constructing values, but breaks
   any consumer doing exhaustive `match`/`if-elif` over the enum without a
   default branch. Worth agreeing now: do we mandate a default/fallback arm in
   all `LanguageCode` matches from day one (so the future addition is a
   non-event), or accept that stretch-language rollout is a coordinated
   multi-service change?

4. **Should the mock server be able to emit `PipelineError` and non-2xx
   responses on demand?** Right now it always succeeds with canned data (plus a
   generic FastAPI 422 for malformed request bodies). If other members need to
   test their error-handling and degraded-mode UI paths against the mock
   server rather than constructing `PipelineError` by hand in their own tests, I
   can add a way to force a given error/degraded response (e.g. a header or a
   special input value) — wanted to confirm this is actually needed before
   adding surface area to the mock.

5. **What confidence threshold routes to `HOLD`, and who owns that number?**
   Deliberately not encoded in the schema (DECISIONS.md #8) because it's a
   policy/prompt concern that changes weekly. Confirming: does the moderation
   policy owner (me, but possibly reviewed by someone else for the "spiritual
   community" judgment calls) track this threshold outside the codebase, or
   should there be a `policy_version -> threshold` table somewhere in
   `services/ai/` once the real moderation service exists?

6. **Does `nudge_text` need to support more than one target action?**
   Currently only populated when `action == NUDGE`. If `HOLD` should also show
   a sender-facing message while awaiting review (mentioned as a possibility in
   the README), should that reuse `nudge_text`, or is a HOLD-specific message a
   different concept the schema doesn't have yet?

7. **`RenderRequest.target_languages` dedupes; is that the right failure mode
   for a genuinely-empty-after-dedup edge case?** Not currently reachable (dedup
   happens after the non-empty check, so `["hi"]` deduping to `["hi"]` is fine;
   you cannot submit `[]` in the first place), but worth the team confirming
   dedup-not-reject is the desired behavior generally, versus surfacing a
   warning back to the caller that a duplicate was silently dropped.

8. **CONTRACTS_VERSION bump policy** — I picked "bump on any field
   add/rename/retype/removal." Does the team want semver discipline here (major
   for breaking, minor for additive), or is a flat incrementing string enough
   given this is an internal contract with four consumers, not a public API?
