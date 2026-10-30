"""Match a CV against collected jobs, always at the searching person's own expense.

Cost rule: every AI call is made with the Hugging Face token of the person who is searching.
  1. ZeroGPU Space: usage counts against *their* daily ZeroGPU quota, whoever owns the Space.
  2. HF Inference Providers (opt-in backup): billed to *their* token's monthly free credits.
  3. No token: free keyword matching on this machine's CPU, so nobody's account is used.
The server never substitutes the app owner's token for someone else's request. The HF login
cached on this computer is only offered to requests coming from this computer itself.
"""
import asyncio
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from .jobs import Job, best_title, words

LLM_ID = "Qwen/Qwen3-4B-Instruct-2507"           # Apache-2.0, 4B, not gated
EMBED_ID = "intfloat/multilingual-e5-small"      # MIT, multilingual, served free by hf-inference
MAX_CANDIDATES = 250                             # jobs sent to the embedding step
LLM_TOP_K = 12                                   # jobs the LLM reviews per search (keeps quota use small)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

SYSTEM_PROMPT = (
    "You are a fair, practical recruiter. Compare a candidate's CV with one job post and judge how "
    "well they fit. Reply with JSON only, no prose: "
    '{"score": <0-100>, "reason": "<one short sentence>", "missing": ["<up to 3 key gaps>"]}'
)


class MatchError(Exception):
    pass


# ---------- tokens and accounts ----------

def resolve_token(header_token: str | None, client_host: str | None, allow_local_login: bool) -> tuple[str | None, str]:
    """The token for *this* request and where it came from."""
    if header_token:
        return header_token.strip(), "yours"
    if allow_local_login and client_host in LOOPBACK:
        from huggingface_hub import get_token
        token = get_token()
        if token:
            return token, "this computer's Hugging Face login"
    return None, "none"


_ACCOUNTS: dict[str, dict] = {}


def account(token: str) -> dict:
    """Name and plan of the token's owner (cached per token, never logged)."""
    key = hashlib.sha256(token.encode()).hexdigest()
    if key not in _ACCOUNTS:
        from huggingface_hub import whoami
        try:
            info = whoami(token=token)
        except Exception as e:
            raise MatchError("That Hugging Face token was not accepted. Check it on hf.co/settings/tokens.") from e
        _ACCOUNTS[key] = {"name": info.get("name"), "pro": bool(info.get("isPro"))}
    return _ACCOUNTS[key]


# ---------- shared helpers ----------

def job_text(job: Job, limit: int = 1500) -> str:
    return f"{job.title} at {job.company or 'a company'} ({job.location or 'location not given'}). {job.description[:limit]}"


def similarity_pct(sim: float) -> int:
    """e5 cosine similarities sit roughly between 0.72 (unrelated) and 0.90 (very close)."""
    return int(max(0, min(100, (sim - 0.72) / 0.18 * 100)))


def parse_llm_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    score = data.get("score")
    return {
        "llm_score": int(max(0, min(100, float(score)))) if isinstance(score, (int, float, str)) and
        str(score).replace(".", "", 1).isdigit() else None,
        "reason": str(data.get("reason") or "")[:240],
        "missing": [str(x)[:120] for x in (data.get("missing") or [])][:3],
    }


def user_prompt(cv_text: str, job: Job) -> str:
    return f"CV:\n{cv_text[:3500]}\n\nJob post:\n{job_text(job, 2200)}\n\nJSON:"


def _titles(wanted: str | list[str]) -> list[str]:
    return [wanted] if isinstance(wanted, str) else [t for t in wanted if t]


def prefilter(cv_text: str, jobs: list[Job], wanted: str | list[str], limit: int = MAX_CANDIDATES) -> list[Job]:
    """Cheap first cut so the AI only sees plausible jobs (any of the wanted titles counts)."""
    cv_words = words(cv_text)
    titles = _titles(wanted)
    def rough(j: Job) -> float:
        overlap = len(cv_words & words(f"{j.title} {' '.join(j.tags)} {j.description[:1500]}"))
        return 3 * best_title(j, titles)[0] + math.log1p(overlap)
    return sorted(jobs, key=rough, reverse=True)[:limit]


