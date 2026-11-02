"""Interview practice: five tough non-technical questions answered on camera, then feedback on body
language, articulation and mindset.

What happens to a recorded answer
  1. The page records the answer and, while recording, takes a frame every 2 seconds. When the
     answer ends it sends the video (kept for playback), the frames and the audio as 16 kHz WAV.
  2. On this computer's CPU (app/youtube/media.mjs, free, nothing leaves the machine):
       speech      onnx-community/whisper-base   transcript with timings, plus pauses from loudness
       body        onnx-community/vitpose-base-simple   17 body keypoints per frame
       face        Xenova/facial_emotions_image_detection   expression of the face area
  3. Code turns that into measurements interviewers' expectations can be checked against: speaking
     pace, filler and hedging words, pauses, length; eye contact with the camera, posture, head tilt,
     hand gestures, fidgeting, expressions.
  4. The free writer Space (the requester's own ZeroGPU time) compares the transcript and the
     measurements with what interviewers look for in that question, and names the flaws with their
     evidence and a fix. Quotes it gives as evidence are checked against the transcript.
"""
import asyncio
import json
import math
import re
import statistics
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import videos
from .learning import SPACE_BUDGET, _read_reply
from .llm import Writer
from .tailor import norm

QUESTIONS = [
    {"q": "Tell me about yourself.",
     "seconds": (60, 120),
     "look_for": "A focused 1-2 minute professional story (present role, key past experience, why this role "
                 "next), relevant to the job, delivered with confidence. Not a life story or a CV recital."},
    {"q": "What is your greatest weakness?",
     "seconds": (45, 120),
     "look_for": "A real, specific weakness that isn't central to the job, honest self-awareness, and concrete "
                 "steps already taken to improve, with evidence. Not a disguised strength (\"I'm a "
                 "perfectionist\"), and not defensive."},
    {"q": "Tell me about a time you failed.",
     "seconds": (60, 150),
     "look_for": "A specific situation told in a clear structure (situation, task, action, result), full "
                 "ownership with no blaming of others, what was learned, and how it has been applied since. "
                 "A growth mindset."},
    {"q": "Describe a conflict with a colleague and how you handled it.",
     "seconds": (60, 150),
     "look_for": "Calm, respectful description of the other person (no badmouthing), listening and empathy, "
                 "a collaborative resolution that the candidate drove, and a positive outcome or lesson."},
    {"q": "Why should we hire you?",
     "seconds": (45, 120),
     "look_for": "Two or three strengths tied directly to what the role needs, each backed by evidence or a "
                 "result, enthusiasm for the role, confident but not arrogant, ending with a clear close."},
]

MODELS = {  # repo -> its ONNX files (q8); every root config/tokenizer file is fetched too
    "onnx-community/whisper-base": ["onnx/encoder_model_quantized.onnx", "onnx/decoder_model_merged_quantized.onnx"],
    "onnx-community/vitpose-base-simple": ["onnx/model_quantized.onnx"],
    "Xenova/facial_emotions_image_detection": ["onnx/model_quantized.onnx"],
}
MEDIA_WORKER = videos.WORKER.parent / "media.mjs"

FILLERS = ["um", "uh", "erm", "er", "ah", "hmm", "you know", "basically", "actually", "literally", "i mean",
           "kind of", "sort of", "like i said", "or whatever", "and stuff", "you see"]
HEDGES = ["i think", "i guess", "maybe", "probably", "i'm not sure", "i don't know", "hopefully", "i feel like",
          "just", "a little bit", "kind of", "sort of", "try to", "i suppose"]


class InterviewError(Exception):
    pass


# ---------- models ----------

_model_lock = threading.Lock()


def _model_done(model_dir: Path) -> bool:
    return all((model_dir / repo / files[-1]).exists() for repo, files in MODELS.items())


@contextmanager
def _get(client, method: str, url: str):
    """A streamed request that waits out Hugging Face's rate limit (429) instead of failing."""
    for attempt in range(6):
        r = client.send(client.build_request(method, url), stream=True)
        if r.status_code != 429:
            try:
                r.raise_for_status()
                yield r
            finally:
                r.close()
            return
        r.close()
        time.sleep(min(60, float(r.headers.get("retry-after") or 5 * (attempt + 1))))
    raise InterviewError("Hugging Face is limiting downloads right now. Please try again in a few minutes.")


