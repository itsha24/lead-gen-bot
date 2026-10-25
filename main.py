import os
import time
from urllib.parse import urlparse

from business_discovery import find_businesses
from company_boards import fetch_company_jobs
from config import (
    EMAIL_MIN_FIT, EMAIL_MIN_SCORE, JOB_KEYWORDS, JOB_SEARCH_LOCATIONS, MAX_JOBS_PER_RUN, MAX_LEADS_PER_RUN,
    RUN_BUDGET_MINUTES,
)
from job_search import search_jobs
from jobs_store import is_known, job_key, known_urls, load_jobs, save_jobs, tracked_urls
from lead_scorer import score_leads
from leads_store import known_keys, lead_key, load_leads, save_leads
from matcher import draft_outreach, score_jobs
from notifier import send_job_digest
from site_audit import audit_businesses

URLS_FILE = "businesses.txt"  # optional manual add-ons (referrals, etc.)

# "jobs_only" = job matching only (daily)
# "full" = job matching + client leads: find businesses, audit their sites, score, draft outreach (weekly)
MODE = os.environ.get("MODE", "full")

# The workflow kills a run at 30 minutes and anything unsaved is lost, so each step
# stops scoring early and saves. In a full run, jobs get the first part of the budget.
START = time.monotonic()
DEADLINE = START + RUN_BUDGET_MINUTES * 60
JOBS_DEADLINE = START + (RUN_BUDGET_MINUTES * 60 * 0.45 if MODE == "full" else RUN_BUDGET_MINUTES * 60)


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

    scored = score_jobs(jobs, max_jobs=MAX_JOBS_PER_RUN, deadline=JOBS_DEADLINE)
    # Only AI-scored jobs are saved; unscored ones get another chance next run.
    scored = [j for j in scored if isinstance(j.get("fit_score"), int)]
    tracked = tracked_urls()
    draft_outreach([j for j in stored if j.get("url") in tracked], deadline=JOBS_DEADLINE)
    send_job_digest(scored, min_score=EMAIL_MIN_SCORE, min_fit=EMAIL_MIN_FIT, tracked_jobs=[j for j in stored if j.get("url") in tracked])
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

    # Rank on Google's details first and only audit the most promising, instead of every site.
    def promise(b):
        site = (b.get("website") or "").lower()
        no_real_site = not site or any(x in site for x in ("facebook.", "instagram.", "linktr.ee"))
        reviews = b.get("reviews") or 0
        return (no_real_site, min(reviews, 1500), b.get("rating") or 0)
    fresh.sort(key=promise, reverse=True)
    to_audit = fresh[:MAX_LEADS_PER_RUN * 2]
    if len(fresh) > len(to_audit):
        print(f"Auditing the {len(to_audit)} most promising (no website first, then most established).")

    audit_businesses(to_audit)
    scored = score_leads(to_audit, max_leads=MAX_LEADS_PER_RUN, deadline=DEADLINE)
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