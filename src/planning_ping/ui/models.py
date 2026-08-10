"""Headless presentation models for the desktop interface."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from math import ceil
from pathlib import Path
from urllib.parse import urlparse

from planning_ping.contracts import (
    APPLICATION_SORT_FIELDS,
    ApplicationFilters,
    ApplicationQueryService,
    ApplicationRow,
    IssueQueryService,
    IssueRow,
    SearchRequest,
)


NAVIGATION_ROUTES: tuple[str, ...] = (
    "home",
    "search_new",
    "search_saved",
    "send_applications",
    "view_issues",
)


def parse_drop_paths(data: str, splitlist: Callable[[str], Iterable[str]]) -> tuple[Path, ...]:
    """Parse Tk drop data using Tcl's own list parser."""

    return tuple(Path(item) for item in splitlist(data))


def validate_geojson_selection(paths: Iterable[Path | str]) -> Path:
    candidates = tuple(Path(path) for path in paths)
    if len(candidates) != 1:
        raise ValueError("Select exactly one GeoJSON file")
    selected = candidates[0]
    if not selected.is_file() or selected.suffix.casefold() != ".geojson":
        raise ValueError("Select an existing .geojson file")
    return selected


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError(f"{field_name} must use YYYY-MM-DD") from error


def build_search_request(
    path: str,
    start_date: str,
    end_date: str,
    exclusion_phrases: str,
) -> SearchRequest:
    return SearchRequest(
        boundary_geojson_path=Path(path.strip()),
        start_date=_parse_date(start_date, "Search From"),
        end_date=_parse_date(end_date, "Search To"),
        exclusion_phrases=tuple(exclusion_phrases.splitlines()),
    )


def is_openable_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def open_url_if_safe(url: str, opener: Callable[[str], object]) -> bool:
    """Open an application URL only after an explicit caller invokes this function."""

    if not is_openable_url(url):
        return False
    opener(url.strip())
    return True


class SavedApplicationsModel:
    FILTER_NAMES = ("reference", "address", "postcode", "application_date", "keywords", "council")

    def __init__(self, service: ApplicationQueryService, page_size: int = 50) -> None:
        self._service = service
        self.page_size = page_size
        self.filter_values = {name: "" for name in self.FILTER_NAMES}
        self.page = 1
        self.total_items = 0
        self.total_pages = 1
        self.sort_by = "application_date"
        self.sort_direction = "desc"
        self.rows: tuple[ApplicationRow, ...] = ()
        self.loading = False
        self.error_message = ""

    def set_filters(self, **values: str) -> None:
        unknown = set(values).difference(self.FILTER_NAMES)
        if unknown:
            raise ValueError(f"Unknown filters: {sorted(unknown)}")
        self.filter_values.update({key: value.strip() for key, value in values.items()})
        self.page = 1

    def clear_filters(self) -> None:
        self.filter_values = {name: "" for name in self.FILTER_NAMES}
        self.page = 1

    def _filters(self) -> ApplicationFilters:
        raw_date = self.filter_values["application_date"]
        application_date = _parse_date(raw_date, "Application Date") if raw_date else None
        return ApplicationFilters(
            reference=self.filter_values["reference"],
            address=self.filter_values["address"],
            postcode=self.filter_values["postcode"],
            application_date=application_date,
            keywords=self.filter_values["keywords"],
            council=self.filter_values["council"],
            page=self.page,
            page_size=self.page_size,
            sort_by=self.sort_by,  # type: ignore[arg-type]
            sort_direction=self.sort_direction,  # type: ignore[arg-type]
        )

    def search(self) -> None:
        self.loading = True
        self.error_message = ""
        try:
            result = self._service.search(self._filters())
        except Exception as error:
            self.rows = ()
            self.page = 1
            self.total_items = 0
            self.total_pages = 1
            self.error_message = str(error) or error.__class__.__name__
        else:
            self.rows = result.items
            self.page = result.page
            self.total_items = result.total_items
            self.total_pages = max(1, ceil(result.total_items / result.page_size))
        finally:
            self.loading = False

    def sort(self, column: str) -> None:
        if column not in APPLICATION_SORT_FIELDS:
            raise ValueError(f"{column!r} is not a sortable column")
        if self.sort_by == column:
            self.sort_direction = "asc" if self.sort_direction == "desc" else "desc"
        else:
            self.sort_by = column
            self.sort_direction = "asc"
        self.page = 1
        self.search()

    def next_page(self) -> None:
        if self.page < self.total_pages:
            self.page += 1
            self.search()

    def previous_page(self) -> None:
        if self.page > 1:
            self.page -= 1
            self.search()


class IssueResultsModel:
    def __init__(self, service: IssueQueryService) -> None:
        self._service = service
        self.rows: tuple[IssueRow, ...] = ()
        self.loading = False
        self.error_message = ""

    def load(self, run_id_text: str = "", outcome: str = "") -> None:
        self.rows = ()
        stripped_run = run_id_text.strip()
        if stripped_run:
            try:
                run_id = int(stripped_run)
            except ValueError as error:
                raise ValueError("Run must be a whole number") from error
            if run_id < 0:
                raise ValueError("Run must be a whole number")
        else:
            run_id = None
        self.loading = True
        self.error_message = ""
        try:
            rows = self._service.list_issues(run_id)
        except Exception as error:
            self.rows = ()
            self.error_message = str(error) or error.__class__.__name__
        else:
            normalized_outcome = outcome.strip().casefold()
            self.rows = tuple(
                row for row in rows if not normalized_outcome or row.outcome.casefold() == normalized_outcome
            )
        finally:
            self.loading = False
