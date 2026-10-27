"""
Reads job-alert emails (LinkedIn, Indeed, Glassdoor, Jobright...) and turns them
into jobs for the normal pipeline: dedupe, free filters, full posting, AI score.

How the emails get here:
  Outlook rule (redirect alerts) -> Gmail filter (label "job-alerts")
  -> a small Google Apps Script web app in your Gmail account (apps_script/)
  -> this module fetches it over HTTPS at the start of each run.

The script only has read-only Gmail access and only returns that one label.
Each email is read by the AI once (its id is remembered); the AI pulls out the
jobs whatever site sent it, so there's no per-site parser to break.

Needs GMAIL_ALERTS_URL (the web app's /exec URL with ?key=...). Without it, this
source is skipped.
"""

import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

MAX_EMAILS_PER_RUN = 10     # one AI call each; the rest wait for the next run
MAX_TEXT_CHARS = 14000      # a long LinkedIn alert is ~8k chars of text
TIMEOUT_SECONDS = 60
# Never treat the bot's own digest (or other non-alerts) as an alert.
NOT_ALERTS = re.compile(r"resend\.dev|job matcher|lead gen", re.IGNORECASE)
JOB_LINK = re.compile(r"job|viewjob|rc/clk|pagead|joblisting|/jobs?/", re.IGNORECASE)
JUNK_LINK = re.compile(r"unsubscribe|preferences|settings|privacy|help|terms|setting|feedback|"
                       r"manage|alert.*(edit|delete)|app\.(apple|google)|play\.google|apps\.apple", re.IGNORECASE)

EXTRACT_SYSTEM = """You extract job postings from a job-alert email (LinkedIn, Indeed, Glassdoor, Jobright or similar).
Links in the text appear as [L<number>] right after the text they belong to.
Return every distinct job listed in the email, and nothing else: skip ads, "jobs you may like" headers,
courses, people, salary tools and navigation links.
For each job give the title, company and location exactly as written (location empty if not shown),
the link id of the job's own link (the one on its title, or "View job"/"Apply"), and a short snippet with
anything else the email says about it (salary, "Easy Apply", posted time, remote/hybrid). Never invent details.
If the email lists no jobs, return an empty list."""

EXTRACT_TOOL = {
    "name": "record_jobs",
    "description": "Record the jobs listed in the alert email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "jobs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "company": {"type": "string"},
                        "location": {"type": "string"},
                        "link_id": {"type": "integer", "description": "The N of the job's [LN] link"},
                        "snippet": {"type": "string"},
                    },
                    "required": ["title", "company", "location", "link_id", "snippet"],
                },
            },
        },
        "required": ["jobs"],
    },
}


def site_of(sender: str) -> str:
    s = (sender or "").lower()
    for key, name in (("linkedin", "LinkedIn"), ("indeed", "Indeed"), ("glassdoor", "Glassdoor"),
                      ("jobright", "Jobright")):
        if key in s:
            return name
    m = re.search(r"@([\w-]+)\.", s)
    return m.group(1).title() if m else "Email"


