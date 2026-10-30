"""Check whether a portal's individual job posts can be read without logging in.

Opens a few real job-post pages as an anonymous visitor (no cookies, no account) and
classifies each one:

  public            the job description is in the page and nothing asks you to sign in
  soft_wall         the description is in the HTML but the page also demands a login/sign-up
  login_required    redirected to a login page, HTTP 401, or a sign-in prompt instead of content
  blocked           the site refuses this crawler (403/405/429, bot challenge) — not verified
  robots_disallowed robots.txt forbids fetching job pages — not checked
  unknown           no job content found in the static HTML (often JS-rendered; try --render)
"""
import asyncio
import gzip
import html
import re
import time
from collections import deque
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlsplit

from .classify import job_links
from .config import Settings
from .doc import Doc
from .fetcher import Fetcher, Page
from .urls import host_of, is_login_url, job_detail_score, normalize

_GATE_ACTION = r"(?:(?:sign|log)\s*-?\s*in|register|sign\s*-?\s*up|create\s+(?:a|an|your)\s+(?:free\s+)?account|" \
               r"become\s+a\s+member|subscribe|join(?:\s+(?:now|free|us))?|upgrade)"
_GATED_THING = r"(?:job|jobs|details?|description|posting|listing|vacancy|position|content|contact|salary|" \
               r"employer|company\s+name|this\s+page)"
LOGIN_WALL = re.compile(
    rf"{_GATE_ACTION}\s+(?:now\s+)?to\s+(?:view|see|read|access|unlock|continue\s+reading)\s+"
    rf"(?:the\s+|this\s+|full\s+|complete\s+|all\s+)*{_GATED_THING}\b"
    r"|\bmembers?[- ]only\b|\blogin\s+required\b|\byou\s+must\s+(?:be\s+)?(?:logged|signed)\s+in\b"
    r"|\bplease\s+(?:log|sign)\s*-?\s*in\s+to\s+(?:continue|view)\b"
    r"|\bbitte\s+(?:melden\s+sie\s+sich\s+an|einloggen)\b|\binicia\s+sesi[oó]n\s+para\s+ver\b"
    r"|\bconnectez-vous\s+pour\s+(?:voir|consulter)\b",
    re.I,
)
CHALLENGE = re.compile(
    r"just a moment\.\.\.|cf-chl|challenge-platform|are you a robot|verify you are (a )?human|"
    r"access denied|pardon our interruption|request unsuccessful|px-captcha|datadome|"
    r"enable javascript and cookies to continue|captcha",
    re.I,
)
JOB_CONTENT = re.compile(
    r"\b(responsibilit\w+|requirements?|qualifications?|experience|salary|compensation|"
    r"job description|about the (?:role|job|position)|skills|benefits|full[- ]time|part[- ]time|"
    r"employment type|apply now|how to apply|job type|location)\b",
    re.I,
)
REFUSED_STATUS = (403, 405, 429, 503)
LISTING_ANCHOR = re.compile(
    r"\b(jobs|job search|search jobs|find (?:a )?jobs?|job listings|job offers|vacancies|openings|"
    r"offres d'emploi|emplois|stellenangebote|stellen|empleos|ofertas|vagas)\b",
    re.I,
)
LISTING_PATH = re.compile(
    r"^/(?:[a-z]{2}(?:[-_][a-z]{2})?/)?(jobs|job-search|jobsearch|vacancies|search|find-jobs|remote-jobs|"
    r"careers|empleos|ofertas|offres|emplois|stellenangebote|vagas)\b",
    re.I,
)
NOT_LISTING = re.compile(r"alert|email|account|saved|my-|post|employer|recruiter|pricing|login|register|"
                         r"advice|blog|salar", re.I)
SITEMAP_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)

_RANK = ["login_required", "soft_wall", "public", "blocked", "robots_disallowed", "error", "unknown"]
_DECISIVE = {"public", "soft_wall", "login_required"}


@dataclass
class Check:
    url: str
    verdict: str
    reason: str
    status: int
    final_url: str
    rendered: bool = False
    schema: bool = False     # page carries JobPosting structured data
    post_like: bool = False  # a single job post rather than a list of jobs


