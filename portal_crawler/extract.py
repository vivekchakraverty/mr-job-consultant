"""Website details recorded for each confirmed job portal."""
import re
from urllib.parse import urlsplit

from .classify import EMPLOYER_PHRASES, has_job_search_form
from .doc import Doc
from .urls import country_suffix, host_of, registered_domain

NICHES = {
    "remote": r"\bremote\b|work from home|\bwfh\b|distributed team|anywhere",
    "tech": r"\b(tech|developers?|software|programm\w+|devops|engineers?|it jobs|coding)\b",
    "data/ai": r"\b(data scien\w+|machine learning|\bai\b|artificial intelligence|analytics)\b",
    "startup": r"\bstart-?ups?\b",
    "web3": r"\b(crypto|web3|blockchain|defi)\b",
    "design": r"\b(design(ers?)?|ux|ui|creative)\b",
    "marketing/media": r"\b(marketing|seo|advertising|media|journalism)\b",
    "healthcare": r"\b(health ?care|nurs(e|es|ing)|medical|physicians?|hospital|pharma\w*|clinical)\b",
    "education": r"\b(teach(er|ers|ing)|education|academic|universit(y|ies)|higher ed)\b",
    "government": r"\b(government|public sector|civil service|federal)\b",
    "nonprofit/impact": r"\b(non-?profits?|ngos?|charit(y|ies)|social impact|climate)\b",
    "finance": r"\b(finance|banking|accounting|fintech|investment)\b",
    "legal": r"\b(legal|lawyers?|attorneys?|paralegal)\b",
    "hospitality": r"\b(hospitality|hotels?|restaurants?|chefs?|culinary)\b",
    "trades/logistics": r"\b(construction|electricians?|plumb\w+|drivers?|trucking|warehouse|logistics)\b",
    "freelance/gig": r"\b(freelanc\w+|gigs?|contract work|contractors?)\b",
    "early career": r"\b(internships?|interns|graduates?|entry[- ]level|campus|freshers?)\b",
    "executive": r"\b(executive|c-suite|senior management)\b",
    "diversity": r"\b(diversity|women in|veterans?|disabilit\w+|inclusive)\b",
}
NICHE_RE = {k: re.compile(v, re.I) for k, v in NICHES.items()}

PLATFORM_MARKERS = {
    "WP Job Manager": ["wp-job-manager", "job_listing"],
    "WordPress": ["wp-content/", "wp-includes/"],
    "Niceboard": ["niceboard"],
    "JobBoard.io": ["jobboard.io"],
    "Jobboardly": ["jobboardly"],
    "SmartJobBoard": ["smartjobboard"],
    "JBoard": ["jboard.io"],
    "Webflow": ["webflow.com", "w-webflow"],
    "Drupal": ["drupal-settings-json", "/sites/default/files/"],
    "Next.js": ["__NEXT_DATA__", "/_next/static"],
    "Nuxt": ["__NUXT__", "/_nuxt/"],
    "Angular": ["ng-version="],
}
SOCIAL = {
    "linkedin": "linkedin.com", "x": ("twitter.com", "x.com"), "facebook": "facebook.com",
    "instagram": "instagram.com", "youtube": "youtube.com", "telegram": ("t.me", "telegram.me"),
}
JOB_COUNT = re.compile(
    r"(\d{1,3}(?:[,.  ]\d{3})+|\d+(?:\.\d+)?\s?[kK]|\d+)\s*\+?\s*(?:new\s+|live\s+|active\s+|open\s+|remote\s+)?"
    r"(?:jobs|job\s+openings|job\s+listings|job\s+offers|vacancies|openings|positions|opportunities)\b",
    re.I,
)
EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")


def _parse_count(raw: str) -> int | None:
    raw = raw.strip().replace(" ", "").replace(" ", "")
    try:
        if raw[-1] in "kK":
            return int(float(raw[:-1]) * 1000)
        return int(re.sub(r"[,.]", "", raw))
    except (ValueError, IndexError):
        return None


def job_count_hint(text: str) -> int | None:
    counts = [c for m in JOB_COUNT.finditer(text) if (c := _parse_count(m.group(1)))]
    counts = [c for c in counts if c < 50_000_000 and not 1990 <= c <= 2035]  # skip years
    return max(counts) if counts else None


def tier(job_count: int | None, hint: str | None) -> str:
    if hint:
        return hint
    if job_count is None:
        return "unknown"
    return "big" if job_count >= 50_000 else "medium" if job_count >= 2_000 else "small"


def extract_details(doc: Doc, tier_hint: str | None = None) -> dict:
    head = doc.head_text
    niches = [k for k, rx in NICHE_RE.items() if rx.search(head)]
    count = job_count_hint(doc.text)
    domain = registered_domain(doc.url)

    socials: dict[str, str] = {}
    emails: list[str] = []
    rss = api = None
    for url, anchor in doc.links:
        h = host_of(url)
        for name, hosts in SOCIAL.items():
            hosts = hosts if isinstance(hosts, tuple) else (hosts,)
            if name not in socials and any(h == x or h.endswith("." + x) for x in hosts):
                socials[name] = url
        low = (url + " " + anchor).lower()
        if rss is None and registered_domain(url) == domain and re.search(r"/(rss|feed)(/|\.xml|$)|\brss\b", low):
            rss = url
        if api is None and re.search(r"\b(api|developers?)\b", anchor, re.I):
            api = url
    for a in doc.soup.select('a[href^="mailto:"]'):
        addr = a["href"][7:].split("?")[0].strip()
        if EMAIL.match(addr) and addr not in emails:
            emails.append(addr)
    feed = doc.soup.find("link", attrs={"type": re.compile(r"(rss|atom)\+xml")})
    if feed and feed.get("href"):
        rss = feed["href"]

    html = doc.raw_html
    platform = [name for name, marks in PLATFORM_MARKERS.items() if any(m in html for m in marks)]
    generator = doc.meta("generator")
    if generator:
        platform.insert(0, generator)

    return {
        "name": doc.meta("og:site_name") or _clean_title(doc.title) or host_of(doc.url),
        "url": doc.url,
        "domain": domain,
        "title": doc.title[:200],
        "description": (doc.meta("description") or doc.meta("og:description"))[:500],
        "language": doc.lang,
        "country": country_suffix(doc.url),
        "niches": niches or ["general"],
        "job_count_hint": count,
        "tier": tier(count, tier_hint),
        "https": urlsplit(doc.url).scheme == "https",
        "job_search_form": has_job_search_form(doc),
        "employers_can_post": bool(EMPLOYER_PHRASES.search(doc.text)),
        "job_alerts": bool(re.search(r"\bjob\s+alerts?\b", doc.text, re.I)),
        "rss_feed": rss,
        "api_or_developers": api,
        "structured_data_jobposting": doc.has_job_posting,
        "platform": list(dict.fromkeys(platform)),
        "server": doc.page.headers.get("server"),
        "response_seconds": round(doc.page.elapsed, 2),
        "social": socials,
        "contact_emails": emails[:3],
    }


def _clean_title(title: str) -> str:
    return re.split(r"\s+[|\-–—:]\s+", title)[0].strip()[:80]
