"""Synchronous, event-emitting council search orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import Event
from typing import Callable, Iterable, Protocol

from planning_ping.contracts import SearchEvent, SearchRequest, SearchSummary

from .catalogue import AuthorityCatalogue
from .filtering import application_matches_request, reconcile_applications
from .geometry import load_geojson, location_match_quality, validate_geojson
from .models import Council, PlanningApplication
from .persistence import PlanningDatabase


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass(frozen=True, slots=True)
class AuthoritySearchResult:
    applications: tuple[PlanningApplication, ...] = ()
    warnings: tuple[str, ...] = ()


class AuthoritySearcher(Protocol):
    def search_primary(
        self,
        council: Council,
        start_date: date,
        end_date: date,
        cancel_event: Event,
    ) -> AuthoritySearchResult: ...

    def search_planit(
        self,
        council: Council,
        start_date: date,
        end_date: date,
        cancel_event: Event,
    ) -> AuthoritySearchResult: ...


@dataclass(slots=True)
class _CouncilState:
    council: Council
    started_at: datetime
    primary: AuthoritySearchResult | None = None
    primary_error: Exception | None = None


class PlanningSearchService:
    """Runs on a UI worker thread and commits one council at a time."""

    def __init__(
        self,
        database: PlanningDatabase,
        catalogue: AuthorityCatalogue,
        searcher: AuthoritySearcher,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._database = database
        self._catalogue = catalogue
        self._searcher = searcher
        self._clock = clock

    def run(
        self,
        request: SearchRequest,
        emit: Callable[[SearchEvent], None],
        cancel_event: Event,
    ) -> SearchSummary:
        started_at = self._clock()
        boundary = load_geojson(request.boundary_geojson_path)
        uploaded_geometries = validate_geojson(boundary)
        councils = self._catalogue.select(boundary)
        run_id = self._database.begin_search(
            input_path=str(request.boundary_geojson_path),
            input_hash=hashlib.sha256(request.boundary_geojson_path.read_bytes()).hexdigest(),
            start_date=request.start_date,
            end_date=request.end_date,
            exclusions=request.exclusion_phrases,
            total_councils=len(councils),
        )
        emit(SearchEvent(kind="started", run_id=run_id, total=len(councils), message="Search started"))

        states: list[_CouncilState] = []
        for council in councils:
            if cancel_event.is_set():
                break
            council_started_at = self._clock()
            emit(
                SearchEvent(
                    kind="council_started",
                    run_id=run_id,
                    council=council.name,
                    completed=len(states),
                    total=len(councils),
                )
            )
            state = _CouncilState(council, council_started_at)
            try:
                state.primary = self._searcher.search_primary(
                    council, request.start_date, request.end_date, cancel_event
                )
            except Exception as exc:
                state.primary_error = exc
            states.append(state)

        if cancel_event.is_set():
            return self._finish_cancelled(
                run_id,
                started_at,
                councils,
                states,
                uploaded_geometries,
                request,
                emit,
            )

        saved_count = 0
        empty_count = 0
        failed_count = 0
        completed = 0
        for state in states:
            if cancel_event.is_set():
                remaining = states[completed:]
                return self._finish_cancelled(
                    run_id,
                    started_at,
                    councils,
                    remaining,
                    uploaded_geometries,
                    request,
                    emit,
                    already_completed=completed,
                    already_saved=saved_count,
                    already_empty=empty_count,
                    already_failed=failed_count,
                )

            planit: AuthoritySearchResult | None = None
            planit_error: Exception | None = None
            try:
                planit = self._searcher.search_planit(
                    state.council, request.start_date, request.end_date, cancel_event
                )
            except Exception as exc:
                planit_error = exc

            if cancel_event.is_set():
                return self._finish_cancelled(
                    run_id,
                    started_at,
                    councils,
                    states[completed:],
                    uploaded_geometries,
                    request,
                    emit,
                    already_completed=completed,
                    already_saved=saved_count,
                    already_empty=empty_count,
                    already_failed=failed_count,
                )

            primary_items = state.primary.applications if state.primary else ()
            planit_items = planit.applications if planit else ()
            merged = reconcile_applications(primary_items, planit_items)
            matched = self._matching_applications(merged, uploaded_geometries, request)
            problem = self._problem_for_sources(state, planit, planit_error)
            if state.primary_error is not None and planit_error is not None:
                outcome = "error"
                failed_count += 1
            elif problem is not None:
                outcome = "warning"
            elif matched:
                outcome = "success"
            else:
                outcome = "empty"
                empty_count += 1

            ids = self._database.save_council_result(
                run_id,
                state.council,
                matched,
                outcome=outcome,
                exception=problem,
                started_at=state.started_at,
                finished_at=self._clock(),
            )
            for application, _application_id in zip(matched, ids):
                saved_count += 1
                emit(
                    SearchEvent(
                        kind="application_saved",
                        run_id=run_id,
                        council=state.council.name,
                        completed=completed,
                        total=len(councils),
                        saved_count=saved_count,
                        message=application.reference,
                    )
                )
            if problem is not None:
                emit(
                    SearchEvent(
                        kind="warning",
                        run_id=run_id,
                        council=state.council.name,
                        completed=completed,
                        total=len(councils),
                        saved_count=saved_count,
                        message=str(problem),
                    )
                )
            completed += 1
            emit(
                SearchEvent(
                    kind="council_finished",
                    run_id=run_id,
                    council=state.council.name,
                    completed=completed,
                    total=len(councils),
                    saved_count=saved_count,
                )
            )

        status = "completed_with_issues" if failed_count or self._run_has_warnings(run_id) else "completed"
        finished_at = self._clock()
        self._database.finish_search(
            run_id,
            status=status,
            searched_councils=completed,
            saved_applications=saved_count,
            empty_councils=empty_count,
            failed_councils=failed_count,
            finished_at=finished_at,
        )
        summary = SearchSummary(
            run_id=run_id,
            status=status,
            total_councils=len(councils),
            searched_councils=completed,
            saved_applications=saved_count,
            empty_councils=empty_count,
            failed_councils=failed_count,
            started_at=started_at,
            finished_at=finished_at,
        )
        emit(
            SearchEvent(
                kind="completed",
                run_id=run_id,
                completed=completed,
                total=len(councils),
                saved_count=saved_count,
                message="Search completed",
            )
        )
        return summary

    def _finish_cancelled(
        self,
        run_id: int,
        started_at: datetime,
        councils: list[Council],
        states: Iterable[_CouncilState],
        uploaded_geometries: list[dict[str, object]],
        request: SearchRequest,
        emit: Callable[[SearchEvent], None],
        *,
        already_completed: int = 0,
        already_saved: int = 0,
        already_empty: int = 0,
        already_failed: int = 0,
    ) -> SearchSummary:
        completed = already_completed
        saved_count = already_saved
        empty_count = already_empty
        failed_count = already_failed
        for state in states:
            primary_items = state.primary.applications if state.primary else ()
            matched = self._matching_applications(primary_items, uploaded_geometries, request)
            problem = state.primary_error
            ids = self._database.save_council_result(
                run_id,
                state.council,
                matched,
                outcome="cancelled",
                exception=problem,
                started_at=state.started_at,
                finished_at=self._clock(),
            )
            for application, _application_id in zip(matched, ids):
                saved_count += 1
                emit(
                    SearchEvent(
                        kind="application_saved",
                        run_id=run_id,
                        council=state.council.name,
                        completed=completed,
                        total=len(councils),
                        saved_count=saved_count,
                        message=application.reference,
                    )
                )
        finished_at = self._clock()
        self._database.finish_search(
            run_id,
            status="cancelled",
            searched_councils=completed,
            saved_applications=saved_count,
            empty_councils=empty_count,
            failed_councils=failed_count,
            finished_at=finished_at,
        )
        summary = SearchSummary(
            run_id=run_id,
            status="cancelled",
            total_councils=len(councils),
            searched_councils=completed,
            saved_applications=saved_count,
            empty_councils=empty_count,
            failed_councils=failed_count,
            started_at=started_at,
            finished_at=finished_at,
        )
        emit(
            SearchEvent(
                kind="cancelled",
                run_id=run_id,
                completed=completed,
                total=len(councils),
                saved_count=saved_count,
                message="Search cancelled",
            )
        )
        return summary

    @staticmethod
    def _problem_for_sources(
        state: _CouncilState,
        planit: AuthoritySearchResult | None,
        planit_error: Exception | None,
    ) -> Exception | None:
        messages: list[str] = []
        if state.primary_error:
            messages.append(f"Primary search failed: {state.primary_error}")
        if state.primary:
            messages.extend(state.primary.warnings)
        if planit_error:
            messages.append(f"PlanIt reconciliation failed: {planit_error}")
        if planit:
            messages.extend(planit.warnings)
        return RuntimeError("; ".join(messages)) if messages else None

    @staticmethod
    def _matching_applications(
        applications: Iterable[PlanningApplication],
        uploaded_geometries: list[dict[str, object]],
        request: SearchRequest,
    ) -> list[PlanningApplication]:
        matched: list[PlanningApplication] = []
        for application in applications:
            if not application_matches_request(
                application, request.start_date, request.end_date, request.exclusion_phrases
            ):
                continue
            quality = location_match_quality(
                application.longitude, application.latitude, uploaded_geometries
            )
            if quality is None:
                continue
            application.location_match_quality = quality
            matched.append(application)
        return matched

    def _run_has_warnings(self, run_id: int) -> bool:
        with self._database._lock:
            row = self._database.connection.execute(
                "SELECT 1 FROM council_search_outcomes WHERE run_id=? AND outcome_status='warning' LIMIT 1",
                (run_id,),
            ).fetchone()
        return row is not None
