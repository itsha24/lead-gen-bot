"""
Searches Adzuna's job API for postings matching automation/web-dev keywords.
Adzuna has a free developer tier: https://developer.adzuna.com/
"""

import os
import requests

from config import ADZUNA_COUNTRY

ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY")


def search_jobs(keywords, location="Montreal", results_per_keyword=10):
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("[job_search] Missing ADZUNA_APP_ID / ADZUNA_APP_KEY env vars — skipping job search.")
        return []

    all_results = []
    seen_urls = set()

    for kw in keywords:
        url = f"https://api.adzuna.com/v1/api/jobs/{ADZUNA_COUNTRY}/search/1"
        params = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "results_per_page": results_per_keyword,
            "what": kw,
            "where": location,
            "content-type": "application/json",
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for job in data.get("results", []):
                job_url = job.get("redirect_url")
                if not job_url or job_url in seen_urls:
                    continue
                seen_urls.add(job_url)
                all_results.append({
                    "source": "Adzuna",
                    "keyword_matched": kw,
                    "title": job.get("title"),
                    "company": (job.get("company") or {}).get("display_name"),
                    "location": (job.get("location") or {}).get("display_name"),
                    "url": job_url,
                    "description": (job.get("description") or "")[:300],
                    "posted": job.get("created"),
                })
        except requests.RequestException as e:
            print(f"[job_search] Error searching '{kw}': {e}")

    return all_results
