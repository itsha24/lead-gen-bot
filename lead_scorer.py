"""
Scores business leads against services.yaml and drafts a first message.

The AI only gets facts the bot actually found (Google listing + website audit)
and the proof projects in services.yaml, and is told not to invent anything —
so every draft is something you can send as-is or tweak.
"""

import json
import os
import time

import yaml

import matcher  # shared Gemini/Claude plumbing: backup model, retries, quota handling

SERVICES_PATH = "services.yaml"

LEAD_TOOL = {
    "name": "record_lead",
    "description": "Record how promising a local business is as a freelance client, and a first message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lead_score": {"type": "integer", "minimum": 0, "maximum": 100,
                           "description": "How likely this business needs AND would pay for one of the offers. Use the rubric."},
            "best_offer": {"type": "string", "description": "The id of the single best offer from services.yaml."},
            "why": {"type": "string", "description": "One sentence, to the freelancer ('you'): why this lead, citing the concrete findings."},
            "opportunities": {"type": "array", "items": {"type": "string"},
                              "description": "2-4 short, concrete things you could fix or build for them, from the findings only."},
            "language": {"type": "string", "enum": ["fr", "en"], "description": "Language to write to them in."},
            "email_subject": {"type": "string", "description": "Short, specific subject line (no clickbait, no emoji)."},
            "message": {"type": "string",
                        "description": "First outreach message, 70-120 words, in `language`. Plain text, signed with the freelancer's name."},
        },
        "required": ["lead_score", "best_offer", "why", "opportunities", "language", "email_subject", "message"],
    },
}

SYSTEM_PROMPT = """You help a local freelance developer find small-business clients and write first messages.

Scoring rubric (lead_score):
- 80-100: Clear, specific gap that one offer fixes (no website, insecure/outdated site, phone-only booking or
  ordering) AND signs the business is active and can pay (good rating, many reviews, multiple locations).
- 60-79: A real gap, but smaller or the business looks less established.
- 40-59: Possible fit, nothing urgent.
- 0-39: Already well served (modern site with booking/ordering/store), a large chain or franchise,
  or not a fit for any offer.

Message rules:
- Write in the business's language: French for French-language or Quebec businesses unless their site is
  clearly English. Natural Québécois-appropriate French, "vous".
- Open with ONE specific, true observation from the findings (e.g. their site shows a security warning,
  they take bookings only by phone). Never invent facts, numbers, results or compliments you can't see.
- Mention at most one proof project, only if it's relevant to the offer, using its summary as written.
- Offer something small and concrete (a quick call, or a free mock-up / short audit). One question at the end.
- Friendly and brief, not salesy. No "I hope this finds you well", no exclamation marks, no emoji.
- Sign with the freelancer's name and website."""


def load_services(path: str | None = None) -> dict:
    with open(path or SERVICES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _lead_facts(b: dict) -> dict:
    a = b.get("audit") or {}
    return {
        "name": b.get("name"), "category": b.get("category"), "address": b.get("address"),
        "google_rating": b.get("rating"), "google_reviews": b.get("reviews"),
        "website": b.get("website") or "none",
        "website_findings": a.get("issues") or ["no problems found"],
        "platform": a.get("platform"), "online_booking_or_ordering_tools": a.get("ordering_booking"),
        "site_language": a.get("language"), "has_contact_email": bool(a.get("emails")),
        "homepage_text_excerpt": (a.get("site_text") or "")[:800],
    }


def build_prompt(services: dict, business: dict) -> str:
    sv = {k: services.get(k) for k in ("name", "based_in", "languages", "contact", "offers", "proof_projects")}
    return ("<freelancer_services>\n" + yaml.safe_dump(sv, sort_keys=False, allow_unicode=True)
            + "</freelancer_services>\n\n<business>\n"
            + json.dumps(_lead_facts(business), ensure_ascii=False, indent=1)
            + "\n</business>\n\nScore this lead and draft the first message using the record_lead tool.")


def _clean(r: dict, offer_ids: set[str]) -> dict:
    r["lead_score"] = max(0, min(100, int(round(float(r.get("lead_score") or 0)))))
    if r.get("best_offer") not in offer_ids:
        r["best_offer"] = next(iter(offer_ids), "")
    r["opportunities"] = list(r.get("opportunities") or [])[:4]
    r["language"] = r.get("language") if r.get("language") in ("fr", "en") else "fr"
    for k in ("why", "email_subject", "message"):
        r[k] = str(r.get(k) or "").strip()
    return r


def score_leads(businesses: list[dict], max_leads: int = 25) -> list[dict]:
    provider = matcher.pick_provider()
    if not provider:
        print("[lead_scorer] No AI key set — skipping lead scoring.")
        return []
    services = load_services()
    offer_ids = {o["id"] for o in services.get("offers") or []}
    client = None
    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic()

    # Audit-based order: no/broken website first, then most issues, then most reviews (established).
    def promise(b):
        a = b.get("audit") or {}
        return (not a.get("reachable"), len(a.get("issues") or []), b.get("reviews") or 0)
    todo = sorted(businesses, key=promise, reverse=True)[:max_leads]
    if len(businesses) > max_leads:
        print(f"[lead_scorer] Scoring the {max_leads} most promising; the other {len(businesses) - max_leads} wait for the next run.")

    scored, errors = [], 0
    for i, b in enumerate(todo):
        if provider == "gemini" and i:
            time.sleep(matcher.GEMINI_DELAY_SECONDS)
        prompt = build_prompt(services, b)
        try:
            if provider == "gemini":
                r = matcher.gemini_structured(SYSTEM_PROMPT, prompt, LEAD_TOOL["input_schema"])
            else:
                r = matcher.claude_structured(client, SYSTEM_PROMPT, prompt, LEAD_TOOL)
        except matcher.QuotaExhausted as e:
            print(f"[lead_scorer] STOPPING: {e}")
            break
        except Exception as e:
            errors += 1
            print(f"[lead_scorer] ERROR scoring {b.get('name')}: {e}")
            if errors >= matcher.MAX_CONSECUTIVE_ERRORS:
                print("[lead_scorer] STOPPING: too many errors in a row.")
                break
            continue
        errors = 0
        r = _clean(r, offer_ids)
        print(f"[lead_scorer] [{r['lead_score']:>3}] {b.get('name')} ({b.get('category')}) — {r['best_offer']}: {r['why']}")
        scored.append({**b, **r})
    print(f"[lead_scorer] Scored {len(scored)} leads.")
    return scored
