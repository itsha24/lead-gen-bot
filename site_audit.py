"""
Free website audit for a business lead: one visit to the homepage (plus the
contact page if no email is on it), looking for things you could fix or build.

Findings are facts the outreach message can mention truthfully, e.g.
"not secure (no HTTPS)" or "no online booking", never guesses.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import TECH_SIGNATURES

TIMEOUT_SECONDS = 15
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "fr-CA,fr;q=0.9,en-CA;q=0.8",
}

# Online booking / ordering / store tools — if none are found, that's an opening.
ORDERING_BOOKING = {
    "Shopify store": ["cdn.shopify.com", "myshopify.com"],
    "Square": ["squareup.com", "square.site", "squarespace-commerce"],
    "Toast": ["toasttab.com"],
    "Uber Eats": ["ubereats.com"],
    "DoorDash": ["doordash.com"],
    "SkipTheDishes": ["skipthedishes.com"],
    "OpenTable": ["opentable.com", "opentable.ca"],
    "Resy": ["resy.com"],
    "Libro": ["libroreserve.com"],
    "Fresha": ["fresha.com"],
    "Booksy": ["booksy.com"],
    "Vagaro": ["vagaro.com"],
    "Mindbody": ["mindbodyonline.com", "mindbody.io"],
    "Calendly": ["calendly.com"],
    "Acuity": ["acuityscheduling.com"],
    "GoRendezvous": ["gorendezvous.com"],
    "WooCommerce": ["woocommerce"],
}
SOCIAL_ONLY = ("facebook.com", "instagram.com", "linktr.ee", "tiktok.com", "yelp.")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
JUNK_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp)$|sentry|wixpress|example\.|domain\.com|@2x", re.IGNORECASE)
FRENCH_WORDS = re.compile(r"\b(nous|votre|accueil|rendez-vous|réservation|heures|ouvert|contactez|bienvenue|menu du jour)\b", re.IGNORECASE)


def _find(signatures: dict, haystack: str) -> list[str]:
    return [name for name, sigs in signatures.items() if sigs and any(s.lower() in haystack for s in sigs)]


def _get(url):
    return requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS, allow_redirects=True)


def _emails(soup: BeautifulSoup, text: str, domain: str) -> list[str]:
    found = [a["href"][7:].split("?")[0] for a in soup.select('a[href^="mailto:"]')]
    found += EMAIL.findall(text)
    clean = []
    for e in found:
        e = e.strip().strip(".").lower()
        if e and not JUNK_EMAIL.search(e) and e not in clean:
            clean.append(e)
    # Prefer an address on the business's own domain.
    return sorted(clean, key=lambda e: domain not in e)[:3]


def _pagespeed(url: str) -> int | None:
    key = os.environ.get("PAGESPEED_API_KEY")
    if not key:
        return None
    try:
        r = requests.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                         params={"url": url, "key": key, "strategy": "mobile"}, timeout=60)
        r.raise_for_status()
        return round(r.json()["lighthouseResult"]["categories"]["performance"]["score"] * 100)
    except Exception:
        return None


def audit_site(url: str | None) -> dict:
    """Return findings for one website (or for having none)."""
    result = {"has_website": bool(url), "reachable": False, "https": None, "mobile_friendly": None,
              "copyright_year": None, "platform": [], "ordering_booking": [], "emails": [],
              "socials": [], "language": None, "speed_score": None, "issues": [], "site_text": ""}
    if not url:
        result["issues"].append("no website")
        return result
    if any(s in url.lower() for s in SOCIAL_ONLY):
        result["issues"].append("only a social media page, no real website")
        return result
    try:
        resp = _get(url)
    except requests.RequestException:
        result["issues"].append("website doesn't load")
        return result
    if resp.status_code >= 400:
        result["issues"].append(f"website returns an error (HTTP {resp.status_code})")
        return result

    result["reachable"] = True
    final = resp.url
    soup = BeautifulSoup(resp.text, "html.parser")
    raw = resp.text.lower()
    domain = urlparse(final).netloc.replace("www.", "")

    result["https"] = final.startswith("https://")
    result["mobile_friendly"] = bool(soup.find("meta", attrs={"name": re.compile("viewport", re.I)}))
    result["platform"] = [p for p in _find(TECH_SIGNATURES, raw + str(resp.headers).lower())
                          if p not in ("Google Analytics", "Custom/Unknown")]
    result["ordering_booking"] = _find(ORDERING_BOOKING, raw)
    result["socials"] = sorted({s for s in ("facebook", "instagram", "tiktok", "linkedin")
                                if f"{s}.com" in raw})

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    result["site_text"] = text[:1500]
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", resp.text, re.I)]
    result["copyright_year"] = max(years) if years else None
    lang = (soup.html.get("lang") if soup.html else "") or ""
    result["language"] = "fr" if lang.lower().startswith("fr") or len(FRENCH_WORDS.findall(text)) >= 3 else "en"

    result["emails"] = _emails(soup, text, domain)
    if not result["emails"]:  # many sites only list the email on a contact page
        link = soup.find("a", href=re.compile(r"contact|nous-joindre|joindre", re.I))
        if link:
            try:
                c = _get(urljoin(final, link["href"]))
                csoup = BeautifulSoup(c.text, "html.parser")
                result["emails"] = _emails(csoup, csoup.get_text(" "), domain)
            except requests.RequestException:
                pass

    result["speed_score"] = _pagespeed(final)

    issues = result["issues"]
    if not result["https"]:
        issues.append("not secure (no HTTPS) — browsers show a warning")
    if not result["mobile_friendly"]:
        issues.append("not mobile-friendly")
    if result["copyright_year"] and result["copyright_year"] <= date.today().year - 3:
        issues.append(f"looks outdated (footer says © {result['copyright_year']})")
    if result["speed_score"] is not None and result["speed_score"] < 50:
        issues.append(f"slow on phones (speed score {result['speed_score']}/100)")
    if not result["ordering_booking"]:
        issues.append("no online booking, ordering or store found")
    if "HubSpot" in result["platform"] and "Zapier Webhook" not in result["platform"]:
        issues.append("uses HubSpot but no visible automation")
    return result


def audit_businesses(businesses: list[dict], workers: int = 6) -> list[dict]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        audits = list(pool.map(lambda b: audit_site(b.get("website")), businesses))
    for b, a in zip(businesses, audits):
        b["audit"] = a
    print(f"[site_audit] Audited {len(businesses)} businesses: "
          f"{sum(1 for a in audits if not a['has_website'])} without a website, "
          f"{sum(1 for a in audits if a['has_website'] and not a['reachable'])} with a broken or social-only site.")
    return businesses