def combine(job: Job, sim: float | None, llm: dict | None, method: str) -> dict:
    sim_score = similarity_pct(sim) if sim is not None else None
    llm_score = (llm or {}).get("llm_score")
    if llm_score is not None and sim_score is not None:
        score = round(0.7 * llm_score + 0.3 * sim_score)
    else:
        score = llm_score if llm_score is not None else sim_score or 0
    return {"url": job.url, "score": score, "similarity": sim_score, "llm_score": llm_score,
            "reason": (llm or {}).get("reason", ""), "missing": (llm or {}).get("missing", []),
            "reviewed": llm_score is not None, "method": method}


# ---------- 3. free keyword matching (no account used) ----------

def keyword_match(cv_text: str, jobs: list[Job], wanted: str | list[str]) -> list[dict]:
    cv_words = words(cv_text)
    titles = _titles(wanted)
    docs = [words(f"{j.title} {' '.join(j.tags)} {j.description}") for j in jobs]
    df = Counter(w for d in docs for w in d)
    n = max(len(docs), 1)
    results = []
    for job, doc in zip(jobs, docs):
        shared = cv_words & doc
        # rarer shared words (skills, tools) count more than common ones
        weight = sum(math.log(n / df[w]) for w in shared if df[w] < n * 0.3)
        overlap = min(100, weight * 6)
        t = best_title(job, titles)[0] if titles else 0
        score = round(0.55 * overlap + 45 * t) if titles else round(overlap)
        top = sorted((w for w in shared if df[w] < n * 0.3 and not w.isdigit()), key=lambda w: df[w])[:5]
        reason = ("Shares " + ", ".join(top) + " with your CV") if top else "Few overlaps with your CV"
        if t >= 0.99:
            reason = "Title matches · " + reason[0].lower() + reason[1:]
        results.append({"url": job.url, "score": min(score, 100), "similarity": None, "llm_score": None,
                        "reason": reason, "missing": [], "reviewed": False, "method": "keywords"})
    return results


# ---------- 1. the person's ZeroGPU quota, through the Space ----------

def _require_token(token: str | None) -> str:
    """Every quota-spending call needs the searcher's own token; never fall back to anything else."""
    if not isinstance(token, str) or not token.strip():
        raise MatchError("No Hugging Face token for this search, so AI matching was skipped.")
    return token


def zerogpu_match(space_id: str, token: str, cv_text: str, jobs: list[Job], top_k: int) -> list[dict]:
    from gradio_client import Client
    token = _require_token(token)
    payload = json.dumps([{"text": job_text(j)} for j in jobs], ensure_ascii=False)
    try:
        for attempt in range(2):  # one retry: a sleeping Space or a flaky TLS handshake often succeeds second time
            try:
                client = Client(space_id, token=token, verbose=False, download_files=False)
                break
            except Exception:
                if attempt:
                    raise
        raw = client.predict(cv_text[:6000], payload, top_k, api_name="/match")
    except Exception as e:
        msg = str(e)
        if "quota" in msg.lower():
            raise MatchError("Your daily ZeroGPU quota is used up. It refreshes 24 hours after first use.") from e
        raise MatchError(f"The ZeroGPU Space couldn't run ({type(e).__name__}). It may still be starting.") from e
    rows = json.loads(raw)
    out = []
    for row in rows:
        job = jobs[row["i"]]
        llm = parse_llm_json(row.get("llm", "")) if row.get("llm") else None
        out.append(combine(job, row.get("sim"), llm, "zerogpu"))
    return out


# ---------- 2. the person's Inference Providers credits (opt-in) ----------

