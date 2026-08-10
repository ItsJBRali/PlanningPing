from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.contracts import SearchRequest
from planning_ping.backend.catalogue import AuthorityCatalogue
from planning_ping.backend.models import Council, PlanningApplication
from planning_ping.backend.orchestration import AuthoritySearchResult, PlanningSearchService
from planning_ping.backend.persistence import PlanningDatabase


BOUNDARY = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]}
NOW = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)


def make_council(code: str) -> Council:
    return Council(code, f"{code.title()} Council", "England", "idox", "Idox", f"https://{code}.test", None, f"https://{code}.test/search", BOUNDARY)


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
        self.calls: list[tuple[str, str]] = []

    def search_primary(self, council: Council, start_date: date, end_date: date, cancel_event: Event) -> AuthoritySearchResult:
        self.calls.append(("primary", council.code))
        result = self.primary[council.code]
        if isinstance(result, Exception):
            raise result
        if self.cancel is not None:
            self.cancel.set()
        return AuthoritySearchResult(tuple(result))

    def search_planit(self, council: Council, start_date: date, end_date: date, cancel_event: Event) -> AuthoritySearchResult:
        self.calls.append(("planit", council.code))
        result = self.planit[council.code]
        if isinstance(result, Exception):
            raise result
        return AuthoritySearchResult(tuple(result))


class PlanItCancellingSearcher(FakeAuthoritySearcher):
    def search_planit(self, council: Council, start_date: date, end_date: date, cancel_event: Event) -> AuthoritySearchResult:
        if council.code == "beta":
            self.calls.append(("planit", council.code))
            cancel_event.set()
            raise RuntimeError("cancelled during PlanIt")
        return super().search_planit(council, start_date, end_date, cancel_event)


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

    def test_primary_phase_precedes_serial_planit_and_persists_outcomes_and_events(self) -> None:
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

        self.assertEqual([("primary", "alpha"), ("primary", "beta"), ("primary", "gamma"), ("planit", "alpha"), ("planit", "beta"), ("planit", "gamma")], searcher.calls)
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
        service = PlanningSearchService(self.database, AuthorityCatalogue(self.councils), searcher, clock=lambda: NOW)

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
        service = PlanningSearchService(self.database, AuthorityCatalogue(self.councils), searcher, clock=lambda: NOW)
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
