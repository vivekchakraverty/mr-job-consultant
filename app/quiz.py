"""Tests on a video: 10 multiple-choice questions written from its transcript, graded and recorded.

The transcript comes from the video's captions through yt-dlp (no login, no download). Questions
are written by the free writer Space on the requester's own ZeroGPU time, two halves side by side
so each call stays inside the Space's GPU budget, and checked in code: four distinct options, one
answer, nothing repeated. Answers stay on this machine until the test is submitted, then it's
graded here and the score saved. A retake reads different parts of the transcript and is told
every question asked before, so it gets a fresh set.
"""
import asyncio
import json
import random
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .learning import SPACE_BUDGET, _read_reply
from .llm import Writer
from .tailor import content

QUESTIONS = 10
PER_CALL = 6          # two calls of six leave room to drop weak or repeated questions
WINDOW = 9000         # transcript characters given to each call
MIN_WORDS = 150       # shorter transcripts can't carry ten fair questions

QUIZ_PROMPT = """You write multiple-choice questions that check whether someone understood a video lesson.
Rules:
- Use ONLY what the TRANSCRIPT teaches. Every correct answer must be stated or clearly shown in it.
- Test understanding of the SUBJECT and how to apply it: concepts, steps, when to use what, what a
  result means. Never ask about the video or course itself (how much of it covers something, what
  comes next, who presents it, the channel, timestamps or the order things were said).
- Exactly 4 short options, one clearly correct. Wrong options are plausible but wrong per the transcript.
- No "all of the above" or "none of the above". Every question differs from the others and from AVOID.
- Keep questions and options short.
Return JSON only:
{"questions": [{"q": "", "options": ["", "", "", ""], "answer": 0, "why": "one short sentence"}]}"""


class QuizError(Exception):
    pass


# ---------- transcript ----------

def _caption_text(data: dict) -> str:
    events = data.get("events") or []
    return " ".join(" ".join("".join(s.get("utf8", "") for s in e.get("segs", [])).split())
                    for e in events if e.get("segs")).strip()


def _vtt_text(vtt: str) -> str:
    lines, last = [], ""
    for line in vtt.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:")) or line == last:
            continue
        lines.append(line)
        last = line
    return " ".join(lines)


