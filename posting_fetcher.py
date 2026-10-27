"""
Fetches the full text of job postings.

Adzuna's API only returns a ~500 character snippet, which makes AI scores vague
("too truncated to verify seniority"). This opens each posting's link and pulls
out the full description:

  1. schema.org JobPosting data (JSON-LD) — most job sites and ATSs embed it
  2. otherwise, the page text starting where the Adzuna snippet appears

If neither works (login wall, blocked, cookie page), the snippet is kept, so a
bad page can never make a score worse.
"""

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

MAX_CHARS = 6000          # plenty for scoring; keeps AI requests small
MIN_GAIN_CHARS = 200      # only use the page if it adds real detail over the snippet
TIMEOUT_SECONDS = 12
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.8",
}


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _html_to_text(fragment: str) -> str:
    return _clean_text(BeautifulSoup(fragment or "", "html.parser").get_text(" "))


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", (text or "").lower())


def _from_json_ld(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types and item.get("description"):
                parts = [_html_to_text(item["description"])]
                for key, label in (("qualifications", "Qualifications"), ("skills", "Skills"),
                                   ("experienceRequirements", "Experience")):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        parts.append(f"{label}: {_html_to_text(val)}")
                return " ".join(parts)
    return None


def _from_page_text(soup: BeautifulSoup, snippet: str) -> str | None:
    """Find where the snippet starts in the page and take the text from there."""
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "svg"]):
        tag.decompose()
    page = _clean_text(soup.get_text(" "))
    snippet_words = _words(snippet)[:8]
    if len(snippet_words) < 5:
        return None
    # Match the first few snippet words, allowing any punctuation/whitespace between them.
    pattern = r"\W+".join(re.escape(w) for w in snippet_words)
    match = re.search(pattern, page, flags=re.IGNORECASE)
    if not match:
        return None  # page doesn't contain this job's text (cookie wall, search page, etc.)
    return page[match.start():]


# Banners job sites show once a posting is taken down. Only checked near the top of the
# page, so a "similar jobs" list further down can't make a live job look closed.
CLOSED = re.compile(
    r"no longer accepting applications|(?:this|the) (?:job|position|posting|role|opportunity) "
    r"(?:is|has) (?:no longer (?:available|open|active|accepting)|expired|been filled|been closed|closed)"
    r"|(?:job|posting|position) (?:has )?expired|this job is closed|job (?:is )?no longer available"
    r"|n'accepte plus de candidatures|(?:ce poste|cette offre|l'offre)(?: d'emploi)? (?:n'est plus disponible|a expiré|est expirée|est fermée)",
    re.IGNORECASE)
CLOSED_CHECK_CHARS = 4000


def _expired_in_json_ld(soup: BeautifulSoup) -> bool:
    from datetime import datetime, timezone
    for tag in soup.find_all("script", type="application/ld+json"):
        m = re.search(r'"validThrough"\s*:\s*"([^"]+)"', tag.string or "")
        if not m:
            continue
        try:
            end = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
        except ValueError:
            continue
        return (end if end.tzinfo else end.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc)
    return False


def fetch_posting(url: str, snippet: str) -> tuple[str | None, bool]:
    """(full posting text or None, whether the posting looks closed)."""
    if not url or not url.startswith(("http://", "https://")):
        return None, False
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS, allow_redirects=True)
    except requests.RequestException:
        return None, False
    if resp.status_code in (404, 410):
        return None, True  # the posting was taken down
    if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "html"):
        return None, False  # blocked or login wall: can't tell, so assume it's open
    soup = BeautifulSoup(resp.text, "html.parser")
    if _expired_in_json_ld(soup):
        return None, True
    text = _from_json_ld(soup)
    page = BeautifulSoup(resp.text, "html.parser")
    for tag in page(["script", "style", "noscript"]):
        tag.decompose()
    top = _clean_text(page.get_text(" "))[:CLOSED_CHECK_CHARS]
    if CLOSED.search(top):
        return None, True
    text = text or _from_page_text(soup, snippet)
    if not text or len(text) < len(snippet or "") + MIN_GAIN_CHARS:
        return None, False
    return text[:MAX_CHARS], False


def fetch_full_description(url: str, snippet: str) -> str | None:
    """Return the full posting text, or None if it couldn't be found reliably."""
    return fetch_posting(url, snippet)[0]


def enrich_jobs(jobs: list[dict], workers: int = 6) -> list[dict]:
    """Replace each job's snippet with its full posting where possible (in place)."""
    if not jobs:
        return jobs

    def work(job):
        return fetch_posting(job.get("url"), job.get("description") or "")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, jobs))

    got = closed = 0
    for job, (full, is_closed) in zip(jobs, results):
        job["snippet"] = job.get("description") or ""
        if is_closed:
            job["closed"] = True
            closed += 1
        if full:
            job["description"] = full
            job["description_source"] = "full posting"
            got += 1
        else:
            job["description_source"] = "snippet"
    print(f"[posting_fetcher] Got the full posting for {got} of {len(jobs)} jobs "
          f"(the rest are scored from the job board's snippet)"
          + (f"; {closed} postings are already closed and were skipped." if closed else "."))
    return jobs
