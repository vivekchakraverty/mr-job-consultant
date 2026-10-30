"""Command line: python -m portal_crawler {run,seed,discover,crawl,export,stats,recheck}"""
import argparse
import asyncio
import logging
from pathlib import Path

from . import discover
from .config import DATA_DIR, Settings
from .crawl import crawl
from .export import export, load_overrides
from .fetcher import Fetcher
from .store import Store


def main() -> None:
    p = argparse.ArgumentParser(prog="portal_crawler", description=__doc__)
    p.add_argument("--db", type=Path, default=Settings.db_path)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def crawl_args(sp):
        sp.add_argument("--limit", type=int, help="max sites to process this run")
        sp.add_argument("--max-depth", type=int, default=2,
                        help="how far to follow links from portal to portal (seeds are depth 0)")
        sp.add_argument("--render", action="store_true",
                        help="re-check JS-rendered job pages in headless Chromium (needs playwright)")
        sp.add_argument("--delay", type=float, help="seconds between requests to one host")

    def export_args(sp):
        sp.add_argument("--out", type=Path, default=DATA_DIR / "public_job_portals")
        sp.add_argument("--include", default="public",
                        help="comma-separated access values to export (default: public). "
                             "Others: soft_wall, login_required, blocked, robots_disallowed, unknown")
        sp.add_argument("--verified-only", action="store_true",
                        help="drop portals whose access comes from a seed hint instead of a live check")
        sp.add_argument("--overrides", type=Path, default=DATA_DIR / "access_overrides.csv",
                        help="CSV of key,access,note you verified by hand (applied if the file exists)")

    def discover_args(sp):
        sp.add_argument("--no-directories", action="store_true", help="skip the directory pages")
        sp.add_argument("--search", action="store_true", help="also discover via web search (ddgs)")
        sp.add_argument("--max-queries", type=int, default=40)

    run = sub.add_parser("run", help="seed + discover + crawl + export")
    crawl_args(run), export_args(run), discover_args(run)
    sub.add_parser("seed", help="add the built-in seed portals")
    discover_args(sub.add_parser("discover", help="add candidates from directories / search"))
    crawl_args(sub.add_parser("crawl", help="process pending candidates"))
    export_args(sub.add_parser("export", help="write CSV + JSON"))
    sub.add_parser("stats", help="counts by state and access")
    rc = sub.add_parser("recheck", help="mark sites pending again")
    rc.add_argument("--states", default="unreachable", help="comma-separated states, e.g. portal,unreachable")
    rc.add_argument("--access", help="only portals with these access values, e.g. unknown,blocked")

    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings(db_path=args.db)
    if getattr(args, "delay", None) is not None:
        settings.per_host_delay = args.delay
    store = Store(settings.db_path)
    asyncio.run(_run(args, store, settings))


async def _run(args, store: Store, settings: Settings) -> None:
    log = logging.getLogger("portal_crawler")
    if args.cmd in ("run", "seed"):
        log.info("seeds: %d new", discover.add_seeds(store))
    if args.cmd in ("run", "discover"):
        if not args.no_directories:
            async with Fetcher(settings) as fetcher:
                log.info("directories: %d new candidates", await discover.from_directories(store, fetcher))
        if args.search:
            log.info("search: %d new candidates", await discover.from_search(store, args.max_queries))
    if args.cmd in ("run", "crawl"):
        n = await crawl(store, settings, args.limit, args.max_depth, args.render)
        log.info("processed %d sites", n)
    if args.cmd in ("run", "export"):
        include = {s.strip() for s in args.include.split(",") if s.strip()}
        n, csv_path, json_path = export(store, args.out, include, args.verified_only,
                                        load_overrides(args.overrides))
        log.info("exported %d portals -> %s, %s", n, csv_path, json_path)
    if args.cmd == "recheck":
        access = [s.strip() for s in args.access.split(",")] if args.access else None
        log.info("reset %d sites to pending", store.reset([s.strip() for s in args.states.split(",")], access))
    if args.cmd in ("run", "stats"):
        stats = store.stats()
        print("\nsites by state:")
        for state, n in stats["state"]:
            print(f"  {state:<14} {n}")
        print("portals by access:")
        for access, source, n in stats["access"]:
            print(f"  {str(access):<18} {str(source):<10} {n}")


if __name__ == "__main__":
    main()
