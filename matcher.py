"""
AI job matcher: scores each job posting against profile.yaml.

Two stages:
  1. prefilter() — free regex checks that drop obvious senior roles
  2. score_posting() — an LLM reads the posting and returns a structured fit score

Works with either provider (whichever key is set; Gemini wins if both are):
  - GEMINI_API_KEY    -> Google Gemini (free tier, key from aistudio.google.com)
  - ANTHROPIC_API_KEY -> Claude (paid, key from platform.claude.com)
Force one with MATCHER_PROVIDER=gemini or MATCHER_PROVIDER=claude.
With no key, scoring is skipped and jobs pass through unscored.

Try it on its own:
    python matcher.py sample_postings.json
"""

import json
import os
import re
import sys
import time

import requests
import yaml

GEMINI_DELAY_SECONDS = 5   # pacing between calls to stay under free-tier per-minute limits
MAX_CONSECUTIVE_ERRORS = 3  # stop scoring if the API keeps failing instead of crawling on


class QuotaExhausted(Exception):
    """The provider's daily quota is used up — no point trying more jobs today."""


class ModelUnavailable(Exception):
    """A model is overloaded, timing out, or missing — worth trying the backup model."""


# Backup Gemini model: used when the main one is overloaded or out of free quota.
# It has its own separate free quota, so it also stretches how many jobs get scored.
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"
GEMINI_TIMEOUT_SECONDS = 30


DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",   # free tier; gemini-3.5-flash-lite if you hit limits
    "claude": "claude-haiku-5-5",
}
PROFILE_PATH = "profile.yaml"

# ---------------------------------------------------------------------------
# 1. Cheap prefilter — runs before any API call, costs nothing
# ---------------------------------------------------------------------------

SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|architect|head of)\b",
    re.IGNORECASE,
)
# "5+ years", "3-5 years", "3 to 5 years", "5 ans" — for a range, the lower number counts.
# Internships/co-ops are excluded entirely. French: "stagiaire", "stage en …", "Stage – …".
INTERNSHIP_TITLE = re.compile(
    r"\b(intern|interns|internship|co-?op|stagiaires?|student|étudiant(?:e)?)\b"
    r"|^\s*stage\b|\bstage\s+(?:en|de|d'|-|–|:)",
    re.IGNORECASE,
)
_YEARS = r"(?:(\d{1,2})\s*(?:-|–|to|à)\s*)?(\d{1,2})\s*\+?\s*(?:years?|yrs?|ans)"
YEARS_REQUIRED = re.compile(_YEARS, re.IGNORECASE)
# Full postings mention years in other ways ("30 years in business"), so there we
# only count numbers tied to experience: "5+ years of relevant experience", "5 ans d'expérience".
YEARS_OF_EXPERIENCE = re.compile(
    _YEARS + r"\W+(?:\w+\W+){0,4}?(?:experience|expérience|exp\b)", re.IGNORECASE)


def required_years(text: str, strict: bool = False) -> int | None:
    pattern = YEARS_OF_EXPERIENCE if strict else YEARS_REQUIRED
    found = [int(low or high) for low, high in pattern.findall(text or "")]
    return min(found) if found else None


def prefilter(posting: dict, max_years: int = 4) -> str | None:
    """Return a rejection reason, or None if the posting should go to the LLM."""
    title = posting.get("title") or ""
    if SENIOR_TITLE.search(title):
        return f"senior title: {title}"
    if INTERNSHIP_TITLE.search(title):
        return f"internship: {title}"
    strict = posting.get("description_source") == "full posting"
    years = required_years(posting.get("description") or "", strict=strict)
    if years is not None and years > max_years:
        return f"requires {years}+ years"
    return None


# ---------------------------------------------------------------------------
# 2. LLM scoring — structured output via a forced tool call
# ---------------------------------------------------------------------------