def ensure_models(model_dir: Path, progress=lambda message: None, token: str | None = None) -> None:
    """Download the three models once (about 200 MB). Each file is renamed into place when complete,
    and a repo's main ONNX file comes last, so its presence means that model is all there. The
    person's own token is sent when there is one: signed-in downloads get far higher rate limits."""
    import httpx
    with _model_lock:
        if _model_done(model_dir):
            return
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120), headers=headers) as client:
            for repo, onnx_files in MODELS.items():
                target = model_dir / repo
                if (target / onnx_files[-1]).exists():
                    continue
                with _get(client, "GET", f"https://huggingface.co/api/models/{repo}") as r:
                    r.read()
                    listing = r.json()
                root = [s["rfilename"] for s in listing.get("siblings", [])
                        if "/" not in s["rfilename"] and s["rfilename"].endswith((".json", ".txt"))]
                for name in root + onnx_files:
                    dest = target / name
                    if dest.exists():
                        continue
                    progress(f"Getting the speech and body-language models ready, a one-time download… ({repo.split('/')[-1]})")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    part = dest.with_name(dest.name + ".part")
                    with _get(client, "GET", f"https://huggingface.co/{repo}/resolve/main/{name}") as r:
                        with part.open("wb") as f:
                            for chunk in r.iter_bytes(1 << 20):
                                f.write(chunk)
                    part.replace(dest)


_worker: videos._Worker | None = None


def _media(model_dir: Path) -> videos._Worker:
    global _worker
    if _worker is None:
        _worker = videos._Worker(model_dir, MEDIA_WORKER)
    return _worker


# ---------- measurements ----------

def _count(text: str, phrases: list[str]) -> dict[str, int]:
    t = " " + re.sub(r"[^a-z' ]+", " ", text.lower()) + " "
    found = {p: len(re.findall(rf"(?<![a-z']){re.escape(p)}(?![a-z'])", t)) for p in phrases}
    return {p: n for p, n in found.items() if n}


def speech_metrics(t: dict, seconds: tuple[int, int]) -> dict:
    words = len(re.findall(r"[A-Za-z0-9']+", t["text"]))
    minutes = max(t["duration"], 1) / 60
    fillers = _count(t["text"], FILLERS)
    hedges = _count(t["text"], HEDGES)
    pauses = [round(b - a, 1) for a, b in t.get("pauses", [])]
    return {
        "length_seconds": round(t["duration"]),
        "expected_seconds": f"{seconds[0]}-{seconds[1]}",
        "words": words,
        "words_per_minute": round(words / minutes),
        "typical_words_per_minute": "130-160 (under 110 sounds hesitant, over 180 rushed)",
        "filler_words": fillers,
        "fillers_per_100_words": round(100 * sum(fillers.values()) / max(words, 1), 1),
        "hedging_phrases": hedges,
        "pauses_over_1_2s": len(pauses),
        "longest_pause_seconds": max(pauses, default=0),
        "speaking_share": round(t.get("speech_seconds", 0) / max(t["duration"], 1), 2),
    }


def _angle(a: list, b: list) -> float:
    """Tilt of the line from a to b, in degrees from horizontal."""
    deg = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    return (deg + 90) % 180 - 90


