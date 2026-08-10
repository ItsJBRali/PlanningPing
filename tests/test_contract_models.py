from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_ping import contracts


class ContractModelsTests(unittest.TestCase):
    """Break caught: UI/service boundaries could expose mutable or incomplete data."""

    def test_application_rows_can_be_returned_in_a_typed_page(self) -> None:
        row = contracts.ApplicationRow(
            application_id="application-1",
            reference="24/00001/FUL",
            description="Single-storey rear extension",
            address="1 High Street, Exampleton",
            local_authority="Example Council",
            region="England",
            received_date=date(2026, 1, 7),
            validated_date=None,
            status="Pending consideration",
            source_url="https://planning.example.test/application-1",
        )
        page = contracts.Page(items=(row,), page=1, page_size=50, total_items=1)

        self.assertEqual((row,), page.items)
        self.assertEqual(1, page.total_items)
        self.assertEqual("24/00001/FUL", page.items[0].reference)

    def test_search_events_and_summaries_preserve_run_outcome(self) -> None:
        started_at = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
        finished_at = datetime(2026, 1, 31, 9, 3, tzinfo=timezone.utc)
        event = contracts.SearchEvent(
            kind="progress",
            message="Searching Example Council",
            current=2,
            total=10,
            run_id="run-1",
        )
        summary = contracts.SearchSummary(
            run_id="run-1",
            started_at=started_at,
            finished_at=finished_at,
            applications_found=12,
            applications_saved=10,
            issues_created=2,
        )

        self.assertEqual("progress", event.kind)
        self.assertEqual(2, event.current)
        self.assertEqual(12, summary.applications_found)
        self.assertFalse(summary.cancelled)

    def test_issue_rows_identify_run_severity_and_time(self) -> None:
        created_at = datetime(2026, 1, 31, 9, 1, tzinfo=timezone.utc)
        issue = contracts.IssueRow(
            issue_id="issue-1",
            run_id="run-1",
            severity="warning",
            message="Portal returned incomplete results",
            created_at=created_at,
            application_reference="24/00001/FUL",
        )

        self.assertEqual("warning", issue.severity)
        self.assertEqual(created_at, issue.created_at)

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
    def run(
        self,
        request: object,
        emit: object,
        cancel_event: Event,
    ) -> object:
        return object()


class _ApplicationQueryServiceFake:
    def search(self, filters: object) -> object:
        return object()


class _IssueQueryServiceFake:
    def list_issues(self, run_id: str | None = None) -> tuple[object, ...]:
        return ()


if __name__ == "__main__":
    unittest.main()
