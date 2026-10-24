"""
Pulls jobs straight from company career pages listed in companies.yaml.

Supports the three hiring systems with free, public job feeds:
  Greenhouse  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  Lever       https://api.lever.co/v0/postings/{slug}?mode=json
  Ashby       https://api.ashbyhq.com/posting-api/job-board/{slug}

Each feed includes the full job description, so these jobs skip the
full-posting fetch and are scored on complete text.
"""

import html
import os
import re
from datetime import datetime, timedelta, timezone

import requests
import yaml
from bs4 import BeautifulSoup

from config import COMPANY_MAX_DAYS_OLD

COMPANIES_PATH = "companies.yaml"
TIMEOUT_SECONDS = 20

# Titles worth scoring. Everything else on a company board (sales, finance, HR…)
# is dropped for free before any AI call.
ROLE_TITLE = re.compile(
    r"\b(developer|engineer|engineering|programmer|software|backend|back[- ]end|front[- ]?end|"
    r"full[- ]?stack|qa|quality|test|tester|testing|sdet|automation|integration|java|\.net|c#|"
    r"développeur|developpeur|développeuse|programmeur|ingénieur|ingenieur|analyste|devops)\b",
    re.IGNORECASE,
)
NOT_A_DEV_ROLE = re.compile(
    r"\b(sales|account|solutions? engineer|sales engineer|customer success|customer support|support engineer|recruit|"
    r"marketing|designer|manager|director|head of|vp|counsel|legal|finance|accountant)\b",
    re.IGNORECASE,
)
# Locations you'd accept (matches profile.yaml). Jobs elsewhere are dropped for free.
NORTH_AMERICA = re.compile(
    r"montr[eé]al|qu[eé]bec|\bqc\b|canada|toronto|ottawa|vancouver|calgary|edmonton|waterloo|"
    r"kitchener|united states|\busa?\b|\bu\.s\.|north america|americas|\b(ca|on|bc|ab)\b",
    re.IGNORECASE,
)
REMOTE_ANYWHERE = re.compile(r"\b(remote|anywhere|worldwide|global)\b", re.IGNORECASE)
# "Remote, Poland" or "Remote - EMEA" means remote *in that country/region*, not for you.
ELSEWHERE = re.compile(
    r"\b(emea|europe|apac|latam|uk|united kingdom|ireland|germany|france|spain|portugal|italy|"
    r"netherlands|poland|romania|sweden|denmark|norway|finland|switzerland|austria|belgium|"
    r"czech|hungary|ukraine|israel|india|singapore|japan|korea|china|hong kong|australia|"
    r"new zealand|brazil|mexico|argentina|colombia|chile|south africa|nigeria|kenya|philippines)\b",
    re.IGNORECASE,
)


def parse_board(entry: str) -> tuple[str, str] | None:
    """Turn a careers link (or 'greenhouse:slug') into (system, slug)."""
    entry = (entry or "").strip()
    m = re.match(r"^(greenhouse|lever|ashby)\s*:\s*([\w.-]+)$", entry, re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2)
    patterns = [
        ("greenhouse", r"greenhouse\.io/v1/boards/([\w.-]+)"),
        ("greenhouse", r"greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)"),
        ("lever", r"jobs\.lever\.co/([\w.-]+)"),
        ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
    ]
    for system, pat in patterns:
        m = re.search(pat, entry, re.IGNORECASE)
        if m and m.group(1).lower() not in ("v1", "boards", "jobs"):
            return system, m.group(1)
    return None


def load_companies(path: str | None = None) -> list[tuple[str, str]]:
    path = path or COMPANIES_PATH
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        entries = (yaml.safe_load(f) or {}).get("companies") or []
    boards = []
    for e in entries:
        parsed = parse_board(str(e))
        if parsed:
            boards.append(parsed)
        else:
            print(f"[company_boards] Skipping '{e}': not a Greenhouse, Lever or Ashby link.")
    return list(dict.fromkeys(boards))


def _text(fragment: str) -> str:
    # Greenhouse double-escapes its HTML (&lt;p&gt;), so unescape before parsing.
    soup = BeautifulSoup(html.unescape(fragment or ""), "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):  # Lever: milliseconds since epoch
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _pretty(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).title()  # "wealthsimple" -> "Wealthsimple", "1password" -> "1Password"


def _greenhouse(slug):
    data = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                        params={"content": "true"}, timeout=TIMEOUT_SECONDS)
    data.raise_for_status()
    for j in data.json().get("jobs", []):
        yield {
            "title": (j.get("title") or "").strip(),
            "company": j.get("company_name") or slug,
            "location": (j.get("location") or {}).get("name") or "",
            "url": j.get("absolute_url"),
            "description": _text(j.get("content")),
            "posted": _iso(j.get("first_published") or j.get("updated_at")),
        }


def _lever(slug):
    data = requests.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"},
                        timeout=TIMEOUT_SECONDS)
    data.raise_for_status()
    for j in data.json():
        cats = j.get("categories") or {}
        locations = cats.get("allLocations") or [cats.get("location")]
        lists = " ".join(f"{l.get('text', '')}: {_text(l.get('content'))}" for l in j.get("lists") or [])
        yield {
            "title": (j.get("text") or "").strip(),
            "company": _pretty(slug),
            "location": " / ".join(l for l in locations if l) + (" (remote)" if j.get("workplaceType") == "remote" else ""),
            "url": j.get("hostedUrl"),
            "description": " ".join(filter(None, [j.get("descriptionPlain"), lists, j.get("additionalPlain")])),
            "posted": _iso(j.get("createdAt")),
        }


def _ashby(slug):
    data = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=TIMEOUT_SECONDS)
    data.raise_for_status()
    for j in data.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        places = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or [] if isinstance(s, dict)]
        location = " / ".join(p for p in places if p) + (" (remote)" if j.get("isRemote") else "")
        yield {
            "title": (j.get("title") or "").strip(),
            "company": _pretty(slug),
            "location": location,
            "url": j.get("jobUrl"),
            "description": j.get("descriptionPlain") or _text(j.get("descriptionHtml")),
            "posted": _iso(j.get("publishedAt")),
        }


FETCHERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def _relevant(job: dict) -> bool:
    title, loc = job["title"], job["location"]
    if not ROLE_TITLE.search(title) or NOT_A_DEV_ROLE.search(title):
        return False
    if not loc or NORTH_AMERICA.search(loc):
        return True
    return bool(REMOTE_ANYWHERE.search(loc)) and not ELSEWHERE.search(loc)


def fetch_company_jobs(max_days_old: int = COMPANY_MAX_DAYS_OLD) -> list[dict]:
    boards = load_companies()
    if not boards:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days_old)
    results, summary = [], []
    for system, slug in boards:
        try:
            jobs = list(FETCHERS[system](slug))
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            print(f"[company_boards] {slug} ({system}): board not found or unavailable (HTTP {code}) — check the link in companies.yaml.")
            continue
        except Exception as e:  # one broken board shouldn't stop the others
            print(f"[company_boards] {slug} ({system}): {type(e).__name__}: {e}")
            continue
        recent = [j for j in jobs if j["posted"] and datetime.fromisoformat(j["posted"]) >= cutoff]
        relevant = [j for j in recent if _relevant(j)]
        for j in relevant:
            j.update(source=f"{j['company']} careers", keyword_matched="company board",
                     description_source="full posting")
        results.extend(relevant)
        summary.append(f"{slug} {len(relevant)}")
    print(f"[company_boards] {len(results)} relevant jobs posted in the last {max_days_old} days "
          f"from {len(summary)} companies ({', '.join(summary)}).")
    return results
