from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread, get_ident

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.contracts import SearchRequest
from planning_ping.backend.catalogue import AuthorityCatalogue
from planning_ping.backend.http import CouncilAccessBlockedError, CouncilRateLimitError
from planning_ping.backend.models import Council, PlanningApplication
from planning_ping.backend.orchestration import AuthoritySearchResult, PlanningSearchService
from planning_ping.backend.persistence import PlanningDatabase


BOUNDARY = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]}
NOW = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)


def make_council(code: str, portal_family: str = "idox") -> Council:
    return Council(code, f"{code.title()} Council", "England", portal_family, portal_family.title(), f"https://{code}.test", None, f"https://{code}.test/search", BOUNDARY)


def make_application(code: str, reference: str, description: str = "Rear extension", *, longitude: float | None = None) -> PlanningApplication:
    return PlanningApplication(
        council_code=code,
        reference=reference,
        authority=f"{code.title()} Council",
        application_url=f"https://{code}.test/{reference}",
        council_url=f"https://{code}.test/search",
        received_date=date(2026, 1, 15),
        description=description,
        longitude=longitude,
        latitude=longitude,
    )


class FakeAuthoritySearcher:
    def __init__(self, primary: dict[str, object], planit: dict[str, object], cancel: Event | None = None) -> None:
        self.primary = primary
        self.planit = planit
        self.cancel = cancel
        self.lock = Lock()
        self.calls: list[tuple[str, str]] = []

    def search_primary(self, council: Council, start_date: date, end_date: date, cancel_event: Event) -> AuthoritySearchResult:
        with self.lock:
            self.calls.append(("primary", council.code))
        result = self.primary[council.code]
        if isinstance(result, Exception):
            raise result
        if self.cancel is not None:
            self.cancel.set()
        return AuthoritySearchResult(tuple(result))

    def search_planit(self, council: Council, start_date: date, end_date: date, cancel_event: Event) -> AuthoritySearchResult:
        with self.lock:
            self.calls.append(("planit", council.code))
        result = self.planit[council.code]
        if isinstance(result, Exception):
            raise result
        return AuthoritySearchResult(tuple(result))


class PlanItCancellingSearcher(FakeAuthoritySearcher):
    def search_planit(self, council: Council, start_date: date, end_date: date, cancel_event: Event) -> AuthoritySearchResult:
        if council.code == "beta":
            with self.lock:
                self.calls.append(("planit", council.code))
            cancel_event.set()
            raise RuntimeError("cancelled during PlanIt")
        return super().search_planit(council, start_date, end_date, cancel_event)


class BlockingConcurrentSearcher:
    def __init__(self) -> None:
        self.lock = Lock()
        self.release_primary = Event()
        self.eight_started = Event()
        self.active_councils: set[str] = set()
        self.max_active = 0
        self.duplicate_council = False
        self.primary_calls: set[str] = set()
        self.planit_calls: set[str] = set()
        self.planit_active = 0
        self.max_planit_active = 0
        self.first_planit_started = Event()
        self.planit_overlap = Event()
        self.release_planit = Event()

    def search_primary(self, council, start_date, end_date, cancel_event):
        with self.lock:
            if council.code in self.active_councils:
                self.duplicate_council = True
            self.active_councils.add(council.code)
            self.primary_calls.add(council.code)
            self.max_active = max(self.max_active, len(self.active_councils))
            if len(self.active_councils) == 8:
                self.eight_started.set()
        self.release_primary.wait(2)
        with self.lock:
            self.active_councils.remove(council.code)
        return AuthoritySearchResult()

    def search_planit(self, council, start_date, end_date, cancel_event):
        with self.lock:
            if council.code in self.active_councils:
                self.duplicate_council = True
            self.active_councils.add(council.code)
            self.planit_calls.add(council.code)
            self.planit_active += 1
            self.max_planit_active = max(self.max_planit_active, self.planit_active)
            self.first_planit_started.set()
            if self.planit_active > 1:
                self.planit_overlap.set()
        self.release_planit.wait(2)
        with self.lock:
            self.planit_active -= 1
            self.active_councils.remove(council.code)
        return AuthoritySearchResult()


