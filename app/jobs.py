"""Collect current job posts from the portals in the crawler's directory.

Two kinds of source, both public and login-free:
  * RSS/Atom feeds the crawler found on portals (``rss_feed`` in public_job_portals.json)
  * the public JSON APIs of a few directory portals (Remotive, RemoteOK, Himalayas, Jobicy)

Results are cached in data/jobs_cache.json for a few hours so repeated searches are instant
and the portals are not hit again and again.
"""
import asyncio
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from portal_crawler.config import Settings
from portal_crawler.fetcher import Fetcher

log = logging.getLogger(__name__)

CACHE_TTL = 6 * 3600
JOB_WORDS = re.compile(r"\b(job|jobs|hiring|engineer|developer|manager|designer|analyst|remote|position|role|"
                       r"intern|specialist|lead|director|consultant|assistant|officer|nurse|teacher)\b", re.I)


@dataclass
class Job:
    title: str
    url: str
    source: str                  # directory key of the portal it came from
    company: str = ""
    location: str = ""
    description: str = ""
    posted: str = ""             # ISO date when known
    job_type: str = ""           # full_time / part_time / contract / ... when the source says
    remote: bool = False
    tags: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.url


def _text(html: str, limit: int = 4000) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True) if "<" in html else html
    return " ".join(text.split())[:limit]


