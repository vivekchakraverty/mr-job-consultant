"""Finding candidate portal sites: directory pages, search results, and links between portals."""
import asyncio
import logging
import re

from .doc import Doc
from .fetcher import Fetcher
from .seeds import DIRECTORY_PAGES, SEED_PORTALS, search_queries
from .store import Store
from .urls import JOB_WORDS, host_of, is_non_portal, normalize, registered_domain, site_key

log = logging.getLogger(__name__)

MD_URL = re.compile(r"https?://[^\s)\]>\"'`]+")


def add_seeds(store: Store) -> int:
    added = 0
    for url, tier, hint in SEED_PORTALS:
        added += store.add(site_key(url, keep_path=True), url, "seed", 0, seed=True,
                           tier_hint=tier, access_hint=hint)
    return added


def _root(url: str) -> str:
    return "/".join(url.split("/")[:3]) + "/"


def add_candidate(store: Store, url: str, source: str, depth: int) -> bool:
    url = normalize(url)
    if not url or is_non_portal(url) or not host_of(url) or store.knows_host(host_of(url)):
        return False
    return store.add(site_key(url), _root(url), source, depth)


async def from_directories(store: Store, fetcher: Fetcher) -> int:
    added = 0
    for page_url in DIRECTORY_PAGES:
        page = await fetcher.get(page_url)
        if not page.ok:
            log.warning("directory %s unavailable (%s)", page_url, page.error or page.status)
            continue
        own = registered_domain(page_url)
        if page_url.endswith(".md") or "text/plain" in page.headers.get("content-type", ""):
            urls = MD_URL.findall(page.text)
        else:
            doc = Doc(page)
            tables = doc.soup.select("table.wikitable a[href]")
            urls = [a["href"] for a in tables] if tables else [u for u, _ in doc.links]
        n = sum(add_candidate(store, u, f"directory:{host_of(page_url)}", 1)
                for u in urls if u.startswith("http") and registered_domain(u) != own)
        log.info("directory %s: %d new candidates", page_url, n)
        added += n
    return added


async def from_search(store: Store, max_queries: int, results_per_query: int = 20) -> int:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            log.error("search discovery needs the 'ddgs' package: pip install ddgs")
            return 0
    added = 0
    queries = search_queries()[:max_queries]
    for i, q in enumerate(queries, 1):
        try:
            results = await asyncio.to_thread(lambda: list(DDGS().text(q, max_results=results_per_query)))
        except Exception as e:  # rate limits and network errors: skip this query
            log.warning("search %r failed: %s", q, e)
            await asyncio.sleep(10)
            continue
        n = sum(add_candidate(store, r.get("href", ""), f"search:{q}", 1) for r in results)
        log.info("search %d/%d %r: %d new candidates", i, len(queries), q, n)
        added += n
        await asyncio.sleep(2)
    return added


def from_portal_links(store: Store, doc: Doc, depth: int) -> int:
    """Other job sites a confirmed portal links to (partner networks, 'our other boards', etc.).

    Only links whose host or anchor text mentions jobs are followed, to keep the
    frontier from filling with advertisers and unrelated sites.
    """
    own = registered_domain(doc.url)
    added = 0
    for url, anchor in doc.links:
        if registered_domain(url) == own:
            continue
        if JOB_WORDS.search(host_of(url).replace(".", " ").replace("-", " ")) or JOB_WORDS.search(anchor):
            added += add_candidate(store, url, f"link:{host_of(doc.url)}", depth)
    return added
