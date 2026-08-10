from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import tkinter
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

import customtkinter as ctk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_ping.backend.factory import create_services
from planning_ping.backend.models import Council
from planning_ping.backend.orchestration import AuthoritySearchResult, PlanningSearchService
from planning_ping.backend.persistence import PlanningDatabase
from planning_ping.backend.queries import SqliteApplicationQueryService, SqliteIssueQueryService
from planning_ping.contracts import AppServices, SearchRequest
from planning_ping.ui.app import PlanningPingApp


BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
}


class ShutdownIntegrationTests(unittest.TestCase):
    def _create_app(self, services: AppServices) -> PlanningPingApp:
        try:
            app = PlanningPingApp(services)
        except (tkinter.TclError, RuntimeError) as error:
            raise unittest.SkipTest(f"Tk display or TkDnD is unavailable: {error}") from error
        app.withdraw()
        app.update_idletasks()
        return app

    def test_app_shutdown_waits_for_real_sqlite_search_to_finalize_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "shutdown-search.sql"
            boundary_path = Path(directory) / "boundary.geojson"
            boundary_path.write_text(
                '{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}',
                encoding="utf-8",
            )
            database = PlanningDatabase(database_path)
            searcher = _DelayedCancellationSearcher()
            search = PlanningSearchService(database, _SingleCouncilCatalogue(), searcher)
            services = AppServices(
                search=search,
                applications=SqliteApplicationQueryService(database),
                issues=SqliteIssueQueryService(database),
            )
            app = self._create_app(services)
            controller = app.screens["search_new"]._controller
            request = SearchRequest(boundary_path, date(2026, 1, 1), date(2026, 1, 31))
            controller.start(request)
            self.assertTrue(searcher.entered.wait(2), "search worker did not reach the authority boundary")
            worker = controller._worker
            self.assertIsNotNone(worker)

            try:
                app.destroy()

                self.assertFalse(worker.is_alive(), "app shutdown returned before the search worker terminated")
                row = database.connection.execute(
                    "SELECT status, finished_at FROM search_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual("cancelled", row["status"])
                self.assertIsNotNone(row["finished_at"])
            finally:
                searcher.release.set()
                worker.join(2)
                database.close()

            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(
                    ("cancelled", 1),
                    connection.execute(
                        "SELECT status, finished_at IS NOT NULL FROM search_runs ORDER BY id DESC LIMIT 1"
                    ).fetchone(),
                )

    def test_app_shutdown_drains_saved_and_issue_queries_before_database_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "shutdown-queries.sql"
            base_services = create_services(database_path)
            release = threading.Event()
            applications = _DelayedApplications(base_services.applications, release)
            issues = _DelayedIssues(base_services.issues, release)
            services = AppServices(base_services.search, applications, issues)
            app = self._create_app(services)
            saved_screen = app.screens["search_saved"]
            issues_screen = app.screens["view_issues"]
            saved_screen._search()
            issues_screen._load()
            self.assertTrue(applications.entered.wait(2), "saved query worker did not start")
            self.assertTrue(issues.entered.wait(2), "issues query worker did not start")
            releaser = threading.Thread(target=_release_after_delay, args=(release,), daemon=True)
            releaser.start()

            try:
                app.destroy()

                self.assertTrue(applications.finished.is_set(), "saved query outlived app shutdown")
                self.assertTrue(issues.finished.is_set(), "issues query outlived app shutdown")
                self.assertFalse(saved_screen._query_controller.running)
                self.assertFalse(issues_screen._query_controller.running)
                self.assertEqual("", saved_screen.model.error_message)
                self.assertEqual("", issues_screen.model.error_message)
            finally:
                release.set()
                applications.finished.wait(2)
                issues.finished.wait(2)
                base_services.search._database.close()

            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(1, connection.execute("PRAGMA user_version").fetchone()[0])

    def test_app_shutdown_unschedules_tk_callbacks_before_widget_destruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            services = create_services(Path(directory) / "shutdown-callbacks.sql")
            app = self._create_app(services)
            pending_at_widget_destruction: list[str] = []
            original_destroy = ctk.CTk.destroy

            def observe_destroy(root) -> None:
                pending_at_widget_destruction.extend(root.tk.splitlist(root.tk.call("after", "info")))
                original_destroy(root)

            try:
                with patch.object(ctk.CTk, "destroy", observe_destroy):
                    app.destroy()
                self.assertEqual([], pending_at_widget_destruction)
            finally:
                services.search._database.close()


class _SingleCouncilCatalogue:
    def select(self, _boundary: object) -> list[Council]:
        return [
            Council(
                code="shutdown-council",
                name="Shutdown Council",
                country="England",
                portal_family="idox",
                scraper_type="Idox",
                base_url="https://example.test",
                listing_url="https://example.test/search",
                planning_url="https://example.test/planning",
                boundary=BOUNDARY,
            )
        ]


class _DelayedCancellationSearcher:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def search_primary(self, council, start_date, end_date, cancel_event):
        self.entered.set()
        if not cancel_event.wait(2):
            raise RuntimeError("shutdown did not signal search cancellation")
        self.release.wait(0.15)
        return AuthoritySearchResult()

    def search_planit(self, council, start_date, end_date, cancel_event):
        raise AssertionError("PlanIt must not start after shutdown cancellation")


class _DelayedApplications:
    def __init__(self, delegate, release: threading.Event) -> None:
        self._delegate = delegate
        self._release = release
        self.entered = threading.Event()
        self.finished = threading.Event()

    def search(self, filters):
        self.entered.set()
        self._release.wait(2)
        try:
            return self._delegate.search(filters)
        finally:
            self.finished.set()


class _DelayedIssues:
    def __init__(self, delegate, release: threading.Event) -> None:
        self._delegate = delegate
        self._release = release
        self.entered = threading.Event()
        self.finished = threading.Event()

    def list_issues(self, run_id=None):
        self.entered.set()
        self._release.wait(2)
        try:
            return self._delegate.list_issues(run_id)
        finally:
            self.finished.set()


def _release_after_delay(release: threading.Event) -> None:
    time.sleep(0.1)
    release.set()


if __name__ == "__main__":
    unittest.main()