SCORE_TOOL = {
    "name": "record_fit",
    "description": "Record how well a job posting fits the candidate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fit_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Overall fit, using the rubric in the instructions.",
            },
            "seniority": {
                "type": "string",
                "enum": ["intern", "junior", "intermediate", "senior", "unclear"],
                "description": "Real seniority, judged from responsibilities and requirements, not just the title.",
            },
            "years_required": {
                "type": ["integer", "null"],
                "description": "Minimum years of experience required, or null if not stated.",
            },
            "matched_skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required or preferred skills the candidate has.",
            },
            "missing_required": {
                "type": "array",
                "items": {"type": "string"},
                "description": "REQUIRED skills the candidate lacks (ignore nice-to-haves).",
            },
            "dealbreakers_hit": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Any of the candidate's dealbreakers this posting triggers.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence on the main reason for the score, addressed to the candidate as 'you' (e.g. 'Your Java and QA automation match well, but it needs 3 years').",
            },
        },
        "required": [
            "fit_score", "seniority", "years_required", "matched_skills",
            "missing_required", "dealbreakers_hit", "reason",
        ],
    },
}

SYSTEM_PROMPT = """You are a career advisor screening job postings for one specific candidate.
Be honest and strict: a wasted application costs the candidate time, so do not inflate scores.

Scoring rubric:
- 85-100: Apply today. Right seniority, most required skills are "strong", no dealbreakers.
- 65-84: Good fit. Right seniority, a gap or two in required skills that are learnable.
- 40-64: Stretch. Seniority slightly high OR several required skills missing.
- 0-39: Skip. Wrong seniority, wrong field, or any dealbreaker hit.

Rules:
- Judge seniority from responsibilities (mentoring, owning architecture, leading teams = senior),
  not just the title. A "Developer" title asking for 6 years is senior.
- Skills under "learning" do NOT count as matches for required skills.
- Missing REQUIRED skills matter far more than missing nice-to-haves.
- If French is required, compare it against the candidate's French level.
- Any dealbreaker caps the score at 30.
- If the role is an internship, co-op, "stage" or student position, set seniority to "intern"
  (it will be hidden from the candidate) and cap the score at 10.
- A role matching anything else under "avoid_roles" caps the score at 35, even if the
  skills match well. Say which avoid_roles entry it hit in the reason.
- Job board descriptions are often short snippets. If key details are missing, score on what
  is there and say so in the reason rather than guessing."""


