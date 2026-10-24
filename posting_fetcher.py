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


def fetch_full_description(url: str, snippet: str) -> str | None:
    """Return the full posting text, or None if it couldn't be found reliably."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "html"):
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    text = _from_json_ld(soup) or _from_page_text(soup, snippet)
    if not text or len(text) < len(snippet or "") + MIN_GAIN_CHARS:
        return None
    return text[:MAX_CHARS]


def enrich_jobs(jobs: list[dict], workers: int = 6) -> list[dict]:
    """Replace each job's snippet with its full posting where possible (in place)."""
    if not jobs:
        return jobs

    def work(job):
        return fetch_full_description(job.get("url"), job.get("description") or "")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, jobs))

    got = 0
    for job, full in zip(jobs, results):
        job["snippet"] = job.get("description") or ""
        if full:
            job["description"] = full
            job["description_source"] = "full posting"
            got += 1
        else:
            job["description_source"] = "snippet"
    print(f"[posting_fetcher] Got the full posting for {got} of {len(jobs)} jobs "
          f"(the rest are scored from Adzuna's snippet).")
    return jobs