def body_metrics(frames: list[dict]) -> dict:
    """Body language over the answer, from keypoints taken every 2 seconds."""
    seen = lambda p: p[2] >= 0.3  # noqa: E731
    n = len(frames)
    face, facing, tilt, shoulders, slouch, hands, framing = 0, 0, [], [], [], 0, []
    nose_path, wrist_path, expressions = [], [], []
    for f in frames:
        k = f["keypoints"]
        has_face = all(seen(k[p]) for p in ("nose", "left_eye", "right_eye"))
        has_shoulders = seen(k["left_shoulder"]) and seen(k["right_shoulder"])
        if has_face:
            face += 1
            eyes = math.dist(k["left_eye"][:2], k["right_eye"][:2]) or 1
            mid = (k["left_eye"][0] + k["right_eye"][0]) / 2
            if abs(k["nose"][0] - mid) / eyes < 0.25:  # nose centred between the eyes: facing the camera
                facing += 1
            tilt.append(abs(_angle(k["right_eye"], k["left_eye"])))
            framing.append(eyes / f["width"])
        if has_shoulders:
            width = math.dist(k["left_shoulder"][:2], k["right_shoulder"][:2]) or 1
            shoulders.append(abs(_angle(k["right_shoulder"], k["left_shoulder"])))
            if has_face:  # head height above the shoulder line, relative to shoulder width
                line_y = (k["left_shoulder"][1] + k["right_shoulder"][1]) / 2
                slouch.append((line_y - k["nose"][1]) / width)
                nose_path.append((k["nose"][0] / width, k["nose"][1] / width))
        wrists = [k[w] for w in ("left_wrist", "right_wrist") if seen(k[w])]
        if wrists:
            hands += 1
            if has_shoulders:
                wrist_path.append(tuple(c / width for w in wrists[:1] for c in w[:2]))
        if f.get("expression"):
            expressions.append({e["label"]: e["score"] for e in f["expression"]})

    def moves(path):  # average move between frames, in shoulder widths
        steps = [math.dist(a, b) for a, b in zip(path, path[1:])]
        return round(statistics.mean(steps), 3) if steps else None

    pct = lambda x: round(100 * x / n) if n else None  # noqa: E731
    return {
        "frames_analysed": n,
        "face_visible_pct": pct(face),
        "facing_camera_pct": pct(facing),
        "typical_facing_camera_pct": "70% or more reads as good eye contact on video",
        "head_tilt_degrees": round(statistics.median(tilt), 1) if tilt else None,
        "shoulder_tilt_degrees": round(statistics.median(shoulders), 1) if shoulders else None,
        "head_height_over_shoulders": round(statistics.median(slouch), 2) if slouch else None,
        "head_height_note": "about 0.5 or more is upright; well under 0.4 suggests slouching or leaning in",
        "hands_visible_pct": pct(hands),
        "head_movement_per_frame": moves(nose_path),
        "hand_movement_per_frame": moves(wrist_path),
        "movement_note": "in shoulder widths every 2 s; over 0.15 for the head reads as fidgeting or swaying",
        "face_size_in_frame": round(statistics.median(framing), 3) if framing else None,
        "framing_note": "eye distance / frame width; under 0.04 means too far from the camera, over 0.12 too close",
        # Only smiles: small expression models spot them reliably, but read neutral faces as "angry" or
        # "sad" far too often to judge anyone on, so those readings aren't used at all.
        "smiling_pct": pct(sum(e.get("happy", 0) >= 0.4 for e in expressions)) if expressions else None,
        "smiling_note": "warm, natural smiles at times help; 10-40% of frames is common in good interviews",
    }


# ---------- feedback ----------

FEEDBACK_PROMPT = """You are an experienced interview coach. You get one answer a candidate gave on video: the
QUESTION, what interviewers commonly LOOK FOR in it, the TRANSCRIPT, and MEASUREMENTS of their speech and body
language (each with what is typical). Judge it against what is commonly required, and be specific and honest.
Rules:
- Three areas: "body_language" (from the body measurements only), "articulation" (pace, fillers, pauses,
  clarity, structure, length), "mindset" (what the content reveals: ownership, confidence, positivity,
  self-awareness, growth, motivation).
- List the real flaws, most important first (at most 5). Each has "evidence": a short exact quote from the
  TRANSCRIPT, or the measurement it comes from. Never invent a quote. Never comment on looks, accent or identity.
- Fewer than 5 frames is too little to judge body language on; say so instead.
- "fix" is one practical thing to do differently.
- Also list 1-3 genuine strengths, and in "stronger_answer" say in 2-3 sentences how a strong answer to this
  question would go for this candidate.
- Scores 1-5 per area (3 = acceptable, 5 = excellent). If the transcript is nearly empty, say so.
Return JSON only:
{"scores": {"body_language": 0, "articulation": 0, "mindset": 0},
 "flaws": [{"area": "", "issue": "", "evidence": "", "fix": ""}],
 "strengths": [""], "stronger_answer": ""}"""


def _clean_feedback(data: dict, transcript: str) -> dict:
    t = norm(transcript)
    scores = {}
    for area in ("body_language", "articulation", "mindset"):
        try:
            scores[area] = max(1, min(5, int(round(float((data.get("scores") or {}).get(area))))))
        except (TypeError, ValueError):
            scores[area] = None
    flaws = []
    for f in data.get("flaws") or []:
        if not isinstance(f, dict) or f.get("area") not in scores or not str(f.get("issue", "")).strip():
            continue
        evidence = " ".join(str(f.get("evidence", "")).split())[:240]
        quotes = re.findall(r"[\"“]([^\"”]{6,})[\"”]", evidence)
        if not quotes and f["area"] != "body_language" and evidence and not re.search(r"\d", evidence):
            quotes = [evidence]  # words with no number in them are a quote, just without quote marks
        if any(norm(q).strip(" .,!?…") not in t for q in quotes):
            evidence = ""  # a quote the candidate didn't say: keep the point, drop the made-up evidence
        flaws.append({"area": f["area"], "issue": " ".join(str(f["issue"]).split())[:300], "evidence": evidence,
                      "fix": " ".join(str(f.get("fix", "")).split())[:300]})
    return {"scores": scores, "flaws": flaws[:5],
            "strengths": [" ".join(str(s).split())[:240] for s in data.get("strengths") or [] if str(s).strip()][:3],
            "stronger_answer": " ".join(str(data.get("stronger_answer", "")).split())[:700]}


