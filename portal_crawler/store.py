"""SQLite store of discovered sites, so crawls can be stopped and resumed."""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    key            TEXT PRIMARY KEY,
    url            TEXT NOT NULL,
    source         TEXT,
    depth          INTEGER NOT NULL DEFAULT 0,
    seed           INTEGER NOT NULL DEFAULT 0,
    tier_hint      TEXT,
    access_hint    TEXT,
    state          TEXT NOT NULL DEFAULT 'pending',  -- pending | portal | not_portal | unreachable
    portal_score   INTEGER,
    access         TEXT,     -- public | soft_wall | login_required | blocked | robots_disallowed | unknown
    access_source  TEXT,     -- verified | seed_hint
    evidence       TEXT,     -- JSON list of per-URL checks
    details        TEXT,     -- JSON
    error          TEXT,
    discovered_at  TEXT,
    checked_at     TEXT
);
CREATE INDEX IF NOT EXISTS sites_state ON sites(state, depth);
"""


@dataclass
class SiteRow:
    key: str
    url: str
    source: str
    depth: int
    seed: bool
    tier_hint: str | None
    access_hint: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add(self, key: str, url: str, source: str, depth: int = 0, seed: bool = False,
            tier_hint: str | None = None, access_hint: str | None = None) -> bool:
        """Insert a pending site; returns False if it was already known."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO sites (key, url, source, depth, seed, tier_hint, access_hint, discovered_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key, url, source, depth, int(seed), tier_hint, access_hint, _now()),
        )
        if seed and cur.rowcount == 0:
            # A seed found earlier by discovery keeps its seed metadata.
            self.db.execute(
                "UPDATE sites SET seed = 1, url = ?, tier_hint = ?, access_hint = ? WHERE key = ?",
                (url, tier_hint, access_hint, key),
            )
        self.db.commit()
        return cur.rowcount > 0

    def knows_host(self, host: str) -> bool:
        """True if the host, or a portal living under a path of it, is already stored."""
        return self.db.execute(
            "SELECT 1 FROM sites WHERE key = ? OR key LIKE ? LIMIT 1", (host, host + "/%")).fetchone() is not None

    def pending(self, max_depth: int, limit: int) -> list[SiteRow]:
        rows = self.db.execute(
            "SELECT key, url, source, depth, seed, tier_hint, access_hint FROM sites"
            " WHERE state = 'pending' AND depth <= ? ORDER BY seed DESC, depth, discovered_at LIMIT ?",
            (max_depth, limit),
        ).fetchall()
        return [SiteRow(r["key"], r["url"], r["source"], r["depth"], bool(r["seed"]),
                        r["tier_hint"], r["access_hint"]) for r in rows]

    def save(self, key: str, state: str, *, portal_score: int | None = None, access: str | None = None,
             access_source: str | None = None, evidence: list | None = None,
             details: dict | None = None, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE sites SET state = ?, portal_score = ?, access = ?, access_source = ?, evidence = ?,"
            " details = ?, error = ?, checked_at = ? WHERE key = ?",
            (state, portal_score, access, access_source,
             json.dumps(evidence, ensure_ascii=False) if evidence is not None else None,
             json.dumps(details, ensure_ascii=False) if details is not None else None,
             error, _now(), key),
        )
        self.db.commit()

    def reset(self, states: list[str], access: list[str] | None = None) -> int:
        sql = f"UPDATE sites SET state = 'pending' WHERE state IN ({','.join('?' * len(states))})"
        if access:
            sql += f" AND access IN ({','.join('?' * len(access))})"
        cur = self.db.execute(sql, states + (access or []))
        self.db.commit()
        return cur.rowcount

    def portals(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM sites WHERE state = 'portal' ORDER BY seed DESC, key").fetchall()

    def stats(self) -> dict[str, list[tuple]]:
        return {
            "state": self.db.execute("SELECT state, COUNT(*) FROM sites GROUP BY state").fetchall(),
            "access": self.db.execute(
                "SELECT access, access_source, COUNT(*) FROM sites WHERE state = 'portal'"
                " GROUP BY access, access_source").fetchall(),
        }
