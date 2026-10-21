# ---- Job search keywords ----
# These drive the "Job Leads" tab — this is YOUR personal job search
# (QA / backend developer roles), matched to your resume skills.
# Add/remove phrases to tune what counts as a relevant posting.
JOB_KEYWORDS = [
    "QA developer",
    "QA automation engineer",
    "software developer in test",
    "backend developer",
    "Java developer",
    "C# developer",
    "ASP.NET developer",
    "Spring Boot developer",
    "test automation engineer",
    "Selenium automation",
]

# ---- AI job matcher ----
# Jobs scoring below this (0-100) are not written to the sheet.
# 40 = keep "stretch" roles and up. Raise it once you trust the scores.
MIN_FIT_SCORE = 40

# Jobs scoring at or above this get emailed to you (if email is set up).
# 85 = "apply today" only. Lower to 75 if you go days without an email.
EMAIL_MIN_SCORE = 85

# Adzuna location filter(s) — searched separately and combined.
# "" (empty string) = no location filter, i.e. Canada-wide, which also
# surfaces most remote-tagged postings.
JOB_SEARCH_LOCATIONS = ["Montreal", ""]

# Adzuna country code (ca = Canada, us = United States, gb = UK, etc.)
ADZUNA_COUNTRY = "ca"

# ---- Tech stack signatures ----
# Rough string signatures found in HTML/headers that indicate a given tool.
# Not perfect, but good enough to flag likely leads.
TECH_SIGNATURES = {
    "Shopify": ["cdn.shopify.com", "Shopify.theme", "myshopify.com"],
    "WordPress": ["wp-content", "wp-includes"],
    "Wix": ["wix.com", "wixstatic.com"],
    "Squarespace": ["squarespace.com", "static1.squarespace.com"],
    "HubSpot": ["js.hs-scripts.com", "hs-analytics.net", "hsforms.com"],
    "Google Analytics": ["gtag(", "google-analytics.com", "googletagmanager.com"],
    "Zapier Webhook": ["hooks.zapier.com"],
    "Klaviyo": ["klaviyo.com"],
    "Mailchimp": ["list-manage.com"],
    "Custom/Unknown": [],  # populated automatically if nothing else matches
}

# Optional: get a free key from Google Cloud Console to enable PageSpeed scoring.
# Leave blank to skip speed checks.
PAGESPEED_API_KEY = ""

# ---- Business discovery (Google Places API) ----
# Each entry is a natural-language search query, same as you'd type into
# Google Maps. Mix categories + neighborhoods/cities to control volume.
DISCOVERY_QUERIES = [
    "restaurants in Montreal",
    "boutique retail stores in Montreal",
    "hair salons in Montreal",
    "small law firms in Montreal",
    "fitness studios in Montreal",
]

# Max businesses to pull per query, per run (keep this modest — costs and
# PageSpeed/tech-check time scale with it). Places API free tier covers a
# solid volume before any billing kicks in.
DISCOVERY_MAX_RESULTS_PER_QUERY = 10