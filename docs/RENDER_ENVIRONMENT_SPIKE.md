# Render step environment spike (Week 7)

Research only. No service code or contract changes. Covers the two parts of the
render step defined in `contracts/ai/render.py`: English pivot text → Hindi/Telugu
text (MT), and Hindi/Telugu text → speech (TTS). Checked 2026-09-29.

## 1. English → Indic MT model

**`ai4bharat/indictrans2-en-indic-dist-200M` exists** (HF API, 200 OK).

| Field | en-indic (new) | indic-en (Week 6) |
|---|---|---|
| gated | `"auto"` | `"auto"` |
| private | false | false |
| license | MIT | MIT |
| library / pipeline | transformers / translation | same |
| last modified | 2025-05-02 | 2025-05-02 |
| custom code | `configuration_indictrans.py`, `modeling_indictrans.py`, `tokenization_indictrans.py` (needs `trust_remote_code=True`) | same |
| files | `model.safetensors` and `pytorch_model.bin`, `model.SRC` / `model.TGT`, `dict.SRC.json` / `dict.TGT.json` | same layout |

- **Gating:** identical to indic-en. It is auto-approved gated, so the same HF
  login and accepted terms are needed. Unauthenticated fetch of its README returned
  "Access ... is restricted". I did not test a download with the Week 6 token.
- **Direction differences** (from the IndicTransToolkit README, not from the gated
  model card):
  - The target language is now the output: `src_lang="eng_Latn"`, `tgt_lang="tel_Telu"`
    or `"hin_Deva"`.
  - `preprocess_batch` takes both languages. `postprocess_batch` takes only
    `lang=<target>`.
  - One model checkpoint serves both Hindi and Telugu, so a fan-out request costs
    one model load. It still costs one generate call per target language.
- **Not done, by scope:** the model was not downloaded and no translation was run.
  The runtime is the same as Week 6 (IndicTransToolkit on native Windows), so I
  expect it to work, but that is an inference, not a test.

## 2. TTS voice catalogue findings

### Piper: Hindi and Telugu voices both exist

The earlier suspicion that Hindi was missing from Piper is **wrong**. Source:
`https://huggingface.co/api/models/rhasspy/piper-voices/tree/main/{hi,te}/...`
(repo not gated, MIT, last modified 2026-09-17).

| Voice | Quality | Rate | Size | Notes |
|---|---|---|---|---|
| `te_IN-maya-medium` | medium | 22,050 Hz | 62.9 MB | 1 speaker, IIT Madras "indictts" data, fine-tuned from US English lessac |
| `te_IN-padmavathi-medium` | medium | 22,050 Hz | 63.5 MB | |
| `te_IN-venkatesh-medium` | medium | 22,050 Hz | 63.5 MB | |
| `hi_IN-pratham-medium` | medium | 22,050 Hz | 63.5 MB | 1 speaker; data: AI4Bharat indicnlp_corpus; **model card says CC BY-NC-SA 4.0** |
| `hi_IN-priyamvada-medium` | medium | 22,050 Hz | 63.5 MB | |
| `hi_IN-rohan-medium` | medium | 22,050 Hz | 63.0 MB | |

- Only the `medium` tier exists for both languages. There is no low, high or x_low.
- All voices use eSpeak phonemes (`espeak.voice` = `hi` / `te`).
- **Licence caveats:**
  - The pratham card states the dataset is CC BY-NC-SA 4.0. Non-commercial terms
    could matter for a product.
  - The maya card points to the IIT Madras licence, which I did not read.
  - I read the cards for maya and pratham only. The other four voices' licences are
    unchecked, and every voice needs a licence read before shipping.

### AI4Bharat Indic-TTS and related HF repos

- **`ai4bharat/vits_rasa_13`:**
  - Gated (`auto`), CC-BY-4.0.
  - `library_name: transformers`, but it needs `AutoModel.from_pretrained(..., trust_remote_code=True)`.
    It ships custom `modeling_vits.py` and `tokenization_vits.py`, so it is not a plain load.
  - The 13 languages listed are Assamese, Bengali, Bodo, Dogri, Kannada, Maithili,
    Malayalam, Marathi, Nepali, Punjabi, Sanskrit, Tamil and Telugu. **Hindi is not
    among them.**
  - Telugu is supported. The card lists 20 speaker IDs across languages.
  - The card is unreadable without auth, so the details come from a fetch summary.
    I could not verify the sample rate (22.05 kHz was inferred).
- **Hindi VITS on HF:** `ai4bharat/vits-hi-itts-base` exists and is not gated, but
  it contains only `D_baseline_hindi.pth`, `G_baseline_hindi.pth` and a README. It has
  no licence tag, 0 downloads and no config or loader. It is an experimental raw
  checkpoint, not usable as a service. `vits-hi-proximal-chha`,
  `vits-hi-asr-enhanced` and similar repos appear to be the same kind of artefact.
  I did not inspect them beyond the listing.
