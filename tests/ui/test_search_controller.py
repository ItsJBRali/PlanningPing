from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from planning_ping.contracts import SearchEvent, SearchRequest, SearchSummary
from planning_ping.ui.controllers import BackgroundTaskController, SearchController

from .fakes import FakeSearchService


def summary(status: str = "completed") -> SearchSummary:
    now = datetime(2026, 1, 1)
    return SearchSummary(9, status, 2, 2, 3, 0, 0, now, now)  # type: ignore[arg-type]


class SearchControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temporary_directory.name, "area.geojson")
        self.boundary.write_text("{}", encoding="utf-8")
        self.request = SearchRequest(self.boundary, datetime(2026, 1, 1).date(), datetime(2026, 1, 2).date())

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _drain_until_finished(self, controller: SearchController) -> None:
        deadline = time.monotonic() + 2
        while controller.state.running and time.monotonic() < deadline:
            controller.poll()
            time.sleep(0.005)
        self.assertFalse(controller.state.running)

    def test_worker_events_are_applied_only_when_polled_on_the_ui_thread(self) -> None:
        service = FakeSearchService(
            summary(),
            (
                SearchEvent("started", run_id=9, total=2),
                SearchEvent("council_started", council="Alpha", completed=0, total=2),
                SearchEvent("application_saved", council="Alpha", saved_count=3),
                SearchEvent("warning", council="Alpha", message="partial"),
                SearchEvent("council_finished", council="Alpha", completed=1, total=2),
                SearchEvent("completed", run_id=9, completed=2, total=2, saved_count=3),
            ),
        )
        callback_threads: list[int] = []
        states = []
        controller = SearchController(service, lambda state: (callback_threads.append(threading.get_ident()), states.append(state)))
        controller.start(self.request)
        self._drain_until_finished(controller)
        self.assertEqual(set(callback_threads), {threading.get_ident()})
        self.assertEqual(controller.state.saved_count, 3)
        self.assertEqual(controller.state.completed_councils, 2)
        self.assertEqual(controller.state.warnings, ("Alpha: partial",))
        self.assertEqual(controller.state.summary, summary())
        self.assertTrue(controller.state.search_enabled)
        self.assertFalse(controller.state.cancel_enabled)

    def test_cancel_signals_worker_and_restores_controls_on_cancelled_terminal(self) -> None:
        service = FakeSearchService(summary("cancelled"), wait_for_cancel=True)
        controller = SearchController(service, lambda state: None)
        controller.start(self.request)
        controller.cancel()
        self._drain_until_finished(controller)
        self.assertTrue(service.cancel_events[0].is_set())
        self.assertEqual(controller.state.status_message, "Search cancelled")
        self.assertTrue(controller.state.search_enabled)

    def test_council_started_uses_coordinator_status_message_when_present(self) -> None:
        controller = SearchController(FakeSearchService(summary()), lambda state: None)
        controller._apply_event(
            SearchEvent(
                "council_started",
                council="Alpha",
                completed=1,
                total=3,
                message="Alpha paused by PlanIt rate limit; retrying in 294 seconds",
            )
        )

        self.assertEqual(
            "Alpha paused by PlanIt rate limit; retrying in 294 seconds",
            controller.state.status_message,
        )

    def test_a_running_search_cannot_create_a_duplicate_worker(self) -> None:
        service = FakeSearchService(summary("cancelled"), wait_for_cancel=True)
        controller = SearchController(service, lambda state: None)
        controller.start(self.request)
        with self.assertRaisesRegex(RuntimeError, "already running"):
            controller.start(self.request)
        controller.cancel()
        self._drain_until_finished(controller)
        self.assertEqual(len(service.requests), 1)

    def test_worker_exception_is_marshaled_and_restores_controls(self) -> None:
        service = FakeSearchService(summary(), error=RuntimeError("portal unavailable"))
        controller = SearchController(service, lambda state: None)
        controller.start(self.request)
        self._drain_until_finished(controller)
        self.assertEqual(controller.state.error_message, "portal unavailable")
        self.assertTrue(controller.state.search_enabled)
        self.assertFalse(controller.state.cancel_enabled)


class BackgroundTaskControllerTests(unittest.TestCase):
    def test_completion_callback_runs_only_when_polled_by_the_ui_thread(self) -> None:
        worker_threads: list[int] = []
        callback_threads: list[int] = []
        controller = BackgroundTaskController(lambda: callback_threads.append(threading.get_ident()))
        controller.start(lambda: worker_threads.append(threading.get_ident()))
        deadline = time.monotonic() + 2
        while controller.running and time.monotonic() < deadline:
            controller.poll()
            time.sleep(0.005)
        self.assertFalse(controller.running)
        self.assertNotEqual(worker_threads, callback_threads)
        self.assertEqual(callback_threads, [threading.get_ident()])

    def test_close_suppresses_completion_and_duplicate_tasks_are_rejected(self) -> None:
        release = threading.Event()
        callbacks: list[str] = []
        controller = BackgroundTaskController(lambda: callbacks.append("finished"))
        controller.start(lambda: release.wait(2))
        with self.assertRaisesRegex(RuntimeError, "already running"):
            controller.start(lambda: None)
        controller.close()
        release.set()
        deadline = time.monotonic() + 2
        while controller.running and time.monotonic() < deadline:
            controller.poll()
            time.sleep(0.005)
        self.assertEqual(callbacks, [])


if __name__ == "__main__":
    unittest.main()
