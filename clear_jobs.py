"""
Clears the Job Matches list so you start fresh.

Keeps every job you've marked on the dashboard (Saved, Applied, Interviewing,
and Not interested, which are kept for measuring the AI's accuracy). Everything
else is removed but remembered as seen, so later runs don't score it again.

    python clear_jobs.py
"""

import json
from datetime import datetime

from jobs_store import LOCAL_TZ, STATUS_PATH, STORE_PATH, job_key, load_seen


def clear_jobs(path: str = STORE_PATH, status_path: str = STATUS_PATH) -> None:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    try:
        with open(status_path, encoding="utf-8") as f:
            marked = set(((json.load(f) or {}).get("jobs") or {}).keys())
    except (OSError, json.JSONDecodeError):
        marked = set()
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    seen = load_seen(path)
    keep = []
    for j in data.get("jobs", []):
        if j.get("url") in marked:
            keep.append(j)
            continue
        for k in (j.get("url"), job_key(j)):
            if k:
                seen.setdefault(k, today)
    print(f"[clear_jobs] Cleared {len(data.get('jobs', [])) - len(keep)} jobs; kept {len(keep)} you marked.")
    data["jobs"], data["seen"] = keep, seen
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    clear_jobs()
