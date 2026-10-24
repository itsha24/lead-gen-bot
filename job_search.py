"""
Searches Adzuna's job API for postings matching your job-search keywords.
Adzuna has a free developer tier: https://developer.adzuna.com/
"""

import os
import requests

from datetime import datetime, timedelta, timezone

from config import ADZUNA_COUNTRY, JOB_MAX_DAYS_OLD

ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY")


def _search_one(keyword, location, results_per_keyword, seen_urls):
    url = f"https://api.adzuna.com/v1/api/jobs/{ADZUNA_COUNTRY}/search/1"
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": results_per_keyword,
        "what": keyword,
        "max_days_old": JOB_MAX_DAYS_OLD,  # recent postings only
        "sort_by": "date",                 # newest first
        "content-type": "application/json",
    }
    if location:
        params["where"] = location

    results = []
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for job in data.get("results", []):
            job_url = job.get("redirect_url")
            if not job_url or job_url in seen_urls:
                continue
            seen_urls.add(job_url)
            results.append({
                "source": "Adzuna",
                "keyword_matched": keyword,
                "title": job.get("title"),
                "company": (job.get("company") or {}).get("display_name"),
                "location": (job.get("location") or {}).get("display_name") or "Not specified",
                "url": job_url,
                "description": job.get("description") or "",
                "posted": job.get("created"),
            })
    except requests.RequestException as e:
        print(f"[job_search] Error searching '{keyword}' in '{location or 'Canada-wide'}': {e}")

    return results


def search_jobs(keywords, locations=None, results_per_keyword=10):
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("[job_search] Missing ADZUNA_APP_ID / ADZUNA_APP_KEY env vars — skipping job search.")
        return []

    locations = locations if locations is not None else ["Montreal"]
    all_results = []
    seen_urls = set()

    for kw in keywords:
        for loc in locations:
            all_results.extend(_search_one(kw, loc, results_per_keyword, seen_urls))

    # Safety net in case the API ignores max_days_old: drop anything older ourselves.
    cutoff = datetime.now(timezone.utc) - timedelta(days=JOB_MAX_DAYS_OLD)
    fresh = [j for j in all_results if _posted_at(j) is None or _posted_at(j) >= cutoff]
    if len(fresh) < len(all_results):
        print(f"[job_search] Dropped {len(all_results) - len(fresh)} postings older than {JOB_MAX_DAYS_OLD} days.")
    return fresh


def _posted_at(job):
    try:
        return datetime.fromisoformat((job.get("posted") or "").replace("Z", "+00:00"))
    except ValueError:
        return None