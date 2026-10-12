# Is the translator a weak link? A text-in comparison (2026-10-12)

Asked by M1 (staging thread, item 5): are the garbled Telugu translations partly the translator, and is there a
better open model that runs on our server, with nothing leaving it? To separate the two problems the test uses
**typed Telugu that is known to be right**, so the speech step cannot be blamed for any error.

**Short answer.** On this evidence the current model is **not** the weak link for Telugu→English: it is as good as
or better than every open candidate that could be run, and 3 to 16 times faster per sentence. Its one serious fault is in the
**English→Hindi** step, which turned *satsang* into *chess* ("शतरंज"). One candidate (Sarvam-Translate 4B) handles
devotional words better but has problems of its own that matter more for elders' messages (below). **Nothing here
justifies swapping the model today.** The transcript, not the translator, remains the dominant error.

**Read the limits first** (section 5): 13 sentences, one reference each, written by me, not checked by a native
reader of Telugu or Hindi.

## 1. What was run

| | |
|---|---|
| Input | 12 short sentences and one 3-sentence paragraph, **typed in Telugu** (`sentences.json`), the kind of thing a temple community sends: times, health, blessings, prasadam. **No audio, no ASR.** |
| References | An English and a Hindi reference for each, written by me. They are one acceptable translation each, not the only one. |
| Current system | The production path, built from `main`: the `mt` service (IndicTrans2 **indic→en 200M distilled**) for Telugu→English, then the `render` service (en→indic 200M distilled) for English→Hindi. Hindi timings include `render`'s speech synthesis, so they **overstate** the translation time. |
| Candidates | Plain `transformers` on CPU, 8 threads, beam 4 for the two encoder-decoder models, greedy for Sarvam. Weights downloaded from Hugging Face; **no text was sent to any service**. |
| Host | The shared server, 16 CPUs, about 25 GiB free, no GPU. Load average about 1.8 during the run. |
| Metric | chrF++ (sacrebleu), corpus level, plus the actual outputs, which matter more than the number. |

## 2. Results

| System | Telugu→English chrF++ | Telugu→Hindi chrF++ | s/sentence →en | s/sentence →hi | 3-sentence paragraph →en / →hi | Licence |
|---|---|---|---|---|---|---|
| **Current: IndicTrans2 200M distilled** (via English for Hindi) | **64.2** | **61.0** | **0.49** | **0.82** | 1.4 s / 1.9 s | MIT |
| NLLB-200 distilled 1.3B (Hindi **direct**) | 53.4 | 59.0 | 2.69 | 2.72 | 5.9 s / 5.5 s | **CC-BY-NC-4.0**, "not released for production deployment" |
| MADLAD-400 3B (Hindi **direct**) | 53.9 | 46.3 | 3.84 | 4.33 | 10.8 s / 10.8 s | Apache-2.0 |
| Sarvam-Translate 4B (Hindi via its own English) | 63.2 | 61.0 | 6.62 | 13.07 | 15.4 s / 28.0 s | **GPL-3.0** on the card, built on Gemma 3 4B (Gemma terms not checked) |

Model load times: NLLB 67 s, MADLAD 133 s, Sarvam 111 s. Memory: Sarvam 4B in bfloat16 is about 8 GB, MADLAD 3B in float32 about 12 GB;
both fitted in the 25 GiB available.

chrF++ over 13 sentences is **coarse** and a single reference penalises correct paraphrases. It is shown because it
agrees with a reading of the outputs, not instead of one.

## 3. What the outputs show

