"""Mr. Job Consultant: local server for the job search and application screens.

Run:  .venv\\Scripts\\python -m uvicorn app.server:app --port 8765
"""
import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import __version__, interview, learning, llm, matching, quiz, render, tailor, videos
from .cv_extract import extract_contact, extract_text
from .jobpage import fetch_job_page
from .jobs import Job, best_title, filter_jobs, load_jobs
from .titles import suggest as suggest_titles

ROOT = Path(__file__).resolve().parent.parent
# ./data when run from source; the builds' launcher (app/launcher.py) points this at a per-user folder.
DATA_DIR = Path(os.environ["MRJC_DATA_DIR"]) if os.environ.get("MRJC_DATA_DIR") else ROOT / "data"
CV_DIR = DATA_DIR / "cv"
PROFILE_PATH = DATA_DIR / "search_profile.json"
PORTALS_PATH = DATA_DIR / "public_job_portals.json"
JOBS_CACHE = DATA_DIR / "jobs_cache.json"
AI_SETTINGS = DATA_DIR / "ai_settings.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"

log = logging.getLogger("app")

MAX_CV_BYTES = 10 * 1024 * 1024
CV_TYPES = {".pdf", ".docx", ".doc", ".txt", ".rtf"}

app = FastAPI(title="Mr. Job Consultant", version=__version__)


