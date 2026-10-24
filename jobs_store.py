"""
Stores scored job matches in docs/data/jobs.json, which the dashboard reads.

The GitHub Actions workflow commits this file after each run, so the dashboard
always shows the latest matches without needing a Google Sheet.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STORE_PATH = os.path.join("docs", "data", "jobs.json")
KEEP_DAYS = 60  # drop jobs found more than this many days ago to keep the file small
LOCAL_TZ = ZoneInfo("America/Toronto")  # dates on the dashboard use your local day

FIELDS = [
    "date_found", "title", "company", "location", "url", "posted", "keyword_matched",
    "fit_score", "seniority", "years_required", "matched_skills", "missing_required",
    "dealbreakers_hit", "reason", "description",
]


def load_jobs(path: str = STORE_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("jobs", [])
    except (json.JSONDecodeError, OSError) as e:
        print(f"[jobs_store] Could not read {path} ({e}) — starting fresh.")
        return []


def known_urls(jobs: list[dict]) -> set[str]:
    return {j["url"] for j in jobs if j.get("url")}


def save_jobs(existing: list[dict], new: list[dict], path: str = STORE_PATH) -> list[dict]:
    """Merge newly scored jobs into the store, drop old ones, and write the file."""
    now = datetime.now(LOCAL_TZ)
    today = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")

    seen = known_urls(existing)
    added = []
    for job in new:
        if job.get("url") in seen:
            continue
        record = {k: job.get(k) for k in FIELDS}
        record["date_found"] = today
        record["description"] = (record["description"] or "")[:300]
        added.append(record)
        seen.add(job["url"])

    kept = [j for j in existing if (j.get("date_found") or today) >= cutoff]
    jobs = added + kept
    jobs.sort(key=lambda j: (j.get("date_found") or "", j.get("fit_score") or 0), reverse=True)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "jobs": jobs},
            f, indent=1, ensure_ascii=False,
        )
    dropped = len(existing) - len(kept)
    print(f"[jobs_store] Added {len(added)} jobs; {len(jobs)} total"
          + (f" (dropped {dropped} older than {KEEP_DAYS} days)." if dropped else "."))
    return jobs
