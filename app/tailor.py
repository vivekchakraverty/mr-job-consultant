"""Tailored resume and cover letter that never claim anything the CV doesn't say.

The model only *proposes*; this module decides what is true. Every piece of model output is
checked against the CV text before it reaches a document:

  * extracted employers, titles, dates, degrees, bullets must be findable in the CV text
  * a rewritten bullet may only rephrase its own source bullet: no new numbers, no new
    names/tools, no keywords pulled from the job ad, and most of its words must come from the source
  * skills must appear in the CV; the summary and headline face the same checks against the whole CV
  * cover-letter sentences about the candidate follow the same rules; sentences that fail are dropped

Anything that fails falls back to the CV's own wording (or is left out), and every such decision
is listed in the report shown to the user.
"""
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .llm import Writer, parse_json

# ---------- text normalisation ----------

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}
TOKEN = re.compile(r"[a-z0-9][a-z0-9+#]*(?:[./&-][a-z0-9+#]+)*")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
STOP = set("""a an and are as at be been but by for from has have he her his i if in into is it its me my of on
or our she so than that the their them they this to was we were which who will with within without you your
also about across over per via while all any each more most other some such no not only own same very can
using use used work worked working role roles team teams new""".split())


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_DASHES).translate(_QUOTES).lower()
    return " ".join(text.split())


def toks(text: str) -> set[str]:
    return set(TOKEN.findall(norm(text)))


def content(text: str) -> set[str]:
    return {t for t in toks(text) if t not in STOP and len(t) > 1}


NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())} | {"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
    "seventy": "70", "eighty": "80", "ninety": "90", "hundred": "100", "dozen": "12", "dozens": "12"}
NUMBER_WORD = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.I)


def nums(text: str) -> set[str]:
    """Every number in the text, digits or words ("five years" counts as 5)."""
    found = {n.replace(",", "") for n in NUMBER.findall(text or "")}
    return found | {NUMBER_WORDS[w.lower()] for w in NUMBER_WORD.findall(text or "")}


def proper_terms(text: str) -> set[str]:
    """Capitalised or technical-looking words (names, tools, acronyms) except at sentence starts."""
    out = set()
    for sentence in re.split(r"(?<=[.!?:;•])\s+|\n", text or ""):
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+#./&-]*", sentence)
        for i, w in enumerate(words):
            w = w.rstrip(".")
            if (i > 0 and w[:1].isupper()) or re.search(r"[0-9+#]|[a-z][A-Z]|\.[a-z]", w) or w.isupper() and len(w) > 1:
                out.add(norm(w))
    return out - {"i"}


@dataclass
class Source:
    """The CV, indexed for fact checks."""
    text: str
    norm: str = ""
    tokens: set = field(default_factory=set)
    numbers: set = field(default_factory=set)

    def __post_init__(self):
        self.norm = norm(self.text)
        self.tokens = toks(self.text)
        self.numbers = nums(self.text)

    def has(self, value: str, coverage: float = 1.0) -> bool:
        """Is this value written in the CV (verbatim, or with every word and number present)?"""
        v = norm(value)
        if not v:
            return False
        if v in self.norm:
            return True
        words = toks(value)
        if not words or not nums(value) <= self.numbers:
            return False
        return len(words & self.tokens) / len(words) >= coverage


# ---------- prompts ----------

EXTRACT_PROMPT = """You convert a CV into JSON. Copy text EXACTLY as written in the CV: same words, same numbers,
same dates. Do not summarise, translate, infer, correct or add anything. Leave a field empty ("" or [])
when the CV doesn't state it. Return JSON only, in this shape:
{"name": "", "headline": "", "location": "", "summary": "",
 "experience": [{"title": "", "company": "", "location": "", "start": "", "end": "", "bullets": [""]}],
 "education": [{"degree": "", "institution": "", "location": "", "start": "", "end": "", "details": [""]}],
 "skills": [""], "certifications": [""], "languages": [""],
 "projects": [{"name": "", "bullets": [""]}],
 "other": [{"heading": "", "items": [""]}]}"""

TAILOR_PROMPT = """You tailor a resume to a job ad WITHOUT inventing anything.

Hard rules:
- Use ONLY facts in the CANDIDATE JSON. Never add a skill, tool, employer, title, date, number, metric,
  responsibility or achievement that is not stated there.
- Never copy requirements from the job ad into the resume unless the candidate JSON states the same thing.
- You may: choose which bullets to keep, reorder them (most relevant first), and lightly rephrase a bullet
  using the job ad's wording ONLY where the bullet already says the same thing in other words.
- Every output bullet must name the id of the ONE source bullet it comes from ("from").
- The summary is 2-3 sentences built only from facts in the candidate JSON.
- List job-ad requirements the candidate does NOT show under "missing_keywords" (never in the resume).

Return JSON only:
{"headline": "", "summary": "",
 "skills": ["skills copied from the candidate's skills, most relevant first"],
 "experience": [{"id": "E1", "bullets": [{"from": "E1.B1", "text": ""}]}],
 "projects": [{"id": "P1", "bullets": [{"from": "P1.B1", "text": ""}]}],
 "matched_keywords": [""], "missing_keywords": [""]}"""

