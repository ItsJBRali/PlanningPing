from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_ping import contracts


class ContractModelsTests(unittest.TestCase):
    """Break caught: the UI/service API could drift from the approved fields."""

    def test_application_rows_expose_searchable_planning_fields(self) -> None:
        row = contracts.ApplicationRow(
            application_id=7,
            reference="24/00001/FUL",
            council="Example Council",
            application_date=date(2026, 1, 7),
            received_date=date(2026, 1, 7),
            validated_date=None,
            description="Single-storey rear extension",
            address="1 High Street, Exampleton",
            postcode="EX1 1AA",
            status="Pending consideration",
            application_url="https://planning.example.test/application-1",
            council_url="https://planning.example.test",
        )
        page = contracts.Page(items=(row,), page=1, page_size=50, total_items=1)

        self.assertEqual(7, page.items[0].application_id)
        self.assertEqual("Example Council", page.items[0].council)
        self.assertEqual(date(2026, 1, 7), page.items[0].application_date)
        self.assertEqual("EX1 1AA", page.items[0].postcode)

    def test_search_events_preserve_structured_progress(self) -> None:
        event = contracts.SearchEvent(
            kind="council_finished",
            run_id=4,
            council="Example Council",
            completed=2,
            total=10,
            saved_count=3,
            message="Saved 3 applications",
        )

        self.assertEqual("council_finished", event.kind)
        self.assertEqual(4, event.run_id)
        self.assertEqual(2, event.completed)
        self.assertEqual(3, event.saved_count)

    def test_search_summary_exposes_terminal_aggregate_counts(self) -> None:
        started_at = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
        finished_at = datetime(2026, 1, 31, 9, 3, tzinfo=timezone.utc)
        summary = contracts.SearchSummary(
            run_id=4,
            status="completed_with_issues",
            total_councils=10,
            searched_councils=10,
            saved_applications=12,
            empty_councils=2,
            failed_councils=1,
            started_at=started_at,
            finished_at=finished_at,
        )

        self.assertEqual("completed_with_issues", summary.status)
        self.assertEqual(12, summary.saved_applications)
        self.assertEqual(1, summary.failed_councils)

    def test_accepts_every_event_kind_and_summary_status(self) -> None:
        event_kinds = (
            "started",
            "council_started",
            "council_finished",
            "application_saved",
            "warning",
            "completed",
            "cancelled",
        )
        for kind in event_kinds:
            with self.subTest(kind=kind):
                self.assertEqual(kind, contracts.SearchEvent(kind=kind).kind)

        now = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
        for status in ("completed", "cancelled", "completed_with_issues"):
            with self.subTest(status=status):
                summary = contracts.SearchSummary(
                    run_id=1,
                    status=status,
                    total_councils=0,
                    searched_councils=0,
                    saved_applications=0,
                    empty_councils=0,
                    failed_councils=0,
                    started_at=now,
                    finished_at=now,
                )
                self.assertEqual(status, summary.status)

    def test_rejects_unknown_event_kind_and_summary_status_at_runtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "kind"):
            contracts.SearchEvent(kind="progress")

        now = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "status"):
            contracts.SearchSummary(
                run_id=1,
                status="error",
                total_councils=0,
                searched_councils=0,
                saved_applications=0,
                empty_councils=0,
                failed_councils=0,
                started_at=now,
                finished_at=now,
            )

    def test_issue_rows_identify_council_outcome_and_error(self) -> None:
        timestamp = datetime(2026, 1, 31, 9, 1, tzinfo=timezone.utc)
        issue = contracts.IssueRow(
            issue_id=9,
            run_id=4,
            timestamp=timestamp,
            council="Example Council",
            portal_family="idox",
            outcome="error",
            error_type="CompletenessError",
            message="Portal returned incomplete results",
        )

        self.assertEqual("error", issue.outcome)
        self.assertEqual("idox", issue.portal_family)
        self.assertEqual(timestamp, issue.timestamp)

    def test_service_bundle_exposes_the_frozen_protocol_boundaries(self) -> None:
        services = contracts.AppServices(
            search=_SearchServiceFake(),
            applications=_ApplicationQueryServiceFake(),
            issues=_IssueQueryServiceFake(),
        )

        self.assertIsInstance(services.search, contracts.SearchService)
        self.assertIsInstance(services.applications, contracts.ApplicationQueryService)
        self.assertIsInstance(services.issues, contracts.IssueQueryService)


class _SearchServiceFake:
    def run(self, request: object, emit: object, cancel_event: Event) -> object:
        return object()


class _ApplicationQueryServiceFake:
    def search(self, filters: object) -> object:
        return object()


class _IssueQueryServiceFake:
    def list_issues(self, run_id: int | None = None) -> tuple[object, ...]:
        return ()


if __name__ == "__main__":
    unittest.main()
