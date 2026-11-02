"""Upskilling and reskilling: skill ideas read from the CV (upskilling), and a curriculum for any
skill, whether picked from those ideas or typed in (reskilling).

Everything runs on the free writer Space, on the caller's own daily ZeroGPU quota, so nothing is
billed to anyone. The CV the job search already processed is reused (its verified facts, when the
tailoring step has extracted them; otherwise its text), and every answer is saved per CV, so
opening the same skill again costs no GPU time.
"""
import asyncio
import hashlib
import json
import re
from pathlib import Path

from . import tailor
from .llm import Writer, parse_json

ENGINES = ("zerogpu", "inference")  # a CV extracted by either engine counts as processed

SUGGEST_UPSKILL = """You are a career coach who follows what employers hire for today. Read the CV and
suggest skills that would make this person noticeably more valuable in their CURRENT line of work.
Rules:
- 8 skills the CV does NOT already show. Skip anything it lists or clearly demonstrates.
- Each one builds on what the CV shows and is asked for in current job ads for this kind of role.
- Mostly hard skills, methods, tools or platforms; at most two soft or business skills.
- Be specific: "SQL window functions" or "Figma auto-layout", never "technology" or "communication".
- "builds_on": the experience or skill from the CV it extends, in a few words.
Return JSON only:
{"current_role": "", "skills": [{"name": "", "category": "technical|tool|domain|soft",
  "why": "one sentence on why it pays off for this person", "builds_on": "",
  "effort": "weeks|months", "demand": "high|growing|steady"}]}"""

OUTLINE_PROMPT = """You are an expert practitioner and instructional designer. Outline a self-study
curriculum that takes this learner to MODERATE PROFICIENCY in the SKILL: able to handle typical
real-world tasks with it independently and know when to look things up; not expert level.
Rules:
- Start from what the LEARNER already knows: skip what their background covers.
- 4 to 6 units in a logical order, each with 2 to 4 modules (titles only here).
- Each unit ends with a checkpoint that proves its goal; the course ends with a capstone project.
- Realistic hours for a working adult. Keep every string short. No URLs, no course or book titles.
Return JSON only:
{"proficiency_goal": "one sentence: what they can do at the end", "prerequisites": [""],
 "hours_total": 0, "weeks": 0,
 "units": [{"title": "", "goal": "", "hours": 0, "modules": [""], "checkpoint": ""}],
 "capstone": ""}"""

TOPICS_PROMPT = """You are an expert practitioner and instructional designer, filling in a self-study
curriculum that takes this learner to MODERATE PROFICIENCY in the SKILL. For every module listed,
give 3 to 6 concrete topics in learning order (short phrases, specific enough to search for) and one
short hands-on practice task that uses examples from the learner's kind of work.
No URLs, no course or book titles. Return JSON only:
{"modules": [{"id": "", "topics": [""], "practice": ""}]}"""

# The writer Space reserves GPU time in proportion to the token cap, and the model stops by itself
# once its JSON is complete, so every call asks for the Space's maximum (about 120 s of GPU time).
# At roughly 10 tokens a second that fits about 1,200 tokens, so each call is kept smaller than that.
SPACE_BUDGET = 3000
MODULES_PER_CALL = 8


# ---------- helpers ----------

