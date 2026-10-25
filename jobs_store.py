"""
Stores scored job matches in docs/data/jobs.json, which the dashboard reads.

The GitHub Actions workflow commits this file after each run, so the dashboard
always shows the latest matches without needing a Google Sheet.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STORE_PATH = os.path.join("docs", "data", "jobs.json")
STATUS_PATH = os.path.join("docs", "data", "status.json")  # written by the dashboard
KEEP_FOREVER = {"Saved", "Applied", "Interviewing"}       # jobs you're tracking are never pruned
KEEP_DAYS = 60          # drop jobs found more than this many days ago
MAX_POSTING_AGE_DAYS = 30  # drop jobs whose posting is older than this — likely filled or closed
LOCAL_TZ = ZoneInfo("America/Toronto")  # dates on the dashboard use your local day

FIELDS = [
    "date_found", "title", "company", "location", "url", "posted", "keyword_matched",
    "fit_score", "seniority", "years_required", "matched_skills", "missing_required",
    "dealbreakers_hit", "reason", "description", "description_source", "source",
    "outreach_note", "outreach_subject", "outreach_email",
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


def tracked_urls(path: str = STATUS_PATH) -> set[str]:
    """URLs of jobs you've marked Saved / Applied / Interviewing on the dashboard."""
    try:
        with open(path, encoding="utf-8") as f:
            jobs = (json.load(f) or {}).get("jobs") or {}
    except (OSError, json.JSONDecodeError):
        return set()
    status = lambda v: v.get("s") if isinstance(v, dict) else v
    return {url for url, v in jobs.items() if status(v) in KEEP_FOREVER}


def _is_internship(job: dict) -> bool:
    from matcher import INTERNSHIP_TITLE
    return job.get("seniority") == "intern" or bool(INTERNSHIP_TITLE.search(job.get("title") or ""))


def job_key(job: dict) -> str:
    """Same title at the same company = same job, even if posted under different links."""
    norm = lambda v: " ".join(re.findall(r"\w+", (v or "").lower()))
    return f"{norm(job.get('title'))}|{norm(job.get('company'))}"


def known_urls(jobs: list[dict]) -> set[str]:
    """URLs and title+company keys of jobs already stored."""
    return {j["url"] for j in jobs if j.get("url")} | {job_key(j) for j in jobs}


def is_known(job: dict, known: set[str]) -> bool:
    return job.get("url") in known or job_key(job) in known


def save_jobs(existing: list[dict], new: list[dict], path: str = STORE_PATH) -> list[dict]:
    """Merge newly scored jobs into the store, drop old ones, and write the file."""
    now = datetime.now(LOCAL_TZ)
    today = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")

    seen = known_urls(existing)
    added = []
    for job in new:
        if is_known(job, seen):
            continue
        record = {k: job.get(k) for k in FIELDS}
        record["date_found"] = today
        record["description"] = (record["description"] or "")[:300]
        added.append(record)
        seen |= {job["url"], job_key(job)}

    posted_cutoff = (now - timedelta(days=MAX_POSTING_AGE_DAYS)).strftime("%Y-%m-%d")
    keep = tracked_urls()
    kept = [j for j in existing if j.get("url") in keep
            or ((j.get("date_found") or today) >= cutoff and (j.get("posted") or today)[:10] >= posted_cutoff)]
    jobs, keys = [], set()
    for j in sorted(added + kept, key=lambda j: j.get("fit_score") or 0, reverse=True):
        if _is_internship(j) or job_key(j) in keys:  # internships are never shown; repeats add nothing
            continue
        keys.add(job_key(j))
        jobs.append(j)
    jobs.sort(key=lambda j: (j.get("date_found") or "", j.get("fit_score") or 0), reverse=True)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "jobs": jobs},
            f, indent=1, ensure_ascii=False,
        )
    dropped = len(existing) - len(kept)
    print(f"[jobs_store] Added {len(added)} jobs; {len(jobs)} total"
          + (f" (dropped {dropped} stale: posted over {MAX_POSTING_AGE_DAYS} days ago or found over {KEEP_DAYS} days ago)." if dropped else "."))
    return jobs
