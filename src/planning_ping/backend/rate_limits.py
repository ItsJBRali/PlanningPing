from __future__ import annotations

from typing import Callable

from .models import Council


PLANIT_RATE_LIMIT_SCOPE = "planit"


def primary_rate_limit_scope(council: Council) -> str:
    platform = council.portal_family.casefold().strip()
    if platform in {"", "custom", "unknown"}:
        platform = council.scraper_type.casefold().strip() or "custom"
    return f"portal:{platform}"


def fallback_retry_delay(
    retry_number: int,
    jitter: Callable[[float, float], float],
) -> float:
    exponent = max(int(retry_number) - 1, 0)
    base = min(2.0 * (2**exponent), 10.0)
    return base + jitter(0.0, min(base * 0.25, 1.0))