LETTER_PROMPT = """Write a short cover letter (3 paragraphs, under 250 words) for the job ad, from the candidate.
Hard rules:
- Every statement about the candidate must come from the CANDIDATE JSON. Do not invent experience,
  skills, numbers, employers, motivations tied to facts you don't have, or achievements.
- You may mention the company and role from the job ad, and say which of the candidate's real
  experience is relevant to it.
- Plain, warm, professional tone. No greeting line and no sign-off (they are added separately).
Return JSON only: {"paragraphs": ["", "", ""]}"""


# ---------- step 1: structured CV (verified) ----------

def _clean_list(values, source: Source, report: list, what: str, coverage: float = 1.0) -> list[str]:
    out = []
    for v in values or []:
        v = str(v).strip()
        if not v:
            continue
        if source.has(v, coverage):
            out.append(v)
        else:
            report.append(f"Left out {what} not found in your CV: “{v[:80]}”")
    return out


def verify_extraction(data: dict, source: Source) -> tuple[dict, list[str]]:
    report: list[str] = []
    s = source.has
    cv = {
        "name": data.get("name", "") if s(data.get("name", "")) else "",
        "headline": data.get("headline", "") if s(data.get("headline", ""), 0.9) else "",
        "location": data.get("location", "") if s(data.get("location", "")) else "",
        "summary": data.get("summary", "") if s(data.get("summary", ""), 0.9) else "",
        "experience": [], "education": [], "projects": [], "other": [],
    }
    for i, e in enumerate(data.get("experience") or [], 1):
        title, company = str(e.get("title", "")).strip(), str(e.get("company", "")).strip()
        if not (title and s(title)) and not (company and s(company)):
            report.append(f"Left out a role the CV doesn't show: “{title} at {company}”")
            continue
        cv["experience"].append({
            "id": f"E{len(cv['experience']) + 1}",
            "title": title if s(title) else "", "company": company if s(company) else "",
            "location": e.get("location", "") if s(e.get("location", "")) else "",
            "start": e.get("start", "") if s(e.get("start", "")) else "",
            "end": e.get("end", "") if s(e.get("end", "")) else "",
            "bullets": _clean_list(e.get("bullets"), source, report, "a bullet", 0.9),
        })
    for e in data.get("education") or []:
        degree, inst = str(e.get("degree", "")), str(e.get("institution", ""))
        if not (s(degree) or s(inst)):
            report.append(f"Left out education the CV doesn't show: “{degree} {inst}”")
            continue
        cv["education"].append({
            "degree": degree if s(degree) else "", "institution": inst if s(inst) else "",
            "location": e.get("location", "") if s(e.get("location", "")) else "",
            "start": e.get("start", "") if s(e.get("start", "")) else "",
            "end": e.get("end", "") if s(e.get("end", "")) else "",
            "details": _clean_list(e.get("details"), source, report, "an education detail", 0.9),
        })
    for p in data.get("projects") or []:
        name = str(p.get("name", ""))
        if not s(name):
            continue
        cv["projects"].append({"id": f"P{len(cv['projects']) + 1}", "name": name,
                               "bullets": _clean_list(p.get("bullets"), source, report, "a project bullet", 0.9)})
    cv["skills"] = list(dict.fromkeys(_clean_list(data.get("skills"), source, report, "a skill")))
    cv["certifications"] = _clean_list(data.get("certifications"), source, report, "a certification")
    cv["languages"] = _clean_list(data.get("languages"), source, report, "a language")
    for o in data.get("other") or []:
        items = _clean_list(o.get("items"), source, report, "an item", 0.9)
        if items and s(o.get("heading", ""), 0.5):
            cv["other"].append({"heading": o.get("heading", ""), "items": items})
    return cv, report


