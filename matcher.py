"""
AI job matcher: scores each job posting against profile.yaml.

Two stages:
  1. prefilter() — free regex checks that drop obvious senior roles
  2. score_posting() — an LLM reads the posting and returns a structured fit score

Requires the ANTHROPIC_API_KEY env var. Without it, scoring is skipped and jobs
pass through unscored (so the rest of the pipeline still runs).

Try it on its own:
    python matcher.py sample_postings.json
"""

import json
import os
import re
import sys

import yaml

MODEL = os.environ.get("MATCHER_MODEL", "claude-haiku-5-5")
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


def score_posting(client, profile: dict, posting: dict) -> dict:
    response = client.messages.create(
        model=MODEL,
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


# ---------------------------------------------------------------------------
# 3. Pipeline entry point — used by main.py
# ---------------------------------------------------------------------------

def score_jobs(jobs: list[dict], min_score: int = 0) -> list[dict]:
    """Prefilter, score, and sort jobs. Returns only jobs with fit_score >= min_score.

    If ANTHROPIC_API_KEY is missing, prefiltered jobs are returned unscored.
    """
    kept = []
    for job in jobs:
        rejection = prefilter(job)
        if rejection:
            print(f"[matcher] FILTERED {job.get('title')} @ {job.get('company')} — {rejection}")
        else:
            kept.append(job)
    print(f"[matcher] {len(kept)} of {len(jobs)} jobs passed the prefilter.")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[matcher] ANTHROPIC_API_KEY not set — skipping AI scoring.")
        return kept

    import anthropic  # imported here so the prefilter works without the SDK installed

    client = anthropic.Anthropic()
    profile = load_profile()
    scored = []
    for job in kept:
        label = f"{job.get('title')} @ {job.get('company')}"
        try:
            result = score_posting(client, profile, job)
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