class Location(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["city", "region", "country", "custom"]
    detail: str | None = None
    code: str | None = Field(default=None, max_length=2)


class Contact(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    website: str | None = None


class CVInfo(BaseModel):
    filename: str
    stored_as: str
    size: int
    contact: Contact = Contact()


class Salary(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    min: int | None = Field(default=None, ge=0, le=100_000_000)
    max: int | None = Field(default=None, ge=0, le=100_000_000)
    period: Literal["year", "month", "hour"] = "year"

    @model_validator(mode="after")
    def _range(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("The lower salary figure is above the upper one.")
        return self


class Details(BaseModel):
    """The extra questions long application forms (Indeed, LinkedIn, Workday…) ask. All optional."""
    authorized_countries: list[Location] = Field(default_factory=list, max_length=30)
    needs_sponsorship: Literal["no", "yes", "depends"] | None = None
    salary: Salary | None = None
    notice: Literal["immediately", "1_week", "2_weeks", "1_month", "2_months", "3_months", "date"] | None = None
    start_date: date | None = None
    years_experience: int | None = Field(default=None, ge=0, le=60)

    @model_validator(mode="after")
    def _start(self):
        if self.notice == "date" and not self.start_date:
            raise ValueError("Pick the date you could start.")
        if self.notice != "date":
            self.start_date = None
        if self.salary and self.salary.min is None and self.salary.max is None:
            self.salary = None
        return self


class SearchProfile(BaseModel):
    job_title: str = Field(min_length=2, max_length=120)
    worldwide: bool = False
    locations: list[Location] = Field(default_factory=list, max_length=20)
    job_types: list[Literal["remote", "full_time", "part_time", "contract"]] = Field(min_length=1)
    schedule: Literal["fixed", "flexible"]
    shifts: list[Literal["morning", "day", "evening", "night", "rotating", "weekend"]] = Field(default_factory=list)
    cv: CVInfo
    contact: Contact = Contact()
    details: Details = Details()
    related_titles: list[str] = Field(default_factory=list, max_length=20)  # searched alongside job_title
    use_cv_matching: bool = True        # rank jobs by how well they fit the CV
    allow_inference_backup: bool = False  # may spend the searcher's monthly Inference credits

    @model_validator(mode="after")
    def _consistent(self):
        if not self.worldwide and not self.locations:
            raise ValueError("Pick at least one location, or choose worldwide.")
        if self.schedule == "flexible":
            self.shifts = []
        if not (CV_DIR / self.cv.stored_as).is_file():
            raise ValueError("The attached CV is no longer on disk — please attach it again.")
        return self


def _portal_summary(profile: SearchProfile) -> dict:
    """How many of the crawler's login-free portals fit this profile."""
    if not PORTALS_PATH.exists():
        return {"total": 0, "matching": 0, "remote_focused": 0}
    portals = json.loads(PORTALS_PATH.read_text(encoding="utf-8"))
    codes = {l.code.upper() for l in profile.locations if l.code}
    remote = "remote" in profile.job_types

    def fits(p: dict) -> bool:
        country = p.get("country")
        if profile.worldwide or not country or country in codes:
            return True
        return remote and "remote" in (p.get("niches") or [])

    return {
        "total": len(portals),
        "matching": sum(fits(p) for p in portals),
        "remote_focused": sum("remote" in (p.get("niches") or []) for p in portals),
    }


@app.get("/api/version")
def version() -> dict:
    """Lets the launcher tell a running copy of this app apart from anything else on the port."""
    return {"app": "MrJobConsultant", "version": __version__}


@app.get("/api/profile")
def get_profile() -> dict:
    if not PROFILE_PATH.exists():
        return {}
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


@app.put("/api/profile")
def save_profile(profile: SearchProfile) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = profile.model_dump(mode="json") | {"saved_at": datetime.now().isoformat(timespec="seconds")}
    PROFILE_PATH.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"saved": True, "portals": _portal_summary(profile)}


@app.post("/api/cv")
async def upload_cv(file: UploadFile = File(...)) -> CVInfo:
    name = Path(file.filename or "cv").name
    suffix = Path(name).suffix.lower()
    if suffix not in CV_TYPES:
        raise HTTPException(415, "Please attach a PDF, Word (.docx/.doc), RTF or plain-text CV.")
    data = await file.read(MAX_CV_BYTES + 1)
    if len(data) > MAX_CV_BYTES:
        raise HTTPException(413, "That file is over 10 MB — try a smaller export of your CV.")
    if not data:
        raise HTTPException(400, "That file is empty.")

    CV_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", Path(name).stem)[:60] or "cv"
    stored = f"{datetime.now():%Y%m%d-%H%M%S}_{safe}{suffix}"
    path = CV_DIR / stored
    path.write_bytes(data)

    try:
        contact = extract_contact(extract_text(path))
    except Exception:  # a damaged or unusual file still uploads; the person fills details in by hand
        contact = {}
    return CVInfo(filename=name, stored_as=stored, size=len(data), contact=Contact(**contact))


# ---------- AI matching ----------

def _ai_settings() -> dict:
    defaults = {"space_id": None, "writer_space_id": None, "allow_local_login": True}
    if AI_SETTINGS.exists():
        defaults |= json.loads(AI_SETTINGS.read_text(encoding="utf-8"))
    return defaults


PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host", "cf-connecting-ip")


def _is_local(request: Request) -> bool:
    """A request from the person running the app, on this machine, not relayed by a proxy.

    Behind a reverse proxy every visitor arrives from 127.0.0.1, so any forwarding header, or a
    Host that isn't a loopback name, means "someone else" and they never get the local login.
    """
    if (request.client.host if request.client else "") not in matching.LOOPBACK:
        return False
    if any(h in request.headers for h in PROXY_HEADERS):
        return False
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    return host in matching.LOOPBACK


def _token(request: Request, header_token: str | None) -> tuple[str | None, str]:
    host = "127.0.0.1" if _is_local(request) else "remote"
    return matching.resolve_token(header_token, host, _ai_settings()["allow_local_login"])


@app.get("/api/ai/status")
def ai_status(request: Request, x_hf_token: str | None = Header(default=None)) -> dict:
    settings = _ai_settings()
    token, origin = _token(request, x_hf_token)
    out = {
        "llm": matching.LLM_ID,
        "embedder": matching.EMBED_ID,
        "is_local": _is_local(request),
        "token_origin": origin,
        "space": matching.space_status(settings["space_id"], token),
        "writer": matching.space_status(settings["writer_space_id"], token),
        "writer_model": llm.SPACE_MODEL,
        "inference_model": llm.ROUTER_MODELS[0],
    }
    if token:
        try:
            out["account"] = matching.account(token)
        except matching.MatchError as e:
            out["token_error"] = str(e)
    return out


@app.post("/api/ai/space")
def setup_space(request: Request, x_hf_token: str | None = Header(default=None)) -> dict:
    """Create the matcher Space on the account of whoever is running this app (local requests only)."""
    if not _is_local(request):
        raise HTTPException(403, "Only the person running this app can set up its Space.")
    token, _ = _token(request, x_hf_token)
    if not token:
        raise HTTPException(400, "Add a Hugging Face token with write access first.")
    try:
        # Public, so other people can call it on their own ZeroGPU quota (it costs the owner nothing).
        space_id = matching.deploy_space(token, private=False)
    except matching.MatchError as e:
        raise HTTPException(400, str(e))
    AI_SETTINGS.write_text(json.dumps(_ai_settings() | {"space_id": space_id}, indent=2), encoding="utf-8")
    return {"space": matching.space_status(space_id, token)}


TASKS: dict[str, dict] = {}


def _search_titles(profile: dict) -> list[str]:
    """The typed title first, then the related titles the person kept (deduplicated)."""
    seen, out = set(), []
    for t in [profile.get("job_title", "")] + list(profile.get("related_titles") or []):
        t = " ".join(str(t).split())[:120]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


_JOBS_MEMO: dict = {}


def _cached_jobs() -> list[Job]:
    """Jobs from the last collection, whatever their age. Suggestions never trigger a fetch."""
    if not JOBS_CACHE.exists():
        return []
    mtime = JOBS_CACHE.stat().st_mtime
    if _JOBS_MEMO.get("mtime") != mtime:
        data = json.loads(JOBS_CACHE.read_text(encoding="utf-8"))
        _JOBS_MEMO.update(mtime=mtime, jobs=[Job(**j) for j in data.get("jobs", [])])
    return _JOBS_MEMO["jobs"]


@app.get("/api/titles/suggest")
def title_suggestions(q: str = "") -> dict:
    """Related job titles for what's typed: rule-based and from live listings, no AI, instant."""
    q = " ".join(q.split())[:120]
    return {"query": q, "suggestions": suggest_titles(q, _cached_jobs()) if len(q) >= 3 else []}


class SearchRequest(BaseModel):
    refresh: bool = False  # ignore the job cache and fetch every source again


@app.post("/api/search")
async def start_search(body: SearchRequest, request: Request, x_hf_token: str | None = Header(default=None)) -> dict:
    if not PROFILE_PATH.exists():
        raise HTTPException(400, "Save your search first.")
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    token, origin = _token(request, x_hf_token)  # lives only in this task's memory, never saved
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"id": task_id, "stage": "collect", "message": "Gathering jobs from the directory…",
                      "progress": 0.0, "results": None, "error": None, "backend": None}
    asyncio.create_task(_run_search(TASKS[task_id], profile, token, origin, body.refresh))
    for old in list(TASKS)[:-20]:  # keep memory bounded
        TASKS.pop(old, None)
    return {"id": task_id}


@app.get("/api/search/{task_id}")
def search_status(task_id: str) -> dict:
    if task_id not in TASKS:
        raise HTTPException(404, "That search has expired. Start a new one.")
    return TASKS[task_id]


async def _run_search(task: dict, profile: dict, token: str | None, origin: str, refresh: bool) -> None:
    try:
        def progress(done: int, total: int) -> None:
            task["progress"] = round(0.5 * done / max(total, 1), 3)
            task["message"] = f"Gathering jobs from the directory… {done} of {total} sources"

        jobs, meta = await load_jobs(PORTALS_PATH, JOBS_CACHE, progress, refresh)
        pool = filter_jobs(jobs, profile)
        titles = _search_titles(profile)
        task.update(progress=0.5, message=f"{len(pool)} of {len(jobs)} jobs fit your location and job type.")

        backend = {"method": "title", "note": ""}
        cv_text = ""
        if profile.get("use_cv_matching", True):
            cv_path = CV_DIR / profile["cv"]["stored_as"]
            cv_text = extract_text(cv_path) if cv_path.exists() else ""
            if not cv_text.strip():
                backend["note"] = "We couldn't read text from your CV, so jobs are ranked by title instead."

        if not cv_text.strip():
            scored = []
            for j in pool:
                score, _ = best_title(j, titles)
                if score >= 0.5:  # a real match to at least one searched title, not a stray shared word
                    scored.append({"url": j.url, "score": round(100 * score), "similarity": None, "llm_score": None,
                                   "reason": "", "missing": [], "reviewed": False, "method": "title"})
        else:
            candidates = matching.prefilter(cv_text, pool, titles)
            scored, backend = await _match_with_best_backend(task, profile, token, origin, cv_text, candidates)

        by_url = {j.url: j for j in pool}
        for s in scored:  # which of the searched titles each job answers to (shown on the card)
            score, matched = best_title(by_url[s["url"]], titles)
            s["matched_title"] = matched if score >= 0.5 else ""
        # AI-reviewed jobs first (they have a reasoned score), then the rest by similarity / keywords.
        scored.sort(key=lambda s: (s["reviewed"], s["score"], by_url[s["url"]].posted), reverse=True)
        task.update(
            stage="done", progress=1.0, backend=backend,
            message=f"Found {min(len(scored), 50)} jobs for you.",
            results=[{**asdict(by_url[s["url"]]), **s} for s in scored[:50]],
            meta={"total_jobs": len(jobs), "in_scope": len(pool), "sources": meta.get("sources"),
                  "cached": meta.get("cached")},
        )
    except Exception as e:  # report, don't crash the server
        log.exception("search failed")
        task.update(stage="error", error=f"Search failed: {type(e).__name__}: {e}"[:300])


async def _match_with_best_backend(task: dict, profile: dict, token: str | None, origin: str,
                                   cv_text: str, candidates: list) -> tuple[list[dict], dict]:
    """ZeroGPU → (opt-in) Inference → free keywords, always on the searcher's own account."""
    notes = []
    if token:
        who = matching.account(token)["name"]
        space = matching.space_status(_ai_settings()["space_id"], token)
        if space.get("exists"):
            task.update(progress=0.6, message=f"Comparing your CV with {len(candidates)} jobs on ZeroGPU…")
            try:
                scored = await asyncio.to_thread(matching.zerogpu_match, space["id"], token, cv_text,
                                                 candidates, matching.LLM_TOP_K)
                return scored, {"method": "zerogpu", "account": who, "origin": origin,
                                "note": f"Ran on {who}'s ZeroGPU quota."}
            except matching.MatchError as e:
                notes.append(str(e))
        if profile.get("allow_inference_backup"):
            task.update(progress=0.6, message=f"Comparing your CV with {len(candidates)} jobs via HF Inference…")
            try:
                scored = await matching.inference_match(token, cv_text, candidates, matching.LLM_TOP_K)
                return scored, {"method": "inference", "account": who, "origin": origin,
                                "note": " ".join(notes + [f"Used {who}'s monthly Inference credits."])}
            except matching.MatchError as e:
                notes.append(str(e))
    else:
        notes.append("No Hugging Face token, so we used free keyword matching.")
    task.update(progress=0.7, message="Matching keywords from your CV…")
    scored = matching.keyword_match(cv_text, candidates, _search_titles(profile))
    return scored, {"method": "keywords", "note": " ".join(notes)}


# ---------- tailored application documents ----------

APPS_DIR = DATA_DIR / "applications"


class JobRef(BaseModel):
    url: str = Field(pattern=r"^https?://")
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""


@app.post("/api/job/details")
async def job_details(job: JobRef) -> dict:
    """The full job post and its apply link: one plain fetch, no AI, nothing billed to anyone."""
    return await fetch_job_page(job.url, job.model_dump())


class ApplyRequest(BaseModel):
    job: dict
    formats: list[Literal["pdf", "docx", "txt"]] = Field(default_factory=lambda: ["pdf", "docx"], min_length=1)
    cover_letter: bool = True
    engine: Literal["zerogpu", "inference"] = "zerogpu"


@app.post("/api/apply")
async def start_apply(body: ApplyRequest, request: Request, x_hf_token: str | None = Header(default=None)) -> dict:
    if not PROFILE_PATH.exists():
        raise HTTPException(400, "Save your profile and CV first.")
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    token, origin = _token(request, x_hf_token)  # the requester's own token, held in memory only
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"id": task_id, "stage": "start", "message": "Getting started…", "progress": 0.0,
                      "result": None, "error": None}
    asyncio.create_task(_run_apply(TASKS[task_id], profile, token, origin, body))
    return {"id": task_id}


@app.get("/api/apply/{task_id}")
def apply_status(task_id: str) -> dict:
    if task_id not in TASKS:
        raise HTTPException(404, "That request has expired. Please try again.")
    return TASKS[task_id]


@app.get("/api/files/{folder}/{name}")
def download(folder: str, name: str) -> FileResponse:
    path = (APPS_DIR / folder / name).resolve()
    if APPS_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "File not found.")
    return FileResponse(path, filename=name)


