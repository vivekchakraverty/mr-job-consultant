"""Grounded resume / cover-letter writer on ZeroGPU.

Every call runs on the *caller's* ZeroGPU quota (the Hugging Face token making the request).
The job-search app verifies every fact the model writes against the user's CV, so this Space
only has to produce text; it never decides what is true.

API: /chat(messages_json: str, max_new_tokens: int) -> str
"""
import json

import gradio as gr
import spaces
import torch
from transformers import AutoModelForCausalLM, AutoModelForImageTextToText, AutoTokenizer

MODEL_ID = "Qwen/Qwen3.6-35B-A3B-FP8"
MAX_NEW_TOKENS = 3000

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
try:
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype="auto", device_map="cuda")
except ValueError:
    # The checkpoint is registered as an image-text-to-text model; text-only chat works the same way.
    model = AutoModelForImageTextToText.from_pretrained(MODEL_ID, dtype="auto", device_map="cuda")
model.eval()


def estimate_duration(messages_json: str, max_new_tokens: float) -> int:
    return int(min(120, 20 + min(int(max_new_tokens or 0), MAX_NEW_TOKENS) / 30))


@spaces.GPU(duration=estimate_duration)
def chat(messages_json: str, max_new_tokens: float) -> str:
    messages = json.loads(messages_json)
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                           enable_thinking=False)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=min(int(max_new_tokens or 1024), MAX_NEW_TOKENS),
                             do_sample=False)
    return tokenizer.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)


with gr.Blocks(title="Application writer") as demo:
    gr.Markdown("### Application writer\nUsed by the job-search app through the API. "
                "Runs on the caller's own ZeroGPU quota.")
    msgs = gr.Textbox(label='Messages JSON: [{"role": "user", "content": "..."}]', lines=8)
    n = gr.Number(label="Max new tokens", value=1024, precision=0)
    out = gr.Textbox(label="Reply", lines=10)
    gr.Button("Generate").click(chat, inputs=[msgs, n], outputs=out, api_name="chat")

demo.queue(default_concurrency_limit=2).launch()
