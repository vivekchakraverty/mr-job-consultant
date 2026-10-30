"""Polite async HTTP fetching: robots.txt, per-host delay, response size cap."""
import asyncio
import logging
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import Settings

log = logging.getLogger(__name__)


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    headers: dict = field(default_factory=dict)
    text: str = ""
    body: bytes = b""
    redirects: list[str] = field(default_factory=list)  # every hop's URL and Location header
    elapsed: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    @property
    def is_html(self) -> bool:
        ctype = self.headers.get("content-type", "").lower()
        return not ctype or "html" in ctype or "xml" in ctype


class Fetcher:
    def __init__(self, settings: Settings):
        self.s = settings
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            },
            follow_redirects=True,
            max_redirects=10,
            verify=_ssl_context(),
            timeout=settings.timeout,
        )
        self._sem = asyncio.Semaphore(settings.concurrency)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()

    async def get(self, url: str, max_bytes: int | None = None) -> Page:
        if not await self.allowed(url):
            return Page(url, url, 0, error="robots")
        return await self._raw_get(url, max_bytes)

    async def allowed(self, url: str) -> bool:
        if not self.s.respect_robots:
            return True
        rp = await self._robots_for(url)
        return rp is None or rp.can_fetch(self.s.robots_agent, url)

    async def sitemaps(self, url: str) -> list[str]:
        rp = await self._robots_for(url)
        return (rp.site_maps() or []) if rp else []

    async def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._robots:
                page = await self._raw_get(origin + "/robots.txt", max_bytes=500_000)
                if page.error or page.status >= 400 or not page.text.strip():
                    self._robots[origin] = None
                else:
                    rp = RobotFileParser()
                    rp.parse(page.text.splitlines())
                    self._robots[origin] = rp
        return self._robots[origin]

    async def _wait_turn(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0.0) + self.s.per_host_delay - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def _raw_get(self, url: str, max_bytes: int | None = None) -> Page:
        host = urlsplit(url).netloc.lower()
        max_bytes = max_bytes or self.s.max_bytes
        await self._wait_turn(host)
        async with self._sem:
            start = time.monotonic()
            try:
                async with self.client.stream("GET", url) as r:
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) >= max_bytes:
                            break
                    redirects = []
                    for hop in r.history:
                        redirects.append(str(hop.url))
                        if hop.headers.get("location"):
                            redirects.append(hop.headers["location"])
                    return Page(
                        url=url,
                        final_url=str(r.url),
                        status=r.status_code,
                        headers={k.lower(): v for k, v in r.headers.items()},
                        text=_decode(bytes(buf), r.encoding),
                        body=bytes(buf),
                        redirects=redirects,
                        elapsed=time.monotonic() - start,
                    )
            except Exception as e:  # network, TLS, protocol and URL errors all count as "unreachable"
                log.debug("fetch failed %s: %s", url, e)
                return Page(url, url, 0, elapsed=time.monotonic() - start,
                            error=f"{type(e).__name__}: {e}"[:300])


def _ssl_context() -> ssl.SSLContext | bool:
    """The OS trust store when truststore is installed: it completes chains that servers send incomplete."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return True


def _decode(data: bytes, encoding: str | None) -> str:
    try:
        return data.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")
