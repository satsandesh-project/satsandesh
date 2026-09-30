# ASR Spike: AI4Bharat IndicConformer as a faster-whisper Alternative

**Status:** Full spike complete. Native install works, the HF gate was accepted by the
user, and one real inference run against `mobile_01.wav` produced a coherent Telugu
transcript — a sharp contrast to faster-whisper `small`'s repetition-loop garbage on the
same file. See Step 4 for the verbatim transcript and the accuracy caveat.

**Context:** faster-whisper `small` has a confirmed, measured accuracy failure on real
Telugu speech — repetition-loop garbage output on two independent clean mobile recordings
(`services/ai/bakeoff/samples/mobile_01.wav`, `mobile_02.wav`) and a third sample
(`winvoicerec_test.wav`) from a prior session. This spike investigates AI4Bharat's
IndicConformer as a candidate replacement. `services/ai/speech/` (faster-whisper) was
**not touched** — it remains the working fallback.

**Branch:** `feat/ai-asr-indicconformer-spike`, based on `feat/ai-speech-asr-w5` (see
"Branch base" below for why).

---

## Branch base

`feat/ai-speech-asr-w5` has commits not present in `main` (the real ASR speech service,
ffmpeg decoding wiring, the bake-off mic CLI) and has not been merged into `main` —
`git merge-base --is-ancestor feat/ai-speech-asr-w5 main` returned false, and there is no
merged/closed PR for it visible from this environment (`gh` is unauthenticated here).
It is "still open" by the task's definition, and it's the branch that actually contains
the ASR service and bake-off harness this spike needs to compare against. Based the new
branch on it rather than on `main`.

The work was done in a separate git worktree
(`C:\Users\admin\Projects\SatSandesh-worktrees\asr-indicconformer-spike`) rather than
switching the primary checkout, so the uncommitted changes already sitting in the main
working directory (on `feat/ai-demo-console`) were never touched. The bake-off `.wav`
samples are gitignored and only existed on disk in the primary checkout, so they were
copied (not committed — still gitignored) into the worktree for testing.

---

## Step 1 — What model actually exists

Web search + direct HuggingFace API/model-card reads (not memory). Sources:

- https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual (README fetched raw via `resolve/main/README.md`, and metadata via `api/models/...`)
- https://huggingface.co/ai4bharat/indicconformer_stt_te_hybrid_ctc_rnnt_large
- https://huggingface.co/ai4bharat/indicconformer_stt_hi_hybrid_ctc_rnnt_large
- https://github.com/AI4Bharat/IndicConformerASR
- https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual/discussions/18

**Two distinct model families exist, not one:**

