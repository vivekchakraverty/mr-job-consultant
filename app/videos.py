"""The best YouTube videos for each curriculum topic, ranked by what viewers say and do.

Search and statistics come from youtubei.js (app/youtube/worker.mjs), which uses YouTube's own web
API from this connection with no login, and needs Node.js. Each topic gets one search, then the
most relevant candidates are scored on:

  comment sentiment  cardiffnlp/twitter-roberta-base-sentiment-latest over the top comments,
                     weighted by the comments' own likes (runs on this computer's CPU, free)
  likes per view     how many viewers liked it
  relevance          how much of the topic the title and description cover
  reach              views and comments (log scale, so one viral video doesn't drown the rest)
  length             a real lesson, not a short and not a 6-hour stream

Results are saved for two weeks per search, so reopening a plan sends nothing to YouTube.
"""
import asyncio
import hashlib
import itertools
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .tailor import content

log = logging.getLogger("app.videos")

WORKER = Path(__file__).resolve().parent / "youtube" / "worker.mjs"
SENTIMENT_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
ONNX_MODEL = "Xenova/twitter-roberta-base-sentiment-latest"  # the same model exported to ONNX, int8
MODEL_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
               "vocab.json", "quantize_config.json", "onnx/model_quantized.onnx")  # the model last
CANDIDATES = 10      # search results looked at per topic
DETAILED = 4         # of those, how many get likes and comments fetched
TOP = 3              # shown per topic
COMMENTS = 15        # top comments read per video (scoring them on the CPU is the slow part)
CACHE_DAYS = 14


class VideoError(Exception):
    pass


def _find_node() -> str | None:
    """Node.js: the copy inside a build, else the installed one, even when the app was started with a
    PATH that doesn't include it (services, IDEs)."""
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "node" / ("node.exe" if os.name == "nt" else "node")
    if getattr(sys, "frozen", False) and bundled.is_file():
        return str(bundled)
    if found := os.environ.get("NODE_BINARY") or shutil.which("node"):
        return found
    roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "NVM_SYMLINK")]
    candidates = [Path(r) / "nodejs" / "node.exe" for r in roots[:2] if r]
    candidates += [Path(roots[2]) / "Programs" / "nodejs" / "node.exe"] if roots[2] else []
    candidates += [Path(roots[3]) / "node.exe"] if roots[3] else []
    candidates += [Path("/usr/local/bin/node"), Path("/opt/homebrew/bin/node"), Path("/usr/bin/node")]
    return next((str(p) for p in candidates if p.is_file()), None)


class _Worker:
    """A Node worker in app/youtube (this module's, or another script there such as the interview
    practice's media.mjs), started on first use and restarted if it dies. Thread-safe."""

    def __init__(self, model_cache: Path, script: Path = WORKER):
        self.model_cache = model_cache
        self.script = script
        self.proc: subprocess.Popen | None = None
        self.pending: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Future]] = {}
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        self.model_lock = threading.Lock()

    def ensure_model(self) -> None:
        """Download the sentiment model's ONNX export once. Ranking works without it, just less well.

        A plain streaming download rather than huggingface_hub's, which stalled inside the frozen
        builds. Each file is written under a .part name and renamed when complete, and the model file
        comes last, so its presence means the whole model is there.
        """
        target = self.model_cache / ONNX_MODEL
        with self.model_lock:
            if (target / MODEL_FILES[-1]).exists():
                return
            import httpx
            try:
                with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as client:
                    for name in MODEL_FILES:
                        dest = target / name
                        if dest.exists():
                            continue
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        part = dest.with_name(dest.name + ".part")
                        with client.stream("GET", f"https://huggingface.co/{ONNX_MODEL}/resolve/main/{name}") as r:
                            r.raise_for_status()
                            with part.open("wb") as f:
                                for chunk in r.iter_bytes(1 << 20):
                                    f.write(chunk)
                        part.replace(dest)
            except Exception as e:
                log.warning("sentiment model download failed: %s", e)

    @staticmethod
    def node_or_raise() -> str:
        """Node.js and the worker's packages, checked before anything is downloaded or started."""
        node = _find_node()
        if not node:
            raise VideoError("Video suggestions need Node.js 20 or newer, which isn't installed on this computer.")
        packages = WORKER.parent / "node_modules"
        if not all((packages / p).exists() for p in ("youtubei.js", "@huggingface/transformers")):
            raise VideoError("Video suggestions aren't installed yet. Run `npm install --prefix app/youtube` "
                             "in the app's folder, then restart the app.")
        return node

    def _start(self) -> None:
        node = self.node_or_raise()
        self.proc = subprocess.Popen([node, str(self.script)], cwd=self.script.parent, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     encoding="utf-8", bufsize=1,
                                     env={**os.environ, "MODEL_DIR": str(self.model_cache.resolve())})  # the worker runs in app/youtube
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            loop, fut = self.pending.pop(msg.get("id"), (None, None))
            if fut:
                loop.call_soon_threadsafe(lambda f=fut, m=msg: f.done() or f.set_result(m))
        for loop, fut in list(self.pending.values()):  # the worker exited: fail whatever was waiting
            loop.call_soon_threadsafe(lambda f=fut: f.done() or f.set_exception(VideoError("The video worker stopped.")))
        self.pending.clear()

    async def call(self, op: str, timeout: float = 90, **args) -> dict | list:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            req_id = next(self.ids)
            self.pending[req_id] = (loop, fut)
            self.proc.stdin.write(json.dumps({"id": req_id, "op": op, **args}) + "\n")
            self.proc.stdin.flush()
        try:
            msg = await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(req_id, None)
        if not msg.get("ok"):
            raise VideoError(msg.get("error") or "YouTube didn't answer.")
        return msg["result"]