async def feedback(writer: Writer, index: int, role: str, transcript: str, speech: dict, body: dict) -> dict:
    q = QUESTIONS[index]
    user = (f"QUESTION: {q['q']}\nROLE THEY ARE INTERVIEWING FOR: {role or 'not given'}\nLOOK FOR: {q['look_for']}\n\n"
            f"TRANSCRIPT:\n{transcript[:6000] or '(nothing was said)'}\n\n"
            f"SPEECH MEASUREMENTS:\n{json.dumps(speech)}\n\nBODY LANGUAGE MEASUREMENTS:\n{json.dumps(body)}")
    data, _ = _read_reply(await writer.chat([{"role": "system", "content": FEEDBACK_PROMPT},
                                             {"role": "user", "content": user}], max_tokens=SPACE_BUDGET))
    if not data:
        raise InterviewError("The model's feedback couldn't be read.")
    return _clean_feedback(data, transcript)


# ---------- a practice session ----------

def answer_files(session_dir: Path, index: int) -> dict:
    base = session_dir / f"q{index + 1}"
    video = next((p for p in session_dir.glob(f"q{index + 1}.*") if p.suffix in (".webm", ".mp4")), None)
    return {"video": video, "wav": base.with_suffix(".wav"), "frames": sorted((session_dir / f"q{index + 1}-frames").glob("*.jpg"))}


async def analyse(session_dir: Path, model_dir: Path, writer: Writer, role: str, progress) -> dict:
    """Every recorded answer: local speech and vision first, then the AI's feedback (calls overlap)."""
    answered = [i for i in range(len(QUESTIONS)) if answer_files(session_dir, i)["wav"].exists()]
    if not answered:
        raise InterviewError("Record at least one answer first.")
    videos._Worker.node_or_raise()  # say what's missing before any download
    progress(0.05, "Getting the speech and body-language models ready…")
    await asyncio.to_thread(ensure_models, model_dir, lambda m: progress(0.05, m), writer.token)
    media = _media(model_dir)
    results: dict[int, dict] = {}
    pending = []
    for step, i in enumerate(answered):
        files = answer_files(session_dir, i)
        share = 0.1 + 0.6 * step / len(answered)
        progress(share, f"Question {i + 1}: transcribing your answer on this computer…")
        t = await media.call("transcribe", wav=str(files["wav"]), timeout=900)
        progress(share + 0.3 / len(answered), f"Question {i + 1}: reading your body language from {len(files['frames'])} frames…")
        frames = await media.call("frames", paths=[str(p) for p in files["frames"]], timeout=900) if files["frames"] else []
        speech, body = speech_metrics(t, QUESTIONS[i]["seconds"]), body_metrics(frames)
        results[i] = {"question": QUESTIONS[i]["q"], "transcript": t["text"], "speech": speech, "body": body}
        pending.append((i, asyncio.create_task(feedback(writer, i, role, t["text"], speech, body))))
    progress(0.75, "Comparing your answers with what interviewers look for… (1-2 minutes per answer on the free GPU)")
    for i, job in pending:
        try:
            results[i]["feedback"] = await job
        except Exception as e:
            results[i]["feedback"] = None
            results[i]["feedback_error"] = str(e)[:200] if isinstance(e, (InterviewError, ValueError)) else \
                "The AI couldn't review this answer. Try again in a minute."
    return summarise(results, role)


def summarise(results: dict[int, dict], role: str) -> dict:
    areas = ("body_language", "articulation", "mindset")
    reviewed = [r for r in results.values() if r.get("feedback")]
    overall = {}
    for a in areas:
        vals = [r["feedback"]["scores"][a] for r in reviewed if r["feedback"]["scores"].get(a)]
        overall[a] = round(statistics.mean(vals), 1) if vals else None
    top = [dict(f, question=r["question"]) for r in reviewed for f in r["feedback"]["flaws"][:1]]
    return {"role": role, "analysed_at": datetime.now().isoformat(timespec="seconds"), "overall": overall,
            "top_fixes": top[:5], "answers": {str(i + 1): r for i, r in sorted(results.items())}}
