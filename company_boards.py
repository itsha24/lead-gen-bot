"""
Pulls jobs straight from company career pages listed in companies.yaml.

Supported hiring systems:
  Greenhouse       https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  Lever            https://api.lever.co/v0/postings/{slug}?mode=json
  Ashby            https://api.ashbyhq.com/posting-api/job-board/{slug}
  SmartRecruiters  https://api.smartrecruiters.com/v1/companies/{id}/postings  (public Posting API)
  Workday          https://{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
                   — unofficial: the JSON every Workday careers site loads its jobs from.
                     Stable in practice, but Workday doesn't guarantee it.

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
    m = re.match(r"^(greenhouse|lever|ashby|smartrecruiters)\s*:\s*([\w.-]+)$", entry, re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2)
    # Workday: https://TENANT.wd3.myworkdayjobs.com/en-US/SITE/...  ->  "host|tenant|site"
    m = re.search(r"https?://(([\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", entry)
    if m and m.group(3).lower() not in ("wday", "job"):
        return "workday", f"{m.group(1)}|{m.group(2)}|{m.group(3)}"
    patterns = [
        ("greenhouse", r"greenhouse\.io/v1/boards/([\w.-]+)"),
        ("greenhouse", r"greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)"),
        ("lever", r"jobs\.lever\.co/([\w.-]+)"),
        ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
        ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w.-]+)"),
        ("smartrecruiters", r"api\.smartrecruiters\.com/v1/companies/([\w.-]+)"),
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
            print(f"[company_boards] Skipping '{e}': not a Greenhouse, Lever, Ashby, SmartRecruiters or Workday link.")
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


def _smartrecruiters(slug, cutoff):
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    items, offset = [], 0
    while offset < 500:  # a few pages is plenty for one week of postings
        r = requests.get(base, params={"limit": 100, "offset": offset, "destination": "PUBLIC",
                                       "releasedAfter": cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                         timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        page = r.json()
        items += page.get("content") or []
        offset += 100
        if offset >= (page.get("totalFound") or 0):
            break
    for it in items:
        loc = it.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country", "").upper()) if x)
        if loc.get("remote"):
            location += " (remote)"
        job = {
            "title": (it.get("name") or "").strip(),
            "company": (it.get("company") or {}).get("name") or slug,
            "location": location,
            "url": f"https://jobs.smartrecruiters.com/{slug}/{it.get('id')}",
            "description": "",
            "posted": _iso(it.get("releasedDate")),
            "_detail": f"{base}/{it.get('id')}",
        }
        yield job


def _smartrecruiters_detail(job):
    r = requests.get(job.pop("_detail"), timeout=TIMEOUT_SECONDS)
    r.raise_for_status()
    d = r.json()
    sections = (d.get("jobAd") or {}).get("sections") or {}
    job["description"] = " ".join(
        _text((sections.get(k) or {}).get("text")) for k in ("jobDescription", "qualifications", "additionalInformation"))
    job["url"] = d.get("postingUrl") or job["url"]


_WD_AGE = re.compile(r"(\d+)\+?\s*day", re.IGNORECASE)


def _workday_age_days(posted_on: str) -> int | None:
    """Workday says 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'."""
    t = (posted_on or "").lower()
    if "today" in t or "aujourd" in t:
        return 0
    if "yesterday" in t or "hier" in t:
        return 1
    m = _WD_AGE.search(t) or re.search(r"(\d+)\+?\s*jour", t)
    return int(m.group(1)) if m else None


def _workday(slug, cutoff):
    host, tenant, site = slug.split("|")
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    max_days = (datetime.now(timezone.utc) - cutoff).days
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    offset = 0
    while offset < 200:  # newest jobs are usually first; 10 pages is plenty
        r = requests.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
                          headers=headers, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        page = r.json()
        postings = page.get("jobPostings") or []
        for p in postings:
            age = _workday_age_days(p.get("postedOn"))
            if age is None or age > max_days:
                continue
            yield {
                "title": (p.get("title") or "").strip(),
                "company": _pretty(tenant),
                "location": p.get("locationsText") or "",
                "url": f"https://{host}/{site}{p.get('externalPath', '')}",
                "description": "",
                "posted": (datetime.now(timezone.utc) - timedelta(days=age)).isoformat(),
                "_detail": f"{api}{p.get('externalPath', '')}",
            }
        offset += 20
        if not postings or offset >= (page.get("total") or 0):
            break


def _workday_detail(job):
    r = requests.get(job.pop("_detail"), headers={"Accept": "application/json"}, timeout=TIMEOUT_SECONDS)
    r.raise_for_status()
    d = r.json()
    info = d.get("jobPostingInfo") or {}
    job["description"] = _text(info.get("jobDescription"))
    job["url"] = info.get("externalUrl") or job["url"]
    job["company"] = (d.get("hiringOrganization") or {}).get("name") or job["company"]
    if info.get("startDate"):  # exact posting date, better than "Posted 3 Days Ago"
        job["posted"] = _iso(info["startDate"] + "T12:00:00+00:00") or job["posted"]
    if info.get("location") and not job["location"]:
        job["location"] = info["location"]


# Systems whose list doesn't include descriptions: fetch details only for relevant jobs.
DETAILS = {"smartrecruiters": _smartrecruiters_detail, "workday": _workday_detail}
FETCHERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby,
            "smartrecruiters": _smartrecruiters, "workday": _workday}
NEEDS_CUTOFF = {"smartrecruiters", "workday"}


def _relevant(job: dict) -> bool:
    title, loc = job["title"], job["location"]
    if not ROLE_TITLE.search(title) or NOT_A_DEV_ROLE.search(title):
        return False
    if not loc or NORTH_AMERICA.search(loc) or re.fullmatch(r"\s*\d+\s+(locations?|emplacements?)\s*", loc, re.IGNORECASE):
        return True  # "2 Locations" (Workday) doesn't say where — let the full posting decide
    return bool(REMOTE_ANYWHERE.search(loc)) and not ELSEWHERE.search(loc)


def fetch_company_jobs(max_days_old: int = COMPANY_MAX_DAYS_OLD) -> list[dict]:
    boards = load_companies()
    if not boards:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days_old)
    results, summary = [], []
    for system, slug in boards:
        name = slug.split("|")[1] if system == "workday" else slug
        try:
            fetch = FETCHERS[system]
            jobs = list(fetch(slug, cutoff) if system in NEEDS_CUTOFF else fetch(slug))
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            print(f"[company_boards] {name} ({system}): board not found or unavailable (HTTP {code}) — check the link in companies.yaml.")
            continue
        except Exception as e:  # one broken board shouldn't stop the others
            print(f"[company_boards] {name} ({system}): {type(e).__name__}: {e}")
            continue
        recent = [j for j in jobs if j["posted"] and datetime.fromisoformat(j["posted"]) >= cutoff]
        relevant = [j for j in recent if _relevant(j)]
        if system in DETAILS:
            for j in relevant:
                try:
                    DETAILS[system](j)
                except Exception as e:  # keep the job; it'll get its full posting fetched later
                    j.pop("_detail", None)
                    print(f"[company_boards] {name}: couldn't load details for '{j['title']}' ({type(e).__name__}).")
        for j in relevant:
            j.update(source=f"{j['company']} careers", keyword_matched="company board",
                     description_source="full posting" if len(j.get("description") or "") > 200 else "snippet")
        results.extend(relevant)
        summary.append(f"{name} {len(relevant)}")
    print(f"[company_boards] {len(results)} relevant jobs posted in the last {max_days_old} days "
          f"from {len(summary)} companies ({', '.join(summary)}).")
    return results
