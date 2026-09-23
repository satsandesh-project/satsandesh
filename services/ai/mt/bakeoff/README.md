# MT pivot adequacy spot-check (`services/ai/mt/bakeoff/`)

Evaluates the real `POST /v1/pivot` service's translation adequacy against
the proposal's **>= 4.0/5** target. Not part of the MT service itself, and
does not judge quality automatically — adequacy is a human, bilingual-fluency
call. This tooling only fetches real sentences, calls the real service, and
records what a real person rates.

## Workflow

1. **(Optional) Fill in real community sentences.** Copy
   `community_samples_template.txt` to `community_samples.txt` (same
   directory) and add ~10 real Telugu/Hindi sentences in an informal,
   voice-note register, per the instructions in the template. This step is
   optional — `run_batch.py` works fine with only the FLORES samples if you
   skip it.

2. **Run the batch.** Start the real MT service first, then run the batch
   script against it:

   ```bash
   cd services/ai
   ./.venv/Scripts/python.exe -m uvicorn mt.app:app --port 8004   # separate terminal, let it finish loading
   PYTHONPATH=../.. ./.venv/Scripts/python.exe mt/bakeoff/run_batch.py
   ```

   Reads `flores_samples.json` (always present) and `community_samples.txt`
   (if you created it), calls the real service for every entry, and writes
   `results.json`.

3. **Rate adequacy.**

   ```bash
   PYTHONPATH=../.. ./.venv/Scripts/python.exe mt/bakeoff/rate_adequacy.py
   ```

   Walks through every un-rated entry in `results.json`, shows the source
   sentence, the translation, and (for FLORES entries) the reference
   translation as context — not a target to match, since natural
   translations legitimately vary. Prompts for a 1-5 integer rating and an
   optional note; saves to `ratings.json` after every single entry, so
   quitting partway (Ctrl+C or typing `q`) never loses progress. Re-running
   later skips already-rated entries automatically; use
   `--redo <id>` to re-rate one specifically.

4. **Compile the baseline doc.**

   ```bash
   PYTHONPATH=../.. ./.venv/Scripts/python.exe mt/bakeoff/compile_baseline.py
   ```

   Reads `results.json` + `ratings.json`, computes mean adequacy overall and
   split by language pair (te-en / hi-en) and source type (FLORES /
   community), and writes `docs/MT_BASELINE.md` — an honest statement of
   where the real number lands against the >= 4.0 target, meets or not.

## Expected scale

Roughly **20 ratings**: the 20 FLORES-200 samples in `flores_samples.json`
(10 te-en + 10 hi-en, real professionally-translated sentences from the
official devtest release), plus however many real community sentences get
added in step 1. Quality/spread over quantity — this is a spot-check, not a
full benchmark run.

## What never leaves this machine via git

`services/ai/mt/bakeoff/.gitignore` excludes exactly three files:
`community_samples.txt`, `results.json`, `ratings.json`. All three can
contain real, human-provided sentences or translations/ratings derived from
them — never tracked, never staged, never committed, under any circumstance,
including a future "just commit everything" request. If sharing a result
with a teammate is ever needed, that's a deliberate manual action (e.g. a
direct file transfer), not something git does automatically.

`flores_samples.json` (FLORES-200, a public, CC-BY-SA-licensed benchmark
with no personal data) and `community_samples_template.txt` (empty of real
content) are **not** in that ignore list and are committed normally, same as
this README and the four scripts.

## Files

| File | Committed? | Contents |
|---|---|---|
| `flores_samples.json` | yes | 20 real FLORES-200 devtest sentence pairs (te-en, hi-en) |
| `community_samples_template.txt` | yes | empty instructions-only template |
| `community_samples.txt` | **no** | real human-provided sentences, once filled in |
| `run_batch.py` | yes | calls the real service, writes `results.json` |
| `results.json` | **no** | real translations + latency, once generated |
| `rate_adequacy.py` | yes | interactive 1-5 rating CLI, writes `ratings.json` |
| `ratings.json` | **no** | real human ratings, once generated |
| `compile_baseline.py` | yes | writes `docs/MT_BASELINE.md` from the two above |
