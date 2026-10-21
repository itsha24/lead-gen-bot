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
YEARS_REQUIRED = re.compile(r"(\d+)\s*\+?\s*(?:years|yrs|ans)", re.IGNORECASE)


def prefilter(posting: dict, max_years: int = 4) -> str | None:
    """Return a rejection reason, or None if the posting should go to the LLM."""
    title = posting.get("title") or ""
    if SENIOR_TITLE.search(title):
        return f"senior title: {title}"
    years = [int(y) for y in YEARS_REQUIRED.findall(posting.get("description") or "")]
    if years and min(years) > max_years:
        return f"requires {min(years)}+ years"
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
                "description": "One sentence: the main reason for the score.",
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
        f"Location: {posting.get('location', '')}\n\n"
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


def score_with_gemini(profile: dict, posting: dict) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{_model('gemini')}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(profile, posting)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(SCORE_TOOL["input_schema"]),
        },
    }
    for attempt in range(3):
        resp = requests.post(
            url, json=body, timeout=60,
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        )
        if resp.status_code not in (429, 503):
            break
        wait = 30 * (attempt + 1)  # free tier is rate-limited per minute
        print(f"[matcher] Gemini busy (HTTP {resp.status_code}), retrying in {wait}s...")
        time.sleep(wait)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    return json.loads("".join(p.get("text", "") for p in parts))


def score_with_claude(client, profile: dict, posting: dict) -> dict:
    response = client.messages.create(
        model=_model("claude"),
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[SCORE_TOOL],
        tool_choice={"type": "tool", "name": "record_fit"},
        messages=[{"role": "user", "content": build_user_prompt(profile, posting)}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Model did not return a record_fit call")


def score_posting(provider: str, client, profile: dict, posting: dict) -> dict:
    if provider == "gemini":
        return _clean(score_with_gemini(profile, posting))
    return _clean(score_with_claude(client, profile, posting))


# ---------------------------------------------------------------------------
# 3. Pipeline entry point — used by main.py
# ---------------------------------------------------------------------------

def score_jobs(jobs: list[dict], min_score: int = 0) -> list[dict]:
    """Prefilter, score, and sort jobs. Returns only jobs with fit_score >= min_score.

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
    print(f"[matcher] Scoring with {provider} ({_model(provider)}).")

    client = None
    if provider == "claude":
        import anthropic  # only needed for Claude
        client = anthropic.Anthropic()
    profile = load_profile()
    scored = []
    for job in kept:
        label = f"{job.get('title')} @ {job.get('company')}"
        try:
            result = score_posting(provider, client, profile, job)
        except Exception as e:  # one bad call shouldn't kill the whole run
            print(f"[matcher] ERROR scoring {label}: {e}")
            continue
        print(f"[matcher] [{result['fit_score']:>3}] {label} — {result['reason']}")
        if result["fit_score"] >= min_score:
            scored.append({**job, **result})

    scored.sort(key=lambda j: j["fit_score"], reverse=True)
    print(f"[matcher] {len(scored)} jobs scored {min_score}+.")
    return scored


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_postings.json"
    with open(path, encoding="utf-8") as f:
        postings = json.load(f)
    results = score_jobs(postings)
    with open("scored_postings.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(results)} postings to scored_postings.json")