def fetch_transcript(video_id: str, cache_dir: Path, node: str | None = None) -> dict:
    """{"text", "language", "source": "captions" | "auto-captions"}; cached per video."""
    path = cache_dir / f"{video_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        import yt_dlp
    except ImportError as e:  # an install upgraded from 0.1.0 without re-running pip
        raise QuizError("Tests need yt-dlp. Run `pip install -r requirements.txt` in the app's folder, "
                        "then restart the app.") from e
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    if node:
        opts["js_runtimes"] = {"node": {"path": node}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
            original = info.get("language") or ""
            choices = ([("captions", k, manual[k]) for k in manual if k.startswith("en")]
                       + [("auto-captions", k, auto[k]) for k in ("en-orig", "en") if k in auto]
                       + [("captions", k, manual[k]) for k in manual]
                       + [("auto-captions", k, auto[k]) for k in auto if k.endswith("-orig") or k == original])
            for source, lang, tracks in choices:
                by_ext = {t.get("ext"): t for t in tracks}
                track = by_ext.get("json3") or by_ext.get("vtt")
                if not track:
                    continue
                raw = ydl.urlopen(track["url"]).read().decode("utf-8", "replace")
                text = _caption_text(json.loads(raw)) if track.get("ext") == "json3" else _vtt_text(raw)
                if len(text.split()) >= MIN_WORDS:
                    result = {"text": text, "language": lang.split("-")[0], "source": source,
                              "title": info.get("title") or ""}
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                    return result
    except yt_dlp.utils.DownloadError as e:
        raise QuizError(f"YouTube wouldn't share this video's details ({str(e).split(':')[-1].strip()[:80]}).") from e
    raise QuizError("This video has no captions long enough to write a fair test from.")


# ---------- questions ----------

def _norm_q(text: str) -> set[str]:
    return content(text)


def _similar(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= 0.6


def _clean(raw: list, seen: list[set[str]]) -> list[dict]:
    out = []
    for q in raw or []:
        if not isinstance(q, dict):
            continue
        question = " ".join(str(q.get("q", "")).split())[:300]
        options = [" ".join(str(o).split())[:160] for o in q.get("options") or []]
        try:
            answer = int(q.get("answer"))
        except (TypeError, ValueError):
            continue
        if (len(question) < 10 or len(options) != 4 or not all(options) or not 0 <= answer < 4
                or len({o.lower() for o in options}) != 4
                or any(re.search(r"\b(all|none) of the (above|options)\b", o, re.I) for o in options)):
            continue
        key = _norm_q(question + " " + options[answer])
        if any(_similar(key, s) for s in seen):
            continue
        seen.append(key)
        # Shuffle the options so the right answer isn't always where the model likes to put it.
        order = random.sample(range(4), 4)
        out.append({"q": question, "options": [options[i] for i in order], "answer": order.index(answer),
                    "why": " ".join(str(q.get("why", "")).split())[:240]})
    return out


def _windows(text: str, count: int, fresh: bool) -> list[str]:
    """Different parts of the transcript, one per call (the Space writes the same thing for the same
    prompt, so calls must never see identical text). A first test spreads them evenly; a retake picks
    random parts."""
    size = min(WINDOW, len(text) // count + 400)
    room = max(len(text) - size, 0)
    if fresh:
        starts = [random.randint(0, room) for _ in range(count)]
    else:
        starts = [round(i * room / max(count - 1, 1)) for i in range(count)]
    return [text[s: s + size] for s in starts]


async def _ask(writer: Writer, title: str, window: str, n: int, avoid: list[str]) -> list:
    user = f"VIDEO: {title}\n\nTRANSCRIPT (part):\n{window}\n\nWrite {n} questions."
    if avoid:
        user += "\n\nAVOID (already asked):\n" + "\n".join(f"- {a}" for a in avoid[-40:])
    data, _ = _read_reply(await writer.chat([{"role": "system", "content": QUIZ_PROMPT},
                                             {"role": "user", "content": user}], max_tokens=SPACE_BUDGET))
    return (data or {}).get("questions") or []


async def make_questions(writer: Writer, title: str, transcript: str, avoid: list[str], fresh: bool,
                         progress=lambda share, message: None) -> list[dict]:
    seen = [_norm_q(a) for a in avoid]
    windows = _windows(transcript, 2, fresh)
    progress(0.35, f"Writing {QUESTIONS} questions from the transcript… (2 to 3 minutes on the free GPU)")
    batches = await asyncio.gather(*[_ask(writer, title, w, PER_CALL, avoid) for w in windows], return_exceptions=True)
    errors = [b for b in batches if isinstance(b, BaseException)]
    questions = []
    for b in batches:
        if not isinstance(b, BaseException):
            questions += _clean(b, seen)
    for _ in range(2):  # top up if checks dropped too many
        if len(questions) >= QUESTIONS:
            break
        progress(0.75, f"{len(questions)} good questions so far. Writing a few more…")
        try:
            extra = await _ask(writer, title, random.choice(_windows(transcript, 3, True)),
                               QUESTIONS - len(questions) + 2, avoid + [q["q"] for q in questions])
        except Exception as e:
            errors.append(e)
            break
        questions += _clean(extra, seen)
    if len(questions) < QUESTIONS:
        if errors:
            raise errors[0]
        raise QuizError("The model couldn't write enough distinct questions for this video. Please try again.")
    return questions[:QUESTIONS]


# ---------- progress: viewed videos and test scores ----------

class Progress:
    """data/learning/progress.json: what was watched and every test score. One person's app, one file."""

    def __init__(self, path: Path, quiz_dir: Path):
        self.path, self.quiz_dir = path, quiz_dir
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"videos": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def all(self) -> dict:
        with self.lock:
            return self._load()["videos"]

    def _video(self, data: dict, video_id: str, title: str = "") -> dict:
        v = data["videos"].setdefault(video_id, {"viewed": False, "viewed_at": None, "title": title, "attempts": []})
        if title:
            v["title"] = title
        return v

    def set_viewed(self, video_id: str, viewed: bool, title: str = "") -> dict:
        with self.lock:
            data = self._load()
            v = self._video(data, video_id, title)
            v["viewed"], v["viewed_at"] = viewed, datetime.now().isoformat(timespec="seconds") if viewed else None
            self._save(data)
            return v

    def asked_before(self, video_id: str) -> list[str]:
        """Every question already put to the person on this video, so a retake can avoid them."""
        out = []
        for a in self.all().get(video_id, {}).get("attempts", []):
            quiz = self.quiz_dir / f"{a.get('quiz_id')}.json"
            if quiz.exists():
                out += [q["q"] for q in json.loads(quiz.read_text(encoding="utf-8"))["questions"]]
        return out

    def new_quiz(self, video_id: str, title: str, questions: list[dict], meta: dict) -> dict:
        quiz = {"id": uuid.uuid4().hex[:12], "video": video_id, "title": title, "questions": questions,
                "created": datetime.now().isoformat(timespec="seconds"), **meta}
        self.quiz_dir.mkdir(parents=True, exist_ok=True)
        (self.quiz_dir / f"{quiz['id']}.json").write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
        return quiz

    def quiz(self, quiz_id: str) -> dict:
        path = self.quiz_dir / f"{quiz_id}.json"
        if not re.fullmatch(r"[0-9a-f]{12}", quiz_id) or not path.exists():
            raise QuizError("That test has expired. Start a new one.")
        return json.loads(path.read_text(encoding="utf-8"))

    def grade(self, quiz_id: str, answers: list[int | None]) -> dict:
        quiz = self.quiz(quiz_id)
        results = [{"correct": a == q["answer"], "chosen": a, "answer": q["answer"], "why": q["why"]}
                    for q, a in zip(quiz["questions"], list(answers) + [None] * len(quiz["questions"]))]
        score = sum(r["correct"] for r in results)
        with self.lock:
            data = self._load()
            v = self._video(data, quiz["video"], quiz["title"])
            if not any(a.get("quiz_id") == quiz_id for a in v["attempts"]):  # submitting twice records once
                v["attempts"].append({"quiz_id": quiz_id, "score": score, "total": len(results),
                                      "at": datetime.now().isoformat(timespec="seconds")})
            self._save(data)
        return {"score": score, "total": len(results), "results": results, "video": v}
