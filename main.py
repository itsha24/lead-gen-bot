import os

from config import JOB_KEYWORDS, JOB_SEARCH_LOCATIONS, MIN_FIT_SCORE
from job_search import search_jobs
from business_discovery import find_business_urls
from tech_checker import check_business_list
from matcher import score_jobs
from sheets_writer import (
    append_job_leads, append_tech_leads, get_existing_job_urls, get_existing_tech_urls,
)

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
    jobs = search_jobs(JOB_KEYWORDS, JOB_SEARCH_LOCATIONS)
    print(f"Found {len(jobs)} job leads.")

    already_logged = get_existing_job_urls(SHEET_ID) if SHEET_ID else set()
    jobs = [j for j in jobs if j["url"] not in already_logged]
    print(f"{len(jobs)} are new (skipped {len(already_logged)} already in the sheet).")

    jobs = score_jobs(jobs, min_score=MIN_FIT_SCORE)

    if SHEET_ID:
        append_job_leads(SHEET_ID, jobs)
    else:
        print("LEAD_SHEET_ID not set — printing instead:")
        for j in jobs:
            print(j)


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