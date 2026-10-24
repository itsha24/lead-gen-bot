import os

from config import EMAIL_MIN_SCORE, JOB_KEYWORDS, JOB_SEARCH_LOCATIONS, MAX_JOBS_PER_RUN
from company_boards import fetch_company_jobs
from job_search import search_jobs
from business_discovery import find_business_urls
from tech_checker import check_business_list
from jobs_store import is_known, job_key, known_urls, load_jobs, save_jobs
from matcher import score_jobs
from notifier import send_job_digest
from sheets_writer import append_tech_leads, get_existing_tech_urls

SHEET_ID = os.environ.get("LEAD_SHEET_ID")
URLS_FILE = "businesses.txt"  # optional manual add-ons (referrals, etc.)

# "jobs_only" = just search job postings (fast, run daily)
# "full" = job postings + business discovery + tech checks (run weekly)
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
    send_job_digest(scored, min_score=EMAIL_MIN_SCORE)
    save_jobs(stored, scored)


def run_business_discovery():
    print("Discovering businesses via Google Places...")
    discovered = find_business_urls()
    discovered_urls = [d["website"] for d in discovered]
    print(f"Discovered {len(discovered_urls)} candidate businesses.")

    manual_urls = load_manual_urls()
    print(f"Loaded {len(manual_urls)} manually-added URLs from {URLS_FILE}.")

    all_candidate_urls = list(dict.fromkeys(discovered_urls + manual_urls))  # dedupe, keep order

    already_checked = get_existing_tech_urls(SHEET_ID) if SHEET_ID else set()
    new_urls = [u for u in all_candidate_urls if u not in already_checked]
    print(f"{len(new_urls)} new businesses to check (skipped {len(all_candidate_urls) - len(new_urls)} already logged).")

    tech_results = check_business_list(new_urls) if new_urls else []
    print(f"Checked {len(tech_results)} businesses.")

    if SHEET_ID:
        append_tech_leads(SHEET_ID, tech_results)
    else:
        for t in tech_results:
            print(t)


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