def email_to_text(html: str) -> tuple[str, list[str]]:
    """Plain text with links marked [L1], [L2]... and the list of their URLs.
    The AI picks a link by number, so it never has to copy a long tracking URL."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["style", "script", "head", "img"]):
        tag.decompose()
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href, label = a["href"].strip(), a.get_text(" ", strip=True)
        if not href.startswith("http") or JUNK_LINK.search(href) or JUNK_LINK.search(label):
            a.unwrap()
            continue
        links.append(href)
        a.replace_with(f"{label} [L{len(links)}] ")
    text = re.sub(r"[ \t ‌͏]+", " ", soup.get_text("\n"))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:MAX_TEXT_CHARS], links


def clean_url(url: str) -> str:
    """Strip tracking so the same job from two alerts has the same link."""
    p = urlparse(url)
    host = p.netloc.lower()
    m = re.search(r"/jobs/view/(?:[\w-]*?-)?(\d{6,})", p.path)
    if "linkedin." in host and m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
    q = parse_qs(p.query)
    if "indeed." in host and q.get("jk"):
        return f"https://ca.indeed.com/viewjob?jk={q['jk'][0]}"
    if "jobright" in host:
        return urlunparse(p._replace(query="", fragment=""))
    keep = {k: v for k, v in q.items() if not k.lower().startswith(("utm_", "trk", "tracking", "ref", "src"))}
    return urlunparse(p._replace(query=urlencode(keep, doseq=True), fragment=""))


def _extract(provider: str, client, text: str) -> list[dict]:
    from matcher import claude_structured, gemini_structured
    user = f"<email>\n{text}\n</email>\n\nList the jobs in this email with the record_jobs tool."
    if provider == "gemini":
        out = gemini_structured(EXTRACT_SYSTEM, user, EXTRACT_TOOL["input_schema"])
    else:
        out = claude_structured(client, EXTRACT_SYSTEM, user, EXTRACT_TOOL, max_tokens=4096)
    return list(out.get("jobs") or [])


def fetch_messages(url: str) -> list[dict]:
    resp = requests.get(url, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("messages") or []


def fetch_alert_jobs(seen: set[str]) -> tuple[list[dict], list[str]]:
    """Jobs from alert emails not read before, and the ids of the emails read.
    Emails that couldn't be read (quota, errors) aren't returned, so they're retried."""
    url = os.environ.get("GMAIL_ALERTS_URL")
    if not url:
        print("[email_alerts] GMAIL_ALERTS_URL isn't set — skipping job-alert emails.")
        return [], []
    from matcher import QuotaExhausted, pick_provider
    provider = pick_provider()
    if not provider:
        print("[email_alerts] No AI key set — can't read job-alert emails.")
        return [], []
    try:
        messages = fetch_messages(url)
    except (requests.RequestException, ValueError, RuntimeError) as e:
        print(f"[email_alerts] Couldn't reach the Gmail script ({e}) — skipping alert emails this run.")
        return [], []

    new = [m for m in messages if f"email:{m.get('id')}" not in seen]
    new.sort(key=lambda m: m.get("date") or "", reverse=True)  # newest alerts first
    client = None
    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic()

    jobs, done, skipped = [], [], 0
    for m in new:
        if len(done) >= MAX_EMAILS_PER_RUN:
            break
        mid = f"email:{m['id']}"
        if m.get("error"):
            print(f"[email_alerts] The Gmail script couldn't read one email ({m['error'][:120]}) — skipping it.")
            continue
        if NOT_ALERTS.search(f"{m.get('from')} {m.get('subject')}"):
            done.append(mid)
            continue
        text, links = email_to_text(m.get("html") or "")
        if not any(JOB_LINK.search(l) for l in links):
            skipped += 1
            done.append(mid)  # no job links: a newsletter or notification, not an alert
            continue
        try:
            found = _extract(provider, client, text)
        except QuotaExhausted as e:
            print(f"[email_alerts] STOPPING: {e}")
            break
        except Exception as e:
            print(f"[email_alerts] Couldn't read '{m.get('subject')}': {e}")
            continue
        site = site_of(m.get("from"))
        n = 0
        for j in found:
            i = j.get("link_id")
            if not isinstance(i, int) or not 1 <= i <= len(links) or not (j.get("title") or "").strip():
                continue
            jobs.append({
                "source": f"{site} alert",
                "keyword_matched": "email alert",
                "title": j["title"].strip(),
                "company": (j.get("company") or "").strip() or "Unknown",
                "location": (j.get("location") or "").strip(),
                "url": clean_url(links[i - 1]),
                "description": (j.get("snippet") or "").strip(),
                "description_source": "snippet",
                "posted": m.get("date") or datetime.now(timezone.utc).isoformat(),  # alerts go out as jobs are posted
            })
            n += 1
        print(f"[email_alerts] {site}: {n} jobs in '{(m.get('subject') or '')[:70]}'.")
        done.append(mid)

    left = len(new) - len(done)
    print(f"[email_alerts] Read {len(done)} new alert emails ({len(jobs)} jobs)"
          + (f"; {skipped} had no job links" if skipped else "")
          + (f"; {left} wait for the next run." if left > 0 else "."))
    return jobs, done
