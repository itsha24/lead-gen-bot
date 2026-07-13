# Lead Gen Bot

Finds two kinds of leads automatically and writes them into a Google Sheet:

1. **Job Leads** — job postings (via Adzuna) where a business is asking for
   help with automation, integrations, or web dev.
2. **Tech Signals** — businesses (auto-discovered via Google Places, plus any
   you add manually) whose sites show signs worth pitching: slow load times,
   a CRM with no visible automation, or no modern tooling at all.

Business discovery, checking, and logging all run automatically — no manual
URL collecting required. Runs on a schedule via GitHub Actions, no server or
laptop needs to stay on. Each business is only checked/logged once; the
script reads your Sheet each run to skip anything already there.

## 1. One-time setup

### a) Adzuna API (free)
1. Sign up at https://developer.adzuna.com/
2. Grab your `app_id` and `app_key`.

### a2) Google Places API (business discovery)
1. In Google Cloud Console (same project as below is fine), enable the
   **Places API (New)**.
2. Create an API key (APIs & Services → Credentials → Create Credentials →
   API Key). Restrict it to the Places API for safety.
3. Free tier covers a solid volume per month; edit `DISCOVERY_QUERIES` and
   `DISCOVERY_MAX_RESULTS_PER_QUERY` in `config.py` to control how much you
   pull per run and keep it within the free tier.

### b) Google Sheet + service account
1. Create a new Google Sheet. Add two tabs named exactly `Job Leads` and
   `Tech Signals` (the script will fill in headers automatically).
2. Copy the Sheet ID from its URL:
   `https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_ID/edit`
3. In Google Cloud Console: create a project → enable the **Google Sheets
   API** → create a **Service Account** → create a JSON key for it.
4. Share your Google Sheet with the service account's email address
   (found in the JSON key file, looks like
   `something@project-id.iam.gserviceaccount.com`), giving it **Editor**
   access.

### c) (Optional) PageSpeed API key
For the "slow site" flag to work, get a free key from Google Cloud Console
(enable the PageSpeed Insights API) and put it in `config.py`. Skip this and
the script still works, just without speed scoring.

## 2. (Optional) Add manual referral URLs

`businesses.txt` is now optional — discovery via Google Places handles
finding businesses automatically. Use `businesses.txt` only for specific
referrals you want checked that discovery might not surface (e.g. a business
Kosta mentions). One URL per line, `#` for comments.

## 3. Running it

### Locally (to test)
```bash
pip install -r requirements.txt

export ADZUNA_APP_ID=your_id
export ADZUNA_APP_KEY=your_key
export LEAD_SHEET_ID=your_sheet_id
export GOOGLE_SHEETS_CREDS_PATH=service_account.json  # path to the JSON key

python main.py
```

If you don't set `LEAD_SHEET_ID`, results print to the console instead of
writing to a sheet — useful for testing.

### Automatically (GitHub Actions — recommended)
1. Push this folder to a new GitHub repo (can be private).
2. In the repo, go to **Settings → Secrets and variables → Actions** and add:
   - `ADZUNA_APP_ID`
   - `ADZUNA_APP_KEY`
   - `GOOGLE_PLACES_API_KEY`
   - `LEAD_SHEET_ID`
   - `GOOGLE_SHEETS_CREDS_JSON` — paste the **entire contents** of your
     service account JSON key file as the value
3. That's it. It'll run every Monday at 9am ET automatically (edit the cron
   line in `.github/workflows/lead_gen.yml` to change the schedule), and you
   can also trigger it manually from the repo's **Actions** tab any time.

## 4. Tuning it

- `config.py` — edit `JOB_KEYWORDS`, `JOB_SEARCH_LOCATION`, `TECH_SIGNATURES`,
  and especially `DISCOVERY_QUERIES` (which categories/areas to search) as
  you learn what actually converts into replies.
- Every new row lands with status "New" — use the sheet itself as your
  tracker: update status to "Contacted," "Replied," "Ghosted," etc. as you
  work leads, same as a lightweight CRM.
- Widen or narrow `DISCOVERY_QUERIES` to control lead volume — more queries
  = more businesses found per run = more PageSpeed/tech-check calls.

## Notes

- Adzuna, Google Places, and PageSpeed are all official, ToS-compliant
  APIs — no scraping risk, no blocked-IP risk.
- Discovery only searches the categories/areas you configure in
  `DISCOVERY_QUERIES` — it's not unlimited, so it stays within free-tier
  usage as long as you keep the query list reasonably sized.
- Each business is checked once — the script reads existing Sheet rows
  before checking, so re-runs won't re-add duplicates.
