"""Synchronous, event-emitting council search orchestration."""

from __future__ import annotations

import hashlib
import random
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from math import ceil
from threading import Event
from time import monotonic
from typing import Callable, Iterable, Protocol

from planning_ping.contracts import SearchEvent, SearchRequest, SearchSummary

from .catalogue import AuthorityCatalogue
from .filtering import application_matches_request, reconcile_applications
from .geometry import load_geojson, location_match_quality, validate_geojson
from .http import CouncilRateLimitError
from .models import Council, PlanningApplication
from .persistence import PlanningDatabase
from .rate_limits import (
    PLANIT_RATE_LIMIT_SCOPE,
    fallback_retry_delay,
    primary_rate_limit_scope,
)
from .scheduler import CouncilPhaseScheduler, CouncilPhaseTask


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
    started_at: datetime | None = None
    primary: AuthoritySearchResult | None = None
    planit: AuthoritySearchResult | None = None
    primary_error: Exception | None = None
    planit_error: Exception | None = None
    rate_limit_attempts: dict[str, int] = field(default_factory=dict)
    deferred_phase: str | None = None
    saved: bool = False


@dataclass(slots=True)
class _RunCounters:
    completed: int = 0
    saved: int = 0
    empty: int = 0
    failed: int = 0


class PlanningSearchService:
    """Coordinates concurrent council phases and persists on the caller thread."""

    def __init__(
        self,
        database: PlanningDatabase,
        catalogue: AuthorityCatalogue,
        searcher: AuthoritySearcher,
        *,
        clock: Callable[[], datetime] = _utc_now,
        worker_limit: int = 8,
        monotonic_clock: Callable[[], float] = monotonic,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._database = database
        self._catalogue = catalogue
        self._searcher = searcher
        self._clock = clock
        self._worker_limit = min(max(int(worker_limit), 1), 8)
        self._monotonic_clock = monotonic_clock
        self._jitter = jitter

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

        states = {council.code: _CouncilState(council) for council in councils}
        counters = _RunCounters()
        scheduler = CouncilPhaseScheduler()
        for council in councils:
            scheduler.enqueue(
                CouncilPhaseTask(
                    council_code=council.code,
                    phase="primary",
                    scope=primary_rate_limit_scope(council),
                )
            )

        if councils:
            worker_count = min(self._worker_limit, len(councils))
            in_flight: dict[Future[AuthoritySearchResult], CouncilPhaseTask] = {}
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="planning-council",
            ) as executor:
                while (scheduler.has_pending() and not cancel_event.is_set()) or in_flight:
                    self._submit_ready_phases(
                        executor,
                        scheduler,
                        in_flight,
                        states,
                        request,
                        emit,
                        run_id,
                        counters.completed,
                        len(councils),
                        cancel_event,
                    )
                    if not in_flight and cancel_event.is_set():
                        break
                    completed_futures = self._wait_for_progress(
                        tuple(in_flight), scheduler, cancel_event
                    )
                    for future in completed_futures:
                        task = in_flight.pop(future)
                        scheduler.release(task)
                        self._handle_phase_result(
                            task,
                            future,
                            scheduler,
                            states,
                            request,
                            uploaded_geometries,
                            emit,
                            run_id,
                            counters,
                            cancel_event,
                        )

        if cancel_event.is_set():
            return self._finish_cancelled(
                run_id,
                started_at,
                councils,
                states.values(),
                uploaded_geometries,
                request,
                emit,
                counters,
            )

        status = "completed_with_issues" if counters.failed or self._run_has_warnings(run_id) else "completed"
        finished_at = self._clock()
        self._database.finish_search(
            run_id,
            status=status,
            searched_councils=counters.completed,
            saved_applications=counters.saved,
            empty_councils=counters.empty,
            failed_councils=counters.failed,
            finished_at=finished_at,
        )
        summary = SearchSummary(
            run_id=run_id,
            status=status,
            total_councils=len(councils),
            searched_councils=counters.completed,
            saved_applications=counters.saved,
            empty_councils=counters.empty,
            failed_councils=counters.failed,
            started_at=started_at,
            finished_at=finished_at,
        )
        emit(
            SearchEvent(
                kind="completed",
                run_id=run_id,
                completed=counters.completed,
                total=len(councils),
                saved_count=counters.saved,
                message="Search completed",
            )
        )
        return summary

    def _run_phase(
        self,
        task: CouncilPhaseTask,
        state: _CouncilState,
        request: SearchRequest,
        cancel_event: Event,
    ) -> AuthoritySearchResult:
        if cancel_event.is_set():
            return AuthoritySearchResult()
        if task.phase == "primary":
            return self._searcher.search_primary(
                state.council,
                request.start_date,
                request.end_date,
                cancel_event,
            )
        return self._searcher.search_planit(
            state.council,
            request.start_date,
            request.end_date,
            cancel_event,
        )

    def _submit_ready_phases(
        self,
        executor: ThreadPoolExecutor,
        scheduler: CouncilPhaseScheduler,
        in_flight: dict[Future[AuthoritySearchResult], CouncilPhaseTask],
        states: dict[str, _CouncilState],
        request: SearchRequest,
        emit: Callable[[SearchEvent], None],
        run_id: int,
        completed: int,
        total: int,
        cancel_event: Event,
    ) -> None:
        while len(in_flight) < self._worker_limit and not cancel_event.is_set():
            task = scheduler.acquire(now=self._monotonic_clock())
            if task is None:
                return
            if cancel_event.is_set():
                scheduler.release(task)
                return
            state = states[task.council_code]
            if task.phase == "primary" and state.started_at is None:
                state.started_at = self._clock()
                emit(
                    SearchEvent(
                        kind="council_started",
                        run_id=run_id,
                        council=state.council.name,
                        completed=completed,
                        total=total,
                    )
                )
            if state.deferred_phase == task.phase:
                emit(
                    SearchEvent(
                        kind="council_started",
                        run_id=run_id,
                        council=state.council.name,
                        completed=completed,
                        total=total,
                        message=(
                            f"Retrying {self._phase_label(task.phase)} for "
                            f"{state.council.name}"
                        ),
                    )
                )
                state.deferred_phase = None
            future = executor.submit(self._run_phase, task, state, request, cancel_event)
            in_flight[future] = task

    def _wait_for_progress(
        self,
        futures: tuple[Future[AuthoritySearchResult], ...],
        scheduler: CouncilPhaseScheduler,
        cancel_event: Event,
    ) -> set[Future[AuthoritySearchResult]]:
        now = self._monotonic_clock()
        next_ready_at = scheduler.next_ready_at(now=now)
        deadline_timeout = (
            None if next_ready_at is None else max(next_ready_at - now, 0.0)
        )
        if futures:
            timeout = (
                0.25
                if deadline_timeout is None
                else min(0.25, deadline_timeout)
            )
            completed, _pending = wait(
                futures,
                timeout=timeout,
                return_when=FIRST_COMPLETED,
            )
            return completed
        if deadline_timeout is not None:
            cancel_event.wait(deadline_timeout)
        return set()

    def _handle_phase_result(
        self,
        task: CouncilPhaseTask,
        future: Future[AuthoritySearchResult],
        scheduler: CouncilPhaseScheduler,
        states: dict[str, _CouncilState],
        request: SearchRequest,
        uploaded_geometries: list[dict[str, object]],
        emit: Callable[[SearchEvent], None],
        run_id: int,
        counters: _RunCounters,
        cancel_event: Event,
    ) -> None:
        state = states[task.council_code]
        try:
            result = future.result()
        except CouncilRateLimitError as error:
            if cancel_event.is_set():
                self._record_phase_error(state, task.phase, error)
                return
            attempt = state.rate_limit_attempts.get(task.phase, 0) + 1
            state.rate_limit_attempts[task.phase] = attempt
            retry_limit = (
                min(error.retry_limit, 2)
                if task.phase == "planit"
                else error.retry_limit
            )
            if attempt > retry_limit:
                self._record_phase_error(state, task.phase, error)
                self._queue_next_or_finalize(
                    task,
                    state,
                    scheduler,
                    request,
                    uploaded_geometries,
                    emit,
                    run_id,
                    counters,
                    len(states),
                )
                return

            delay = (
                error.retry_after_seconds
                if error.retry_after_seconds is not None
                else fallback_retry_delay(attempt, self._jitter)
            )
            ready_at = self._monotonic_clock() + max(delay, 0.0)
            scheduler.set_scope_cooldown(error.scope, ready_at=ready_at)
            scheduler.defer(task, ready_at=ready_at)
            state.deferred_phase = task.phase
            emit(
                SearchEvent(
                    kind="council_started",
                    run_id=run_id,
                    council=state.council.name,
                    completed=counters.completed,
                    total=len(states),
                    saved_count=counters.saved,
                    message=(
                        f"{state.council.name} paused by "
                        f"{self._phase_label(task.phase)} rate limit; "
                        f"retrying in {ceil(delay)} seconds"
                    ),
                )
            )
            return
        except Exception as error:
            self._record_phase_error(state, task.phase, error)
        else:
            if task.phase == "primary":
                state.primary = result
            else:
                state.planit = result

        if cancel_event.is_set():
            return
        self._queue_next_or_finalize(
            task,
            state,
            scheduler,
            request,
            uploaded_geometries,
            emit,
            run_id,
            counters,
            len(states),
        )

    @staticmethod
    def _record_phase_error(
        state: _CouncilState,
        phase: str,
        error: Exception,
    ) -> None:
        if phase == "primary":
            state.primary_error = error
        else:
            state.planit_error = error

    def _queue_next_or_finalize(
        self,
        task: CouncilPhaseTask,
        state: _CouncilState,
        scheduler: CouncilPhaseScheduler,
        request: SearchRequest,
        uploaded_geometries: list[dict[str, object]],
        emit: Callable[[SearchEvent], None],
        run_id: int,
        counters: _RunCounters,
        total: int,
    ) -> None:
        if task.phase == "primary":
            scheduler.enqueue(
                CouncilPhaseTask(
                    council_code=task.council_code,
                    phase="planit",
                    scope=PLANIT_RATE_LIMIT_SCOPE,
                )
            )
            return
        self._finalize_council(
            state,
            request,
            uploaded_geometries,
            emit,
            run_id,
            counters,
            total,
        )

    @staticmethod
    def _phase_label(phase: str) -> str:
        return "Primary portal" if phase == "primary" else "PlanIt"

    def _finalize_council(
        self,
        state: _CouncilState,
        request: SearchRequest,
        uploaded_geometries: list[dict[str, object]],
        emit: Callable[[SearchEvent], None],
        run_id: int,
        counters: _RunCounters,
        total: int,
    ) -> None:
        if state.saved:
            return
        primary_items = state.primary.applications if state.primary else ()
        planit_items = state.planit.applications if state.planit else ()
        merged = reconcile_applications(primary_items, planit_items)
        matched = self._matching_applications(merged, uploaded_geometries, request)
        problem = self._problem_for_sources(state)
        if state.primary_error is not None and state.planit_error is not None:
            outcome = "error"
            counters.failed += 1
        elif problem is not None:
            outcome = "warning"
        elif matched:
            outcome = "success"
        else:
            outcome = "empty"
            counters.empty += 1

        ids = self._database.save_council_result(
            run_id,
            state.council,
            matched,
            outcome=outcome,
            exception=problem,
            started_at=state.started_at,
            finished_at=self._clock(),
        )
        state.saved = True
        for application, _application_id in zip(matched, ids):
            counters.saved += 1
            emit(
                SearchEvent(
                    kind="application_saved",
                    run_id=run_id,
                    council=state.council.name,
                    completed=counters.completed,
                    total=total,
                    saved_count=counters.saved,
                    message=application.reference,
                )
            )
        if problem is not None:
            emit(
                SearchEvent(
                    kind="warning",
                    run_id=run_id,
                    council=state.council.name,
                    completed=counters.completed,
                    total=total,
                    saved_count=counters.saved,
                    message=str(problem),
                )
            )
        counters.completed += 1
        emit(
            SearchEvent(
                kind="council_finished",
                run_id=run_id,
                council=state.council.name,
                completed=counters.completed,
                total=total,
                saved_count=counters.saved,
            )
        )

    def _finish_cancelled(
        self,
        run_id: int,
        started_at: datetime,
        councils: list[Council],
        states: Iterable[_CouncilState],
        uploaded_geometries: list[dict[str, object]],
        request: SearchRequest,
        emit: Callable[[SearchEvent], None],
        counters: _RunCounters,
    ) -> SearchSummary:
        for state in states:
            if state.saved or state.started_at is None or state.deferred_phase is not None:
                continue
            primary_items = state.primary.applications if state.primary else ()
            planit_items = state.planit.applications if state.planit else ()
            merged = reconcile_applications(primary_items, planit_items)
            matched = self._matching_applications(merged, uploaded_geometries, request)
            problem = self._problem_for_sources(state)
            ids = self._database.save_council_result(
                run_id,
                state.council,
                matched,
                outcome="cancelled",
                exception=problem,
                started_at=state.started_at,
                finished_at=self._clock(),
            )
            state.saved = True
            for application, _application_id in zip(matched, ids):
                counters.saved += 1
                emit(
                    SearchEvent(
                        kind="application_saved",
                        run_id=run_id,
                        council=state.council.name,
                        completed=counters.completed,
                        total=len(councils),
                        saved_count=counters.saved,
                        message=application.reference,
                    )
                )
        finished_at = self._clock()
        self._database.finish_search(
            run_id,
            status="cancelled",
            searched_councils=counters.completed,
            saved_applications=counters.saved,
            empty_councils=counters.empty,
            failed_councils=counters.failed,
            finished_at=finished_at,
        )
        summary = SearchSummary(
            run_id=run_id,
            status="cancelled",
            total_councils=len(councils),
            searched_councils=counters.completed,
            saved_applications=counters.saved,
            empty_councils=counters.empty,
            failed_councils=counters.failed,
            started_at=started_at,
            finished_at=finished_at,
        )
        emit(
            SearchEvent(
                kind="cancelled",
                run_id=run_id,
                completed=counters.completed,
                total=len(councils),
                saved_count=counters.saved,
                message="Search cancelled",
            )
        )
        return summary

    @staticmethod
    def _problem_for_sources(
        state: _CouncilState,
    ) -> Exception | None:
        messages: list[str] = []
        if state.primary_error:
            messages.append(f"Primary search failed: {state.primary_error}")
        if state.primary:
            messages.extend(state.primary.warnings)
        if state.planit_error:
            messages.append(f"PlanIt reconciliation failed: {state.planit_error}")
        if state.planit:
            messages.extend(state.planit.warnings)
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