def _plain_blocks(cv_text: str, contact: list[str], name: str) -> list:
    """No AI available: the CV itself in a clean ATS layout, untailored."""
    heading = re.compile(r"^(summary|profile|experience|work experience|employment|education|skills|projects|"
                         r"certifications?|languages|awards|publications|interests)\s*:?$", re.I)
    blocks: list = [("name", name or "Resume"), ("contact", " | ".join(contact))]
    for line in cv_text.splitlines():
        line = line.strip()
        if not line or line == name:
            continue
        if heading.match(line) or (line.isupper() and len(line) < 40):
            blocks.append(("heading", line.rstrip(":").title()))
        elif line[:1] in "•-*–▪●":
            blocks.append(("bullet", line[1:].strip()))
        else:
            blocks.append(("para", line))
    return blocks


async def _run_apply(task: dict, profile: dict, token: str | None, origin: str, body: ApplyRequest) -> None:
    job = body.job
    report: list[str] = []
    try:
        cv_path = CV_DIR / profile["cv"]["stored_as"]
        cv_text = extract_text(cv_path) if cv_path.exists() else ""
        if not cv_text.strip():
            raise ValueError("We couldn't read text from your CV. Try attaching it as PDF or DOCX.")
        c = profile.get("contact") or {}
        contact = [x for x in (c.get("email"), c.get("phone"), c.get("linkedin"), c.get("website")) if x]

        folder = f"{datetime.now():%Y%m%d-%H%M%S}-{render.safe_name(job.get('company') or 'job')[:30]}".replace(" ", "_")
        out_dir = APPS_DIR / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        engine_info: dict = {"engine": "none", "account": None, "model": None}

        if not token:
            task.update(progress=0.3, message="No Hugging Face token: making a clean ATS copy of your CV (not tailored)…")
            name = c.get("name") or ""
            resume_blocks = _plain_blocks(cv_text, contact, name)
            letter = []
            report.append("Without a Hugging Face token nothing was tailored and no cover letter was written. "
                          "Your CV is reformatted as it is.")
        else:
            who = matching.account(token)["name"]
            settings = _ai_settings()
            engine = body.engine
            writer = llm.Writer(engine, token, settings["writer_space_id"])
            engine_info = {"engine": engine, "account": who, "origin": origin, "model": writer.model_used}

            task.update(progress=0.15, message="Reading the facts in your CV…")
            try:
                cv, extract_report = await tailor.structured_cv(writer, cv_text, CV_DIR / "cache")
            except matching.MatchError as e:
                if engine == "zerogpu" and profile.get("allow_inference_backup"):
                    report.append(f"{e} Switched to HF Inference (your credits).")
                    writer = llm.Writer("inference", token, None)
                    engine_info["engine"] = "inference"
                    cv, extract_report = await tailor.structured_cv(writer, cv_text, CV_DIR / "cache")
                else:
                    raise
            report += extract_report
            source = tailor.Source(cv_text)

            task.update(progress=0.45, message="Tailoring your resume to this job (checking every fact)…")
            resume = await tailor.tailor(writer, cv, source, job, report)
            name = c.get("name") or cv.get("name") or ""
            resume.update(name=name, contact=[x for x in [cv.get("location")] + contact if x])
            resume_blocks = render.resume_blocks(resume)
            task["keywords"] = {"matched": resume["matched_keywords"], "missing": resume["missing_keywords"]}

            letter = []
            if body.cover_letter:
                task.update(progress=0.7, message="Writing your cover letter (checking every sentence)…")
                letter = await tailor.cover_letter(writer, cv, source, job, report)
            engine_info["model"] = writer.model_used

        task.update(progress=0.9, message="Making your files…")
        base = render.safe_name(f"{name or 'Resume'} - Resume - {job.get('company') or job.get('title') or 'Job'}")
        files = [("resume", p) for p in render.write_files(resume_blocks, out_dir / base, body.formats)]
        if letter:
            lb = render.letter_blocks(name, contact, job.get("company", ""), job.get("title", ""), letter)
            lbase = render.safe_name(f"{name or 'Cover letter'} - Cover letter - {job.get('company') or 'Job'}")
            files += [("cover_letter", p) for p in render.write_files(lb, out_dir / lbase, body.formats)]
        (out_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

        task.update(stage="done", progress=1.0, message="Your documents are ready.", result={
            "files": [{"kind": k, "format": p.suffix[1:], "name": p.name, "url": f"/api/files/{folder}/{p.name}"}
                      for k, p in files],
            "apply_url": job.get("apply_url") or job.get("source_url") or job.get("url"),
            "report": report, "keywords": task.get("keywords"), **engine_info,
            "letter_written": bool(letter), "letter_requested": body.cover_letter,
        })
    except Exception as e:
        log.exception("apply failed")
        msg = str(e) if isinstance(e, (matching.MatchError, ValueError)) else f"{type(e).__name__}: {e}"
        task.update(stage="error", error=msg[:300])


# ---------- upskilling & reskilling ----------

LEARN_DIR = DATA_DIR / "learning"
LEARN_FILES = LEARN_DIR / "files"


class LearnRequest(BaseModel):
    mode: Literal["upskill", "reskill"]
    cv: str = Field(pattern=r"^[\w.-]+$", max_length=160)  # stored_as of a CV in data/cv
    target: str = Field(default="", max_length=120)          # role to grow towards / field to move into


class SkillPick(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    why: str = Field(default="", max_length=400)
    role: str = Field(default="", max_length=120)  # reskilling: the role this skill leads to


class CurriculumRequest(LearnRequest):
    skill: SkillPick
    fresh: bool = False  # write a new version instead of opening the saved one


def _free_writer(request: Request, x_hf_token: str | None) -> llm.Writer:
    """The free writer Space on the requester's own token. Fails fast, before a task starts."""
    token, _ = _token(request, x_hf_token)
    if not token:
        raise HTTPException(400, "Add your Hugging Face token first. A free read token is enough.")
    space_id = _ai_settings()["writer_space_id"]
    if not space_id:
        raise HTTPException(400, "The free writer Space isn't set up, so there's no model to ask.")
    return llm.Writer("zerogpu", token, space_id)


def _learn_inputs(body: LearnRequest, request: Request, x_hf_token: str | None) -> tuple[llm.Writer, str]:
    """The free writer Space and the CV's text."""
    path = CV_DIR / body.cv
    if not path.is_file():
        raise HTTPException(400, "That CV is no longer on disk. Please attach it again.")
    cv_text = extract_text(path)
    if not cv_text.strip():
        raise HTTPException(400, "We couldn't read text from that CV. Try a PDF or DOCX export.")
    return _free_writer(request, x_hf_token), cv_text


def _start_task(work, message: str) -> dict:
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = task = {"id": task_id, "stage": "working", "message": message, "progress": 0.1,
                             "result": None, "error": None}

    async def run():
        try:
            task.update(stage="done", progress=1.0, message="Ready.", result=await work(task))
        except Exception as e:
            log.exception("learning task failed")
            msg = str(e) if isinstance(e, (matching.MatchError, ValueError)) else f"{type(e).__name__}: {e}"
            task.update(stage="error", error=msg[:300])

    asyncio.create_task(run())
    for old in list(TASKS)[:-20]:
        TASKS.pop(old, None)
    return {"id": task_id}


@app.post("/api/learn/suggest")
async def learn_suggest(body: LearnRequest, request: Request, x_hf_token: str | None = Header(default=None)) -> dict:
    """Upskilling ideas from the CV. Reskilling has none: the person types the skill they want."""
    if body.mode != "upskill":
        raise HTTPException(400, "Skill ideas are for Upskilling. In Reskilling, type the skill you want to learn.")
    writer, cv_text = _learn_inputs(body, request, x_hf_token)

    async def work(task):
        task.update(progress=0.3, message=f"Reading your CV with {llm.SPACE_MODEL.split('/')[-1]} on your free ZeroGPU time… "
                                          "(a minute or two)")
        return await learning.suggest(writer, cv_text, body.target.strip(), CV_DIR / "cache", LEARN_DIR / "cache")

    return _start_task(work, "Getting started…")


@app.post("/api/learn/curriculum")
async def learn_curriculum(body: CurriculumRequest, request: Request, x_hf_token: str | None = Header(default=None)) -> dict:
    writer, cv_text = _learn_inputs(body, request, x_hf_token)

    async def work(task):
        c = await learning.curriculum(writer, body.mode, cv_text, body.skill.model_dump(), body.target.strip(),
                                      CV_DIR / "cache", LEARN_DIR / "cache", body.fresh,
                                      progress=lambda share, message: task.update(progress=share, message=message))
        task.update(progress=0.9, message="Making your files…")
        out_dir = LEARN_FILES / c["key"]
        out_dir.mkdir(parents=True, exist_ok=True)
        base = render.safe_name(f"{c['skill']} - learning plan")
        files = render.write_files(learning.curriculum_blocks(c), out_dir / base, ["pdf", "docx"])
        return c | {"files": [{"format": p.suffix[1:], "name": p.name, "url": f"/api/learn/files/{c['key']}/{p.name}"}
                              for p in files]}

    return _start_task(work, "Getting started…")


class VideosRequest(BaseModel):
    skill: str = Field(min_length=2, max_length=120)
    topics: list[str] = Field(min_length=1, max_length=40)


@app.post("/api/learn/videos")
async def learn_videos(body: VideosRequest) -> dict:
    """Top YouTube videos for each topic. Free: YouTube's public web API and a sentiment model on this CPU."""
    topics = [" ".join(t.split())[:160] for t in body.topics]

    async def work(task):
        task["videos"] = [None] * len(topics)  # filled in topic by topic, so the page can show them as they come
        done = 0
        if not videos.model_ready(LEARN_DIR / "models"):
            task.update(message="Getting the comment-sentiment model ready: a one-time 125 MB download…")

        def on_topic(i, found, error):
            nonlocal done
            done += 1
            task["videos"][i] = {"videos": found, "error": error}
            task.update(progress=round(done / len(topics), 3), message=f"Ranked videos for {done} of {len(topics)} topics…")

        await videos.for_topics(topics, body.skill, LEARN_DIR / "videos", LEARN_DIR / "models", on_topic)
        return {"sentiment_model": videos.SENTIMENT_MODEL}

    return _start_task(work, "Searching YouTube…")


PROGRESS = quiz.Progress(LEARN_DIR / "progress.json", LEARN_DIR / "quizzes")
VIDEO_ID = r"^[\w-]{11}$"


@app.get("/api/learn/progress")
def learn_progress() -> dict:
    """Which videos were watched, and every test score."""
    return PROGRESS.all()


class ViewedRequest(BaseModel):
    video: str = Field(pattern=VIDEO_ID)
    viewed: bool = True
    title: str = Field(default="", max_length=300)


@app.put("/api/learn/viewed")
def learn_viewed(body: ViewedRequest) -> dict:
    return PROGRESS.set_viewed(body.video, body.viewed, body.title)


class QuizRequest(BaseModel):
    video: str = Field(pattern=VIDEO_ID)
    title: str = Field(default="", max_length=300)
    retake: bool = False  # a new set of questions, avoiding every one asked before


@app.post("/api/learn/quiz")
async def learn_quiz(body: QuizRequest, request: Request, x_hf_token: str | None = Header(default=None)) -> dict:
    writer = _free_writer(request, x_hf_token)

    async def work(task):
        task.update(progress=0.1, message="Reading the video's transcript…")
        try:
            t = await asyncio.to_thread(quiz.fetch_transcript, body.video, LEARN_DIR / "transcripts", videos._find_node())
        except quiz.QuizError as e:
            raise ValueError(str(e)) from e
        title = body.title or t.get("title") or "this video"
        avoid = PROGRESS.asked_before(body.video)
        try:
            questions = await quiz.make_questions(writer, title, t["text"], avoid, fresh=body.retake or bool(avoid),
                                                  progress=lambda share, message: task.update(progress=share, message=message))
        except quiz.QuizError as e:
            raise ValueError(str(e)) from e
        made = PROGRESS.new_quiz(body.video, title, questions, {"transcript": t["source"], "model": writer.model_used})
        return {"quiz_id": made["id"], "title": title, "transcript": t["source"], "model": writer.model_used,
                "questions": [{"q": q["q"], "options": q["options"]} for q in questions],  # answers stay here
                "attempts": PROGRESS.all().get(body.video, {}).get("attempts", [])}

    return _start_task(work, "Getting started…")


class QuizAnswers(BaseModel):
    answers: list[int | None] = Field(max_length=quiz.QUESTIONS)


@app.post("/api/learn/quiz/{quiz_id}")
def learn_quiz_submit(quiz_id: str, body: QuizAnswers) -> dict:
    try:
        return PROGRESS.grade(quiz_id, body.answers)
    except quiz.QuizError as e:
        raise HTTPException(404, str(e))


@app.get("/api/learn/files/{folder}/{name}")
def learn_download(folder: str, name: str) -> FileResponse:
    path = (LEARN_FILES / folder / name).resolve()
    if LEARN_FILES.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "File not found.")
    return FileResponse(path, filename=name)


@app.get("/api/learn/{task_id}")
def learn_status(task_id: str) -> dict:
    if task_id not in TASKS:
        raise HTTPException(404, "That request has expired. Please try again.")
    return TASKS[task_id]


# ---------- interview practice ----------

INTERVIEW_DIR = DATA_DIR / "interview"
SESSION_ID = r"^[0-9a-f]{12}$"
MAX_ANSWER_VIDEO = 200 * 1024 * 1024
MAX_FRAMES = 120


def _session_dir(session: str) -> Path:
    path = INTERVIEW_DIR / "sessions" / session
    if not re.fullmatch(SESSION_ID, session) or not path.is_dir():
        raise HTTPException(404, "That practice session is gone. Start a new one.")
    return path


@app.get("/api/interview/questions")
def interview_questions() -> dict:
    return {"questions": [{"q": q["q"], "seconds": q["seconds"]} for q in interview.QUESTIONS]}


class InterviewStart(BaseModel):
    role: str = Field(default="", max_length=120)


@app.post("/api/interview/sessions")
def interview_start(body: InterviewStart) -> dict:
    session = uuid.uuid4().hex[:12]
    path = INTERVIEW_DIR / "sessions" / session
    path.mkdir(parents=True)
    (path / "session.json").write_text(json.dumps({"id": session, "role": body.role.strip(),
                                                   "created": datetime.now().isoformat(timespec="seconds")}),
                                       encoding="utf-8")
    return {"id": session}


@app.post("/api/interview/sessions/{session}/answers/{number}")
async def interview_answer(session: str, number: int, video: UploadFile = File(...), audio: UploadFile = File(...),
                           frames: list[UploadFile] = File(default=[])) -> dict:
    """One recorded answer: the video (kept for playback), its audio as WAV, and frames taken every 2 s."""
    path = _session_dir(session)
    if not 1 <= number <= len(interview.QUESTIONS):
        raise HTTPException(404, "There's no such question.")
    if len(frames) > MAX_FRAMES:
        raise HTTPException(413, "That answer is too long. Keep answers under 4 minutes.")
    suffix = ".mp4" if "mp4" in (video.content_type or "") else ".webm"
    data = await video.read(MAX_ANSWER_VIDEO + 1)
    if len(data) > MAX_ANSWER_VIDEO:
        raise HTTPException(413, "That recording is too large. Keep answers under 4 minutes.")
    wav = await audio.read(60 * 1024 * 1024)
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise HTTPException(400, "The audio didn't come through as WAV. Please record the answer again.")
    for old in path.glob(f"q{number}.*"):  # re-recording replaces the earlier take
        old.unlink()
    frame_dir = path / f"q{number}-frames"
    shutil.rmtree(frame_dir, ignore_errors=True)
    frame_dir.mkdir()
    (path / f"q{number}{suffix}").write_bytes(data)
    (path / f"q{number}.wav").write_bytes(wav)
    for i, frame in enumerate(frames):
        jpg = await frame.read(2 * 1024 * 1024)
        if jpg[:3] == b"\xff\xd8\xff":  # JPEG only
            (frame_dir / f"{i:03d}.jpg").write_bytes(jpg)
    (path / "result.json").unlink(missing_ok=True)  # feedback no longer matches the answers
    return {"saved": number, "frames": len(list(frame_dir.glob("*.jpg")))}


@app.get("/api/interview/sessions/{session}/answers/{number}/video")
def interview_video(session: str, number: int) -> FileResponse:
    video = interview.answer_files(_session_dir(session), number - 1)["video"]
    if not video:
        raise HTTPException(404, "No recording for that question yet.")
    return FileResponse(video)


@app.get("/api/interview/latest")
def interview_latest() -> dict:
    """The most recent practice session: its answers and, once analysed, its feedback."""
    sessions = sorted((INTERVIEW_DIR / "sessions").glob("*/session.json"), key=lambda p: p.stat().st_mtime) \
        if (INTERVIEW_DIR / "sessions").exists() else []
    if not sessions:
        return {}
    path = sessions[-1].parent
    meta = json.loads(sessions[-1].read_text(encoding="utf-8"))
    answered = [i + 1 for i in range(len(interview.QUESTIONS)) if interview.answer_files(path, i)["wav"].exists()]
    result = json.loads((path / "result.json").read_text(encoding="utf-8")) if (path / "result.json").exists() else None
    return {**meta, "answered": answered, "result": result}


@app.post("/api/interview/sessions/{session}/analyse")
async def interview_analyse(session: str, request: Request, x_hf_token: str | None = Header(default=None)) -> dict:
    path = _session_dir(session)
    writer = _free_writer(request, x_hf_token)
    meta = json.loads((path / "session.json").read_text(encoding="utf-8"))

    async def work(task):
        try:
            result = await interview.analyse(path, INTERVIEW_DIR / "models", writer, meta.get("role", ""),
                                             lambda share, message: task.update(progress=share, message=message))
        except (interview.InterviewError, videos.VideoError) as e:
            raise ValueError(str(e)) from e
        (path / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    return _start_task(work, "Getting started…")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