async def inference_match(token: str, cv_text: str, jobs: list[Job], top_k: int) -> list[dict]:
    import numpy as np
    from huggingface_hub import InferenceClient

    token = _require_token(token)
    embedder = InferenceClient(provider="hf-inference", api_key=token)
    try:
        cv_vec = np.asarray(await asyncio.to_thread(
            embedder.feature_extraction, "query: " + cv_text[:2000], model=EMBED_ID)).reshape(-1)
        texts = ["passage: " + job_text(j, 900) for j in jobs]
        chunks = [texts[i:i + 32] for i in range(0, len(texts), 32)]
        vecs = []
        for chunk in chunks:
            arr = np.asarray(await asyncio.to_thread(embedder.feature_extraction, chunk, model=EMBED_ID))
            vecs.append(arr.reshape(len(chunk), -1))
        job_vecs = np.vstack(vecs)
    except Exception as e:
        if "402" in str(e) or "credit" in str(e).lower():
            raise MatchError("Your free Inference credits for this month are used up.") from e
        raise MatchError(f"Hugging Face Inference didn't respond ({type(e).__name__}).") from e

    cv_vec = cv_vec / (np.linalg.norm(cv_vec) or 1)
    job_vecs = job_vecs / np.maximum(np.linalg.norm(job_vecs, axis=1, keepdims=True), 1e-9)
    sims = job_vecs @ cv_vec
    order = list(np.argsort(-sims))

    chat = InferenceClient(provider="auto", api_key=token)
    sem = asyncio.Semaphore(4)

    async def review(i: int) -> dict | None:
        async with sem:
            try:
                resp = await asyncio.to_thread(
                    chat.chat_completion,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT},
                              {"role": "user", "content": user_prompt(cv_text, jobs[i])}],
                    model=LLM_ID, max_tokens=160, temperature=0)
                return parse_llm_json(resp.choices[0].message.content)
            except Exception:
                return None  # one failed review only loses that job's explanation

    top = order[:top_k]
    reviews = dict(zip(top, await asyncio.gather(*(review(i) for i in top))))
    return [combine(jobs[i], float(sims[i]), reviews.get(i), "inference") for i in order]


# ---------- the ZeroGPU Space itself ----------

SPACE_DIR = Path(__file__).resolve().parent / "hf_space"
DEFAULT_SPACE_NAME = "job-cv-matcher"
WRITER_SPACE_DIR = Path(__file__).resolve().parent / "hf_space_writer"
WRITER_SPACE_NAME = "job-application-writer"


def space_status(space_id: str | None, token: str | None) -> dict:
    if not space_id:
        return {"exists": False}
    from huggingface_hub import HfApi
    try:
        runtime = HfApi(token=token or False).get_space_runtime(space_id)  # False: send no token at all
        return {"exists": True, "id": space_id, "stage": runtime.stage, "hardware": runtime.hardware}
    except Exception:
        return {"exists": False, "id": space_id}


def deploy_space(token: str, private: bool = False, folder: Path = SPACE_DIR,
                 name: str = DEFAULT_SPACE_NAME) -> str:
    """Create (or update) one of the app's Spaces on the token owner's account with ZeroGPU hardware."""
    from huggingface_hub import HfApi
    me = account(token)
    if not me["pro"]:
        raise MatchError("Hosting a ZeroGPU Space needs a Hugging Face PRO account.")
    api = HfApi(token=token)
    space_id = f"{me['name']}/{name}"
    # Re-creating an existing Space counts as a new ZeroGPU request against the 10-Space limit.
    if not api.repo_exists(space_id, repo_type="space"):
        api.create_repo(space_id, repo_type="space", space_sdk="gradio", private=private,
                        space_hardware="zero-a10g")
    api.upload_folder(repo_id=space_id, repo_type="space", folder_path=folder,
                      commit_message=f"Update {name}")
    return space_id
