"""
Finds businesses automatically using the Google Places API (New) — the
official, ToS-compliant way to query what you'd otherwise search for by
hand on Google Maps. Returns website URLs to feed into tech_checker.

Requires a GOOGLE_PLACES_API_KEY env var. Get one via Google Cloud Console:
enable the "Places API (New)", create an API key.
"""

import os
import requests

from config import DISCOVERY_QUERIES, DISCOVERY_MAX_RESULTS_PER_QUERY

GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


def find_business_urls(queries=None, max_results_per_query=None):
    if not GOOGLE_PLACES_API_KEY:
        print("[business_discovery] Missing GOOGLE_PLACES_API_KEY — skipping discovery.")
        return []

    queries = queries or DISCOVERY_QUERIES
    max_results_per_query = max_results_per_query or DISCOVERY_MAX_RESULTS_PER_QUERY

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        # Field mask keeps requests cheap — only ask for what we use
        "X-Goog-FieldMask": "places.displayName,places.websiteUri,places.formattedAddress",
    }

    found = []
    seen_urls = set()

    for query in queries:
        body = {
            "textQuery": query,
            "maxResultCount": max_results_per_query,
        }
        try:
            resp = requests.post(SEARCH_URL, headers=headers, json=body, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            for place in data.get("places", []):
                website = place.get("websiteUri")
                if not website or website in seen_urls:
                    continue
                seen_urls.add(website)
                found.append({
                    "name": (place.get("displayName") or {}).get("text"),
                    "address": place.get("formattedAddress"),
                    "website": website,
                    "matched_query": query,
                })
        except requests.RequestException as e:
            print(f"[business_discovery] Error searching '{query}': {e}")

    return found