def load_profile(path: str = PROFILE_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_user_prompt(profile: dict, posting: dict) -> str:
    return (
        "<candidate_profile>\n"
        f"{yaml.safe_dump(profile, sort_keys=False)}"
        "</candidate_profile>\n\n"
        "<job_posting>\n"
        f"Title: {posting.get('title', '')}\n"
        f"Company: {posting.get('company', '')}\n"
        f"Location: {posting.get('location', '')}\n"
        f"Text below is: {posting.get('description_source', 'a job board snippet')}\n\n"
        f"{posting.get('description', '')}\n"
        "</job_posting>\n\n"
        "Score this posting for the candidate using the record_fit tool."
    )


def pick_provider() -> str | None:
    forced = os.environ.get("MATCHER_PROVIDER", "").lower()
    if forced in DEFAULT_MODELS:
        return forced
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return None


def _model(provider: str) -> str:
    return os.environ.get("MATCHER_MODEL") or DEFAULT_MODELS[provider]


def _clean(result: dict) -> dict:
    """Guard against slightly-off model output so one bad row can't break the sheet."""
    score = int(round(float(result.get("fit_score", 0))))
    result["fit_score"] = max(0, min(100, score))
    for key in ("matched_skills", "missing_required", "dealbreakers_hit"):
        result[key] = list(result.get(key) or [])
    result.setdefault("seniority", "unclear")
    result.setdefault("years_required", None)
    result["reason"] = str(result.get("reason", "")).strip()
    return result


def _gemini_schema(schema: dict) -> dict:
    """Convert our JSON Schema to Gemini's responseSchema dialect
    (["integer","null"] -> nullable, no min/max)."""
    out = {}
    for k, v in schema.items():
        if k in ("minimum", "maximum"):
            continue
        if k == "type" and isinstance(v, list):
            out["type"] = next(t for t in v if t != "null")
            out["nullable"] = "null" in v
        elif k == "properties":
            out[k] = {name: _gemini_schema(sub) for name, sub in v.items()}
        elif k == "items":
            out[k] = _gemini_schema(v)
        else:
            out[k] = v
    return out


# Run-level state: models out of daily quota are skipped; overloaded ones are tried last.
_gemini_exhausted: set[str] = set()
_gemini_order: list[str] = []


def gemini_models() -> list[str]:
    if not _gemini_order:
        primary = _model("gemini")
        backup = os.environ.get("MATCHER_FALLBACK_MODEL", GEMINI_FALLBACK_MODEL)
        _gemini_order.extend(dict.fromkeys(m for m in (primary, backup) if m))
    return [m for m in _gemini_order if m not in _gemini_exhausted]


def _call_gemini(model: str, body: dict, attempts: int) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    problem = ""
    for attempt in range(attempts):
        if attempt:
            wait = 15 * attempt
            print(f"[matcher] {model}: {problem}, retrying in {wait}s...")
            time.sleep(wait)
        try:
            resp = requests.post(
                url, json=body, timeout=GEMINI_TIMEOUT_SECONDS,
                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            problem = "no response (timeout)" if isinstance(e, requests.Timeout) else "connection error"
            continue
        if resp.status_code == 200:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            return json.loads("".join(p.get("text", "") for p in parts))
        if resp.status_code == 429 and "perday" in resp.text.lower().replace(" ", "").replace("_", ""):
            raise QuotaExhausted(f"{model} daily quota used up")
        if resp.status_code in (429, 500, 503, 504):
            problem = f"busy (HTTP {resp.status_code})"
            continue
        if resp.status_code == 404:
            raise ModelUnavailable(f"{model} not found (HTTP 404)")
        raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
    raise ModelUnavailable(f"{model} {problem} after {attempts} tries")


def gemini_structured(system_prompt: str, user_prompt: str, schema: dict, max_tokens: int | None = None) -> dict:
    """Ask Gemini for JSON matching `schema`, with retries and the backup model.
    Shared by job scoring and lead scoring."""
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(schema),
        },
    }
    if max_tokens:
        body["generationConfig"]["maxOutputTokens"] = max_tokens
    last_error = None
    for model in gemini_models():
        try:
            return _call_gemini(model, body, attempts=2)
        except QuotaExhausted as e:
            _gemini_exhausted.add(model)
            left = gemini_models()
            print(f"[ai] {e}" + (f" — using {left[0]} for the rest of this run." if left else "."))
            last_error = e
        except ModelUnavailable as e:
            print(f"[ai] {e}.")
            if len(gemini_models()) > 1 and model == _gemini_order[0]:
                _gemini_order.append(_gemini_order.pop(0))  # lead with the other model from now on
            last_error = e
    if not gemini_models():
        raise QuotaExhausted("all Gemini models are out of daily quota")
    raise RuntimeError(str(last_error))


