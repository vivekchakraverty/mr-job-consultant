"""Processing pending sites: classify the homepage, extract details, verify login-free access."""
import asyncio
import logging

from .access import REFUSED_STATUS, Renderer, check_access
from .classify import classify
from .config import Settings
from .discover import from_portal_links
from .doc import Doc
from .extract import extract_details
from .fetcher import Fetcher
from .store import SiteRow, Store
from .urls import is_login_url

log = logging.getLogger(__name__)

UNVERIFIED = {"blocked", "robots_disallowed", "unknown", "error"}


async def process_site(row: SiteRow, store: Store, fetcher: Fetcher, settings: Settings,
                       max_depth: int, renderer: Renderer | None) -> None:
    page = await fetcher.get(row.url)
    reachable = page.ok and page.is_html
    refused = page.error == "robots" or page.status in (401, *REFUSED_STATUS)
    if not reachable and not (row.seed and refused):
        if page.error == "robots":
            store.save(row.key, "unreachable", access="robots_disallowed", error="robots.txt disallows homepage")
        else:
            store.save(row.key, "unreachable", error=page.error or f"HTTP {page.status}")
        log.info("%-40s unreachable (%s)", row.key, page.error or page.status)
        return

    if not reachable:
        # A known portal that refuses this crawler: keep it, but its access is unverified.
        if page.error == "robots":
            access, reason = "robots_disallowed", "robots.txt disallows homepage"
        elif page.status == 401 or is_login_url(page.final_url):
            access, reason = "login_required", f"homepage HTTP {page.status}"
        else:
            access, reason = "blocked", f"homepage HTTP {page.status}"
        evidence = [{"url": row.url, "verdict": access, "reason": reason,
                     "status": page.status, "final_url": page.final_url, "rendered": False}]
        _save_portal(store, row, None, {"url": row.url, "tier": row.tier_hint}, access, evidence)
        return

    doc = Doc(page)
    is_portal, score, reasons = classify(doc, settings.portal_threshold)
    if not (is_portal or row.seed):
        store.save(row.key, "not_portal", portal_score=score, details={"reasons": reasons, "title": doc.title})
        log.info("%-40s not a portal (score %d)", row.key, score)
        return

    details = extract_details(doc, row.tier_hint)
    details["portal_reasons"] = reasons
    access, evidence = await check_access(doc, fetcher, settings, renderer)
    _save_portal(store, row, score, details, access, evidence)

    if row.depth < max_depth:
        n = from_portal_links(store, doc, row.depth + 1)
        if n:
            log.info("%-40s +%d linked candidates", row.key, n)


def _save_portal(store: Store, row: SiteRow, score: int | None, details: dict,
                 access: str, evidence: list[dict]) -> None:
    source = "verified"
    if access in UNVERIFIED and row.access_hint:
        access, source = row.access_hint, "seed_hint"
    store.save(row.key, "portal", portal_score=score, access=access, access_source=source,
               evidence=evidence, details=details)
    log.info("%-40s PORTAL  access=%s (%s)", row.key, access, source)


async def crawl(store: Store, settings: Settings, limit: int | None, max_depth: int, render: bool) -> int:
    renderer = Renderer(settings.user_agent) if render else None
    processed = 0
    sem = asyncio.Semaphore(settings.site_concurrency)

    async def guarded(row: SiteRow) -> None:
        async with sem:
            try:
                await process_site(row, store, fetcher, settings, max_depth, renderer)
            except Exception as e:  # one broken site must not stop the crawl
                log.exception("%s failed", row.key)
                store.save(row.key, "unreachable", error=f"crawler error: {type(e).__name__}: {e}"[:300])

    try:
        async with Fetcher(settings) as fetcher:
            while limit is None or processed < limit:
                batch_size = settings.site_concurrency * 4
                if limit is not None:
                    batch_size = min(batch_size, limit - processed)
                batch = store.pending(max_depth, batch_size)
                if not batch:
                    break
                await asyncio.gather(*(guarded(r) for r in batch))
                processed += len(batch)
    finally:
        if renderer:
            await renderer.close()
    return processed
