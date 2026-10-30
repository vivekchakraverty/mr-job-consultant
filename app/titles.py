"""Related job titles for a typed title: free, instant, no AI.

Three sources, best first:
  1. seniority variants of the title itself ("Senior Content Writer", "Lead Content Writer")
  2. curated families of related roles (title_families.json): "Content Writer" → Copywriter, Content Marketer…
  3. real titles in the current job listings that share the title's key words, with how many are open
"""
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from .jobs import WORD, Job

FAMILIES_PATH = Path(__file__).resolve().parent / "title_families.json"
SENIORITY = {"junior", "jr", "senior", "sr", "lead", "principal", "staff", "head", "chief", "intern", "trainee",
             "graduate", "i", "ii", "iii", "iv"}
NOISE = {"remote", "hybrid", "onsite", "contract", "freelance", "part", "time", "full", "temporary", "temp",
         "urgent", "hiring", "new", "the", "and", "of", "for", "in", "with", "to", "a", "an", "m", "f", "d", "w", "x"}
# Role nouns too general to make two titles related on their own ("iOS Developer" ≠ "React Developer").
GENERIC = {"developer", "engineer", "analyst", "manager", "specialist", "executive", "officer", "coordinator",
           "consultant", "assistant", "associate", "representative", "designer", "administrator", "agent",
           "technician", "operator", "worker", "director", "partner", "advisor", "expert", "professional"}
# Text after these usually names the employer or place, not the role.
TITLE_CUT = re.compile(r"\s+(?:at|@|for|with|in)\s+[A-Z].*$|\s+[–—|•]\s*.*$|\s+-\s+.*$|\s*\(.*?\)|\s*\[.*?\]|,.*$")
LEADING_NOISE = re.compile(r"^(?:(?:remote|hybrid|urgent|hiring|now hiring|new|freelance|contract)\b[\s:!-]*)+", re.I)


def _words(text: str) -> list[str]:
    return [w.strip(".") for w in WORD.findall(text.lower())]


def core_words(title: str) -> set[str]:
    return {w for w in _words(title) if w not in SENIORITY and w not in NOISE}


def clean_title(raw: str) -> str:
    """'Senior Data Analyst at Electronic Arts (Remote)' → 'Senior Data Analyst'."""
    t = TITLE_CUT.sub("", " ".join(raw.split())).strip(" -–—|:,")
    t = LEADING_NOISE.sub("", t).strip()
    return t if 2 <= len(t) <= 70 else ""


def _display(title: str) -> str:
    keep_upper = {"seo", "ppc", "sem", "ui", "ux", "qa", "it", "hr", "ai", "ml", "bi", "crm", "api", "sql", "php",
                  "aws", "fp&a", "pr", "dba", "sdet", "noc", "soc", "kyc", "aml", "pmo", "hgv", "iot", "nlp", "llm", "rlhf"}
    def word(w: str) -> str:
        if "/" in w:  # "ui/ux" → "UI/UX"
            return "/".join(word(p) for p in w.split("/"))
        if w.lower() in keep_upper:
            return w.upper()
        if w.lower() in ("sr", "sr."):
            return "Senior"
        if w.lower() in ("jr", "jr."):
            return "Junior"
        if w.isupper() and len(w) > 3:  # "REACT NATIVE" shouting in a listing
            return w.capitalize()
        return w if any(c.isupper() for c in w[1:]) else w.capitalize()  # keep "iOS", "JavaScript"

    return " ".join(word(w) for w in title.split())


@lru_cache(maxsize=1)
def _families() -> list[list[str]]:
    return json.loads(FAMILIES_PATH.read_text(encoding="utf-8"))


def seniority_variants(title: str) -> list[str]:
    base_words = _words(title)
    core = " ".join(w for w in title.split() if w.lower().strip(".") not in SENIORITY)
    if not core:
        return []
    if base_words and base_words[0] in SENIORITY:
        return [core, f"Senior {core}", f"Lead {core}"]
    return [f"Senior {core}", f"Lead {core}", f"Junior {core}"]