def assess(page: Page, rendered: bool = False, doc: Doc | None = None) -> Check:
    def done(verdict: str, reason: str) -> Check:
        schema = bool(doc and doc.has_job_posting)
        return Check(page.url, verdict, reason, page.status, page.final_url, rendered,
                     schema, schema or job_detail_score(page.url) >= 3)

    if page.error == "robots":
        return done("robots_disallowed", "robots.txt disallows this URL")
    if page.error:
        return done("error", page.error)
    hops = page.redirects + [page.final_url]
    if page.status == 401:
        return done("login_required", "HTTP 401")
    if not is_login_url(page.url) and any(is_login_url(u) for u in hops):
        return done("login_required", f"redirected to login: {next(u for u in hops if is_login_url(u))}")
    if page.status >= 400:
        if page.status in REFUSED_STATUS:
            why = "bot challenge" if CHALLENGE.search(page.text[:50_000]) else f"HTTP {page.status}"
            return done("blocked", why)
        return done("error", f"HTTP {page.status}")

    doc = doc or Doc(page)
    text = doc.text
    if len(text) < 1500 and CHALLENGE.search(page.text[:50_000]):
        return done("blocked", "bot challenge page")
    wall = LOGIN_WALL.search(text)
    signals = {m.lower() for m in JOB_CONTENT.findall(text)}
    has_content = doc.has_job_posting or (len(text) >= 1200 and len(signals) >= 3)
    if has_content and not wall:
        return done("public", "JobPosting data" if doc.has_job_posting else f"job text ({len(text)} chars)")
    if has_content and wall:
        return done("soft_wall", f"content present but page says: '{wall.group(0)[:80]}'")
    if wall:
        return done("login_required", f"page says: '{wall.group(0)[:80]}'")
    if doc.has_password_field and len(text) < 1500:
        return done("login_required", "password form instead of job content")
    return done("unknown", f"no job content in static HTML ({len(text)} chars)")


def combine(checks: list[Check]) -> str:
    if not checks:
        return "unknown"
    verdicts = {c.verdict for c in checks}
    return next(v for v in _RANK if v in verdicts)


class Renderer:
    """Headless Chromium for job pages whose content only appears after JavaScript runs."""

    def __init__(self, user_agent: str, concurrency: int = 3):
        self.user_agent = user_agent
        self._pw = self._browser = None
        self._sem = asyncio.Semaphore(concurrency)
        self._start_lock = asyncio.Lock()

    async def render(self, url: str) -> Page:
        async with self._start_lock:
            if self._browser is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                try:
                    self._browser = await self._pw.chromium.launch()
                except Exception:
                    # chromium-headless-shell missing: full Chromium in its new headless mode works too.
                    self._browser = await self._pw.chromium.launch(channel="chromium")
        async with self._sem:
            ctx = await self._browser.new_context(user_agent=self.user_agent)  # fresh context: no cookies
            start = time.monotonic()
            try:
                tab = await ctx.new_page()
                resp = await tab.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await tab.wait_for_timeout(2500)  # let client-side rendering / login redirects happen
                return Page(url, tab.url, resp.status if resp else 0, text=await tab.content(),
                            redirects=[url], elapsed=time.monotonic() - start)
            except Exception as e:
                return Page(url, url, 0, error=f"render: {type(e).__name__}: {e}"[:300])
            finally:
                await ctx.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            await self._pw.stop()


def _listing_urls(home: Doc) -> list[str]:
    host = host_of(home.url)
    by_anchor, by_path = [], []
    for u, a in home.links:
        path = urlsplit(u).path
        if host_of(u) != host or NOT_LISTING.search(path) or path in ("", "/"):
            continue
        if a and len(a) <= 40 and LISTING_ANCHOR.search(a):
            by_anchor.append(u)
        elif LISTING_PATH.match(path):
            by_path.append(u)
    found = by_anchor + by_path or ["/".join(home.url.split("/")[:3]) + "/jobs"]
    return list(dict.fromkeys(found))


async def find_job_urls(home: Doc, fetcher: Fetcher, want: int,
                        renderer: Renderer | None = None) -> tuple[list[str], list[Check]]:
    """Job-post URLs from the homepage, then listing pages, then the sitemap.

    Also returns checks of listing pages that turned out to be gated or refused.
    """
    urls = job_links(home, structural=True)
    listing_checks: list[Check] = []

    if len(urls) < want:
        for listing in _listing_urls(home)[:3]:
            page = await fetcher.get(listing)
            check = assess(page)
            if check.verdict in ("login_required", "blocked"):
                listing_checks.append(check)
                continue
            if page.ok and page.is_html:
                urls += [u for u in job_links(Doc(page), structural=True) if u not in urls]
            if len(urls) >= want:
                break

    if not urls:
        urls = await _sitemap_job_urls(home.url, fetcher)
    if not urls and renderer:
        # The job links may only exist after JavaScript runs: look again in rendered pages.
        for target in [home.url] + _listing_urls(home)[:2]:
            if not await fetcher.allowed(target):
                continue
            page = await renderer.render(target)
            if page.ok:
                urls += [u for u in job_links(Doc(page), structural=True) if u not in urls]
            if len(urls) >= want:
                break
    return urls, listing_checks


