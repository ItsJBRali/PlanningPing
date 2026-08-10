"""Public contracts shared by PlanningPing's UI and services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from threading import Event
from typing import Callable, Generic, Literal, Protocol, TypeVar, runtime_checkable


ApplicationSortField = Literal[
    "reference",
    "council",
    "application_date",
    "received_date",
    "validated_date",
    "description",
    "address",
    "postcode",
    "status",
]
SortDirection = Literal["asc", "desc"]
SearchEventKind = Literal[
    "started",
    "council_started",
    "council_finished",
    "application_saved",
    "warning",
    "completed",
    "cancelled",
]
SearchStatus = Literal["completed", "cancelled", "completed_with_issues"]

T = TypeVar("T")

APPLICATION_SORT_FIELDS: frozenset[str] = frozenset(
    {
        "reference",
        "council",
        "application_date",
        "description",
        "received_date",
        "validated_date",
        "address",
        "postcode",
        "status",
    }
)
SORT_DIRECTIONS: frozenset[str] = frozenset({"asc", "desc"})
SEARCH_EVENT_KINDS: frozenset[str] = frozenset(
    {"started", "council_started", "council_finished", "application_saved", "warning", "completed", "cancelled"}
)
SEARCH_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "completed_with_issues"})


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """The inclusive date range and local GeoJSON boundary for a search."""

    boundary_geojson_path: Path
    start_date: date
    end_date: date
    exclusion_phrases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        boundary_path = Path(self.boundary_geojson_path)
        if not boundary_path.is_file() or boundary_path.suffix.casefold() != ".geojson":
            raise ValueError("boundary_geojson_path must be an existing .geojson file")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")

        normalized_phrases: list[str] = []
        seen_phrases: set[str] = set()
        for phrase in self.exclusion_phrases:
            normalized = phrase.strip()
            comparison_key = normalized.casefold()
            if normalized and comparison_key not in seen_phrases:
                normalized_phrases.append(normalized)
                seen_phrases.add(comparison_key)

        object.__setattr__(self, "boundary_geojson_path", boundary_path)
        object.__setattr__(self, "exclusion_phrases", tuple(normalized_phrases))


@dataclass(frozen=True, slots=True)
class ApplicationFilters:
    """The supported filters, page, and ordering for application results."""

    reference: str = ""
    address: str = ""
    postcode: str = ""
    application_date: date | None = None
    keywords: str = ""
    council: str = ""
    page: int = 1
    page_size: int = 50
    sort_by: ApplicationSortField = "application_date"
    sort_direction: SortDirection = "desc"

    def __post_init__(self) -> None:
        if type(self.page) is not int:
            raise ValueError("page must be an integer")
        if type(self.page_size) is not int:
            raise ValueError("page_size must be an integer")
        if self.page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= self.page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")
        if self.sort_by not in APPLICATION_SORT_FIELDS:
            raise ValueError(f"sort_by must be one of {sorted(APPLICATION_SORT_FIELDS)}")
        if self.sort_direction not in SORT_DIRECTIONS:
            raise ValueError(f"sort_direction must be one of {sorted(SORT_DIRECTIONS)}")


@dataclass(frozen=True, slots=True)
class SearchEvent:
    """A progress, warning, or completion update emitted during a search."""

    kind: SearchEventKind
    run_id: int | None = None
    council: str | None = None
    completed: int = 0
    total: int = 0
    saved_count: int = 0
    message: str | None = None
    def __post_init__(self) -> None:
        if self.kind not in SEARCH_EVENT_KINDS:
            raise ValueError(f"kind must be one of {sorted(SEARCH_EVENT_KINDS)}")


@dataclass(frozen=True, slots=True)
class SearchSummary:
    """The final outcome of a completed or cancelled search run."""

    run_id: int
    status: SearchStatus
    total_councils: int
    searched_councils: int
    saved_applications: int
    empty_councils: int
    failed_councils: int
    started_at: datetime
    finished_at: datetime
    def __post_init__(self) -> None:
        if self.status not in SEARCH_STATUSES:
            raise ValueError(f"status must be one of {sorted(SEARCH_STATUSES)}")


@dataclass(frozen=True, slots=True)
class ApplicationRow:
    """An application returned by the applications query service."""

    application_id: int
    reference: str
    council: str
    application_date: date | None
    received_date: date | None
    validated_date: date | None
    description: str
    address: str
    postcode: str
    status: str | None
    application_url: str
    council_url: str


@dataclass(frozen=True, slots=True)
class IssueRow:
    """A bounded-completeness issue recorded during a search run."""

    issue_id: int
    run_id: int | None
    timestamp: datetime
    council: str
    portal_family: str
    outcome: str
    error_type: str | None
    message: str


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """A single page of a query result."""

    items: tuple[T, ...]
    page: int
    page_size: int
    total_items: int


@runtime_checkable
class SearchService(Protocol):
    """Runs a search while emitting queue-safe progress events."""

    def run(
        self,
        request: SearchRequest,
        emit: Callable[[SearchEvent], None],
        cancel_event: Event,
    ) -> SearchSummary:
        """Run the requested search and return its terminal summary."""


@runtime_checkable
class ApplicationQueryService(Protocol):
    """Provides pageable application results for the desktop UI."""

    def search(self, filters: ApplicationFilters) -> Page[ApplicationRow]:
        """Return applications matching the supplied filters."""


@runtime_checkable
class IssueQueryService(Protocol):
    """Provides issues recorded by a particular search run or all runs."""

    def list_issues(self, run_id: int | None = None) -> tuple[IssueRow, ...]:
        """Return recorded issues, optionally scoped to one run."""


@dataclass(frozen=True, slots=True)
class AppServices:
    """The service dependencies supplied to the desktop application."""

    search: SearchService
    applications: ApplicationQueryService
    issues: IssueQueryService
