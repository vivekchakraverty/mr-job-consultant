"""CV ↔ job matcher on ZeroGPU.

Every call runs on the *caller's* ZeroGPU quota (whoever's Hugging Face token makes the
request), so the Space owner does not pay for other people's searches.

API: /match(cv_text: str, jobs_json: str, top_k: int) -> JSON string
  jobs_json is a list of {"text": "..."}; the result lists {"i", "sim", "llm"} for every job,
  with "llm" (raw model JSON) only for the top_k most similar jobs.
"""
import json

import gradio as gr
import spaces
import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoModelForCausalLM, AutoTokenizer

EMBED_ID = "intfloat/multilingual-e5-small"
LLM_ID = "Qwen/Qwen3-4B-Instruct-2507"
MAX_JOBS = 300
MAX_TOP_K = 20

SYSTEM_PROMPT = (
    "You are a fair, practical recruiter. Compare a candidate's CV with one job post and judge how "
    "well they fit. Reply with JSON only, no prose: "
    '{"score": <0-100>, "reason": "<one short sentence>", "missing": ["<up to 3 key gaps>"]}'
)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
embedder = SentenceTransformer(EMBED_ID, device=str(device))
tokenizer = AutoTokenizer.from_pretrained(LLM_ID)
tokenizer.padding_side = "left"
llm = AutoModelForCausalLM.from_pretrained(LLM_ID, torch_dtype=torch.bfloat16).to(device)


def estimate_duration(cv_text: str, jobs_json: str, top_k: float) -> int:
    return int(15 + 2.5 * min(int(top_k or 0), MAX_TOP_K))


@spaces.GPU(duration=estimate_duration)
def match(cv_text: str, jobs_json: str, top_k: float) -> str:
    jobs = json.loads(jobs_json)[:MAX_JOBS]
    top_k = max(0, min(int(top_k or 0), MAX_TOP_K, len(jobs)))
    if not jobs:
        return "[]"

    cv_vec = embedder.encode(["query: " + cv_text[:2000]], normalize_embeddings=True)
    job_vecs = embedder.encode(["passage: " + j["text"][:1200] for j in jobs],
                               normalize_embeddings=True, batch_size=64)
    sims = (job_vecs @ cv_vec.T).ravel()
    order = sims.argsort()[::-1].tolist()

    reviews: dict[int, str] = {}
    top = order[:top_k]
    if top:
        prompts = [
            tokenizer.apply_chat_template(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": f"CV:\n{cv_text[:3500]}\n\nJob post:\n{jobs[i]['text'][:2200]}\n\nJSON:"}],
                tokenize=False, add_generation_prompt=True)
            for i in top
        ]
        batch = tokenizer(prompts, return_tensors="pt", padding=True).to(device)
        with torch.no_grad():
            out = llm.generate(**batch, max_new_tokens=160, do_sample=False,
                               pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
        texts = tokenizer.batch_decode(out[:, batch["input_ids"].shape[1]:], skip_special_tokens=True)
        reviews = dict(zip(top, texts))

    # plain Python values only: nothing CUDA-backed crosses the process boundary
    return json.dumps([{"i": i, "sim": float(sims[i]), "llm": reviews.get(i, "")} for i in order])


with gr.Blocks(title="CV job matcher") as demo:
    gr.Markdown("### CV ↔ job matcher\nUsed by the job-search app through the API. "
                "Runs on the caller's own ZeroGPU quota.")
    cv = gr.Textbox(label="CV text", lines=6)
    jobs_in = gr.Textbox(label='Jobs JSON: [{"text": "..."}]', lines=6)
    k = gr.Number(label="Jobs to review with the LLM", value=12, precision=0)
    result = gr.Textbox(label="Result JSON", lines=8)
    gr.Button("Match").click(match, inputs=[cv, jobs_in, k], outputs=result, api_name="match")

demo.queue(default_concurrency_limit=4).launch()