def _similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def family_matches(title: str) -> list[tuple[str, bool]]:
    """(related title, is_close) from every family that contains this title or a close version of it.

    A family *headed* by the title contributes all its members. A family that merely includes it
    (e.g. "Data Analyst" inside the Data Scientist family) only contributes members sharing a
    specific word with it, so "Data Scientist" comes along but "Machine Learning Engineer" doesn't.
    """
    want = core_words(title)
    specific = want - GENERIC
    if not want:
        return []
    out: list[tuple[str, bool]] = []
    for family in _families():
        head_sim = _similarity(want, core_words(family[0]))
        best = max(_similarity(want, core_words(m)) for m in family)
        if head_sim >= 0.99:
            out += [(m, True) for m in family]
        elif best >= 0.66:
            if best >= 0.99:  # the title itself is in this family: its anchor role is related too
                out.append((family[0], False))
            out += [(m, False) for m in family if core_words(m) & specific]
    return out


_INDEX: dict = {}


def _index(jobs: list[Job]) -> list[tuple[str, str, set[str]]]:
    """(phrase-searchable title, cleaned title, core words) per job, built once per job list."""
    key = (id(jobs), len(jobs))
    if _INDEX.get("key") != key:
        rows = []
        for j in jobs:
            cleaned = clean_title(j.title)
            rows.append((" " + " ".join(WORD.findall(j.title.lower())) + " ", cleaned, core_words(cleaned)))
        _INDEX.update(key=key, rows=rows)
    return _INDEX["rows"]


def open_jobs(title: str, jobs: list[Job]) -> int:
    phrase = " " + " ".join(WORD.findall(title.lower())) + " "
    return sum(phrase in padded for padded, _, _ in _index(jobs))


def live_titles(title: str, jobs: list[Job], related: list[str]) -> Counter:
    """Real titles in the listings that contain the typed title's specific words or a related role."""
    want = core_words(title)
    specific = want - GENERIC or want
    related_sets = [core_words(r) for r in related if core_words(r) - GENERIC]
    counts: Counter = Counter()
    for _, cleaned, words in _index(jobs):
        if not cleaned or not words or len(words) > 5:
            continue
        has_specific = specific <= words and len(words - want) <= 2  # "Travel Registered Nurse"
        close_to_family = any(r <= words for r in related_sets) and len(words) <= 4
        if has_specific or close_to_family:
            counts[_display(cleaned)] += 1
    return counts


def suggest(title: str, jobs: list[Job], limit: int = 14) -> list[dict]:
    title = " ".join(title.split())
    if len(core_words(title)) == 0:
        return []
    typed = core_words(title)
    seen = {" ".join(sorted(_words(title)))}
    ranked: list[tuple[float, str, str]] = []

    def add(candidate: str, kind: str, base: float) -> None:
        key = " ".join(sorted(_words(candidate)))
        if not candidate or key in seen:
            return
        seen.add(key)
        ranked.append((base, _display(candidate), kind))

    for i, v in enumerate(seniority_variants(title)):
        add(v, "seniority", 3.0 - i * 0.1)
    family = family_matches(title)
    for i, (m, close) in enumerate(family):
        # the title's own family first; within it, members sharing a specific word first
        add(m, "related", 2.0 + 0.3 * close + 0.2 * bool(core_words(m) & (typed - GENERIC)) - i * 0.005)
    for t, n in live_titles(title, jobs, [m for m, _ in family]).most_common(20):
        add(t, "live", 1.0 + min(n, 20) / 40)

    out = []
    for score, t, kind in sorted(ranked, key=lambda r: -r[0])[: limit * 2]:
        n = open_jobs(t, jobs)
        out.append({"title": t, "kind": kind, "jobs": n, "score": score + min(n, 30) / 60})
    # Titles with open jobs right now rank a little higher within their group.
    out.sort(key=lambda s: -s["score"])
    return out[:limit]
