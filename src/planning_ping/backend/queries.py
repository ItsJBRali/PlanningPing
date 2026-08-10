"""Frozen application and issue query-service implementations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from planning_ping.contracts import ApplicationFilters, ApplicationRow, IssueRow, Page

from .persistence import PlanningDatabase

_SORT_COLUMNS = {
    "reference": "a.reference",
    "council": "c.name",
    "application_date": "COALESCE(a.received_date, a.validated_date)",
    "received_date": "a.received_date",
    "validated_date": "a.validated_date",
    "description": "a.description",
    "address": "a.address",
    "postcode": "a.postcode",
    "status": "a.status",
}


class SqliteApplicationQueryService:
    def __init__(self, database: PlanningDatabase) -> None:
        self._database = database

    def search(self, filters: ApplicationFilters) -> Page[ApplicationRow]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for value, expression in (
            (filters.reference, "a.reference"),
            (filters.address, "a.address"),
            (filters.postcode, "a.postcode"),
            (filters.keywords, "a.description"),
        ):
            normalized = value.strip()
            if normalized:
                clauses.append(f"{expression} LIKE ? ESCAPE '\\' COLLATE NOCASE")
                parameters.append(f"%{_escape_like(normalized)}%")
        if filters.application_date:
            clauses.append("COALESCE(a.received_date, a.validated_date) = ?")
            parameters.append(filters.application_date.isoformat())
        if filters.council.strip():
            clauses.append("c.name = ? COLLATE NOCASE")
            parameters.append(filters.council.strip())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        base = f"FROM applications a JOIN councils c ON c.id=a.council_id {where}"
        with self._database._lock:
            total = int(self._database.connection.execute(f"SELECT COUNT(*) {base}", parameters).fetchone()[0])
            sort_column = _SORT_COLUMNS[filters.sort_by]
            direction = filters.sort_direction.upper()
            offset = (filters.page - 1) * filters.page_size
            rows = self._database.connection.execute(
                f"""SELECT a.id, a.reference, c.name AS council,
                    COALESCE(a.received_date, a.validated_date) AS application_date,
                    a.received_date, a.validated_date, a.description, a.address,
                    a.postcode, a.status, a.application_url, a.council_url
                    {base}
                    ORDER BY ({sort_column} IS NULL) ASC, {sort_column} {direction}, a.id ASC
                    LIMIT ? OFFSET ?""",
                (*parameters, filters.page_size, offset),
            ).fetchall()
        items = tuple(
            ApplicationRow(
                application_id=int(row["id"]),
                reference=row["reference"],
                council=row["council"],
                application_date=_parse_date(row["application_date"]),
                received_date=_parse_date(row["received_date"]),
                validated_date=_parse_date(row["validated_date"]),
                description=row["description"],
                address=row["address"],
                postcode=row["postcode"],
                status=row["status"],
                application_url=row["application_url"],
                council_url=row["council_url"],
            )
            for row in rows
        )
        return Page(items=items, page=filters.page, page_size=filters.page_size, total_items=total)


class SqliteIssueQueryService:
    def __init__(self, database: PlanningDatabase) -> None:
        self._database = database

    def list_issues(self, run_id: int | None = None) -> tuple[IssueRow, ...]:
        where = "WHERE o.outcome_status IN ('warning', 'error', 'cancelled')"
        parameters: tuple[Any, ...] = ()
        if run_id is not None:
            where += " AND o.run_id=?"
            parameters = (run_id,)
        with self._database._lock:
            rows = self._database.connection.execute(
                f"""SELECT o.id, o.run_id, o.finished_at, c.name AS council,
                    o.portal_family, o.outcome_status, o.exception_type,
                    COALESCE(o.exception_message, '') AS message
                    FROM council_search_outcomes o JOIN councils c ON c.id=o.council_id
                    {where} ORDER BY o.finished_at DESC, o.id DESC""",
                parameters,
            ).fetchall()
        return tuple(
            IssueRow(
                issue_id=int(row["id"]),
                run_id=int(row["run_id"]),
                timestamp=datetime.fromisoformat(row["finished_at"]),
                council=row["council"],
                portal_family=row["portal_family"],
                outcome=row["outcome_status"],
                error_type=row["exception_type"],
                message=row["message"],
            )
            for row in rows
        )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
