"""Is a newer version out? One request to GitHub's "latest release" API, at most twice a day.

The answer says what to do on this install:
  a desktop build   the download for this system (the asset names packaging/build.py makes)
  from source       the update steps in the README

Nothing is downloaded or installed automatically. If GitHub can't be reached the answer is simply
"no news". Turn the check off with "check_for_updates": false in data/ai_settings.json, or the
MRJC_NO_UPDATE_CHECK environment variable.
"""
import json
import os
import platform
import re
import sys
import time
from pathlib import Path

REPO = "vivekchakraverty/mr-job-consultant"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
UPGRADE_DOCS = f"https://github.com/{REPO}#upgrading-from-010"
CHECK_EVERY = 12 * 3600


def parse(version: str) -> tuple[int, ...] | None:
    """ "v0.3.0" or "0.3.0" -> (0, 3, 0); pre-releases and anything odd -> None (never offered)."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (version or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def platform_tag() -> tuple[str, str]:
    """(os, arch) as in the download names: ("windows", "x64"), ("macos", "arm64")…"""
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return system, arch


def summarise(release: dict, current: str, frozen: bool, tag: tuple[str, str]) -> dict:
    """What the page needs from a GitHub release, for this install."""
    latest = parse(release.get("tag_name", ""))
    newer = bool(latest and parse(current) and latest > parse(current)
                 and not release.get("draft") and not release.get("prerelease"))
    out = {"current": current, "latest": ".".join(map(str, latest)) if latest else None, "newer": newer,
           "title": release.get("name") or "", "notes_url": release.get("html_url") or f"https://github.com/{REPO}/releases",
           "how": "download" if frozen else "source", "download": None, "upgrade_docs": UPGRADE_DOCS}
    if newer and frozen:
        marker = f"-{tag[0]}-{tag[1]}."
        for asset in release.get("assets") or []:
            if marker in asset.get("name", ""):
                out["download"] = {"name": asset["name"], "url": asset.get("browser_download_url"),
                                   "mb": round((asset.get("size") or 0) / 1e6)}
                break
    return out


def check(current: str, cache: Path, enabled: bool = True) -> dict:
    """The latest release compared with this version. Cached in `cache` for 12 hours."""
    frozen = bool(getattr(sys, "frozen", False))
    if not enabled or os.environ.get("MRJC_NO_UPDATE_CHECK"):
        return {"current": current, "newer": False, "disabled": True}
    release = None
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if time.time() - saved.get("checked", 0) < CHECK_EVERY:
            release = saved.get("release")
    except (OSError, json.JSONDecodeError):
        pass
    if release is None:
        import httpx
        try:
            r = httpx.get(LATEST_URL, timeout=6, follow_redirects=True,
                          headers={"Accept": "application/vnd.github+json", "User-Agent": "MrJobConsultant/" + current})
            r.raise_for_status()
            data = r.json()
            # Only what's needed, so the cache stays small.
            release = {k: data.get(k) for k in ("tag_name", "name", "html_url", "draft", "prerelease")}
            release["assets"] = [{k: a.get(k) for k in ("name", "browser_download_url", "size")}
                                 for a in data.get("assets") or []]
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"checked": time.time(), "release": release}), encoding="utf-8")
        except Exception:
            return {"current": current, "newer": False, "offline": True}  # no news is fine
    return summarise(release, current, frozen, platform_tag())
