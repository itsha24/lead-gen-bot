import os
from urllib.parse import urlparse

from business_discovery import find_businesses
from company_boards import fetch_company_jobs
from config import (
    EMAIL_MIN_FIT, EMAIL_MIN_SCORE, JOB_KEYWORDS, JOB_SEARCH_LOCATIONS, MAX_JOBS_PER_RUN, MAX_LEADS_PER_RUN,
)
from job_search import search_jobs
from jobs_store import is_known, job_key, known_urls, load_jobs, save_jobs
from lead_scorer import score_leads
from leads_store import known_keys, lead_key, load_leads, save_leads
from matcher import score_jobs
from notifier import send_job_digest
from site_audit import audit_businesses

URLS_FILE = "businesses.txt"  # optional manual add-ons (referrals, etc.)

# "jobs_only" = job matching only (daily)
# "full" = job matching + client leads: find businesses, audit their sites, score, draft outreach (weekly)
MODE = os.environ.get("MODE", "full")


def load_manual_urls():
    if not os.path.exists(URLS_FILE):
        return []
    with open(URLS_FILE) as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]


def run_job_search():
    print("Searching job postings...")
    company_jobs = fetch_company_jobs()  # first, so a job on both keeps the direct link
    adzuna_jobs = search_jobs(JOB_KEYWORDS, JOB_SEARCH_LOCATIONS)
    jobs = company_jobs + adzuna_jobs
    print(f"Found {len(jobs)} job leads ({len(company_jobs)} from company career pages, {len(adzuna_jobs)} from Adzuna).")

    stored = load_jobs()
    known = known_urls(stored)
    fresh, seen_now = [], set()
    for j in jobs:  # skip jobs already on the dashboard, and repeat postings within this run
        if is_known(j, known) or job_key(j) in seen_now:
            continue
        seen_now.add(job_key(j))
        fresh.append(j)
    print(f"{len(fresh)} are new (skipped {len(jobs) - len(fresh)} already seen or duplicate postings).")
    jobs = fresh

    scored = score_jobs(jobs, max_jobs=MAX_JOBS_PER_RUN)
    # Only AI-scored jobs are saved; unscored ones get another chance next run.
    scored = [j for j in scored if isinstance(j.get("fit_score"), int)]
    send_job_digest(scored, min_score=EMAIL_MIN_SCORE, min_fit=EMAIL_MIN_FIT)
    save_jobs(stored, scored)


def run_business_discovery():
    print("Finding business leads...")
    businesses = find_businesses()
    manual = [{"name": urlparse(u).netloc.removeprefix("www.") or u, "website": u, "matched_query": "businesses.txt"}
              for u in load_manual_urls()]
    if manual:
        print(f"Loaded {len(manual)} manually-added websites from {URLS_FILE}.")

    stored = load_leads()
    known = known_keys(stored)
    fresh = [b for b in businesses + manual if lead_key(b) not in known]
    print(f"{len(fresh)} new businesses (skipped {len(businesses) + len(manual) - len(fresh)} already on the dashboard).")
    if not fresh:
        return

    audit_businesses(fresh)
    scored = score_leads(fresh, max_leads=MAX_LEADS_PER_RUN)
    save_leads(stored, scored)


def main():
    print(f"Running in MODE={MODE}")
    run_job_search()
    if MODE == "full":
        run_business_discovery()
    else:
        print("Skipping business discovery (jobs_only mode).")
    print("Done.")


if __name__ == "__main__":
    main()