async def structured_cv(writer: Writer, cv_text: str, cache_dir: Path) -> tuple[dict, list[str]]:
    """Extract (once per CV and engine, cached) and verify the CV's facts."""
    key = hashlib.sha256((writer.engine + "\n" + cv_text).encode()).hexdigest()[:16]
    cache = cache_dir / f"cv-{key}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
    else:
        reply = await writer.chat([{"role": "system", "content": EXTRACT_PROMPT},
                                   {"role": "user", "content": f"CV:\n{cv_text[:14000]}"}], max_tokens=3000)
        data = parse_json(reply)
        if not isinstance(data, dict):
            raise ValueError("The model didn't return a readable CV structure. Please try again.")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return verify_extraction(data, Source(cv_text))


# ---------- step 2: tailoring (verified) ----------

def check_rewrite(text: str, original: str, source: Source, jd_tokens: set[str]) -> str | None:
    """None when the rewrite only rephrases `original`; otherwise the reason it was rejected."""
    new_numbers = nums(text) - nums(original)
    if new_numbers:
        return f"adds numbers not in the original ({', '.join(sorted(new_numbers))})"
    orig_tokens = toks(original)
    new_terms = {t for t in proper_terms(text) if not (toks(t) <= orig_tokens)}
    if new_terms:
        return f"adds names or tools not in the original ({', '.join(sorted(new_terms)[:4])})"
    from_ad = (content(text) - orig_tokens) & jd_tokens
    if from_ad:
        return f"brings in wording from the job ad ({', '.join(sorted(from_ad)[:4])})"
    words = content(text)
    if words and len(words & content(original)) / len(words) < 0.6:
        return "changes too much of the original"
    return None


def check_free_text(text: str, source: Source, jd_tokens: set[str], allowed_terms: set[str] = frozenset(),
                    allowed_numbers: set[str] = frozenset()) -> str | None:
    """Same idea for text built from the whole CV (summary, headline, letter sentences about the candidate).

    `allowed_terms` / `allowed_numbers` cover the company and job title, which a letter may name.
    """
    new_numbers = nums(text) - source.numbers - allowed_numbers
    if new_numbers:
        return f"numbers not in your CV ({', '.join(sorted(new_numbers))})"
    unknown = {t for t in proper_terms(text) if not (toks(t) <= source.tokens) and t not in allowed_terms}
    if unknown:
        return f"names or tools not in your CV ({', '.join(sorted(unknown)[:4])})"
    from_ad = (content(text) - source.tokens) & jd_tokens - allowed_terms
    if from_ad:
        return f"claims from the job ad your CV doesn't support ({', '.join(sorted(from_ad)[:4])})"
    return None


def _candidate_json(cv: dict) -> dict:
    """The CV as the model sees it, with ids on every bullet."""
    return {
        "headline": cv["headline"], "summary": cv["summary"], "skills": cv["skills"],
        "experience": [{"id": e["id"], "title": e["title"], "company": e["company"],
                        "dates": f"{e['start']} - {e['end']}".strip(" -"),
                        "bullets": [{"id": f"{e['id']}.B{j}", "text": b} for j, b in enumerate(e["bullets"], 1)]}
                       for e in cv["experience"]],
        "projects": [{"id": p["id"], "name": p["name"],
                      "bullets": [{"id": f"{p['id']}.B{j}", "text": b} for j, b in enumerate(p["bullets"], 1)]}
                     for p in cv["projects"]],
        "education": cv["education"], "certifications": cv["certifications"],
    }


def _apply_bullets(entries: list[dict], proposals: list[dict], source: Source, jd_tokens: set[str],
                   report: list[str]) -> list[dict]:
    by_id = {p.get("id"): p for p in proposals or [] if isinstance(p, dict)}
    out = []
    for e in entries:
        originals = {f"{e['id']}.B{j}": b for j, b in enumerate(e["bullets"], 1)}
        chosen, used = [], set()
        for b in (by_id.get(e["id"]) or {}).get("bullets") or []:
            if not isinstance(b, dict):
                continue
            src = b.get("from")
            if src not in originals or src in used:
                continue
            used.add(src)
            text = " ".join(str(b.get("text", "")).split())
            problem = check_rewrite(text, originals[src], source, jd_tokens) if text else "empty"
            if problem:
                report.append(f"Kept your original wording ({problem}): “{originals[src][:90]}”")
                text = originals[src]
            chosen.append(text)
        if not chosen:
            chosen = list(originals.values())  # the model skipped this role: keep it as written
        out.append({**e, "bullets": chosen})
    return out


