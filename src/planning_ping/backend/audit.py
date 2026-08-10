"""Deterministic authority-catalogue validation and report rows."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import urlsplit

from .geometry import validate_geojson
from .models import Council

SUPPORTED_COUNTRIES = frozenset({"England", "Wales", "Scotland"})


@dataclass(frozen=True, slots=True)
class AuditRow:
    authority: str
    family: str
    endpoint: str
    checked_at: str
    result: str
    error: str | None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AuditReport:
    rows: tuple[AuditRow, ...]

    @property
    def ok(self) -> bool:
        return all(row.result in {"pass", "not_checked"} for row in self.rows)

    @property
    def result_counts(self) -> Counter[str]:
        return Counter(row.result for row in self.rows)

    def to_json(self) -> str:
        return json.dumps([row.to_dict() for row in self.rows], indent=2, sort_keys=True)

    def to_csv(self) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=("authority", "family", "endpoint", "checked_at", "result", "error"))
        writer.writeheader()
        writer.writerows(row.to_dict() for row in self.rows)
        return output.getvalue()


def audit_catalogue(
    councils: Iterable[Council],
    *,
    supported_families: frozenset[str] | set[str],
    adapter_check: Callable[[Council], object] | None = None,
    checked_at: datetime | None = None,
) -> AuditReport:
    active = [item for item in councils if item.metadata.get("active", True) is not False]
    code_counts = Counter(item.code for item in active)
    timestamp = (checked_at or datetime.now(timezone.utc).replace(microsecond=0)).isoformat()
    rows: list[AuditRow] = []
    for council in sorted(active, key=lambda item: (item.name.casefold(), item.code)):
        errors: list[str] = []
        if not council.code:
            errors.append("missing stable code")
        elif code_counts[council.code] > 1:
            errors.append("duplicate stable code")
        if council.country not in SUPPORTED_COUNTRIES:
            errors.append(f"unsupported country {council.country!r}")
        endpoint = council.planning_url or council.listing_url or council.base_url
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("invalid endpoint URL")
        try:
            validate_geojson(council.boundary)
        except ValueError as exc:
            errors.append(f"invalid boundary: {exc}")
        if council.portal_family not in supported_families:
            errors.append(f"unmapped portal family {council.portal_family!r}")
        elif adapter_check is not None:
            try:
                adapter_check(council)
            except Exception as exc:
                errors.append(f"adapter mapping failed: {exc}")
        rows.append(
            AuditRow(
                authority=council.name,
                family=council.portal_family,
                endpoint=endpoint,
                checked_at=timestamp,
                result="fail" if errors else "pass",
                error="; ".join(errors) if errors else None,
            )
        )
    return AuditReport(tuple(rows))