def _iso(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return value[:10] if re.match(r"\d{4}-\d{2}-\d{2}", value) else ""


def _job_type(*values) -> str:
    blob = " ".join(str(v) for v in values if v).lower().replace("-", "_").replace(" ", "_")
    for t in ("full_time", "part_time", "contract", "freelance", "internship", "temporary"):
        if t in blob:
            return "contract" if t == "freelance" else t
    return ""


def _is_remote(*values) -> bool:
    return bool(re.search(r"remote|anywhere|worldwide|work from home|wfh", " ".join(map(str, values)), re.I))


# ---------- RSS / Atom ----------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_text: str, source: str, base_url: str) -> list[Job]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8", errors="replace"))
    except ET.ParseError:
        return []
    items = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    jobs = []
    for item in items[:200]:
        fields: dict[str, str] = {}
        cats = []
        link = ""
        for child in item:
            name = _local(child.tag)
            if name == "link":
                link = link or (child.get("href") or (child.text or "")).strip()
            elif name == "category":
                cats.append((child.get("term") or child.text or "").strip())
            elif child.text and name not in fields:
                fields[name] = child.text.strip()
        title = fields.get("title", "")
        if not title or not link:
            continue
        description = _text(fields.get("description") or fields.get("encoded") or fields.get("content")
                             or fields.get("summary") or "")
        location = fields.get("location") or fields.get("region") or fields.get("country") or ""
        jobs.append(Job(
            title=_text(title, 200),
            url=urljoin(base_url, link),
            source=source,
            company=fields.get("company") or fields.get("company_name") or fields.get("creator")
            or fields.get("author") or "",
            location=location,
            description=description,
            posted=_iso(fields.get("pubdate") or fields.get("published") or fields.get("updated") or ""),
            job_type=_job_type(fields.get("job_type"), fields.get("type"), *cats),
            remote=_is_remote(title, location, *cats),
            tags=[c for c in cats if c][:8],
        ))
    # Feeds that are really blogs (no job-like titles at all) are dropped.
    if jobs and not any(JOB_WORDS.search(j.title) for j in jobs):
        return []
    return jobs


# ---------- public JSON APIs of directory portals ----------

def _api_remotive(data: dict) -> list[Job]:
    return [Job(title=j.get("title", ""), url=j.get("url", ""), source="remotive.com",
                company=j.get("company_name", ""), location=j.get("candidate_required_location", ""),
                description=_text(j.get("description", "")), posted=(j.get("publication_date") or "")[:10],
                job_type=_job_type(j.get("job_type")), remote=True, tags=(j.get("tags") or [])[:8])
            for j in data.get("jobs", [])]


def _api_remoteok(data: list) -> list[Job]:
    return [Job(title=j.get("position", ""), url=j.get("url", ""), source="remoteok.com",
                company=j.get("company", ""), location=j.get("location", "") or "Remote",
                description=_text(j.get("description", "")), posted=(j.get("date") or "")[:10],
                remote=True, tags=(j.get("tags") or [])[:8])
            for j in data if isinstance(j, dict) and j.get("position")]


def _api_himalayas(data: dict) -> list[Job]:
    out = []
    for j in data.get("jobs", []):
        restrictions = j.get("locationRestrictions") or []
        out.append(Job(title=j.get("title", ""), url=j.get("applicationLink") or j.get("guid") or "",
                       source="himalayas.app", company=j.get("companyName", ""),
                       location=", ".join(map(str, restrictions)) or "Worldwide",
                       description=_text(j.get("description") or j.get("excerpt") or ""),
                       posted=_iso(str(j.get("pubDate") or "")) if not str(j.get("pubDate", "")).isdigit()
                       else time.strftime("%Y-%m-%d", time.gmtime(int(j["pubDate"]))),
                       job_type=_job_type(j.get("employmentType")), remote=True,
                       tags=(j.get("categories") or [])[:8]))
    return out


def _api_jobicy(data: dict) -> list[Job]:
    return [Job(title=j.get("jobTitle", ""), url=j.get("url", ""), source="jobicy.com",
                company=j.get("companyName", ""), location=j.get("jobGeo", ""),
                description=_text(j.get("jobDescription") or j.get("jobExcerpt") or ""),
                posted=(j.get("pubDate") or "")[:10], job_type=_job_type(*(j.get("jobType") or [])),
                remote=True, tags=(j.get("jobIndustry") or [])[:8])
            for j in data.get("jobs", [])]


APIS = {
    "remotive.com": ("https://remotive.com/api/remote-jobs?limit=300", _api_remotive),
    "remoteok.com": ("https://remoteok.com/api", _api_remoteok),
    "himalayas.app": ("https://himalayas.app/jobs/api?limit=100", _api_himalayas),
    "jobicy.com": ("https://jobicy.com/api/v2/remote-jobs?count=100", _api_jobicy),
}


# ---------- collection ----------

async def _collect(portals: list[dict], progress) -> list[Job]:
    keys = {p["key"] for p in portals}
    feeds = [(p["key"], urljoin(p.get("url") or f"https://{p['key']}/", p["rss_feed"]), p.get("url") or "")
             for p in portals if p.get("rss_feed") and p["key"] not in APIS]
    tasks_total = len(feeds) + sum(k in keys for k in APIS)
    done = 0
    jobs: list[Job] = []

    settings = Settings(per_host_delay=1.0, timeout=15.0, max_bytes=5_000_000)
    async with Fetcher(settings) as fetcher:
        async def one_api(key: str, url: str, parse) -> None:
            nonlocal done
            page = await fetcher.get(url)
            try:
                if page.ok:
                    jobs.extend(j for j in parse(json.loads(page.text)) if j.title and j.url)
            except (ValueError, AttributeError, TypeError) as e:
                log.warning("api %s: %s", key, e)
            done += 1
            progress(done, tasks_total)

        async def one_feed(key: str, url: str, base: str) -> None:
            nonlocal done
            page = await fetcher.get(url)
            if page.ok:
                jobs.extend(parse_feed(page.text, key, base or url))
            done += 1
            progress(done, tasks_total)

        await asyncio.gather(
            *(one_api(k, url, parse) for k, (url, parse) in APIS.items() if k in keys),
            *(one_feed(k, url, base) for k, url, base in feeds),
        )

    unique = {}
    for j in jobs:
        unique.setdefault(j.url, j)
    return list(unique.values())


async def load_jobs(portals_path: Path, cache_path: Path, progress=lambda done, total: None,
                    refresh: bool = False) -> tuple[list[Job], dict]:
    """All current jobs from the directory, from cache when fresh."""
    if not refresh and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if time.time() - cached.get("fetched_at", 0) < CACHE_TTL:
            return [Job(**j) for j in cached["jobs"]], {"cached": True, "fetched_at": cached["fetched_at"],
                                                        "sources": cached.get("sources", 0)}
    portals = json.loads(portals_path.read_text(encoding="utf-8")) if portals_path.exists() else []
    jobs = await _collect(portals, progress)
    meta = {"cached": False, "fetched_at": time.time(), "sources": len({j.source for j in jobs})}
    cache_path.write_text(json.dumps({**meta, "jobs": [asdict(j) for j in jobs]}, ensure_ascii=False),
                          encoding="utf-8")
    return jobs, meta


# ---------- filtering against the search profile ----------

WORD = re.compile(r"[a-z0-9+#.]{2,}")
STOP = {"and", "the", "for", "with", "of", "in", "to", "a", "an", "at", "on", "senior", "junior", "sr", "jr", "lead"}


def words(text: str) -> set[str]:
    return {w.strip(".") for w in WORD.findall(text.lower())} - STOP


def title_score(job: Job, wanted: str) -> float:
    """How closely the job title matches the wanted title (0-1).

    The exact phrase ("data analyst") scores 1; scattered words score less, and less again
    in long titles where they are more likely to be incidental.
    """
    want = words(wanted)
    if not want:
        return 0.0
    phrase = " ".join(WORD.findall(wanted.lower()))
    if phrase and re.search(rf"\b{re.escape(phrase)}\b", " ".join(WORD.findall(job.title.lower()))):
        return 1.0
    have = words(job.title)
    share = len(want & have) / len(want)
    return 0.8 * share * min(1.0, (len(want) + 2) / max(len(have), 1)) ** 0.5


def location_ok(job: Job, profile: dict) -> bool:
    if profile.get("worldwide"):
        return True
    places = profile.get("locations") or []
    loc = f"{job.location} {job.title}".lower()
    names = {p["name"].lower() for p in places} | {(p.get("detail") or "").lower() for p in places
                                                   if p.get("type") == "city"}
    codes = {(p.get("code") or "").lower() for p in places if p.get("code")}
    if any(n and n in loc for n in names) or any(re.search(rf"\b{c}\b", loc) for c in codes):
        return True
    if job.remote and "remote" in (profile.get("job_types") or []):
        # A remote job is fine unless it is restricted to somewhere else ("US only", "Canada").
        restricted = re.search(r"\bonly\b|^(?!.*(anywhere|worldwide|global)).*\b(usa?|united states|canada|"
                               r"uk|europe|emea|americas|apac|latam|india|australia)\b", loc)
        if not restricted:
            return True
        return any(n and n in loc for n in names | {"europe"} if "europe" in names)
    return False


def type_ok(job: Job, profile: dict) -> bool:
    wanted = set(profile.get("job_types") or [])
    if not wanted:
        return True
    if wanted == {"remote"}:
        return job.remote
    employment = wanted - {"remote"}
    if "remote" in wanted and not job.remote and not employment:
        return False
    return not job.job_type or job.job_type in employment or (job.remote and "remote" in wanted and not employment)


def best_title(job: Job, titles: list[str]) -> tuple[float, str]:
    """Best match among the wanted titles: the first (typed) title counts in full, related ones at 90%."""
    best, matched = 0.0, ""
    for i, t in enumerate(titles):
        s = title_score(job, t) * (1.0 if i == 0 else 0.9)
        if s > best:
            best, matched = s, t
    return best, matched


# Feed items that are listing/category pages ("Remote Data Jobs in Europe", "Post a job"), not posts.
NOT_A_POST = re.compile(r"\bjobs\b|^post\b|\bjob board\b", re.I)


def filter_jobs(jobs: list[Job], profile: dict) -> list[Job]:
    return [j for j in jobs if not NOT_A_POST.search(j.title) and location_ok(j, profile) and type_ok(j, profile)]
