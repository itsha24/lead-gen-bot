"""
Finds local businesses with the Google Places API (New) — the official way to
query what you'd otherwise search by hand on Google Maps.

Keeps the details that matter for outreach (name, phone, website, rating,
review count, Maps link, business type) and keeps businesses with NO website:
for a website offer those are often the best leads.

Requires a GOOGLE_PLACES_API_KEY env var (Google Cloud Console → enable
"Places API (New)" → create an API key). Cost: these fields bill as
"Text Search Enterprise", which has 1,000 free requests a month; a weekly run
uses MAX_PLACES_REQUESTS or fewer.
"""

import itertools
import os
import random
from datetime import date

import requests
import yaml

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
SERVICES_PATH = "services.yaml"
MAX_PLACES_REQUESTS = 24   # hard cap per run (each page of 20 results = 1 request)
PAGES_PER_QUERY = 2        # up to 40 businesses per query
FIELDS = ",".join(f"places.{f}" for f in (
    "id", "displayName", "formattedAddress", "nationalPhoneNumber", "websiteUri", "rating",
    "userRatingCount", "businessStatus", "googleMapsUri", "primaryType", "primaryTypeDisplayName",
)) + ",nextPageToken"


def load_search_config(path: str | None = None) -> dict:
    with open(path or SERVICES_PATH, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("search") or {}


def queries_for_this_week(config: dict, today: date | None = None) -> list[str]:
    """Every category x area combination, rotated by week so each run sees new ones."""
    combos = [f"{c} in {a}" for a, c in itertools.product(config.get("areas") or [], config.get("categories") or [])]
    if not combos:
        return []
    random.Random(7).shuffle(combos)  # fixed shuffle: each week mixes areas and categories, still repeatable
    per_run = max(1, int(config.get("queries_per_run") or 8))
    week = (today or date.today()).isocalendar()[1]
    start = (week * per_run) % len(combos)
    return [combos[(start + i) % len(combos)] for i in range(min(per_run, len(combos)))]


def find_businesses(queries: list[str] | None = None) -> list[dict]:
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        print("[business_discovery] GOOGLE_PLACES_API_KEY isn't set — skipping business discovery.")
        return []
    queries = queries if queries is not None else queries_for_this_week(load_search_config())
    print(f"[business_discovery] This week's searches: {', '.join(queries)}")
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS}

    found, seen, requests_made = [], set(), 0
    for query in queries:
        token = None
        for _ in range(PAGES_PER_QUERY):
            if requests_made >= MAX_PLACES_REQUESTS:
                print(f"[business_discovery] Reached the {MAX_PLACES_REQUESTS}-request cap for this run.")
                return found
            body = {"textQuery": query, "pageSize": 20}
            if token:
                body["pageToken"] = token
            try:
                resp = requests.post(SEARCH_URL, headers=headers, json=body, timeout=20)
                requests_made += 1
                if resp.status_code == 403:
                    print(f"[business_discovery] Google refused the key (403): {resp.text[:200]}. "
                          "Check that 'Places API (New)' is enabled for it.")
                    return found
                resp.raise_for_status()
            except requests.RequestException as e:
                print(f"[business_discovery] Error searching '{query}': {e}")
                break
            data = resp.json()
            for p in data.get("places", []):
                if p.get("id") in seen or p.get("businessStatus") not in (None, "OPERATIONAL"):
                    continue
                seen.add(p.get("id"))
                found.append({
                    "place_id": p.get("id"),
                    "name": (p.get("displayName") or {}).get("text"),
                    "address": p.get("formattedAddress"),
                    "phone": p.get("nationalPhoneNumber"),
                    "website": p.get("websiteUri"),
                    "rating": p.get("rating"),
                    "reviews": p.get("userRatingCount"),
                    "maps_url": p.get("googleMapsUri"),
                    "category": (p.get("primaryTypeDisplayName") or {}).get("text") or p.get("primaryType"),
                    "matched_query": query,
                })
            token = data.get("nextPageToken")
            if not token:
                break
    print(f"[business_discovery] Found {len(found)} businesses with {requests_made} Places requests "
          f"({sum(1 for b in found if not b['website'])} have no website).")
    return found