_worker: _Worker | None = None


def model_ready(model_cache: Path) -> bool:
    return (model_cache / ONNX_MODEL / MODEL_FILES[-1]).exists()


def _get_worker(model_cache: Path) -> _Worker:
    global _worker
    if _worker is None:
        _worker = _Worker(model_cache)
    return _worker


# ---------- scoring ----------

def _duration_fit(seconds: int | None) -> float:
    if not seconds:
        return 0.5
    minutes = seconds / 60
    if 4 <= minutes <= 45:
        return 1.0
    if 2 <= minutes < 4 or 45 < minutes <= 120:
        return 0.6
    return 0.2


def _relevance(topic: str, skill: str, video: dict) -> float:
    text = content(f"{video['title']} {video.get('description', '')}")
    want = content(topic)
    covered = len(want & text) / len(want) if want else 0.5
    about_skill = 1.0 if content(skill) & text else 0.0  # "AND, OR, NOT" in a Tableau course isn't SQL
    return 0.75 * covered + 0.25 * about_skill


# Small samples are noisy (5 likes on 100 views isn't better than 40,000 on 1M), so both rates are
# pulled toward typical values until there are enough views or comments to trust them.
TYPICAL_LIKE_RATE, LIKE_PRIOR_VIEWS = 0.02, 3000
COMMENT_PRIOR = 8


def score(topic: str, skill: str, video: dict) -> dict:
    views = video.get("views") or 0
    likes = video.get("likes")
    s = video.get("sentiment")
    n = video.get("comments") or 0
    raw_sentiment = (1 + s["positive"] - s["negative"]) / 2 if s else 0.5  # -1..1 → 0..1
    like_rate = (likes + TYPICAL_LIKE_RATE * LIKE_PRIOR_VIEWS) / (views + LIKE_PRIOR_VIEWS) if likes is not None else None
    parts = {
        "sentiment": (n * raw_sentiment + COMMENT_PRIOR * 0.5) / (n + COMMENT_PRIOR),
        "likes": min(1.0, like_rate / 0.04) if like_rate is not None else 0.4,
        "relevance": _relevance(topic, skill, video),
        "reach": min(1.0, math.log10(views + 1) / 6.5),
        "discussion": min(1.0, math.log10((video.get("comment_count") or 0) + 1) / 3.5),
        "length": _duration_fit(video.get("duration")),
    }
    weights = {"sentiment": 0.30, "likes": 0.20, "relevance": 0.25, "reach": 0.15, "discussion": 0.05, "length": 0.05}
    return {**video, "score": round(100 * sum(weights[k] * parts[k] for k in weights)),
            "positive_share": round(100 * s["positive"]) if s else None,
            "like_rate": round(100 * likes / views, 1) if likes is not None and views else None}


# ---------- per topic ----------

def _cache_get(path: Path):
    try:
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_DAYS * 86400:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


async def top_videos(topic: str, skill: str, cache_dir: Path, model_cache: Path) -> list[dict]:
    query = f"{topic} {skill} tutorial"
    path = cache_dir / f"{hashlib.sha256(query.lower().encode()).hexdigest()[:20]}.json"
    if (saved := _cache_get(path)) is not None:
        return saved
    worker = _get_worker(model_cache)
    worker.node_or_raise()  # say what's missing at once, rather than after a 125 MB model download
    await asyncio.to_thread(worker.ensure_model)
    found = await worker.call("search", query=query, limit=CANDIDATES)
    found = [v for v in found if not v.get("duration") or 90 <= v["duration"] <= 4 * 3600]
    # Only the most promising candidates cost two more requests each.
    found.sort(key=lambda v: (_relevance(topic, skill, v), math.log10((v.get("views") or 0) + 1)), reverse=True)
    picked = found[:DETAILED]
    stats = await asyncio.gather(*[worker.call("details", video=v["id"], comments=COMMENTS, timeout=180)
                                   for v in picked], return_exceptions=True)
    ranked = []
    for v, st in zip(picked, stats):
        if isinstance(st, BaseException):
            log.info("no details for %s: %s", v["id"], st)
            st = {}
        ranked.append(score(topic, skill, {**v, **{k: val for k, val in st.items() if val is not None}}))
    ranked.sort(key=lambda v: v["score"], reverse=True)
    distinct, seen = [], []
    for v in ranked:  # the same lesson re-uploaded by another channel only counts once
        words = content(v["title"])
        if not any(words and len(words & s) / len(words | s) >= 0.7 for s in seen):
            distinct.append(v)
            seen.append(words)
    best = [{k: v.get(k) for k in ("id", "title", "channel", "duration", "views", "likes", "comment_count",
                                   "published", "score", "positive_share", "like_rate", "top_comment")}
            for v in distinct[:TOP]]
    if any(v["likes"] is not None for v in best):  # a ranking without any statistics is retried next time
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8")
    return best


async def for_topics(topics: list[str], skill: str, cache_dir: Path, model_cache: Path, on_topic) -> None:
    """Fill in every topic, a few at a time so YouTube sees a gentle, browser-like pace."""
    gate = asyncio.Semaphore(3)

    async def one(i: int, topic: str):
        async with gate:
            try:
                on_topic(i, await top_videos(topic, skill, cache_dir, model_cache), None)
            except Exception as e:  # one failed topic doesn't stop the rest
                log.info("videos for %r failed: %s", topic, e)
                on_topic(i, [], str(e) if isinstance(e, VideoError) else "YouTube didn't answer for this topic.")

    await asyncio.gather(*[one(i, t) for i, t in enumerate(topics)])
