"""Application date, exclusion, and source-precedence rules."""

from __future__ import annotations

from datetime import date
from typing import Iterable

from .models import PlanningApplication


def application_matches_request(
    application: PlanningApplication,
    start_date: date,
    end_date: date,
    exclusion_phrases: tuple[str, ...],
) -> bool:
    application_date = application.application_date
    if application_date is None or not start_date <= application_date <= end_date:
        return False
    description = application.description.casefold()
    return not any(phrase.casefold() in description for phrase in exclusion_phrases if phrase)


def reconcile_applications(
    primary: Iterable[PlanningApplication],
    planit: Iterable[PlanningApplication],
) -> list[PlanningApplication]:
    """Return stable, de-duplicated results with primary records winning."""

    merged: list[PlanningApplication] = []
    seen: set[tuple[str, str]] = set()
    for application in (*tuple(primary), *tuple(planit)):
        key = application.identity
        if not key[1] or key in seen:
            continue
        seen.add(key)
        merged.append(application)
    return merged