def _s(value, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _strings(values, limit: int, length: int = 160) -> list[str]:
    out = [_s(v, length) for v in values or [] if isinstance(v, (str, int, float))]
    return [v for v in out if v][:limit]


def _int(value, low: int = 1, high: int = 2000) -> int | None:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def _key(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:20]


def _load(cache_dir: Path, key: str) -> dict | None:
    path = cache_dir / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _save(cache_dir: Path, key: str, data: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def salvage_json(text: str):
    """A reply cut off mid-JSON (the model hit its length limit): keep every part that is complete."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    start = text.find("{")
    if start < 0:
        return None
    stack, in_str, esc, cuts = [], False, False, []
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
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack:
                break
            stack.pop()
            if not stack:
                break
            cuts.append((i, "".join(reversed(stack))))
    for i, closers in reversed(cuts):
        try:
            return json.loads(text[start:i + 1] + closers)
        except json.JSONDecodeError:
            continue
    return None


def _read_reply(reply: str) -> tuple[dict | None, bool]:
    """(data, was_cut_off)."""
    data = parse_json(reply)
    if isinstance(data, dict):
        return data, False
    data = salvage_json(reply)
    return (data, True) if isinstance(data, dict) else (None, False)


# ---------- the CV, as already processed ----------

def processed_cv(cv_text: str, cv_cache: Path) -> dict | None:
    """The CV's verified facts, if the tailoring step has already extracted them. No AI call."""
    for engine in ENGINES:
        path = tailor.cache_path(engine, cv_text, cv_cache)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cv, _ = tailor.verify_extraction(data, tailor.Source(cv_text))
        if cv["experience"] or cv["skills"]:
            return cv
    return None


def _cv_for_prompt(cv_text: str, cv: dict | None) -> str:
    if not cv:
        return "CV TEXT:\n" + cv_text[:12000]
    facts = {k: cv[k] for k in ("headline", "summary", "skills", "certifications", "languages")}
    facts["experience"] = [{k: e[k] for k in ("title", "company", "start", "end", "bullets")} for e in cv["experience"]]
    facts["education"] = [{k: e[k] for k in ("degree", "institution", "end")} for e in cv["education"]]
    facts["projects"] = [{"name": p["name"], "bullets": p["bullets"]} for p in cv["projects"]]
    return "CV FACTS:\n" + json.dumps(facts, ensure_ascii=False)[:12000]


def _learner_brief(cv_text: str, cv: dict | None) -> str:
    """A short picture of the learner, so the curriculum starts where they are."""
    if not cv:
        return cv_text[:5000]
    lines = [cv["headline"]] if cv["headline"] else []
    lines += [f"{e['title']}, {e['company']} ({e['start']} - {e['end']})".replace(" ( - )", "")
              for e in cv["experience"][:6]]
    if cv["skills"]:
        lines.append("Skills: " + ", ".join(cv["skills"][:40]))
    lines += [f"Education: {e['degree']}, {e['institution']}".strip(", ") for e in cv["education"][:3]]
    if cv["certifications"]:
        lines.append("Certifications: " + ", ".join(cv["certifications"][:10]))
    return "\n".join(lines)


# ---------- skill suggestions ----------

def _skills(values, source: tailor.Source, limit: int, skipped: list[str]) -> list[dict]:
    out, seen = [], set()
    for v in values or []:
        if not isinstance(v, dict):
            continue
        name = _s(v.get("name"), 80)
        if len(name) < 2 or tailor.norm(name) in seen:
            continue
        seen.add(tailor.norm(name))
        if tailor.norm(name) in source.norm:  # the CV already names it: nothing to learn
            skipped.append(name)
            continue
        out.append({"name": name, "category": _s(v.get("category"), 20).lower(), "why": _s(v.get("why")),
                    "builds_on": _s(v.get("builds_on"), 120), "effort": _s(v.get("effort"), 20).lower(),
                    "demand": _s(v.get("demand"), 20).lower()})
    return out[:limit]


async def suggest(writer: Writer, cv_text: str, target: str, cv_cache: Path, cache_dir: Path) -> dict:
    """Upskilling ideas. (Reskilling has no suggestions: the person names the skill they want.)"""
    key = _key("suggest", "upskill", target.lower(), cv_text)
    if saved := _load(cache_dir, key):
        return saved | {"cached": True}
    cv = processed_cv(cv_text, cv_cache)
    ask = f"ROLE THEY ARE GROWING TOWARDS: {target}" if target else "Use the role their CV shows."
    reply = await writer.chat([
        {"role": "system", "content": SUGGEST_UPSKILL},
        {"role": "user", "content": f"{_cv_for_prompt(cv_text, cv)}\n\n{ask}"},
    ], max_tokens=SPACE_BUDGET)
    data, _ = _read_reply(reply)
    if not data:
        raise ValueError("The model's suggestions couldn't be read. Please try again.")

    source, skipped = tailor.Source(cv_text), []
    result = {"mode": "upskill", "target": target, "current_role": _s(data.get("current_role"), 120),
              "used_processed_cv": bool(cv), "model": writer.model_used,
              "skills": _skills(data.get("skills"), source, 10, skipped)}
    if not result["skills"]:
        raise ValueError("The model didn't suggest anything usable. Please try again.")
    result["skipped"] = list(dict.fromkeys(skipped))
    _save(cache_dir, key, result)
    return result


# ---------- curriculum ----------

def _clean_outline(data: dict) -> dict:
    units = []
    for u in data.get("units") or []:
        if not isinstance(u, dict) or not _s(u.get("title")):
            continue
        titles = [_s(m.get("title") if isinstance(m, dict) else m, 120) for m in u.get("modules") or []]
        modules = [{"title": t, "topics": [], "practice": ""} for t in titles if t][:5]
        if modules:
            units.append({"title": _s(u.get("title"), 120), "goal": _s(u.get("goal")), "hours": _int(u.get("hours"), 1, 300),
                          "modules": modules, "checkpoint": _s(u.get("checkpoint"))})
    return {"proficiency_goal": _s(data.get("proficiency_goal")),
            "prerequisites": _strings(data.get("prerequisites"), 6, 120),
            "hours_total": _int(data.get("hours_total")) or sum(u["hours"] or 0 for u in units) or None,
            "weeks": _int(data.get("weeks"), 1, 104), "units": units[:6], "capstone": _s(data.get("capstone"), 500)}


def _chunks(units: list[dict]) -> list[list[tuple[int, dict]]]:
    """Units grouped so each topics call covers at most MODULES_PER_CALL modules."""
    chunks, size = [[]], 0
    for i, u in enumerate(units):
        if chunks[-1] and size + len(u["modules"]) > MODULES_PER_CALL:
            chunks.append([])
            size = 0
        chunks[-1].append((i, u))
        size += len(u["modules"])
    return chunks


async def _fill_topics(writer: Writer, ask: str, chunk: list[tuple[int, dict]]) -> int:
    """Topics and practice for the modules of these units, written into them in place. Returns how many were filled."""
    listing = [{"unit": u["title"], "goal": u["goal"],
                "modules": [{"id": f"U{i + 1}.M{j + 1}", "title": m["title"]} for j, m in enumerate(u["modules"])]}
               for i, u in chunk]
    reply = await writer.chat([{"role": "system", "content": TOPICS_PROMPT},
                               {"role": "user", "content": f"{ask}\n\nMODULES:\n{json.dumps(listing, ensure_ascii=False)}"}],
                              max_tokens=SPACE_BUDGET)
    data, _ = _read_reply(reply)
    by_id = {str(m.get("id")): m for m in (data or {}).get("modules") or [] if isinstance(m, dict)}
    filled = 0
    for i, u in chunk:
        for j, m in enumerate(u["modules"]):
            got = by_id.get(f"U{i + 1}.M{j + 1}") or {}
            m["topics"], m["practice"] = _strings(got.get("topics"), 8), _s(got.get("practice"))
            filled += bool(m["topics"])
    return filled


async def curriculum(writer: Writer, mode: str, cv_text: str, skill: dict, target: str,
                     cv_cache: Path, cache_dir: Path, fresh: bool = False, progress=None) -> dict:
    """Outline first (units, modules, checkpoints), then topics for groups of units side by side."""
    progress = progress or (lambda share, message: None)
    goal_role = skill.get("role") or target
    key = _key("curriculum", mode, skill["name"].lower(), goal_role.lower(), cv_text)
    if not fresh and (saved := _load(cache_dir, key)):
        return saved | {"cached": True}
    cv = processed_cv(cv_text, cv_cache)
    ask = [f"SKILL: {skill['name']}"]
    if skill.get("why"):
        ask.append(f"WHY IT MATTERS FOR THEM: {skill['why']}")
    if goal_role:
        ask.append(f"{'ROLE THEY ARE MOVING INTO' if mode == 'reskill' else 'ROLE THEY ARE GROWING TOWARDS'}: {goal_role}")
    ask.append(f"LEARNER:\n{_learner_brief(cv_text, cv)[:3000]}")
    ask = "\n".join(ask)

    progress(0.2, f"Planning the units and modules for {skill['name']}… (about a minute)")
    data, cut = _read_reply(await writer.chat([{"role": "system", "content": OUTLINE_PROMPT},
                                               {"role": "user", "content": ask}], max_tokens=SPACE_BUDGET))
    plan = _clean_outline(data or {})
    if not plan["units"]:
        raise ValueError("The model's curriculum couldn't be read. Please try again.")

    modules = sum(len(u["modules"]) for u in plan["units"])
    progress(0.5, f"{len(plan['units'])} units and {modules} modules planned. Choosing the topics for each… (a minute or two)")
    outcomes = await asyncio.gather(*[_fill_topics(writer, ask, c) for c in _chunks(plan["units"])], return_exceptions=True)
    errors = [o for o in outcomes if isinstance(o, BaseException)]
    filled = sum(o for o in outcomes if isinstance(o, int))
    if not filled:
        raise errors[0] if errors else ValueError("The model's topics couldn't be read. Please try again.")

    result = {"key": key, "mode": mode, "skill": skill["name"], "role": goal_role, **plan,
              "truncated": cut, "missing_topics": modules - filled, "model": writer.model_used}
    if not result["missing_topics"]:  # an incomplete plan isn't saved, so opening it again tries afresh
        _save(cache_dir, key, result)
    return result


def curriculum_blocks(c: dict) -> list:
    """The curriculum in the document layout render.py writes (PDF, DOCX, TXT)."""
    meta = [f"About {c['hours_total']} hours" if c.get("hours_total") else "",
            f"over {c['weeks']} weeks" if c.get("weeks") else "",
            f"towards {c['role']}" if c.get("role") else ""]
    blocks: list = [("name", f"{c['skill']}: learning plan"),
                    ("headline", c.get("proficiency_goal") or "Moderate proficiency")]
    if any(meta):
        blocks.append(("contact", " ".join(m for m in meta if m)))
    if c.get("prerequisites"):
        blocks += [("heading", "Before you start")] + [("bullet", p) for p in c["prerequisites"]]
    for i, u in enumerate(c["units"], 1):
        blocks.append(("heading", f"Unit {i}: {u['title']}"))
        if u.get("goal"):
            blocks.append(("para", u["goal"] + (f" (about {u['hours']} hours)" if u.get("hours") else "")))
        for j, m in enumerate(u["modules"], 1):
            blocks.append(("role", (f"Module {i}.{j}: {m['title']}", "")))
            blocks += [("bullet", t) for t in m["topics"]]
            if m.get("practice"):
                blocks.append(("para", f"Practice: {m['practice']}"))
        if u.get("checkpoint"):
            blocks.append(("para", f"Checkpoint: {u['checkpoint']}"))
    if c.get("capstone"):
        blocks += [("heading", "Capstone project"), ("para", c["capstone"])]
    return blocks
