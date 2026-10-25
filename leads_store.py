"""
Stores scored business leads in docs/data/leads.json for the dashboard's
Client Leads tab (committed by the workflow, like jobs.json).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

STORE_PATH = os.path.join("docs", "data", "leads.json")
STATUS_PATH = os.path.join("docs", "data", "status.json")  # written by the dashboard
KEEP_FOREVER = {"Contacted", "Replied", "Won"}            # leads you're working are never pruned
KEEP_DAYS = 120  # outreach is slower than job hunting; keep leads around longer
LOCAL_TZ = ZoneInfo("America/Toronto")

FIELDS = [
    "date_found", "place_id", "name", "category", "address", "phone", "website", "rating", "reviews",
    "maps_url", "matched_query", "lead_score", "best_offer", "why", "opportunities", "language",
    "email_subject", "message",
]
AUDIT_FIELDS = ["emails", "issues", "platform", "ordering_booking", "socials", "copyright_year", "https"]


def _offer_names() -> dict:
    try:
        import yaml
        with open("services.yaml", encoding="utf-8") as f:
            return {o["id"]: o["name"] for o in (yaml.safe_load(f) or {}).get("offers") or []}
    except Exception:
        return {}


def tracked_keys(path: str | None = None) -> set[str]:
    """Dashboard keys (place id, else website, else name) of leads in Outreach."""
    try:
        with open(path or STATUS_PATH, encoding="utf-8") as f:
            leads = (json.load(f) or {}).get("leads") or {}
    except (OSError, json.JSONDecodeError):
        return set()
    status = lambda v: v.get("s") if isinstance(v, dict) else v
    return {k for k, v in leads.items() if status(v) in KEEP_FOREVER}


def _dashboard_key(lead: dict) -> str:
    return lead.get("place_id") or lead.get("website") or lead.get("name") or ""


def lead_key(b: dict) -> str:
    if b.get("place_id"):
        return f"place:{b['place_id']}"
    host = urlparse(b.get("website") or "").netloc.lower().removeprefix("www.")
    return f"site:{host}" if host else f"name:{(b.get('name') or '').lower()}"


def load_leads(path: str | None = None) -> list[dict]:
    path = path or STORE_PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("leads", [])
    except (json.JSONDecodeError, OSError) as e:
        print(f"[leads_store] Could not read {path} ({e}) — starting fresh.")
        return []


def known_keys(leads: list[dict]) -> set[str]:
    keys = {lead_key(l) for l in leads}
    keys |= {f"site:{urlparse(l['website']).netloc.lower().removeprefix('www.')}" for l in leads if l.get("website")}
    return keys


def save_leads(existing: list[dict], new: list[dict], path: str | None = None) -> list[dict]:
    path = path or STORE_PATH
    now = datetime.now(LOCAL_TZ)
    today, cutoff = now.strftime("%Y-%m-%d"), (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    seen = known_keys(existing)
    added = []
    for b in new:
        if lead_key(b) in seen:
            continue
        rec = {k: b.get(k) for k in FIELDS}
        audit = b.get("audit") or {}
        rec.update({k: audit.get(k) for k in AUDIT_FIELDS})
        rec["date_found"] = today
        added.append(rec)
        seen.add(lead_key(b))
    keep = tracked_keys()
    kept = [l for l in existing if _dashboard_key(l) in keep or (l.get("date_found") or today) >= cutoff]
    leads = sorted(added + kept, key=lambda l: (l.get("date_found") or "", l.get("lead_score") or 0), reverse=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "offers": _offer_names(), "leads": leads}, f, indent=1, ensure_ascii=False)
    print(f"[leads_store] Added {len(added)} leads; {len(leads)} total.")
    return leads
