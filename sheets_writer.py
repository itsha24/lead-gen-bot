"""
Appends new leads to a Google Sheet using a service account.
Requires:
  - A Google Cloud service account with a JSON key
  - The target sheet shared (Editor access) with the service account's email
  - Two tabs in the sheet: "Job Leads" and "Tech Signals"
"""

import os
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from config import MIN_FIT_SCORE

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

JOB_LEADS_HEADER = [
    "Date Found", "Keyword Matched", "Title", "Company",
    "Location", "URL", "Description", "Posted", "Status",
    "Fit Score", "Seniority", "Matched Skills", "Missing Skills", "Reason",
]

TECH_SIGNALS_HEADER = [
    "Date Found", "URL", "Tech Detected", "PageSpeed Score",
    "Flag", "Error", "Status",
]


def get_client():
    creds_path = os.environ.get("GOOGLE_SHEETS_CREDS_PATH", "service_account.json")
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _ensure_header(worksheet, header):
    first_row = worksheet.row_values(1)
    if first_row != header:
        worksheet.update("A1", [header])


def get_existing_tech_urls(sheet_id):
    """Returns the set of URLs already logged in the Tech Signals tab, so
    discovery doesn't re-check (and re-add) the same business every run."""
    try:
        client = get_client()
        sheet = client.open_by_key(sheet_id).worksheet("Tech Signals")
        urls = sheet.col_values(2)  # column B = URL
        return set(urls[1:])  # skip header row
    except Exception as e:
        print(f"[sheets_writer] Could not read existing URLs (first run is normal): {e}")
        return set()


def get_existing_job_urls(sheet_id):
    """Returns the set of job URLs already in the Job Leads tab, so the same
    posting isn't re-scored (and re-added) every day."""
    try:
        client = get_client()
        sheet = client.open_by_key(sheet_id).worksheet("Job Leads")
        urls = sheet.col_values(6)  # column F = URL
        return set(urls[1:])  # skip header row
    except Exception as e:
        print(f"[sheets_writer] Could not read existing job URLs (first run is normal): {e}")
        return set()


def append_job_leads(sheet_id, leads):
    if not leads:
        print("[sheets_writer] No job leads to write.")
        return
    client = get_client()
    sheet = client.open_by_key(sheet_id).worksheet("Job Leads")
    _ensure_header(sheet, JOB_LEADS_HEADER)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [
        [
            today, l["keyword_matched"], l["title"], l["company"],
            l["location"], l["url"], (l["description"] or "")[:300], l["posted"],
            "Low fit" if isinstance(l.get("fit_score"), int) and l["fit_score"] < MIN_FIT_SCORE else "New",
            l.get("fit_score", ""), l.get("seniority", ""),
            ", ".join(l.get("matched_skills", [])),
            ", ".join(l.get("missing_required", [])),
            l.get("reason", ""),
        ]
        for l in leads
    ]
    sheet.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"[sheets_writer] Wrote {len(rows)} job leads.")


def append_tech_leads(sheet_id, results):
    if not results:
        print("[sheets_writer] No tech signal results to write.")
        return
    client = get_client()
    sheet = client.open_by_key(sheet_id).worksheet("Tech Signals")
    _ensure_header(sheet, TECH_SIGNALS_HEADER)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [
        [
            today, r["url"], ", ".join(r["tech_detected"]),
            r["pagespeed_score"] if r["pagespeed_score"] is not None else "",
            r["flag"], r.get("error") or "",
            "New",
        ]
        for r in results
    ]
    sheet.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"[sheets_writer] Wrote {len(rows)} tech signal rows.")