- **Original Indic-TTS** (github.com/AI4Bharat/Indic-TTS) uses FastPitch + HiFi-GAN,
  not VITS, and covers Hindi and Telugu. I did not evaluate it. It needs the
  separate Coqui-based repo and its own checkpoints.
- **`ai4bharat/indic-parler-tts`:**
  - Apache-2.0, `gated: auto`, `library_name: transformers`, 21 Indian languages.
  - It is a description-conditioned model (a style prompt is required) and is a
    much heavier autoregressive model than Piper.
  - I did not test it or check its VRAM footprint.

### IndicF5

- Repo `ai4bharat/IndicF5`: MIT, `gated: auto`, `trust_remote_code`, 0.4B params,
  built on F5-TTS. It covers 11 languages including Hindi and Telugu, and outputs 24 kHz.
- Architecture is flow-matching. Usage is
  `model(text, ref_audio_path=..., ref_text=...)`, so **a reference audio clip and its
  exact transcript are mandatory on every call**.
- **Is that a hard blocker?** No. The system supplies the reference, not the end user.
  We would keep one licensed reference clip and transcript per language and pass it
  each time. The real issues are different:
  - We must own or license a suitable Hindi and Telugu reference voice. This is a
    consent and licensing question.
  - Output quality and speaker identity depend on that clip.
  - The model documents no speed or GPU figures, and I did not measure any.
  - It would compete with other models for VRAM.

## 3. Install and synthesis test (Piper, throwaway venv)

Venv in the session scratchpad, Python 3.11, Windows 11 (native), CPU only.

- `pip install piper-tts` succeeded first try: `piper-tts 1.8.0`, `onnxruntime 1.30.0`,
  `numpy 2.4.6`. There were no build errors and no espeak-ng installation was needed.
- Voice download: plain `curl` failed with Windows schannel
  `CRYPT_E_NO_REVOCATION_CHECK`. `curl --ssl-no-revoke` worked. File sizes matched the
  catalogue byte for byte (62,950,044 and 63,516,050).
- Synthesis used `PiperVoice.load()` and `synthesize_wav()`:

| Voice | Text | Model load | 1st synth | 2nd synth | Output |
|---|---|---|---|---|---|
| `te_IN-maya-medium` | నేను ఈ రోజు మా అమ్మతో మార్కెట్‌కి వెళ్తున్నాను. | 4.06 s | 5.50 s (cold) | 0.65 s | WAV, mono, 16-bit, 22,050 Hz, 4.13 s, 182 KB |
| `hi_IN-pratham-medium` | मैं आज अपनी माँ के साथ बाज़ार जा रहा हूँ। | 7.28 s | 0.55 s | 0.48 s | WAV, mono, 16-bit, 22,050 Hz, 2.74 s, 121 KB |

- Warm synthesis runs at roughly 0.12 to 0.16 of the audio length. That is
  about 6 to 8× faster than real time on CPU.
- **What was verified:** the files parse as valid WAV with plausible durations and
  non-silent content (RMS about 4,300 and 5,300).
- **What was not verified:** I cannot listen to the audio. **Intelligibility and
  pronunciation quality are unassessed.** Both files hit peak sample 32767, which may
  mean clipping. A human needs to listen, ideally a Telugu and a Hindi speaker.
- The first synthesis of the first voice was slow (5.5 s), so a service must warm up
  the voice at startup.

## 4. Recommendation for Phase 1

1. **MT (English → Hindi/Telugu):** use `ai4bharat/indictrans2-en-indic-dist-200M`
   with IndicTransToolkit, the same runtime as Week 6. Reuse the HF token and
   `trust_remote_code` handling. Confirm the download works with a 5-minute smoke test.
2. **TTS:** build on **Piper** for both languages.
   - Telugu: `te_IN-maya-medium`, with `padmavathi` or `venkatesh` as alternatives.
   - Hindi: `hi_IN-pratham-medium`, with `priyamvada` or `rohan` as alternatives.
   - It installs cleanly, has no gating, is CPU-only and fast, and fits the
     `RenderResult` shape (`audio` as WAV, `duration_ms`, `model_version_tts` as the
     voice name).
   - Wrap it behind a swappable interface, because quality is unproven.
3. **Before committing:** a human listening test for both voices. Also do a licence
   read: pratham is CC BY-NC-SA per its card, which may rule it out if the product is
   commercial. If so, use priyamvada or rohan only after checking their cards.
4. **Fallback candidates, if listening finds Piper unacceptable:**
   - Telugu: `vits_rasa_13` (gated, custom code, CC-BY-4.0).
   - Hindi and Telugu: `indic-parler-tts` (Apache-2.0, heavier) or IndicF5 (needs a
     system-owned reference clip per language).

**Languages with no viable option found:** none. Both Hindi and Telugu have Piper
voices that run. The gap is quality assurance, not availability. Hindi has no good
option in AI4Bharat's VITS line, but Piper covers it.

## Not done

- No MT model download or translation test.
- No listening test.
- No test of `indic-parler-tts`, IndicF5 or `vits_rasa_13`.
- No licence read for the alternate Piper voices.
