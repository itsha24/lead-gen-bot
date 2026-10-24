"""
Priority = AI fit score + a freshness bonus.

The fit score stays an honest "how well does this job fit me". Priority is what
to act on *now*: applying in the first day or two matters a lot, so fresh
postings get a boost and older ones slowly sink. The dashboard mirrors this
formula in JavaScript (docs/index.html, freshnessBonus) — keep them in sync.
"""

from datetime import datetime, timezone


def hours_since(posted: str | None) -> float | None:
    if not posted:
        return None
    try:
        dt = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)


def freshness_bonus(posted: str | None) -> int:
    h = hours_since(posted)
    if h is None:
        return 0
    if h < 24:
        return 12   # posted today: the window recruiters and ATSs favour most
    if h < 48:
        return 8
    if h < 72:
        return 4
    days = h / 24
    if days <= 7:
        return 0
    return -min(15, int(days - 7))  # loses a point a day after a week, at most -15


def priority(job: dict) -> int:
    return int(job.get("fit_score") or 0) + freshness_bonus(job.get("posted"))


def age_text(posted: str | None) -> str:
    h = hours_since(posted)
    if h is None:
        return ""
    if h < 1:
        return "just posted"
    if h < 24:
        return f"posted {int(h)}h ago"
    d = int(h // 24)
    return "posted 1 day ago" if d == 1 else f"posted {d} days ago"
