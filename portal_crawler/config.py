from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


@dataclass
class Settings:
    db_path: Path = DATA_DIR / "portals.db"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 JobPortalCrawler/0.1"
    )
    robots_agent: str = "JobPortalCrawler"
    respect_robots: bool = True
    timeout: float = 20.0
    concurrency: int = 16          # simultaneous HTTP requests overall
    site_concurrency: int = 8      # sites processed at once
    per_host_delay: float = 1.5    # seconds between requests to the same host
    max_bytes: int = 3_000_000     # stop reading a response after this many bytes
    job_pages_to_check: int = 3    # job posts that must be checked per portal for the login check
    max_access_fetches: int = 8    # page budget per portal for finding and checking those posts
    portal_threshold: int = 5      # classifier score needed to count as a job portal
