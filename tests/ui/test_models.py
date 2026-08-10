from __future__ import annotations

import tempfile
import tkinter
import unittest
from datetime import date, datetime
from pathlib import Path

from planning_ping.contracts import ApplicationRow, IssueRow, Page
from planning_ping.ui.models import (
    NAVIGATION_ROUTES,
    IssueResultsModel,
    SavedApplicationsModel,
    build_search_request,
    is_openable_url,
    open_url_if_safe,
    parse_drop_paths,
    validate_geojson_selection,
)

from .fakes import FakeApplicationQueryService, FakeIssueQueryService


class DropAndSearchInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temporary_directory.name, "Area with spaces.geojson")
        self.boundary.write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_tcl_splitlist_preserves_a_braced_path_with_spaces(self) -> None:
        tcl = tkinter.Tcl()
        paths = parse_drop_paths("{%s}" % self.boundary, tcl.splitlist)
        self.assertEqual(paths, (self.boundary,))

    def test_geojson_selection_requires_exactly_one_existing_geojson(self) -> None:
        other = Path(self.temporary_directory.name, "other.geojson")
        other.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_geojson_selection((self.boundary, other))
        with self.assertRaisesRegex(ValueError, "existing .geojson"):
            validate_geojson_selection((self.boundary.with_suffix(".json"),))

    def test_search_request_uses_real_contract_for_dates_and_phrase_normalization(self) -> None:
        request = build_search_request(
            str(self.boundary), "2026-01-01", "2026-01-31", " sheds \n\nSHEDS\n garages "
        )
        self.assertEqual(request.start_date, date(2026, 1, 1))
        self.assertEqual(request.end_date, date(2026, 1, 31))
        self.assertEqual(request.exclusion_phrases, ("sheds", "garages"))
        with self.assertRaisesRegex(ValueError, "on or before"):
            build_search_request(str(self.boundary), "2026-02-01", "2026-01-31", "")


class NavigationAndUrlTests(unittest.TestCase):
    def test_every_required_destination_has_a_unique_route(self) -> None:
        self.assertEqual(
            tuple(NAVIGATION_ROUTES),
            ("home", "search_new", "search_saved", "send_applications", "view_issues"),
        )
        self.assertEqual(len(set(NAVIGATION_ROUTES)), 5)

    def test_only_explicit_http_and_https_urls_are_openable(self) -> None:
        self.assertTrue(is_openable_url("https://planning.example/app/1"))
        self.assertTrue(is_openable_url("http://planning.example/app/1"))
        for unsafe in ("", "planning.example", "javascript:alert(1)", "file:///tmp/a", "https:///missing"):
            with self.subTest(unsafe=unsafe):
                self.assertFalse(is_openable_url(unsafe))

    def test_url_opener_runs_only_for_a_safe_explicit_action(self) -> None:
        opened: list[str] = []
        self.assertFalse(open_url_if_safe("javascript:alert(1)", opened.append))
        self.assertTrue(open_url_if_safe("https://planning.example/1", opened.append))
        self.assertEqual(opened, ["https://planning.example/1"])


class SavedApplicationsModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row = ApplicationRow(
            application_id=1,
            reference="REF-1",
            council="Example Council",
            application_date=date(2026, 1, 12),
            received_date=date(2026, 1, 10),
            validated_date=date(2026, 1, 12),
            description="Rear extension",
            address="1 High Street",
            postcode="AB1 2CD",
            status="Pending",
            application_url="https://planning.example/1",
            council_url="https://council.example",
        )
        page_one = Page(items=(self.row,), page=1, page_size=20, total_items=21)
        page_two = Page(items=(), page=2, page_size=20, total_items=21)
        self.service = FakeApplicationQueryService((page_one, page_one, page_one, page_two, page_one))
        self.model = SavedApplicationsModel(self.service, page_size=20)

    def test_filters_sorting_and_pagination_are_sent_through_the_contract(self) -> None:
        self.model.set_filters(
            reference="REF", address="High", postcode="AB1", application_date="2026-01-12",
            keywords="extension", council="Example",
        )
        self.model.search()
        sent = self.service.filters[-1]
        self.assertEqual(
            (sent.reference, sent.address, sent.postcode, sent.application_date, sent.keywords, sent.council),
            ("REF", "High", "AB1", date(2026, 1, 12), "extension", "Example"),
        )
        self.model.sort("reference")
        self.assertEqual((self.service.filters[-1].sort_by, self.service.filters[-1].sort_direction), ("reference", "asc"))
        self.model.sort("reference")
        self.assertEqual(self.service.filters[-1].sort_direction, "desc")
        with self.assertRaisesRegex(ValueError, "sortable"):
            self.model.sort("application_url")
        self.model.next_page()
        self.assertEqual(self.service.filters[-1].page, 2)
        self.model.previous_page()
        self.assertEqual(self.service.filters[-1].page, 1)

    def test_clear_filters_restores_defaults_and_result_state_tracks_loading_empty_and_errors(self) -> None:
        self.model.set_filters(reference="REF", council="Example")
        self.model.clear_filters()
        self.assertEqual(self.model.filter_values, {name: "" for name in self.model.FILTER_NAMES})
        self.model.search()
        self.assertEqual(self.model.rows, (self.row,))
        self.assertEqual((self.model.page, self.model.total_items, self.model.total_pages), (1, 21, 2))

    def test_query_failures_restore_loading_and_show_an_error_state(self) -> None:
        class FailingApplicationService:
            def search(self, filters):
                raise RuntimeError("query unavailable")

        model = SavedApplicationsModel(FailingApplicationService())
        model.search()
        self.assertFalse(model.loading)
        self.assertEqual(model.rows, ())
        self.assertEqual(model.error_message, "query unavailable")

    def test_query_failure_clears_totals_from_an_earlier_success(self) -> None:
        successful = Page(items=(self.row,), page=1, page_size=20, total_items=21)

        class SuccessThenFailureService:
            def __init__(self):
                self.calls = 0

            def search(self, filters):
                self.calls += 1
                if self.calls == 1:
                    return successful
                raise RuntimeError("query unavailable")

        model = SavedApplicationsModel(SuccessThenFailureService(), page_size=20)
        model.search()
        model.search()
        self.assertEqual((model.total_items, model.total_pages, model.page), (0, 1, 1))


class IssueResultsModelTests(unittest.TestCase):
    def test_filters_by_run_through_service_and_by_outcome_locally(self) -> None:
        rows = (
            IssueRow(1, 7, datetime(2026, 1, 1), "A", "Idox", "failed", "Timeout", "late"),
            IssueRow(2, 7, datetime(2026, 1, 2), "B", "Arcus", "warning", None, "partial"),
            IssueRow(3, 8, datetime(2026, 1, 3), "C", "Idox", "failed", "Parse", "bad"),
        )
        service = FakeIssueQueryService(rows)
        model = IssueResultsModel(service)
        model.load("7", "failed")
        self.assertEqual(service.run_ids, [7])
        self.assertEqual(tuple(row.issue_id for row in model.rows), (1,))
        with self.assertRaisesRegex(ValueError, "whole number"):
            model.load("seven", "")
        self.assertEqual(model.rows, ())

    def test_query_failures_become_error_states(self) -> None:
        class FailingIssueService:
            def list_issues(self, run_id: int | None = None):
                raise RuntimeError("database busy")

        model = IssueResultsModel(FailingIssueService())
        model.load()
        self.assertEqual(model.rows, ())
        self.assertEqual(model.error_message, "database busy")


if __name__ == "__main__":
    unittest.main()
