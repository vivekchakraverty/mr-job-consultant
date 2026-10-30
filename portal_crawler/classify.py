"""Decide whether a homepage belongs to a job portal (many employers) rather than any other site."""
import re
from collections import defaultdict
from urllib.parse import urlsplit

from .doc import Doc
from .urls import JOB_WORDS, LISTING_SEGMENT, NOT_DETAIL, is_non_portal, job_detail_score, registered_domain

SEARCH_PHRASES = re.compile(
    r"\b(search|find|browse|explore|view all|see all|latest|discover)\s+(\d[\d,.]*\+?\s+)?"
    r"(remote\s+)?(jobs|vacancies|openings|positions|roles|opportunities|internships)\b",
    re.I,
)
EMPLOYER_PHRASES = re.compile(
    r"\b(post\s+(a\s+)?jobs?|for\s+employers|employers?\s+(sign|log|zone|area|centre|center)|"
    r"hire\s+(top\s+)?talent|for\s+recruiters|advertise\s+(a\s+|your\s+)?(job|vacanc)|"
    r"list\s+your\s+job|post\s+a\s+vacancy|find\s+candidates)\b",
    re.I,
)
SINGLE_EMPLOYER = re.compile(r"\b(join\s+our\s+team|work\s+(with|at)\s+us|life\s+at\s+\w+)\b", re.I)
SEARCH_INPUT = re.compile(r"(keyword|q|what|search|title|skill|query|job|term|designation)", re.I)


def has_job_search_form(doc: Doc) -> bool:
    for form in doc.soup.find_all("form"):
        blob = " ".join([form.get("action") or "", form.get("id") or "", form.get_text(" ", strip=True)])
        for inp in form.find_all("input"):
            if (inp.get("type") or "text").lower() not in ("text", "search"):
                continue
            attrs = " ".join(inp.get(a) or "" for a in ("name", "id", "placeholder", "aria-label"))
            if SEARCH_INPUT.search(attrs) and (JOB_WORDS.search(blob + " " + attrs) or "location" in attrs.lower()
                                               or "where" in attrs.lower()):
                return True
    return False


def job_links(doc: Doc, min_score: int = 2, structural: bool = False) -> list[str]:
    """Links on the same site that look like individual job posts, best first.

    With ``structural``, a page whose job URLs carry no job words (``/acme-senior-dev-4821``)
    falls back to the largest group of same-shaped, id-bearing links on the page.
    """
    domain = registered_domain(doc.url)
    scored = [(job_detail_score(u), u) for u, _ in doc.links if registered_domain(u) == domain]
    found = [u for s, u in sorted(scored, key=lambda x: -x[0]) if s >= min_score]
    if structural and len(found) < 3:
        found += [u for u in _repeated_item_links(doc, domain) if u not in found]
    return found


def _repeated_item_links(doc: Doc, domain: str, min_group: int = 6) -> list[str]:
    groups: dict[tuple, list[str]] = defaultdict(list)
    for url, _ in doc.links:
        if registered_domain(url) != domain:
            continue
        path = urlsplit(url).path
        segs = [s for s in path.split("/") if s]
        if not segs or NOT_DETAIL.search(path):
            continue
        last = segs[-1]
        if LISTING_SEGMENT.match(last) or urlsplit(url).query.startswith("page="):
            continue
        if re.search(r"\d{3,}", last) or last.count("-") >= 3:
            groups[(len(segs), segs[0] if len(segs) > 1 else "")].append(url)
    best = max(groups.values(), key=len, default=[])
    return best if len(best) >= min_group else []


def classify(doc: Doc, threshold: int) -> tuple[bool, int, list[str]]:
    if is_non_portal(doc.url):
        return False, 0, ["excluded domain"]
    score, reasons = 0, []

    def add(points: int, why: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{points:+d} {why}")

    if JOB_WORDS.search(doc.head_text):
        add(2, "job words in title/description")
    if SEARCH_PHRASES.search(doc.text):
        add(2, "'search/browse jobs' wording")
    employer = bool(EMPLOYER_PHRASES.search(doc.text))
    if employer:
        add(2, "employer / post-a-job wording")
    if has_job_search_form(doc):
        add(2, "job search form")
    if doc.has_job_posting:
        add(3, "JobPosting structured data")
    n = len(job_links(doc))
    if n >= 5:
        add(3, f"{n} job-post links")
    elif n >= 2:
        add(1, f"{n} job-post links")
    if SINGLE_EMPLOYER.search(doc.head_text) and not employer:
        add(-2, "looks like a single company's careers page")
    return score >= threshold, score, reasons
