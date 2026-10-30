"""Chat calls for writing documents, always billed to the requesting person's own HF token.

Engines
  zerogpu    the app's writer Space; runs on the caller's free daily ZeroGPU quota
  inference  HF Inference Providers; billed to the caller's own credits (their token)
"""
import asyncio
import json
import re

from .matching import MatchError, _require_token

ROUTER_URL = "https://router.huggingface.co/v1"
# Best faithful-extraction scores (llamaindex/ExtractBench) among models HF can serve.
ROUTER_MODELS = ["Qwen/Qwen3.8-27B", "Qwen/Qwen3.6-35B-A3B", "deepseek-ai/DeepSeek-V4.1-Flash"]
SPACE_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"
THINK = re.compile(r"<think>.*?</think>", re.S)


class Writer:
    def __init__(self, engine: str, token: str, space_id: str | None):
        self.engine = engine
        self.token = _require_token(token)
        self.space_id = space_id
        self.model_used = SPACE_MODEL if engine == "zerogpu" else None
        self._client = None

    async def chat(self, messages: list[dict], max_tokens: int = 1500) -> str:
        if self.engine == "zerogpu":
            text = await asyncio.to_thread(self._space_chat, messages, max_tokens)
        else:
            text = await asyncio.to_thread(self._router_chat, messages, max_tokens)
        return THINK.sub("", text or "").strip()

    def _space_chat(self, messages: list[dict], max_tokens: int) -> str:
        from gradio_client import Client
        if not self.space_id:
            raise MatchError("The writer Space isn't set up yet.")
        try:
            if self._client is None:
                self._client = Client(self.space_id, token=self.token, verbose=False, download_files=False)
            return self._client.predict(json.dumps(messages, ensure_ascii=False), max_tokens, api_name="/chat")
        except Exception as e:
            msg = str(e)
            if "quota" in msg.lower():
                raise MatchError("Your daily ZeroGPU quota is used up. It refreshes 24 hours after first use.") from e
            raise MatchError(f"The writer Space couldn't run ({type(e).__name__}). It may still be starting.") from e

    def _router_chat(self, messages: list[dict], max_tokens: int) -> str:
        from huggingface_hub import InferenceClient
        client = InferenceClient(base_url=ROUTER_URL, api_key=self.token)  # billed to this token only
        last = None
        for model in ROUTER_MODELS:
            try:
                r = client.chat_completion(model=f"{model}:cheapest", messages=messages,
                                           max_tokens=max_tokens, temperature=0)
                self.model_used = model
                return r.choices[0].message.content or ""
            except Exception as e:
                if "402" in str(e) or "credit" in str(e).lower():
                    raise MatchError("Your Hugging Face account has no Inference credits left. "
                                     "Use the free ZeroGPU option instead.") from e
                last = e  # model unavailable right now: try the next one
        raise MatchError(f"No writing model answered ({type(last).__name__}).") from last


def parse_json(text: str):
    """The first JSON object in a model reply (models sometimes wrap it in prose or code fences)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