class SplitCompletionSearcher:
    def __init__(self) -> None:
        self.lock = Lock()
        self.beta_entered = Event()
        self.release_beta = Event()
        self.network_thread_ids: set[int] = set()

    def search_primary(self, council, start_date, end_date, cancel_event):
        with self.lock:
            self.network_thread_ids.add(get_ident())
        if council.code == "beta":
            self.beta_entered.set()
            self.release_beta.wait(2)
        applications = (make_application("alpha", "A1"),) if council.code == "alpha" else ()
        return AuthoritySearchResult(applications)

    def search_planit(self, council, start_date, end_date, cancel_event):
        with self.lock:
            self.network_thread_ids.add(get_ident())
        return AuthoritySearchResult()


class DeferredPlanItSearcher:
    def __init__(self, monotonic_clock) -> None:
        self.lock = Lock()
        self.monotonic_clock = monotonic_clock
        self.blocker_calls = 0
        self.blocker_rate_limited = Event()
        self.blocker_primary_started_behind_planit = Event()
        self.beta_started = Event()
        self.release_beta = Event()
        self.alpha_rate_limited = Event()
        self.gamma_primary_started = Event()
        self.gamma_planit_started = Event()
        self.alpha_retry_started = Event()
        self.gamma_started_before_rate_limit = False
        self.planit_calls: dict[str, int] = {}

    def search_primary(self, council, start_date, end_date, cancel_event):
        if council.code == "blocker":
            self.blocker_calls += 1
            if self.blocker_calls == 1:
                self.blocker_rate_limited.set()
                raise CouncilRateLimitError(
                    url="https://blocker.test/search",
                    scope="portal:idox",
                    retry_after_seconds=10.0,
                    retry_limit=2,
                )
            if self.blocker_calls == 2:
                raise CouncilRateLimitError(
                    url="https://blocker.test/search",
                    scope="portal:arcus",
                    retry_after_seconds=0.0,
                    retry_limit=2,
                )
            self.blocker_primary_started_behind_planit.set()
        if council.code == "alpha":
            self.beta_started.wait()
        if council.code == "beta":
            self.beta_started.set()
            self.release_beta.wait()
        if council.code == "gamma":
            with self.lock:
                self.gamma_started_before_rate_limit = not self.alpha_rate_limited.is_set()
            self.gamma_primary_started.set()
        return AuthoritySearchResult()

    def search_planit(self, council, start_date, end_date, cancel_event):
        with self.lock:
            calls = self.planit_calls.get(council.code, 0) + 1
            self.planit_calls[council.code] = calls
        if council.code == "alpha" and calls == 1:
            self.monotonic_clock.advance(10.0)
            self.alpha_rate_limited.set()
            raise CouncilRateLimitError(
                url="https://www.planit.org.uk/api/applics/json",
                scope="planit",
                retry_after_seconds=60.0,
                retry_limit=2,
            )
        if council.code == "alpha":
            self.alpha_retry_started.set()
        if council.code == "gamma":
            self.gamma_planit_started.set()
        return AuthoritySearchResult()


class ControlledMonotonicClock:
    def __init__(self) -> None:
        self.current = 0.0
        self.lock = Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.current

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.current += seconds


class AdvancingDeadlineEvent(Event):
    def __init__(self, clock: ControlledMonotonicClock) -> None:
        super().__init__()
        self.clock = clock
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_timeouts.append(timeout)
        if timeout is not None:
            self.clock.advance(timeout)
        return self.is_set()


class ObservableDeadlineEvent(Event):
    def __init__(self) -> None:
        super().__init__()
        self.wait_called = Event()
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_timeouts.append(timeout)
        self.wait_called.set()
        return super().wait(timeout)


class OrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.boundary_path = Path(self.temp_directory.name) / "area.geojson"
        self.boundary_path.write_text(json.dumps(BOUNDARY), encoding="utf-8")
        self.database = PlanningDatabase(Path(self.temp_directory.name) / "search.sql", clock=lambda: NOW)
        self.councils = [make_council("alpha"), make_council("beta"), make_council("gamma")]
        self.request = SearchRequest(self.boundary_path, date(2026, 1, 1), date(2026, 1, 31), ("shed",))

    def tearDown(self) -> None:
        self.database.close()
        self.temp_directory.cleanup()

    def test_each_primary_phase_precedes_its_planit_phase_and_persists_outcomes_and_events(self) -> None:
        searcher = FakeAuthoritySearcher(
            primary={
                "alpha": [make_application("alpha", "A1"), make_application("alpha", "SKIP", "Garden shed"), make_application("alpha", "OUT", longitude=8)],
                "beta": [make_application("beta", "B1")],
                "gamma": RuntimeError("primary unavailable"),
            },
            planit={
                "alpha": [make_application("alpha", "a1", "PlanIt duplicate"), make_application("alpha", "A2", "New dwelling")],
                "beta": RuntimeError("PlanIt unavailable"),
                "gamma": RuntimeError("PlanIt unavailable"),
            },
        )
        events = []
        service = PlanningSearchService(self.database, AuthorityCatalogue(self.councils), searcher, clock=lambda: NOW)

        summary = service.run(self.request, events.append, Event())

        for code in ("alpha", "beta", "gamma"):
            with self.subTest(code=code):
                self.assertLess(
                    searcher.calls.index(("primary", code)),
                    searcher.calls.index(("planit", code)),
                )
        self.assertEqual("completed_with_issues", summary.status)
        self.assertEqual((3, 3, 3, 0, 1), (summary.total_councils, summary.searched_councils, summary.saved_applications, summary.empty_councils, summary.failed_councils))
        self.assertEqual(["A1", "A2", "B1"], [row[0] for row in self.database.connection.execute("SELECT reference FROM applications ORDER BY reference")])
        qualities = {row[0] for row in self.database.connection.execute("SELECT location_match_quality FROM applications")}
        self.assertEqual({"council_overlap"}, qualities)
        kinds = [event.kind for event in events]
        self.assertEqual("started", kinds[0])
        self.assertEqual("completed", kinds[-1])
        self.assertEqual(3, kinds.count("council_started"))
        self.assertEqual(3, kinds.count("council_finished"))
        self.assertEqual(3, kinds.count("application_saved"))
        self.assertEqual(2, kinds.count("warning"))
        progress = [event.completed for event in events if event.completed is not None]
        self.assertEqual(sorted(progress), progress)

    def test_primary_access_block_falls_back_to_planit_and_saves_warning(self) -> None:
        access_block = CouncilAccessBlockedError(
            url="https://alpha.test/search",
            category="network_filter",
            reason="Local network content filter blocked this council portal",
        )
        searcher = FakeAuthoritySearcher(
            primary={"alpha": access_block},
            planit={"alpha": [make_application("alpha", "A1")]},
        )
        events = []
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=lambda: NOW,
        )

        summary = service.run(self.request, events.append, Event())

        self.assertEqual("completed_with_issues", summary.status)
        self.assertEqual(0, summary.failed_councils)
        self.assertEqual(["A1"], [row[0] for row in self.database.connection.execute("SELECT reference FROM applications")])
        outcome = self.database.connection.execute(
            "SELECT outcome_status, exception_message FROM council_search_outcomes"
        ).fetchone()
        self.assertEqual("warning", outcome[0])
        self.assertIn("local network content filter", outcome[1].casefold())
        warnings = [event.message for event in events if event.kind == "warning"]
        self.assertEqual(1, len(warnings))
        self.assertIn("local network content filter", warnings[0].casefold())
        self.assertEqual([("primary", "alpha"), ("planit", "alpha")], searcher.calls)

    def test_planit_access_block_retains_primary_result_as_warning(self) -> None:
        access_block = CouncilAccessBlockedError(
            url="https://www.planit.org.uk/api/applics/json",
            category="network_filter",
            reason="Local network content filter blocked this council portal",
        )
        searcher = FakeAuthoritySearcher(
            primary={"alpha": [make_application("alpha", "A1")]},
            planit={"alpha": access_block},
        )
        events = []
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=lambda: NOW,
        )

        summary = service.run(self.request, events.append, Event())

        self.assertEqual("completed_with_issues", summary.status)
        self.assertEqual(0, summary.failed_councils)
        self.assertEqual(["A1"], [row[0] for row in self.database.connection.execute("SELECT reference FROM applications")])
        outcome = self.database.connection.execute(
            "SELECT outcome_status, exception_message FROM council_search_outcomes"
        ).fetchone()
        self.assertEqual("warning", outcome[0])
        self.assertIn("local network content filter", outcome[1].casefold())
        warnings = [event.message for event in events if event.kind == "warning"]
        self.assertEqual(1, len(warnings))
        self.assertIn("local network content filter", warnings[0].casefold())
        self.assertEqual([("primary", "alpha"), ("planit", "alpha")], searcher.calls)

    def test_searches_at_most_eight_councils_concurrently_and_planit_is_single_flight(self) -> None:
        councils = [make_council(f"council-{index}") for index in range(10)]
        searcher = BlockingConcurrentSearcher()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue(councils),
            searcher,
            clock=lambda: NOW,
        )
        result: list[object] = []
        errors: list[BaseException] = []

        def run_search() -> None:
            try:
                result.append(service.run(self.request, lambda event: None, Event()))
            except BaseException as error:
                errors.append(error)

        search_thread = Thread(target=run_search)
        search_thread.start()
        try:
            self.assertTrue(searcher.eight_started.wait(2))
            self.assertEqual(8, searcher.max_active)
            searcher.release_primary.set()
            self.assertTrue(searcher.first_planit_started.wait(2))
            self.assertFalse(searcher.planit_overlap.wait(0.1))
            searcher.release_planit.set()
            search_thread.join(2)
        finally:
            searcher.release_primary.set()
            searcher.release_planit.set()
            search_thread.join(2)

        self.assertFalse(search_thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(result))
        self.assertEqual(1, searcher.max_planit_active)
        self.assertFalse(searcher.duplicate_council)
        expected_codes = {council.code for council in councils}
        self.assertEqual(expected_codes, searcher.primary_calls)
        self.assertEqual(expected_codes, searcher.planit_calls)

    def test_finished_council_is_saved_immediately_on_the_coordinator_thread(self) -> None:
        councils = [make_council("alpha"), make_council("beta")]
        searcher = SplitCompletionSearcher()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue(councils),
            searcher,
            clock=lambda: NOW,
        )
        alpha_finished = Event()
        coordinator_thread_ids: list[int] = []
        save_thread_ids: list[int] = []
        event_thread_ids: list[int] = []
        errors: list[BaseException] = []
        original_save = self.database.save_council_result

        def observed_save(*args, **kwargs):
            save_thread_ids.append(get_ident())
            return original_save(*args, **kwargs)

        def observed_emit(event) -> None:
            event_thread_ids.append(get_ident())
            if event.kind == "council_finished" and event.council == "Alpha Council":
                alpha_finished.set()

        def run_search() -> None:
            coordinator_thread_ids.append(get_ident())
            try:
                service.run(self.request, observed_emit, Event())
            except BaseException as error:
                errors.append(error)

        self.database.save_council_result = observed_save
        search_thread = Thread(target=run_search)
        search_thread.start()
        try:
            self.assertTrue(alpha_finished.wait(2))
            self.assertTrue(searcher.beta_entered.is_set())
            self.assertEqual(
                [("alpha", "success")],
                [
                    tuple(row)
                    for row in self.database.connection.execute(
                        "SELECT c.code, o.outcome_status "
                        "FROM council_search_outcomes o "
                        "JOIN councils c ON c.id=o.council_id"
                    )
                ],
            )
            self.assertTrue(search_thread.is_alive())
            searcher.release_beta.set()
            search_thread.join(2)
        finally:
            self.database.save_council_result = original_save
            searcher.release_beta.set()
            search_thread.join(2)

        self.assertFalse(search_thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(coordinator_thread_ids))
        coordinator_thread_id = coordinator_thread_ids[0]
        self.assertTrue(save_thread_ids)
        self.assertTrue(event_thread_ids)
        self.assertTrue(all(identifier == coordinator_thread_id for identifier in save_thread_ids))
        self.assertTrue(all(identifier == coordinator_thread_id for identifier in event_thread_ids))
        self.assertTrue(set(save_thread_ids).isdisjoint(searcher.network_thread_ids))
        self.assertTrue(set(event_thread_ids).isdisjoint(searcher.network_thread_ids))

    def test_rate_limited_planit_phase_releases_worker_for_another_council(self) -> None:
        monotonic_clock = ControlledMonotonicClock()
        searcher = DeferredPlanItSearcher(monotonic_clock)
        councils = [
            make_council("blocker", "arcus"),
            make_council("beta", "arcus"),
            make_council("alpha", "arcus"),
            make_council("gamma", "idox"),
        ]
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue(councils),
            searcher,
            clock=lambda: NOW,
            worker_limit=2,
            monotonic_clock=monotonic_clock,
        )
        cancel_event = Event()
        results = []
        errors: list[BaseException] = []
        finished_before_cleanup = False

        def run_search() -> None:
            try:
                results.append(service.run(self.request, lambda event: None, cancel_event))
            except BaseException as error:
                errors.append(error)

        search_thread = Thread(target=run_search)
        search_thread.start()
        try:
            self.assertTrue(searcher.blocker_rate_limited.wait(2))
            self.assertTrue(searcher.alpha_rate_limited.wait(2))
            self.assertTrue(
                searcher.gamma_primary_started.wait(2),
                "the deferred alpha retry kept a worker from processing gamma",
            )
            self.assertTrue(searcher.blocker_primary_started_behind_planit.wait(2))
            self.assertFalse(
                searcher.gamma_started_before_rate_limit,
                "gamma entered before alpha's PlanIt worker was released by the 429",
            )
            self.assertTrue(searcher.beta_started.is_set())
            self.assertFalse(searcher.release_beta.is_set())
            self.assertEqual(1, searcher.planit_calls["alpha"])
            self.assertFalse(
                searcher.gamma_planit_started.is_set(),
                "the shared PlanIt cooldown did not hold gamma before the deadline",
            )
            self.assertTrue(search_thread.is_alive())
            monotonic_clock.advance(60.0)
            searcher.release_beta.set()
            self.assertTrue(searcher.alpha_retry_started.wait(2))
            search_thread.join(2)
            finished_before_cleanup = not search_thread.is_alive()
        finally:
            if search_thread.is_alive():
                cancel_event.set()
                monotonic_clock.advance(100.0)
                searcher.release_beta.set()
                search_thread.join(2)

        self.assertTrue(finished_before_cleanup)
        self.assertFalse(search_thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(results))
        self.assertEqual("completed", results[0].status)
        self.assertEqual(2, searcher.planit_calls["alpha"])

    def test_planit_retry_cap_is_two_even_when_error_advertises_more(self) -> None:
        class ExhaustedPlanItSearcher:
            def __init__(self) -> None:
                self.planit_calls: dict[str, int] = {}

            def search_primary(self, council, start_date, end_date, cancel_event):
                return AuthoritySearchResult((make_application("alpha", "A1"),))

            def search_planit(self, council, start_date, end_date, cancel_event):
                self.planit_calls[council.code] = self.planit_calls.get(council.code, 0) + 1
                raise CouncilRateLimitError(
                    url="https://www.planit.org.uk/api/applics/json",
                    scope="planit",
                    retry_after_seconds=0.0,
                    retry_limit=6,
                )

        searcher = ExhaustedPlanItSearcher()
        events = []
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=lambda: NOW,
        )

        summary = service.run(self.request, events.append, Event())

        self.assertEqual(3, searcher.planit_calls["alpha"])
        self.assertEqual("completed_with_issues", summary.status)
        self.assertEqual(1, summary.saved_applications)
        self.assertEqual(
            ("warning", "A1"),
            tuple(
                self.database.connection.execute(
                    "SELECT o.outcome_status, a.reference "
                    "FROM council_search_outcomes o "
                    "JOIN search_run_applications sra ON sra.run_id=o.run_id "
                    "JOIN applications a ON a.id=sra.application_id"
                ).fetchone()
            ),
        )
        messages = [
            event.message
            for event in events
            if event.kind == "council_started" and event.message
        ]
        self.assertTrue(any("retrying in" in message for message in messages))
        self.assertTrue(any("Retrying PlanIt" in message for message in messages))
        self.assertEqual(
            1,
            sum(
                event.kind == "council_finished" and event.council == "Alpha Council"
                for event in events
            ),
        )

    def test_explicit_retry_after_uses_full_monotonic_deadline_without_fallback(self) -> None:
        class ExplicitDelaySearcher:
            def __init__(self) -> None:
                self.planit_calls = 0

            def search_primary(self, council, start_date, end_date, cancel_event):
                return AuthoritySearchResult()

            def search_planit(self, council, start_date, end_date, cancel_event):
                self.planit_calls += 1
                if self.planit_calls == 1:
                    raise CouncilRateLimitError(
                        url="https://www.planit.org.uk/api/applics/json",
                        scope="planit",
                        retry_after_seconds=294.0,
                        retry_limit=2,
                    )
                return AuthoritySearchResult()

        clock = ControlledMonotonicClock()
        cancel_event = AdvancingDeadlineEvent(clock)
        searcher = ExplicitDelaySearcher()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=lambda: NOW,
            monotonic_clock=clock,
            jitter=lambda low, high: (_ for _ in ()).throw(
                AssertionError("fallback jitter used for explicit Retry-After")
            ),
        )

        summary = service.run(self.request, lambda event: None, cancel_event)

        self.assertEqual("completed", summary.status)
        self.assertEqual(2, searcher.planit_calls)
        self.assertEqual([294.0], cancel_event.wait_timeouts)

    def test_missing_retry_after_uses_fallback_deadline_and_injected_jitter(self) -> None:
        class MissingDelaySearcher:
            def __init__(self) -> None:
                self.planit_calls = 0

            def search_primary(self, council, start_date, end_date, cancel_event):
                return AuthoritySearchResult()

            def search_planit(self, council, start_date, end_date, cancel_event):
                self.planit_calls += 1
                if self.planit_calls == 1:
                    raise CouncilRateLimitError(
                        url="https://www.planit.org.uk/api/applics/json",
                        scope="planit",
                        retry_after_seconds=None,
                        retry_limit=2,
                    )
                return AuthoritySearchResult()

        jitter_calls: list[tuple[float, float]] = []

        def jitter(low: float, high: float) -> float:
            jitter_calls.append((low, high))
            return 0.5

        clock = ControlledMonotonicClock()
        cancel_event = AdvancingDeadlineEvent(clock)
        searcher = MissingDelaySearcher()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=lambda: NOW,
            monotonic_clock=clock,
            jitter=jitter,
        )

        summary = service.run(self.request, lambda event: None, cancel_event)

        self.assertEqual("completed", summary.status)
        self.assertEqual(2, searcher.planit_calls)
        self.assertEqual([(0.0, 0.5)], jitter_calls)
        self.assertEqual([2.5], cancel_event.wait_timeouts)

    def test_cancellation_interrupts_deadline_only_wait(self) -> None:
        class RateLimitedSearcher:
            def __init__(self) -> None:
                self.planit_calls = 0

            def search_primary(self, council, start_date, end_date, cancel_event):
                return AuthoritySearchResult()

            def search_planit(self, council, start_date, end_date, cancel_event):
                self.planit_calls += 1
                raise CouncilRateLimitError(
                    url="https://www.planit.org.uk/api/applics/json",
                    scope="planit",
                    retry_after_seconds=294.0,
                    retry_limit=2,
                )

        cancel_event = ObservableDeadlineEvent()
        searcher = RateLimitedSearcher()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=lambda: NOW,
            monotonic_clock=lambda: 100.0,
        )
        results = []
        errors: list[BaseException] = []

        def run_search() -> None:
            try:
                results.append(service.run(self.request, lambda event: None, cancel_event))
            except BaseException as error:
                errors.append(error)

        search_thread = Thread(target=run_search)
        search_thread.start()
        try:
            self.assertTrue(cancel_event.wait_called.wait(2))
            self.assertEqual([294.0], cancel_event.wait_timeouts)
            cancel_event.set()
            search_thread.join(2)
        finally:
            cancel_event.set()
            search_thread.join(2)

        self.assertFalse(search_thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(results))
        self.assertEqual("cancelled", results[0].status)
        self.assertEqual(1, searcher.planit_calls)

    def test_cancellation_drains_active_work_once_and_leaves_deferred_retry_unsaved(self) -> None:
        class ActiveAndDeferredSearcher:
            def __init__(self) -> None:
                self.primary_calls: list[str] = []
                self.planit_calls: list[str] = []

            def search_primary(self, council, start_date, end_date, cancel_event):
                self.primary_calls.append(council.code)
                if council.code == "beta":
                    cancel_event.wait(2)
                    return AuthoritySearchResult((make_application("beta", "B1"),))
                if council.code == "gamma":
                    raise CouncilRateLimitError(
                        url="https://gamma.test/search",
                        scope="portal:idox",
                        retry_after_seconds=300.0,
                        retry_limit=2,
                    )
                return AuthoritySearchResult((make_application("alpha", "A1"),))

            def search_planit(self, council, start_date, end_date, cancel_event):
                self.planit_calls.append(council.code)
                return AuthoritySearchResult()

        cancel_event = Event()
        searcher = ActiveAndDeferredSearcher()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue(self.councils),
            searcher,
            clock=lambda: NOW,
            worker_limit=3,
            monotonic_clock=lambda: 100.0,
        )
        events = []
        alpha_finished = False
        gamma_deferred = False
        saved_codes: list[str] = []
        original_save = self.database.save_council_result

        def recording_save(run_id, council, applications, **kwargs):
            saved_codes.append(council.code)
            return original_save(run_id, council, applications, **kwargs)

        def observed_emit(event) -> None:
            nonlocal alpha_finished, gamma_deferred
            events.append(event)
            alpha_finished = alpha_finished or (
                event.kind == "council_finished" and event.council == "Alpha Council"
            )
            gamma_deferred = gamma_deferred or (
                event.kind == "council_started"
                and event.council == "Gamma Council"
                and event.message is not None
                and "paused by Primary portal rate limit" in event.message
            )
            if alpha_finished and gamma_deferred:
                cancel_event.set()

        self.database.save_council_result = recording_save
        try:
            summary = service.run(self.request, observed_emit, cancel_event)
        finally:
            self.database.save_council_result = original_save
            cancel_event.set()

        self.assertEqual("cancelled", summary.status)
        self.assertEqual(1, saved_codes.count("alpha"))
        self.assertLessEqual(saved_codes.count("beta"), 1)
        self.assertNotIn("gamma", saved_codes)
        self.assertEqual(
            "success",
            self.database.connection.execute(
                "SELECT o.outcome_status FROM council_search_outcomes o "
                "JOIN councils c ON c.id=o.council_id WHERE c.code='alpha'"
            ).fetchone()[0],
        )
        self.assertEqual(
            1,
            sum(
                event.kind == "council_finished" and event.council == "Alpha Council"
                for event in events
            ),
        )
        self.assertEqual(1, searcher.primary_calls.count("gamma"))
        self.assertNotIn("gamma", searcher.planit_calls)
        terminal = self.database.connection.execute(
            "SELECT status, finished_at FROM search_runs"
        ).fetchone()
        self.assertEqual("cancelled", terminal[0])
        self.assertIsNotNone(terminal[1])

    def test_cancellation_retains_primary_data_when_active_planit_returns_rate_limit(self) -> None:
        class CancelledRateLimitedPlanItSearcher:
            def __init__(self) -> None:
                self.planit_started = Event()
                self.release_planit = Event()
                self.planit_calls = 0

            def search_primary(self, council, start_date, end_date, cancel_event):
                return AuthoritySearchResult((make_application("alpha", "A1"),))

            def search_planit(self, council, start_date, end_date, cancel_event):
                self.planit_calls += 1
                self.planit_started.set()
                self.release_planit.wait(2)
                raise CouncilRateLimitError(
                    url="https://www.planit.org.uk/api/applics/json",
                    scope="planit",
                    retry_after_seconds=300.0,
                    retry_limit=2,
                )

        cancel_event = Event()
        searcher = CancelledRateLimitedPlanItSearcher()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=lambda: NOW,
        )
        saved_codes: list[str] = []
        summaries = []
        errors: list[BaseException] = []
        original_save = self.database.save_council_result

        def recording_save(run_id, council, applications, **kwargs):
            saved_codes.append(council.code)
            return original_save(run_id, council, applications, **kwargs)

        def run_search() -> None:
            try:
                summaries.append(service.run(self.request, lambda event: None, cancel_event))
            except BaseException as error:
                errors.append(error)

        self.database.save_council_result = recording_save
        search_thread = Thread(target=run_search)
        search_thread.start()
        try:
            self.assertTrue(searcher.planit_started.wait(2))
            cancel_event.set()
            searcher.release_planit.set()
            search_thread.join(2)
        finally:
            cancel_event.set()
            searcher.release_planit.set()
            search_thread.join(2)
            self.database.save_council_result = original_save

        self.assertFalse(search_thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(summaries))
        self.assertEqual("cancelled", summaries[0].status)
        self.assertEqual(1, summaries[0].saved_applications)
        self.assertEqual(["alpha"], saved_codes)
        self.assertEqual(1, searcher.planit_calls)
        self.assertEqual(
            ("cancelled", "A1"),
            tuple(
                self.database.connection.execute(
                    "SELECT o.outcome_status, a.reference "
                    "FROM council_search_outcomes o "
                    "JOIN search_run_applications sra ON sra.run_id=o.run_id "
                    "JOIN applications a ON a.id=sra.application_id"
                ).fetchone()
            ),
        )

    def test_primary_rate_limit_retries_only_primary_and_saves_once(self) -> None:
        class RetriedPrimarySearcher:
            def __init__(self) -> None:
                self.primary_calls = 0
                self.planit_calls = 0

            def search_primary(self, council, start_date, end_date, cancel_event):
                self.primary_calls += 1
                if self.primary_calls <= 3:
                    raise CouncilRateLimitError(
                        url="https://alpha.test/search",
                        scope="portal:idox",
                        retry_after_seconds=0.0,
                        retry_limit=3,
                    )
                return AuthoritySearchResult((make_application("alpha", "A1"),))

            def search_planit(self, council, start_date, end_date, cancel_event):
                self.planit_calls += 1
                return AuthoritySearchResult()

        searcher = RetriedPrimarySearcher()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=lambda: NOW,
        )

        summary = service.run(self.request, lambda event: None, Event())

        self.assertEqual(4, searcher.primary_calls)
        self.assertEqual(1, searcher.planit_calls)
        self.assertEqual(1, summary.saved_applications)
        self.assertEqual(
            1,
            self.database.connection.execute(
                "SELECT COUNT(*) FROM council_search_outcomes"
            ).fetchone()[0],
        )

    def test_no_intersection_is_a_successful_zero_council_run(self) -> None:
        far = {"type": "Polygon", "coordinates": [[[10, 10], [11, 10], [11, 11], [10, 11], [10, 10]]]}
        self.boundary_path.write_text(json.dumps(far), encoding="utf-8")
        events = []
        searcher = FakeAuthoritySearcher({}, {})
        service = PlanningSearchService(self.database, AuthorityCatalogue(self.councils), searcher, clock=lambda: NOW)

        summary = service.run(self.request, events.append, Event())

        self.assertEqual("completed", summary.status)
        self.assertEqual(0, summary.total_councils)
        self.assertEqual([], searcher.calls)
        self.assertEqual(["started", "completed"], [event.kind for event in events])

    def test_cancellation_persists_already_completed_primary_council_and_finalizes_run(self) -> None:
        cancel = Event()
        searcher = FakeAuthoritySearcher(
            primary={"alpha": [make_application("alpha", "A1")]},
            planit={},
            cancel=cancel,
        )
        events = []
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue(self.councils),
            searcher,
            clock=lambda: NOW,
            worker_limit=1,
        )

        summary = service.run(self.request, events.append, cancel)

        self.assertEqual("cancelled", summary.status)
        self.assertEqual(0, summary.searched_councils)
        self.assertEqual(1, summary.saved_applications)
        self.assertEqual((0, 0), (summary.empty_councils, summary.failed_councils))
        self.assertEqual([("primary", "alpha")], searcher.calls)
        self.assertEqual("cancelled", events[-1].kind)
        self.assertNotIn("council_finished", [event.kind for event in events])
        outcome = self.database.connection.execute("SELECT outcome_status FROM council_search_outcomes").fetchone()[0]
        self.assertEqual("cancelled", outcome)

    def test_cancellation_during_planit_records_current_and_remaining_councils_as_cancelled(self) -> None:
        searcher = PlanItCancellingSearcher(
            primary={code: [make_application(code, f"{code}/1")] for code in ("alpha", "beta", "gamma")},
            planit={"alpha": [], "gamma": []},
        )
        cancel = Event()
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue(self.councils),
            searcher,
            clock=lambda: NOW,
            worker_limit=1,
        )
        events = []

        summary = service.run(self.request, events.append, cancel)

        outcomes = dict(
            self.database.connection.execute(
                "SELECT c.code, o.outcome_status FROM council_search_outcomes o JOIN councils c ON c.id=o.council_id"
            )
        )
        self.assertEqual("cancelled", summary.status)
        self.assertEqual((1, 0, 0), (summary.searched_councils, summary.empty_councils, summary.failed_councils))
        self.assertEqual(1, [event.kind for event in events].count("council_finished"))
        self.assertEqual({"alpha": "success", "beta": "cancelled", "gamma": "cancelled"}, outcomes)

    def test_council_outcome_started_at_is_the_primary_phase_start(self) -> None:
        primary_started = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
        planit_started = datetime(2026, 1, 31, 9, 5, tzinfo=timezone.utc)

        class MutableClock:
            current = primary_started

            def __call__(self) -> datetime:
                return self.current

        clock = MutableClock()

        class AdvancingSearcher(FakeAuthoritySearcher):
            def search_primary(self, council: Council, start_date: date, end_date: date, cancel_event: Event) -> AuthoritySearchResult:
                result = super().search_primary(council, start_date, end_date, cancel_event)
                clock.current = planit_started
                return result

        searcher = AdvancingSearcher(
            primary={"alpha": [make_application("alpha", "A1")]},
            planit={"alpha": []},
        )
        service = PlanningSearchService(
            self.database,
            AuthorityCatalogue([self.councils[0]]),
            searcher,
            clock=clock,
        )

        service.run(self.request, lambda event: None, Event())

        stored = self.database.connection.execute(
            "SELECT started_at FROM council_search_outcomes"
        ).fetchone()[0]
        self.assertEqual(primary_started.isoformat(), stored)


if __name__ == "__main__":
    unittest.main()