async def tailor(writer: Writer, cv: dict, source: Source, job: dict, report: list[str]) -> dict:
    jd_text = job.get("description", "")[:9000]
    jd_tokens = content(jd_text) | content(job.get("title", ""))
    reply = await writer.chat([
        {"role": "system", "content": TAILOR_PROMPT},
        {"role": "user", "content": "CANDIDATE JSON:\n" + json.dumps(_candidate_json(cv), ensure_ascii=False)
                                    + f"\n\nJOB AD ({job.get('title', '')} at {job.get('company', '')}):\n{jd_text}"},
    ], max_tokens=3000)
    plan = parse_json(reply) or {}
    if not plan:
        report.append("The model's tailoring couldn't be read, so your CV content is used as written.")

    summary = " ".join(str(plan.get("summary", "")).split())
    problem = check_free_text(summary, source, jd_tokens) if summary else "empty"
    if problem:
        if summary:
            report.append(f"Used your CV's own summary instead of the suggested one ({problem}).")
        summary = cv["summary"]

    headline = " ".join(str(plan.get("headline", "")).split())
    if not headline or not source.has(headline):
        headline = cv["headline"] or (cv["experience"][0]["title"] if cv["experience"] else "")

    proposed = [str(s).strip() for s in plan.get("skills") or []]
    picked = [s for s in proposed if s and source.has(s)]
    for s in proposed:
        if s and not source.has(s):
            report.append(f"Left out a suggested skill your CV doesn't mention: “{s}”")
    by_norm = {norm(s): s for s in cv["skills"]}
    skills = list(dict.fromkeys([by_norm.get(norm(s), s) for s in picked] + cv["skills"]))

    return {
        "headline": headline, "summary": summary, "skills": skills,
        "experience": _apply_bullets(cv["experience"], plan.get("experience"), source, jd_tokens, report),
        "projects": _apply_bullets(cv["projects"], plan.get("projects"), source, jd_tokens, report),
        "education": cv["education"], "certifications": cv["certifications"],
        "languages": cv["languages"], "other": cv["other"],
        "matched_keywords": [k for k in (plan.get("matched_keywords") or []) if isinstance(k, str) and source.has(k)][:15],
        "missing_keywords": [k for k in (plan.get("missing_keywords") or []) if isinstance(k, str)][:12],
    }


# ---------- step 3: cover letter (verified sentence by sentence) ----------

FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'd|i'll|my|me|mine)\b", re.I)


def _verify_letter(paragraphs: list[str], source: Source, job: dict, jd_tokens: set[str]) -> tuple[list[str], list[str]]:
    names = f"{job.get('company', '')} {job.get('title', '')}"
    company_terms = {norm(w) for w in re.findall(r"[A-Za-z0-9][\w&.-]*", names)}
    # "7shifts" is a name, not a claim: digits inside the company/title words are allowed
    name_numbers = {n for w in re.findall(r"\w+", names) for n in nums(w)}
    job_source = Source(job.get("description", "") + " " + names)
    kept, dropped = [], []
    for para in paragraphs:
        good = []
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(str(para).split())):
            if not sentence:
                continue
            if FIRST_PERSON.search(sentence):
                problem = check_free_text(sentence, source, jd_tokens, company_terms, name_numbers)
            else:  # about the company or role: may use the job ad, but nothing from nowhere
                both = Source(source.text + "\n" + job_source.text)
                problem = check_free_text(sentence, both, set(), company_terms, name_numbers)
            (dropped.append(f"“{sentence[:90]}” ({problem})") if problem else good.append(sentence))
        if good:
            kept.append(" ".join(good))
    return kept, dropped


async def cover_letter(writer: Writer, cv: dict, source: Source, job: dict, report: list[str]) -> list[str]:
    jd_text = job.get("description", "")[:7000]
    jd_tokens = content(jd_text)
    messages = [
        {"role": "system", "content": LETTER_PROMPT},
        {"role": "user", "content": "CANDIDATE JSON:\n" + json.dumps(_candidate_json(cv), ensure_ascii=False)
                                    + f"\n\nJOB AD ({job.get('title', '')} at {job.get('company', '')}):\n{jd_text}"},
    ]
    paragraphs, dropped = [], []
    for attempt in range(2):
        plan = parse_json(await writer.chat(messages, max_tokens=900)) or {}
        raw = [p for p in plan.get("paragraphs") or [] if isinstance(p, str) and p.strip()]
        paragraphs, dropped = _verify_letter(raw, source, job, jd_tokens)
        total = sum(len(re.split(r"(?<=[.!?])\s+", p)) for p in raw) or 1
        if raw and len(dropped) / total <= 0.3:
            break
        if attempt == 0 and dropped:  # one retry, telling the model exactly what wasn't allowed
            messages.append({"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)})
            messages.append({"role": "user", "content": "These sentences claim things the CV doesn't support. "
                             "Rewrite the letter without them or anything like them:\n" + "\n".join(dropped)})
    for d in dropped:
        report.append(f"Removed a cover-letter sentence: {d}")
    return paragraphs
