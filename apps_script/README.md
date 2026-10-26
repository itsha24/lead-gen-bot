# Job-alert emails → Lead Gen Bot

Your LinkedIn / Indeed / Glassdoor / Jobright alerts become a job source like
Adzuna: deduped, filtered, scored, and shown on the dashboard with a
`LinkedIn alert`-style tag.

```
Outlook rule (redirect alerts) → Gmail label "job-alerts" → this Apps Script → daily GitHub run
```

The script runs in your own Google account, has **read-only** Gmail access, and
only returns emails under the `job-alerts` label from the last 3 days.

## 1. Outlook: send the alerts to Gmail

outlook.com → ⚙ Settings → Mail → **Rules** → Add new rule
- Name: `Job alerts to Gmail`
- Condition: **From** → the sender of each alert. Open one alert from each site and copy its exact address
  (usually `jobalerts-noreply@linkedin.com`, `alert@indeed.com`, `noreply@glassdoor.com`).
- Action: **Redirect to** → `awanha98@gmail.com` (Redirect, not Forward, keeps the original sender so Gmail can sort it).
- Save. You'll still get them in Outlook too.

## 2. Gmail: label them

Gmail (awanha98@gmail.com) → search bar → ⚙ "Show search options" → **From**:
```
jobalerts-noreply@linkedin.com OR alert@indeed.com OR noreply@glassdoor.com OR jobright.ai
```
(use the exact addresses from step 1) → **Create filter** → ✅ **Apply the label** → New label `job-alerts`
→ optionally ✅ Skip the Inbox → ✅ Also apply to matching conversations → **Create filter**.

## 3. The script

1. Go to https://script.google.com signed in as **awanha98@gmail.com** → **New project** → name it `Job alerts reader`.
2. ⚙ **Project Settings** → ✅ *Show "appsscript.json" manifest file in editor*.
3. Back in the editor (`< >`): replace `Code.gs` with [Code.gs](Code.gs) and `appsscript.json` with [appsscript.json](appsscript.json). Save.
4. In the function dropdown pick **setup** → **Run** → Review permissions → your account →
   "Google hasn't verified this app" → **Advanced** → **Go to Job alerts reader**. (It's your own script; the only
   permission it asks for is *View your email messages and settings*.)
   The log shows a **Key** and how many alert emails it can see.
5. **Deploy** → **New deployment** → ⚙ **Web app** → Execute as: **Me** · Who has access: **Anyone** → **Deploy** →
   copy the **Web app URL** (ends in `/exec`).
6. Check it: open `<Web app URL>?key=<Key>` in your browser. You should see JSON starting with `{"messages":`.

## 4. GitHub

lead-gen-bot → Settings → Secrets and variables → Actions → **New repository secret**
- Name: `GMAIL_ALERTS_URL`
- Value: `<Web app URL>?key=<Key>`

Then Actions → Daily Job Matcher → Run workflow (`jobs_only`). The log shows lines starting `[email_alerts]`.

**Keep the URL + key private**: with both, someone could read the alert emails (only that label). To change the
key: Project Settings → Script properties → delete `KEY` → run `setup` again → update the secret.
If you edit Code.gs later, use Deploy → **Manage deployments** → ✏ → Version: *New version*, so the URL stays the same.