1. **Per-language "large" models** — e.g. `ai4bharat/indicconformer_stt_te_hybrid_ctc_rnnt_large`
   (Telugu), `ai4bharat/indicconformer_stt_hi_hybrid_ctc_rnnt_large` (Hindi). 120M-parameter
   conformer-large encoder (17 blocks, 512-dim), hybrid CTC-RNNT decoder. Model card
   explicitly requires **AI4Bharat's NeMo fork**: `git clone
   https://github.com/AI4Bharat/NeMo.git && cd NeMo && git checkout nemo-v2 && bash
   reinstall.sh`. No ONNX or plain-PyTorch path documented for these.

2. **`ai4bharat/indic-conformer-600m-multilingual`** — one 600M-parameter multilingual
   model covering all 22 official Indian languages including both Telugu (`te`) and
   Hindi (`hi`). This is the one that matters for this spike: the HF repo's `config.json`
   declares `AutoModel: model_onnx.IndicASRModel`, the repo tags include `onnx`, and the
   file list contains ONNX assets directly — `assets/encoder.onnx`, `assets/ctc_decoder.onnx`,
   `assets/joint_enc.onnx`, `assets/joint_post_net_te.onnx`, `assets/joint_post_net_hi.onnx`,
   plus one `joint_post_net_<lang>.onnx` per supported language. **This confirms an
   ONNX-exported, non-NeMo path exists**, per the task's Step 1.5 question. It does not
   require NeMo.

   Model card's stated install/usage (quoted verbatim from README.md):
   ```
   pip install transformers torchaudio "onnxruntime==1.20.1" "onnx==1.20.1" "onnxruntime-gpu==1.20.1"

   from transformers import AutoModel
   model = AutoModel.from_pretrained("ai4bharat/indic-conformer-600m-multilingual", trust_remote_code=True)
   transcription_ctc = model(wav, "hi", "ctc")
   transcription_rnnt = model(wav, "hi", "rnnt")
   ```

**Licensing/gating:** Both families are MIT-licensed but **gated** on HuggingFace. The
600M multilingual repo's API metadata reports `"gated":"auto"`, and the repo's own page
states verbatim: *"You need to agree to share your contact information to access this
model."* This is the same gate pattern as `indictrans2-indic-en` from the prior MT spike
— "auto" means access is granted automatically once a logged-in HF account accepts the
terms, not a manual human review, but it still requires accepting terms under an HF
account before any file download succeeds. Confirmed by direct test (see Step 2).

**Published accuracy numbers:** None from AI4Bharat officially for Telugu or Hindi found
on the model card itself. One community-submitted (not AI4Bharat) pull request titled
"Add Vaani-Benchmark-V1.0 Hindi WER eval results (RNNT decoding)"
(discussion #18, submitted by a third-party contributor, "Ready to merge") exists on the
600M model's HF discussions, but the actual WER number was not visible in the fetched
content. **No Telugu WER number was found anywhere** — not fabricating one.

---

## Step 2 — Native Windows install (throwaway venv, not `services/ai/.venv`)

Venv: `C:\Users\admin\AppData\Local\Temp\claude\indicconformer_venv` (Python 3.11.5, via
the `py` launcher — the bare `python` command on this machine resolves to the Microsoft
Store stub and does not work).

Ran the model card's exact install command (minus `onnxruntime-gpu`, since there's no
CUDA GPU path being tested here):

```
pip install transformers torchaudio "onnxruntime==1.20.1" "onnx==1.20.1"
```

**Result: succeeded, exit code 0.** Installed `transformers-5.17.0`, `torchaudio-2.11.0`,
`onnxruntime-1.20.1`, `onnx-1.20.1`, plus their dependency tree — all as native Windows
wheels (`win_amd64`), no compilation, no Linux-only package errors. This is a sharp
contrast to the prior IndicTransToolkit/NeMo spikes.

**One gap found and fixed:** `torchaudio` did not pull in `torch` itself as a dependency
in this resolve. `pip install torch` was run separately and succeeded (`torch-2.14.0+cpu`,
124 MB wheel, CPU build — no CUDA detected/requested). Final versions:

```
torch          2.14.0+cpu
torchaudio     2.11.0+cpu
transformers   5.17.0
onnxruntime    1.20.1
```

Note the `torch`/`torchaudio` version skew (2.14.0 vs. 2.11.0) — unusual, but all four
libraries imported cleanly with no error.

**Audio loading gap found and worked around:** the model card's example calls
`torchaudio.load(...)`. On this install, `torchaudio.load()` requires the `torchcodec`
package plus a "full-shared" FFmpeg build that exposes dynamic libavcodec/libavformat
DLLs. This machine has FFmpeg 9.0.1 installed (`gyan.dev` full_build, static), but
`torchcodec` could not load its native library against it — confirmed failure, not
assumed:

```
OSError: Could not load this library: ...\torchcodec\libtorchcodec_core9.dll
```

(and the same for versions 8 down to 4 — torchcodec tries each in turn and all fail
against this FFmpeg build). Rather than chase down a Windows FFmpeg "full-shared" build
to fix `torchaudio.load()`, the spike bypassed it: the bake-off `.wav` files are already
plain 16-bit PCM mono 16kHz WAV (verified: `RIFF ... WAVE ... Microsoft PCM, 16 bit, mono
16000 Hz`), so they load correctly with Python's built-in `wave` module + `numpy`, no
torchcodec/ffmpeg dependency needed at all. This is a viable permanent solution for this
project's WAV-only inputs, not just a spike workaround — confirmed working:

```
sr 16000  channels 1  frames 232448
wav tensor shape torch.Size([1, 232448])  dtype torch.float32
```

**Conclusion: the native Windows runtime for `indic-conformer-600m-multilingual` is
fully viable.** No NeMo, no WSL2, no Docker needed. Step 3 (WSL2/Docker) was not
attempted because native install succeeded — no need to fall back.

---

## Step 3 — WSL2 / Docker

Not needed — native Step 2 succeeded. For completeness, current state was checked:
WSL2 has an `Ubuntu` distro registered (state: Stopped, WSL version 2) and Docker
Desktop 29.7.2 is installed but its daemon is not currently running. Both match the
"already proven working" state from the prior MT spike and remain available as a
fallback if the ONNX path had failed, but were not exercised here.

---

## Step 4 — Real inference

**Gate check (before download):** confirmed with a direct, non-destructive test —
attempting to download one asset file (`assets/encoder.onnx`) via
`huggingface_hub.hf_hub_download`, unauthenticated, before asking the user for a token:

```
ERROR TYPE: GatedRepoError
403 Client Error ... Cannot access gated repo for url
https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual/resolve/main/assets/encoder.onnx.
Access to model ai4bharat/indic-conformer-600m-multilingual is restricted and you are not
in the authorized list. Visit https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual
to ask for access.
```

Per this spike's instructions, this is the same situation as the `indictrans2-indic-en`
gate in the prior MT spike, so the run stopped and asked the user before downloading
anything further. The user accepted the model's terms on huggingface.co and supplied a
personal HF read token (passed only as an `HF_TOKEN` environment variable to the
inference script for this run — never written to any file, never committed, not present
in `indicconformer_infer.py` itself).

**Download:** with the token, `AutoModel.from_pretrained(..., token=token)` triggered
`snapshot_download` of 404 files (the ONNX assets — one `joint_post_net_<lang>.onnx` per
language plus shared encoder/decoder graphs). First attempt got to 403/404 files then hit
a transient error in HuggingFace's "Xet" fast-transfer backend (`CAS Client Error:
Request middleware error`) — a network/CDN issue, not a gating or code problem. A retry a
few seconds later resumed from the already-downloaded cache and completed all 404 files
in under a second.

**Inference run** (CPU only, no GPU used), against `services/ai/bakeoff/samples/mobile_01.wav`
(14.53s, 16kHz mono PCM16 — the same file that produced repetition-loop garbage on
faster-whisper `small` in the prior session):

```
[load] model load took 7.2s
[infer] CTC took 2.1s
[infer] RNNT took 3.1s
```

**CTC transcript (verbatim, Telugu script):**
```
అందరికీ నమస్కారం అందరూ బాగున్నారా నేను బాగున్నాను మీరు బాగున్నారా భోజనం చేసారా నేను భోజనం చేశాను ఎవరికైనా భోజనం చేయకపోతే అక్కడ ఇడ్లీలో ఉన్నా వెళతానండి
```

**RNNT transcript (verbatim, Telugu script):**
```
అందరికీ నమస్కారం అందరూ బాగున్నారా నేను బాగున్నాను మీరు బాగున్నారా భోజనం చేశారా నేను భోజనం చేశాను ఎవరికైనా భోజనం చేయకపోతే అక్కడ ఇడ్లీలోనే వెళ్తానండి
```

**Honest accuracy note:** this is coherent, varied, grammatical-looking Telugu text —
structurally the opposite of faster-whisper `small`'s repetition-loop failure on the same
file. That is a real and meaningful signal on its own. But nobody on this task read the
model's output against what was actually said into the microphone — **only the user (or
a Telugu speaker) can judge whether this transcript is actually correct.** The two decoder
outputs differ in a few words (చేసారా/చేశారా, ఉన్నా వెళతానండి/లోనే వెళ్తానండి), which on
its own says nothing about which one is right — that also needs a human judgment call.

---

## Recommendation

**Viable — clearly worth pursuing in Phase 1.** Unlike the NeMo/IndicTransToolkit spikes,
this was not a Windows-installability dead end: the ONNX + `transformers` + `onnxruntime`
runtime installs cleanly and natively on this machine (no NeMo, no Docker, no WSL2), the
HF gate was a one-click self-serve accept (not a human review queue), and one real
inference run against a file that broke faster-whisper produced coherent, non-garbage
Telugu output in ~2-3s of CPU inference for a 14.5s clip.

**What Phase 1 should do, not this spike:**
1. Get the transcript above (and a run against `mobile_02.wav`, `winvoicerec_test.wav`,
   and the Hindi samples if any) checked by a Telugu/Hindi speaker for actual correctness
   — this spike has no accuracy verdict, only "not garbage."
2. Decide CTC vs. RNNT decoding (they gave slightly different output here; no basis yet
   to prefer one).
3. Design the real service integration: model load cost (7.2s) matters for a
   request-scoped service — this wants to be a long-lived loaded model, not a per-request
   `from_pretrained` call. Also decide the FFmpeg/`torchcodec` question for real (this
   spike bypassed it with a manual WAV-only loader, which was fine for these bake-off
   samples but not for other formats like the ogg_opus/mp3 handling `feat/ai-speech-asr-w5`
   already does for faster-whisper).
4. Decide how the HF gate acceptance / token is handled outside a throwaway spike venv
   (service credentials, not a personal token in an env var).

**Not touched by this spike:** `services/ai/speech/` (faster-whisper) is untouched and
remains the working fallback until this is proven out further.

---

## Phase 1 update

`services/ai/speech_indicconformer/` now exists (same contract as `services/ai/speech/`,
which is untouched and remains the fallback). Real-model runs with `language_hint=te`
(model load 14.3 s this run; `mobile_01` CTC/RNNT output is identical to the spike's):

- `mobile_02.wav` (9.98 s) CTC/RNNT identical: `నా పేరు ప్రణీత్ సత్యసాయి నా పక్కన గునసాయి కూర్చున్నాడు ఇద్దరం పని పాట లేకుండా ఇక్కడ కూర్చొని ఫోన్లో ఆడుకుంటున్నా`
- `winvoicerec_test.wav` (13.29 s) is visibly weak and the decoders disagree — human review needed.

Still not done: correctness check of all transcripts by a Telugu speaker; CTC-vs-RNNT decision.
Findings: the model needs an explicit `hi`/`te` hint (no auto-detect, no English);
`decode_wav_pcm16`/`decode_via_ffmpeg` are imported from `speech/engine.py`, which couples the
two services via a `faster_whisper` import.
