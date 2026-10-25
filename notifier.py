"""
Emails a daily digest of the best new job matches.

Two ways to send (whichever EMAIL_APP_PASSWORD holds):
  - a Resend API key (starts with "re_") -> sent over HTTPS via resend.com.
    Free (100/day). Works from GitHub Actions, where Gmail SMTP is often blocked.
  - a Gmail app password -> sent via Gmail SMTP.

Requires (as env vars / GitHub secrets):
  EMAIL_ADDRESS       your Gmail address (the sender)
  EMAIL_APP_PASSWORD  a Gmail app password (myaccount.google.com/apppasswords),
                      NOT your normal Gmail password
  EMAIL_TO            optional — where to send it; defaults to EMAIL_ADDRESS

If they're missing, the email step is skipped and nothing else is affected.
"""

import html
import json
import os
import smtplib

import requests

from ranking import age_text, freshness_bonus, priority
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

DASHBOARD_URL = "https://itsha24.github.io/lead-gen-bot/"
STATUS_PATH = os.path.join("docs", "data", "status.json")
FOLLOW_UP_DAYS = (7, 14)  # remind about an application this many days after you marked it Applied
LOCAL_TZ = ZoneInfo("America/Toronto")


def follow_ups_due(tracked_jobs, today=None):
    """Applied jobs hitting day 7 or 14 today, unless you've marked them followed up."""
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            statuses = (json.load(f) or {}).get("jobs") or {}
    except (OSError, json.JSONDecodeError):
        return []
    today = today or datetime.now(LOCAL_TZ).date()
    due = []
    for job in tracked_jobs or []:
        v = statuses.get(job.get("url"))
        if not isinstance(v, dict) or v.get("s") != "Applied" or not v.get("t") or v.get("f"):
            continue
        applied = datetime.fromisoformat(v["t"].replace("Z", "+00:00")).astimezone(LOCAL_TZ).date()
        days = (today - applied).days
        if days in FOLLOW_UP_DAYS:
            due.append({**job, "_days": days})
    return due


def _job_lines_text(jobs):
    lines = []
    for j in jobs:
        age = age_text(j.get("posted"))
        lines.append(f"{j['fit_score']} — {j.get('title')} @ {j.get('company')} ({j.get('location')})"
                     + (f" — {age}" if age else ""))
        if j.get("reason"):
            lines.append(f"   {j['reason']}")
        if j.get("missing_required"):
            lines.append(f"   Gaps: {', '.join(j['missing_required'])}")
        lines.append(f"   {j.get('url')}")
        lines.append("")
    return "\n".join(lines)


def _age_html(job):
    age = age_text(job.get("posted"))
    if not age:
        return ""
    fresh = freshness_bonus(job.get("posted")) >= 12
    style = "color:#1f7a4d;font-weight:600" if fresh else "color:#555"
    return f' · <span style="{style}">{html.escape(age)}</span>'


def _job_cards_html(jobs):
    cards = []
    for j in jobs:
        e = lambda v: html.escape(str(v or ""))
        gaps = (
            f'<div style="color:#8a5a00;font-size:13px;margin-top:4px">Gaps: {e(", ".join(j["missing_required"]))}</div>'
            if j.get("missing_required") else ""
        )
        cards.append(f"""
        <div style="border:1px solid #e3e3e3;border-radius:8px;padding:14px 16px;margin:0 0 12px">
          <div style="font-size:16px;font-weight:600">
            <span style="background:#1f7a4d;color:#fff;border-radius:4px;padding:1px 7px;margin-right:6px">{e(j['fit_score'])}</span>
            {e(j.get('title'))}
          </div>
          <div style="color:#555;font-size:14px;margin-top:4px">{e(j.get('company'))} · {e(j.get('location'))}{_age_html(j)}</div>
          <div style="font-size:14px;margin-top:8px">{e(j.get('reason'))}</div>
          {gaps}
          <div style="margin-top:10px"><a href="{e(j.get('url'))}" style="color:#1a5fb4;font-weight:600">View posting →</a></div>
        </div>""")
    return "".join(cards)


def _follow_up_html(due):
    e = lambda v: html.escape(str(v or ""))
    rows = "".join(f"""<li style="margin:0 0 8px"><a href="{e(j.get('url'))}" style="color:#1a5fb4;font-weight:600">{e(j.get('title'))}</a>
        · {e(j.get('company'))} — applied {j['_days']} days ago</li>""" for j in due)
    return f"""<h3 style="margin:22px 0 6px">Follow up today</h3>
      <div style="color:#666;margin:0 0 8px">No news yet? A short note to a recruiter helps. Drafts are on the dashboard under My Applications → Reach out.</div>
      <ul style="padding-left:18px;margin:0">{rows}</ul>"""


