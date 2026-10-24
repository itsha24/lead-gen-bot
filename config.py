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
# Jobs below this score (0-100) are still saved, so they're never re-scored,
# and show as "Low fit" on the dashboard (filter them out with the status chips).
MIN_FIT_SCORE = 40

# Max jobs sent to the AI per run (newest first). Keeps runs short and inside the
# free Gemini quota; anything left over is scored on the next run.
MAX_JOBS_PER_RUN = 40

# Emails go out for jobs whose PRIORITY (fit score + freshness bonus, see ranking.py)
# is at least EMAIL_MIN_SCORE — so a strong fit posted today beats an old 85 —
# and whose fit score alone is at least EMAIL_MIN_FIT, so freshness can't push a weak match in.
EMAIL_MIN_SCORE = 85
EMAIL_MIN_FIT = 70

# Only fetch postings this many days old or newer. Applying in the first day or two
# matters most, and the bot runs daily, so 3 days leaves margin for a missed run.
JOB_MAX_DAYS_OLD = 3

# Company career pages (companies.yaml) are fewer and higher quality, so look back
# a little further: a week-old opening at a company you picked is still worth seeing.
COMPANY_MAX_DAYS_OLD = 7

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

# Optional page-speed check in the site audit: add a free PageSpeed Insights key as
# a PAGESPEED_API_KEY env var / secret. Without it the audit skips speed (faster).

# ---- Client leads (weekly "full" run) ----
# What to search for and what you sell live in services.yaml.
# Leads sent to the AI per run (most promising first); the rest wait a week.
MAX_LEADS_PER_RUN = 25
