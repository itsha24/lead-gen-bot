"""
Given a list of business website URLs, detects likely tech stack and flags
signals worth a pitch (slow site, CRM with no visible automation, no
recognizable modern tooling at all, etc.)
"""

import requests

from config import TECH_SIGNATURES, PAGESPEED_API_KEY


def detect_tech(html, headers):
    found = []
    haystack = html.lower()
    header_str = str(headers).lower()
    for tech, sigs in TECH_SIGNATURES.items():
        if not sigs:
            continue
        for sig in sigs:
            if sig.lower() in haystack or sig.lower() in header_str:
                found.append(tech)
                break
    return found


def get_pagespeed_score(url):
    if not PAGESPEED_API_KEY:
        return None
    try:
        resp = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "key": PAGESPEED_API_KEY, "strategy": "mobile"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        score = data["lighthouseResult"]["categories"]["performance"]["score"]
        return round(score * 100)
    except Exception as e:
        print(f"[tech_checker] PageSpeed error for {url}: {e}")
        return None


def check_business(url):
    result = {
        "url": url,
        "tech_detected": [],
        "pagespeed_score": None,
        "flag": "",
        "error": None,
    }
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        result["tech_detected"] = detect_tech(resp.text, resp.headers)
    except requests.RequestException as e:
        result["error"] = str(e)
        return result

    result["pagespeed_score"] = get_pagespeed_score(url)

    signals = []
    if result["pagespeed_score"] is not None and result["pagespeed_score"] < 50:
        signals.append("slow site")
    if "HubSpot" in result["tech_detected"] and "Zapier Webhook" not in result["tech_detected"]:
        signals.append("CRM without visible automation")
    if not result["tech_detected"]:
        signals.append("no common tech detected (possibly custom/outdated)")
    result["flag"] = ", ".join(signals)

    return result


def check_business_list(urls):
    cleaned = [u.strip() for u in urls if u.strip() and not u.strip().startswith("#")]
    return [check_business(u) for u in cleaned]
