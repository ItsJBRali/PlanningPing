from __future__ import annotations

from collections.abc import Callable
from threading import Event

from planning_ping.contracts import (
    ApplicationFilters,
    ApplicationRow,
    IssueRow,
    Page,
    SearchEvent,
    SearchRequest,
    SearchSummary,
)


class FakeSearchService:
    def __init__(
        self,
        summary: SearchSummary,
        events: tuple[SearchEvent, ...] = (),
        wait_for_cancel: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.summary = summary
        self.events = events
        self.wait_for_cancel = wait_for_cancel
        self.error = error
        self.requests: list[SearchRequest] = []
        self.cancel_events: list[Event] = []

    def run(
        self,
        request: SearchRequest,
        emit: Callable[[SearchEvent], None],
        cancel_event: Event,
    ) -> SearchSummary:
        self.requests.append(request)
        self.cancel_events.append(cancel_event)
        for event in self.events:
            emit(event)
        if self.wait_for_cancel:
            cancel_event.wait(2)
        if self.error is not None:
            raise self.error
        return self.summary


class FakeApplicationQueryService:
    def __init__(self, pages: tuple[Page[ApplicationRow], ...]) -> None:
        self.pages = pages
        self.filters: list[ApplicationFilters] = []

    def search(self, filters: ApplicationFilters) -> Page[ApplicationRow]:
        self.filters.append(filters)
        return self.pages[min(len(self.filters) - 1, len(self.pages) - 1)]


class FakeIssueQueryService:
    def __init__(self, rows: tuple[IssueRow, ...]) -> None:
        self.rows = rows
        self.run_ids: list[int | None] = []

    def list_issues(self, run_id: int | None = None) -> tuple[IssueRow, ...]:
        self.run_ids.append(run_id)
        return tuple(row for row in self.rows if run_id is None or row.run_id == run_id)
