"""Normalized backend domain records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass(frozen=True, slots=True)
class Council:
    code: str
    name: str
    country: str
    portal_family: str
    scraper_type: str
    base_url: str
    listing_url: str | None
    planning_url: str
    boundary: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApplicationDocument:
    title: str
    document_url: str
    document_type: str | None = None
    date: date | None = None
    size: str | None = None
    description: str | None = None
    source_url: str | None = None


@dataclass(slots=True)
class PlanningApplication:
    council_code: str
    reference: str
    authority: str = ""
    application_url: str = ""
    council_url: str = ""
    received_date: date | None = None
    validated_date: date | None = None
    description: str = ""
    address: str = ""
    postcode: str = ""
    status: str | None = None
    decision: str | None = None
    applicant: str | None = None
    agent: str | None = None
    case_officer: str | None = None
    ward: str | None = None
    parish: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    location_match_quality: str = "council_overlap"
    scraped_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict)
    documents: tuple[ApplicationDocument, ...] = ()

    @property
    def application_date(self) -> date | None:
        return self.received_date or self.validated_date

    @property
    def identity(self) -> tuple[str, str]:
        return self.council_code, self.reference_key

    @property
    def reference_key(self) -> str:
        return self.reference.strip().casefold()
