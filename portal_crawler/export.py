"""Writing the portal list to CSV and JSON."""
import csv
import json
from pathlib import Path

from .access import confidence
from .store import Store

CSV_COLUMNS = [
    "name", "url", "tier", "access", "access_source", "confidence", "access_reason", "country", "language",
    "niches", "job_count_hint", "job_search_form", "employers_can_post", "job_alerts",
    "structured_data_jobposting", "rss_feed", "api_or_developers", "platform", "https",
    "response_seconds", "contact_emails", "social", "description", "discovered_via", "checked_at",
]


def load_overrides(path: Path) -> dict[str, str]:
    """Access values you checked by hand (CSV columns: key, access, note) — they win over the crawler."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {r["key"].strip(): r["access"].strip() for r in csv.DictReader(f) if r.get("key") and r.get("access")}


def export(store: Store, out_base: Path, include: set[str], verified_only: bool,
           overrides: dict[str, str] | None = None) -> tuple[int, Path, Path]:
    overrides = overrides or {}
    records = []
    for row in store.portals():
        access, source = row["access"], row["access_source"]
        if row["key"] in overrides:
            access, source = overrides[row["key"]], "manual"
        if access not in include:
            continue
        if verified_only and source == "seed_hint":
            continue
        d = json.loads(row["details"] or "{}")
        evidence = json.loads(row["evidence"] or "[]")
        records.append({
            **d,
            "url": d.get("url") or row["url"],
            "key": row["key"],
            "access": access,
            "access_source": source,
            "confidence": confidence(evidence) if source == "verified" else "",
            "access_reason": "; ".join(f"{e['verdict']}: {e['reason']}" for e in evidence)[:500],
            "access_evidence": evidence,
            "discovered_via": row["source"],
            "checked_at": row["checked_at"],
        })

    out_base.parent.mkdir(parents=True, exist_ok=True)
    json_path, csv_path = out_base.with_suffix(".json"), out_base.with_suffix(".csv")
    json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({k: _cell(r.get(k)) for k in CSV_COLUMNS})
    return len(records), csv_path, json_path


def _cell(v):
    if isinstance(v, list):
        return "; ".join(map(str, v))
    if isinstance(v, dict):
        return "; ".join(f"{k}={x}" for k, x in v.items())
    return v
