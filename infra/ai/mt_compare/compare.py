# ruff: noqa: E501, E731  (kept as it ran: the lambdas and long lines are the code that produced results/)
# Runs in a throwaway python container (CPU torch). One candidate per invocation:
#   python mt_compare.py <candidate> <sentences.json> <out.json> [max_sentences]
# Typed Telugu in -> English out, and Telugu -> Hindi (direct where the model does it, else via its own
# English). Every output is the model's, untouched. Time is wall-clock per sentence, CPU only, N threads.
# Nothing here calls out to any service: the only network use is downloading weights from Hugging Face.
import json
import os
import sys
import time

import torch

N_THREADS = int(os.environ.get("MT_THREADS", "8"))
torch.set_num_threads(N_THREADS)
cand, sent_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
limit = int(sys.argv[4]) if len(sys.argv) > 4 else None
sents = json.load(open(sent_path, encoding="utf8"))[: limit or None]
res = {"candidate": cand, "threads": N_THREADS, "rows": [], "load_s": None}


def timed(fn):
    t0 = time.time()
    out = fn()
    return out, round(time.time() - t0, 2)


if cand.startswith("nllb"):
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    repo = {
        "nllb-1.3b": "facebook/nllb-200-distilled-1.3B",
        "nllb-600m": "facebook/nllb-200-distilled-600M",
    }[cand]
    (tok, model), res["load_s"] = timed(
        lambda: (
            AutoTokenizer.from_pretrained(repo, src_lang="tel_Telu"),
            AutoModelForSeq2SeqLM.from_pretrained(repo).eval(),
        )
    )

    def tr(text, tgt):
        enc = tok(text, return_tensors="pt")
        with torch.inference_mode():
            out = model.generate(
                **enc,
                forced_bos_token_id=tok.convert_tokens_to_ids(tgt),
                num_beams=4,
                max_new_tokens=256,
            )
        return tok.batch_decode(out, skip_special_tokens=True)[0]

    to_en = lambda t: tr(t, "eng_Latn")
    to_hi = lambda t: tr(t, "hin_Deva")

elif cand == "madlad-3b":
    from transformers import AutoTokenizer, T5ForConditionalGeneration

    repo = "google/madlad400-3b-mt"
    (tok, model), res["load_s"] = timed(
        lambda: (
            AutoTokenizer.from_pretrained(repo),
            T5ForConditionalGeneration.from_pretrained(repo).eval(),
        )
    )

    def tr(text, code):
        ids = tok(f"<2{code}> {text}", return_tensors="pt").input_ids
        with torch.inference_mode():
            out = model.generate(input_ids=ids, num_beams=4, max_new_tokens=256)
        return tok.decode(out[0], skip_special_tokens=True)

    to_en = lambda t: tr(t, "en")
    to_hi = lambda t: tr(t, "hi")

elif cand == "sarvam-4b":
    from transformers import AutoModelForCausalLM, AutoTokenizer

    repo = "sarvamai/sarvam-translate"
    dtype = torch.bfloat16 if os.environ.get("MT_DTYPE", "bf16") == "bf16" else torch.float32
    (tok, model), res["load_s"] = timed(
        lambda: (
            AutoTokenizer.from_pretrained(repo),
            AutoModelForCausalLM.from_pretrained(repo, torch_dtype=dtype).eval(),
        )
    )
    res["dtype"] = str(dtype)

    def tr(text, target):
        chat = [
            {"role": "system", "content": f"Translate the text below to {target}."},
            {"role": "user", "content": text},
        ]
        prompt = tok.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        enc = tok([prompt], return_tensors="pt")
        with torch.inference_mode():
            out = model.generate(**enc, max_new_tokens=256, do_sample=False)
        return tok.decode(out[0][enc.input_ids.shape[1] :], skip_special_tokens=True).strip()

    to_en = lambda t: tr(t, "English")
    # Sarvam-Translate is English <-> Indic only (model card): Telugu -> Hindi goes through its own English.
    to_hi = None
else:
    raise SystemExit(f"unknown candidate {cand}")

# warm-up: the first call pays one-off setup costs that are not the model's per-sentence speed
to_en(sents[0]["te"])
for s in sents:
    en, t_en = timed(lambda: to_en(s["te"]))
    row = {"id": s["id"], "en": en, "t_en_s": t_en}
    if to_hi is not None:
        hi, t_hi = timed(lambda: to_hi(s["te"]))
        row.update({"hi": hi, "t_hi_s": t_hi, "hi_route": "direct"})
    else:
        # via English: the English above is the pivot. Time = a second English -> Hindi call.
        def en_to_hi():
            chat = [
                {"role": "system", "content": "Translate the text below to Hindi."},
                {"role": "user", "content": en},
            ]
            prompt = tok.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
            enc = tok([prompt], return_tensors="pt")
            with torch.inference_mode():
                out = model.generate(**enc, max_new_tokens=256, do_sample=False)
            return tok.decode(out[0][enc.input_ids.shape[1] :], skip_special_tokens=True).strip()

        hi, t_hi = timed(en_to_hi)
        row.update(
            {"hi": hi, "t_hi_s": round(t_en + t_hi, 2), "hi_route": "via English (te->en + en->hi)"}
        )
    res["rows"].append(row)
    print(f"  [{cand}] #{s['id']:>2} en {t_en:6.2f}s | hi {row['t_hi_s']:6.2f}s", flush=True)
json.dump(res, open(out_path, "w", encoding="utf8"), ensure_ascii=False, indent=1)
print(f"{cand}: load {res['load_s']}s, wrote {out_path}")