def _jobby_first(urls: list[str]) -> list[str]:
    return sorted(urls, key=lambda u: not re.search(r"job|vacanc|posting|listing|career", u, re.I))


async def _sitemap_job_urls(url: str, fetcher: Fetcher, limit: int = 20) -> list[str]:
    origin = "/".join(url.split("/")[:3])
    maps = _jobby_first(await fetcher.sitemaps(url) or [origin + "/sitemap.xml"])
    for sitemap in maps[:2]:
        locs, is_index = await _read_sitemap(sitemap, fetcher)
        if is_index:
            children = _jobby_first(locs)
            if not children:
                continue
            locs, _ = await _read_sitemap(children[0], fetcher)
        scored = sorted(((job_detail_score(u), u) for u in locs), key=lambda x: -x[0])
        found = [u for s, u in scored if s >= 2][:limit]
        if found:
            return found
    return []


async def _read_sitemap(url: str, fetcher: Fetcher) -> tuple[list[str], bool]:
    page = await fetcher.get(url)
    if not page.ok:
        return [], False
    text = page.text
    if page.body[:2] == b"\x1f\x8b":  # .xml.gz served without Content-Encoding
        try:
            text = gzip.decompress(page.body)[:20_000_000].decode("utf-8", errors="replace")
        except (OSError, EOFError):  # truncated by the size cap
            return [], False
    locs = [u for loc in SITEMAP_LOC.findall(text) if (u := normalize(urljoin(url, html.unescape(loc))))]
    return locs, "<sitemapindex" in text[:3000].lower()


def confidence(evidence: list[dict]) -> str:
    """How directly the 'public' verdict was observed."""
    public = [e for e in evidence if e["verdict"] == "public"]
    if any(e.get("schema") and e.get("post_like") for e in public):
        return "high"    # a job post with JobPosting data, read anonymously
    if any(e.get("post_like") for e in public):
        return "medium"  # a job post's full text, read anonymously
    return "low" if public else ""  # only job listing pages were seen


async def check_access(home: Doc, fetcher: Fetcher, settings: Settings,
                       renderer: Renderer | None = None) -> tuple[str, list[dict]]:
    if is_login_url(home.url):
        return "login_required", [asdict(Check(home.page.url, "login_required",
                                               "homepage redirects to login", home.page.status, home.url))]
    urls, listing_checks = await find_job_urls(home, fetcher, settings.job_pages_to_check, renderer)
    if not urls and not listing_checks:
        return "unknown", [asdict(Check(home.url, "unknown", "no job-post links found", home.page.status, home.url))]

    # Spread the sample across different URL shapes rather than three near-identical links.
    queue, shapes = deque(), set()
    for u in urls:
        shape = re.sub(r"\d+", "#", u.split("?")[0].rsplit("/", 1)[0])
        if shape not in shapes or len(urls) <= settings.job_pages_to_check * 2:
            queue.append(u)
            shapes.add(shape)

    checks: list[Check] = []
    seen: set[str] = set()
    budget, confirmed = settings.max_access_fetches, 0
    last_listing: Check | None = None
    while queue and budget > 0 and confirmed < settings.job_pages_to_check:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        budget -= 1
        check, doc = await _load_and_assess(url, fetcher, renderer)
        # A list of jobs rather than one post (no JobPosting data, not a post-like URL): go one level deeper.
        if doc is not None and not doc.has_job_posting and job_detail_score(url) < 3 \
                and check.verdict in ("public", "unknown"):
            deeper = [u for u in job_links(doc, min_score=2, structural=True) if u not in seen]
            if len(deeper) >= 5:
                queue.extendleft(reversed(deeper[:4]))
                last_listing = check
                continue
        checks.append(check)
        if check.verdict in _DECISIVE:
            confirmed += 1
        if check.verdict == "login_required":
            break

    if not checks and last_listing:
        last_listing.verdict, last_listing.reason = "unknown", "only job listing pages reached within budget"
        checks.append(last_listing)
    # A gated listing page only decides the verdict when no individual post could be checked.
    decided = checks if any(c.verdict in _DECISIVE for c in checks) else checks + listing_checks
    return combine(decided), [asdict(c) for c in listing_checks + checks]


async def _load_and_assess(url: str, fetcher: Fetcher, renderer: Renderer | None) -> tuple[Check, Doc | None]:
    page = await fetcher.get(url)
    doc = Doc(page) if page.ok and page.is_html else None
    check = assess(page, doc=doc)
    # Only for pages that loaded fine but carry no content in static HTML; never to get past a refusal.
    if check.verdict == "unknown" and renderer:
        page = await renderer.render(url)
        doc = Doc(page) if page.ok else None
        check = assess(page, rendered=True, doc=doc)
    return check, doc
