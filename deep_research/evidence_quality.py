"""Structural evidence gates, not a substitute for citation entailment checks."""
from datetime import date
from urllib.parse import urlsplit

from deep_research.citation_check import is_verified


def usable_evidence(item: object) -> bool:
    """Structurally valid and not rejected by citation checks (see citation_check)."""
    if not isinstance(item, dict) or not str(item.get("claim") or "").strip():
        return False
    if not is_verified(item):
        return False
    try:
        url = urlsplit(str(item.get("url") or ""))
        return url.scheme in {"http", "https"} and bool(url.hostname)
    except ValueError:
        return False


def evidence_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def in_time_scope(item: dict, scope: dict) -> bool:
    """Require explicit ISO bounds; an arbitrary date is not proof of recency."""
    published = evidence_date(item.get("date"))
    start = evidence_date(scope.get("date_start"))
    end = evidence_date(scope.get("date_end"))
    return bool(published and start and end and start <= published <= end <= date.today())
