"""URL normalisation and the patterns that recognise job-post and login URLs."""
import re
from urllib.parse import urlsplit, urlunsplit

import tldextract

# Bundled public-suffix snapshot only, so no network fetch on first use.
_extract = tldextract.TLDExtract(suffix_list_urls=())

# Hosts that are never job portals themselves (or are single-company ATS career pages).
NON_PORTAL_DOMAINS = {
    "google.com", "youtube.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "pinterest.com", "reddit.com", "wikipedia.org", "wikimedia.org",
    "wikidata.org", "archive.org", "github.com", "githubusercontent.com", "gitlab.com",
    "apple.com", "microsoft.com", "amazon.com", "cloudflare.com", "medium.com",
    "substack.com", "t.me", "telegram.org", "whatsapp.com", "discord.gg", "discord.com",
    "slack.com", "bit.ly", "goo.gl", "doi.org", "creativecommons.org", "w3.org",
    "nytimes.com", "forbes.com", "bloomberg.com", "techcrunch.com", "theverge.com",
    "wsj.com", "reuters.com", "bbc.co.uk", "cnn.com", "businessinsider.com",
    # single-company applicant tracking systems
    "greenhouse.io", "lever.co", "myworkdayjobs.com", "workday.com", "ashbyhq.com",
    "smartrecruiters.com", "bamboohr.com", "recruitee.com", "workable.com", "jazzhr.com",
    "breezy.hr", "icims.com", "taleo.net", "successfactors.com", "teamtailor.com",
    "personio.de", "pinpointhq.com", "jobvite.com",
}

JOB_WORDS = re.compile(
    r"\b(jobs?|careers?|vacanc(?:y|ies)|hiring|recruit\w*|employment|empleos?|emplois?|"
    r"stellen\w*|vagas?|lavoro|naukri|vacatures?|trabajos?|praca|jobb\w*|työpaikat|"
    r"rozee|kerja|lowongan|gigs?|internships?|talent)\b",
    re.I,
)

_JOB_PATH = re.compile(
    r"/(jobs?|viewjob|job[-_]?details?|job[-_]?listings?|job[-_]?posts?|jobposting|"
    r"vacanc(?:y|ies)|vacatures?|positions?|openings?|offres?|ofertas?|empleos?|"
    r"stellenangebote?|stellen|vagas?|annonces?|careers?|listings?|remote-jobs|gigs?|"
    r"jobad|job-ad|jobview|anuncios?|view)(?=[/_.?=]|-\d|$)",
    re.I,
)
_JOB_QUERY = re.compile(r"(?:^|&)(jk|vjk|jobid|job_id|jobId|job|id|pid|postid)=", re.I)
_LISTING_LAST = {
    "jobs", "job", "search", "browse", "all", "categories", "category", "locations",
    "companies", "remote-jobs", "careers", "vacancies", "new", "latest", "post", "post-a-job",
}
NOT_DETAIL = re.compile(
    r"/(category|categories|location|locations|search|tags?|compan(y|ies)[^/]*|employers?|blog|"
    r"profiles?|company-profile|news|login|signin|sign-in|register|signup|post-a-job|pricing|about|faq|help|rss|feed|"
    r"career-advice|advice|courses?|articles?|guides?|salar(y|ies)|resources|events|job-search|"
    r"job-collections|collections|browse-jobs|q-[^/]*)(/|$)",
    re.I,
)
# Last path segments that name a list of jobs rather than one job: "jobs-in-agra", "python-jobs".
LISTING_SEGMENT = re.compile(r"^(jobs?-in-|in-|at-|.*-jobs$|.*-vacancies$|remote-jobs-)", re.I)

LOGIN_URL = re.compile(
    r"/(login|log-in|logon|signin|sign-in|sign_in|signup|sign-up|register|registration|"
    r"auth|authenticate|authwall|sso|session|uas/login|users/sign_in|account/log-?in|"
    r"members?/login|checkpoint)(/|\?|$|\.)",
    re.I,
)


def host_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.lower().removeprefix("www.")


def registered_domain(url_or_host: str) -> str:
    ext = _extract(url_or_host)
    return ext.top_domain_under_public_suffix if hasattr(ext, "top_domain_under_public_suffix") \
        else ext.registered_domain


def country_suffix(url: str) -> str | None:
    suffix = _extract(url).suffix.split(".")[-1]
    generic_cc = {"io", "co", "ai", "me", "tv", "app", "ly", "fm", "gg", "to", "cc", "ws", "sh"}
    return suffix.upper() if len(suffix) == 2 and suffix not in generic_cc else None


def is_non_portal(url: str) -> bool:
    return registered_domain(url) in NON_PORTAL_DOMAINS


def normalize(url: str) -> str:
    """Absolute http(s) URL without fragment; '' for anything else."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def site_key(url: str, keep_path: bool = False) -> str:
    """Identity of a portal: its host, plus the path for portals that live under a path."""
    key = host_of(url)
    path = urlsplit(url).path.rstrip("/")
    return f"{key}{path}" if keep_path and path else key


def job_detail_score(url: str) -> int:
    """How much a URL looks like a single job post (0 = not at all)."""
    parts = urlsplit(url)
    path, query = parts.path, parts.query
    segs = [s for s in path.split("/") if s]
    has_query_id = bool(_JOB_QUERY.search(query))
    if not (_JOB_PATH.search(path + "/") or has_query_id) or (not segs and not has_query_id):
        return 0
    if NOT_DETAIL.search(path):
        return 0
    last = segs[-1].lower() if segs else ""
    if (last in _LISTING_LAST or LISTING_SEGMENT.match(last)) and not has_query_id:
        return 0
    score = 1
    if re.search(r"\d{3,}", path) or has_query_id:
        score += 2
    if last.count("-") >= 3 or len(last) >= 25:
        score += 1
    if len(segs) >= 2:
        score += 1
    return score


def is_login_url(url: str) -> bool:
    return bool(LOGIN_URL.search(urlsplit(url).path + "?"))