def claude_structured(client, system_prompt: str, user_prompt: str, tool: dict, max_tokens: int = 1024) -> dict:
    response = client.messages.create(
        model=_model("claude"),
        max_tokens=max_tokens,
        system=system_prompt,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user_prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError(f"Model did not return a {tool['name']} call")


def score_with_gemini(profile: dict, posting: dict) -> dict:
    return gemini_structured(SYSTEM_PROMPT, build_user_prompt(profile, posting), SCORE_TOOL["input_schema"])


def score_with_claude(client, profile: dict, posting: dict) -> dict:
    return claude_structured(client, SYSTEM_PROMPT, build_user_prompt(profile, posting), SCORE_TOOL)


def score_posting(provider: str, client, profile: dict, posting: dict) -> dict:
    if provider == "gemini":
        return _clean(score_with_gemini(profile, posting))
    return _clean(score_with_claude(client, profile, posting))


# ---------------------------------------------------------------------------
# 3. Pipeline entry point — used by main.py
# ---------------------------------------------------------------------------

def score_jobs(jobs: list[dict], max_jobs: int | None = None, deadline: float | None = None) -> list[dict]:
    """Prefilter, score, and sort jobs (best first).

    Returns every job that was successfully scored, whatever its score, so the
    sheet records it and it isn't re-scored tomorrow. Jobs that couldn't be scored
    (quota, errors, over max_jobs) are left out so the next run picks them up.
    If no API key is set, prefiltered jobs are returned unscored.
    """
    kept = []
    for job in jobs:
        rejection = prefilter(job)
        if rejection:
            print(f"[matcher] FILTERED {job.get('title')} @ {job.get('company')} — {rejection}")
        else:
            kept.append(job)
    print(f"[matcher] {len(kept)} of {len(jobs)} jobs passed the prefilter.")

    provider = pick_provider()
    if not provider:
        print("[matcher] No GEMINI_API_KEY or ANTHROPIC_API_KEY set — skipping AI scoring.")
        return kept
    models = ", backup ".join(gemini_models()) if provider == "gemini" else _model(provider)
    print(f"[matcher] Scoring with {provider} ({models}).")

    client = None
    if provider == "claude":
        import anthropic  # only needed for Claude
        client = anthropic.Anthropic()
    if max_jobs is not None and len(kept) > max_jobs:
        # Companies you picked (companies.yaml) first, then newest first.
        kept.sort(key=lambda j: (j.get("keyword_matched") == "company board", j.get("posted") or ""), reverse=True)
        print(f"[matcher] Scoring {max_jobs} (your companies first, then newest); "
              f"the other {len(kept) - max_jobs} wait for the next run.")
        kept = kept[:max_jobs]

    # Swap Adzuna's snippets for the full postings, then re-check the free filter:
    # full text often reveals "7+ years" the snippet cut off. Those are saved as
    # low fits without an AI call, so they never come back to take a slot.
    from posting_fetcher import enrich_jobs  # local import: only needed when scoring
    enrich_jobs([j for j in kept if j.get("description_source") != "full posting"])
    scored = []
    to_score = []
    for job in kept:
        rejection = prefilter(job) if job.get("description_source") == "full posting" else None
        if rejection:
            print(f"[matcher] FILTERED after reading full posting: {job.get('title')} @ {job.get('company')} — {rejection}")
            scored.append({**job, "fit_score": 15, "seniority": "senior", "years_required": None,
                           "matched_skills": [], "missing_required": [], "dealbreakers_hit": [],
                           "reason": f"Full posting {rejection} — filtered without AI."})
        else:
            to_score.append(job)
    kept = to_score

    profile = load_profile()
    errors_in_a_row = 0
    ai_scored = 0
    for i, job in enumerate(kept):
        if deadline and time.monotonic() > deadline:
            print("[matcher] Out of time for this run — saving what's scored; the rest wait for the next run.")
            break
        label = f"{job.get('title')} @ {job.get('company')}"
        if provider == "gemini" and i > 0:
            time.sleep(GEMINI_DELAY_SECONDS)
        try:
            result = score_posting(provider, client, profile, job)
        except QuotaExhausted as e:
            print(f"[matcher] STOPPING: {e}")
            break
        except Exception as e:  # one bad call shouldn't kill the whole run
            errors_in_a_row += 1
            print(f"[matcher] ERROR scoring {label}: {e}")
            if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                print(f"[matcher] STOPPING: {errors_in_a_row} errors in a row.")
                break
            continue
        errors_in_a_row = 0
        ai_scored += 1
        print(f"[matcher] [{result['fit_score']:>3}] {label} — {result['reason']}")
        scored.append({**job, **result})

    scored.sort(key=lambda j: j["fit_score"], reverse=True)
    unscored = len(kept) - ai_scored
    print(f"[matcher] Scored {len(scored)} jobs" + (f"; {unscored} left for the next run." if unscored else "."))
    return scored


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_postings.json"
    with open(path, encoding="utf-8") as f:
        postings = json.load(f)
    results = score_jobs(postings)
    with open("scored_postings.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(results)} postings to scored_postings.json")