**Current model (IndicTrans2 200M).** Telugu→English was right or near-right on all 13 (for example #1 "There is a
bhajan in the temple at seven o'clock in the evening today", #3 "There will be no satsang tomorrow morning"). Its
Hindi uses the respectful **आप** form throughout. Faults, all in the English→Hindi step:
- **#3 "There will be no satsang tomorrow morning" → "कल सुबह कोई शतरंज नहीं होगा" ("there will be no chess tomorrow
  morning").** A word central to this community is mistranslated into an unrelated one, silently. *bhajan*, *puja* and
  *prasad* came through correctly in the same run, so it is this word, not devotional vocabulary in general.
- Small tense and aspect slips in the Hindi (#1 "भजन होता है" = "a bhajan happens", habitual, for "there is a bhajan";
  #10 "स्थगित कर दिया गया था" = "was postponed", for "has been postponed").
- In the Telugu→English step, #8 became "did you have **lunch**" for "have you eaten" (*bhojanam* narrowed to a meal). NLLB
  made the same choice.

**Sarvam-Translate 4B.** Best on devotional vocabulary: it kept *Satsang*, *bhajan*, *prasadam* and *puja*, and got #3 and
#1 in Hindi exactly right. Its problems:
- **It invented content.** #9 "My grandson got good marks in his exams" became "…has done exceedingly well in his exams
  **and has secured a place in the top percentile**", in English and then in Hindi. An invented fact in a family message.
- **Wrong register for elders.** Its Hindi addresses the reader with the informal **तुम** ("तुमने अभी तक खाना खाया है?",
  "तुम्हारी आवाज़"), where the current model uses **आप**. Calling an elder "tum" is disrespectful.
- #7 Hindi "तुम्हें ज़रूर जाना चाहिए" means "you should go", not "please come".
- Slow: 6.6 s (to English) and 13 s (to Hindi) per short sentence, about 13 and 16 times the current model, and 15 s and 28 s
  for a three-sentence note, on top of an ASR step that already takes 67 to 130 s; and a **GPL-3.0** weights licence (plus the Gemma base terms) that the project would have to read.

**NLLB-200 1.3B.** Mistranslated places and rituals in ways that matter here: **temple → "church"** (#7 English) and
**मंदिर → "मस्जिद" (mosque)** (#7 Hindi, direct route), *bhajan* → "worship service", *prasadam* → "offering", and
it appends "Amen". Also the informal **तुम**. And its licence is **non-commercial** and it says it is a research model.
Not suitable.

**MADLAD-400 3B.** Unreliable on short text: #1 became "It's seven o'clock in the evening today" with a Hindi sentence
about a fair ("मेला"), #3 "There's no party", #10 in Hindi lost the reason (the rain) and invented "as a result … the next session"; digits
fused to words ("शाम7बजे").

## 4. The two direct questions

**Is there a better open model that runs on our server?** Of the ones that could be run: **no**. The closest, Sarvam 4B,
trades better devotional vocabulary for invented content, the wrong register and about 13 to 16 times the time per sentence. Not run
(access, below): IndicTrans2's larger variants, TranslateGemma, and Bodhan's `indic-translate` (8B in bfloat16, whose card
states a 24 GB GPU minimum, so it is unlikely to be usable on CPU at all).

**Is translating Telugu→Hindi directly better than going through English?** **Not shown.** The only direct models I could
run were NLLB (chrF++ 59.0 vs 61.0 via English, and the mosque error) and MADLAD (46.3). IndicTrans2's own
Indic→Indic model is the one that should be tested, and it could not be.

**Does anything leave the server?** No text, no audio. The only network traffic was downloading model weights. All
candidate models ran locally on CPU. (A hosted translation API was deliberately not tried.)

## 5. Limits: what this does not show

- **13 sentences, one reference each, all short and cheerful.** A reference of mine is one valid translation. No
  significance testing is meaningful at this size.
- **I am not a native reader of Telugu or Hindi.** The sentences and references were written by me. The models'
  outputs agreeing with my English references suggests the Telugu says what I meant, but a native reader must confirm
  the sentences before any decision rests on this.
- Typed text only. **It says nothing about speech recognition**, which is where the garbling is known to come from.
  The same sentences spoken and transcribed (the real ASR → these models) is the next test.
- Timings are on a shared server with 8 threads, bfloat16 for Sarvam. They are indicative, not a benchmark.
- Beam search for NLLB and MADLAD, greedy for Sarvam (the card's example samples at temperature 0.01).
- No test of long, messy, code-mixed or noisy input, or of names.

## 6. Not run, and why (access)

The token on the server can read only the two 200M IndicTrans2 repos in use. These return **403 (terms not accepted on
that Hugging Face account)**, so they were **not** tested: `ai4bharat/indictrans2-indic-en-1B`,
`ai4bharat/indictrans2-en-indic-1B`, `ai4bharat/indictrans2-indic-indic-dist-320M`,
`ai4bharat/indictrans2-indic-indic-1B`, `bodhan-ai/indic-translate`, `google/translategemma-4b-it`.
Accepting the terms is a click by a person on the account behind the token. The first four are the natural upgrade path
(same family, MIT) and the Indic→Indic ones answer the direct-translation question properly.
Found but not verified: an `ai4bharat/IndicTrans3-beta` Space exists and shows a configuration error; no IndicTrans3
model repository was found on the organisation page.

## 7. Recommendation (mine, to be argued with)

1. **Keep the current model.** Nothing tested beats it on the dimensions that matter here.
2. **Fix the one concrete fault: *satsang* → "chess".** That is the English→Hindi `render` step (M3's lane). A small
   protected-terms list (satsang, bhajan, puja, prasad, aarti, darshan, …) kept out of translation, or a check that a
   known term survives, would remove it without a new model. It is one word that was found; others may exist.
3. **Accept the Hugging Face terms** for the four IndicTrans2 repos in section 6 and rerun this script: it is the
   upgrade with the best chance (same family, MIT) and answers the direct-versus-English question.
4. Re-run on **real ASR transcripts** of the same sentences, with a native reader scoring them, to measure how much of
   the garbling is the transcript. That, not the translator, is where the evidence points.

## Re-running

`run.sh` (copy `run.sh`, `compare.py` and `sentences.json` to the server's home directory as `mt_compare.sh`,
`mt_compare.py` and `mt_sentences.json`; it builds a throwaway compose project and a throwaway container, never
touches staging, and removes everything it made). `results/` holds the raw outputs this page is built from.