def send_job_digest(jobs, min_score, min_fit=0, tracked_jobs=None):
    """Email jobs scoring >= min_score. Skips quietly if none, or if email isn't configured."""
    # Secrets pasted into GitHub often carry stray spaces/newlines, and Google shows
    # app passwords as "abcd efgh ijkl mnop" — normalise both.
    sender = (os.environ.get("EMAIL_ADDRESS") or "").strip()
    password = "".join((os.environ.get("EMAIL_APP_PASSWORD") or "").split())
    if not sender or not password:
        print("[notifier] EMAIL_ADDRESS / EMAIL_APP_PASSWORD not set — skipping email.")
        return

    top = sorted(
        (j for j in jobs if isinstance(j.get("fit_score"), int) and j.get("seniority") != "intern"
         and j["fit_score"] >= min_fit and priority(j) >= min_score),
        key=priority, reverse=True,
    )
    # Follow-up reminders go out once (morning run only) on day 7 and day 14.
    due = follow_ups_due(tracked_jobs) if datetime.now(LOCAL_TZ).hour < 12 else []
    if not top and not due:
        print(f"[notifier] No new jobs reached priority {min_score}+ (fit + freshness) and no follow-ups due — no email today.")
        return

    count = f"{len(top)} new job match{'es' if len(top) != 1 else ''}"
    fresh = sum(1 for j in top if freshness_bonus(j.get("posted")) >= 12)
    parts = []
    if top:
        parts.append(count + (f", {fresh} posted today" if fresh else ""))
    if due:
        parts.append(f"{len(due)} follow-up{'s' if len(due) != 1 else ''} due")
    msg = EmailMessage()
    msg["Subject"] = f"{' · '.join(parts)} — {datetime.now(LOCAL_TZ).strftime('%b %d')}"
    msg["From"] = sender
    msg["To"] = (os.environ.get("EMAIL_TO") or "").strip() or sender
    text = ""
    if top:
        text += f"{count}, best first (fit score + a boost for fresh postings):\n\n{_job_lines_text(top)}\n"
    if due:
        text += "Follow up today (no news yet):\n" + "".join(
            f"  - {j.get('title')} @ {j.get('company')}, applied {j['_days']} days ago: {j.get('url')}\n" for j in due)
    msg.set_content(text + f"\nDashboard: {DASHBOARD_URL}\n")
    matches_html = (f"""<h2 style="margin:0 0 4px">{html.escape(count)}</h2>
      <div style="color:#666;margin:0 0 16px">Best first — fit score plus a boost for fresh postings. Apply to the green ones first.</div>
      {_job_cards_html(top)}""" if top else "")
    msg.add_alternative(f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:600px;color:#222">
      {matches_html}
      {_follow_up_html(due) if due else ""}
      <div style="margin-top:18px;font-size:13px;color:#666">
        <a href="{DASHBOARD_URL}" style="color:#1a5fb4">Open the dashboard</a>
      </div>
    </div>""", subtype="html")

    if password.startswith("re_"):
        _send_resend(password, msg, f"{len(top)} jobs and {len(due)} follow-ups")
        return

    for name, send in (("SSL :465", _send_ssl), ("STARTTLS :587", _send_starttls)):
        stage = ["connecting"]
        try:
            send(sender, password, msg, stage)
            print(f"[notifier] Emailed {len(top)} jobs and {len(due)} follow-ups to {msg['To']} (via Gmail {name}).")
            return
        except smtplib.SMTPAuthenticationError as e:
            print(f"[notifier] Email failed: Gmail rejected the login ({e.smtp_code}). "
                  "Check EMAIL_ADDRESS is the full Gmail address and EMAIL_APP_PASSWORD is a "
                  "16-letter app password from myaccount.google.com/apppasswords, not your normal password.")
            return
        except Exception as e:  # never let email break the run
            print(f"[notifier] Gmail {name} failed while {stage[0]}: {type(e).__name__}: {e}")
    print("[notifier] Email failed on both Gmail ports. Gmail often blocks GitHub's servers — "
          "put a free Resend API key (resend.com) in the EMAIL_APP_PASSWORD secret instead.")


def _send_resend(api_key, msg, count):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": "Job Matcher <onboarding@resend.dev>",
            "to": [msg["To"]],
            "subject": msg["Subject"],
            "html": msg.get_body(("html",)).get_content(),
            "text": msg.get_body(("plain",)).get_content(),
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        print(f"[notifier] Emailed {count} to {msg['To']} (via Resend).")
    elif resp.status_code == 403:
        print(f"[notifier] Resend refused (403): {resp.text[:200]}. Without your own domain, Resend only "
              "sends to the address you signed up with — set EMAIL_ADDRESS (or EMAIL_TO) to that address.")
    else:
        print(f"[notifier] Resend failed (HTTP {resp.status_code}): {resp.text[:200]}")


def _send_ssl(sender, password, msg, stage):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        stage[0] = "logging in"
        smtp.login(sender, password)
        stage[0] = "sending"
        smtp.send_message(msg)


def _send_starttls(sender, password, msg, stage):
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        stage[0] = "starting encryption"
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        stage[0] = "logging in"
        smtp.login(sender, password)
        stage[0] = "sending"
        smtp.send_message(msg)


if __name__ == "__main__":
    # Send a test email with one sample job:  python notifier.py
    # Uses the same EMAIL_ADDRESS / EMAIL_APP_PASSWORD env vars as the daily run.
    send_job_digest([{
        "fit_score": 99, "title": "Test email — your job matcher works", "company": "Job Matcher",
        "location": "Montréal, QC", "seniority": "junior", "missing_required": [],
        "reason": "If you can read this, daily emails for 85+ matches will arrive here.",
        "url": DASHBOARD_URL,
    }], min_score=85)
