from __future__ import annotations

import tkinter
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from planning_ping.contracts import AppServices, IssueRow, Page, SearchSummary
from planning_ping.ui import PlanningPingApp

from .fakes import FakeApplicationQueryService, FakeIssueQueryService, FakeSearchService


class PlanningPingAppSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        now = datetime(2026, 1, 1)
        services = AppServices(
            search=FakeSearchService(SearchSummary(1, "completed", 0, 0, 0, 0, 0, now, now)),
            applications=FakeApplicationQueryService((Page((), 1, 50, 0),)),
            issues=FakeIssueQueryService(
                (
                    IssueRow(
                        issue_id=1,
                        run_id=0,
                        timestamp=datetime(2026, 1, 2, 12, 30),
                        council="Zero Council",
                        portal_family="Idox",
                        outcome="failed",
                        error_type="Timeout",
                        message="Portal timed out",
                    ),
                )
            ),
        )
        try:
            cls.app = PlanningPingApp(services)
        except (tkinter.TclError, RuntimeError) as error:
            raise unittest.SkipTest(f"Tk display or TkDnD is unavailable: {error}") from error
        cls.app.withdraw()
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls) -> None:
        app = getattr(cls, "app", None)
        if app is not None:
            app.destroy()

    def setUp(self) -> None:
        self.app.navigate("home")

    def _drain_query(self, screen) -> None:
        deadline = time.monotonic() + 2
        while screen._query_controller.running and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.005)
        self.assertFalse(screen._query_controller.running)

    @staticmethod
    def _table_texts(table) -> tuple[str, ...]:
        content = getattr(table, "content", table)
        texts: list[str] = []
        for widget in content.winfo_children():
            try:
                texts.append(str(widget.cget("text")))
            except (AttributeError, ValueError):
                continue
        return tuple(texts)

    def test_sidebar_routes_reuse_one_screen_instance_each(self) -> None:
        original_ids = {route: id(screen) for route, screen in self.app.screens.items()}
        for route in ("home", "search_new", "search_saved", "send_applications", "view_issues"):
            self.app.navigate(route)
            self.assertEqual(self.app.active_route, route)
            self.assertEqual(id(self.app.screens[route]), original_ids[route])
            self.app.update_idletasks()
            self.assertEqual(
                [name for name, screen in self.app.screens.items() if screen.grid_info()],
                [route],
            )

    def test_home_action_cards_reach_each_primary_destination(self) -> None:
        home = self.app.screens["home"]
        for route in ("search_new", "search_saved", "send_applications", "view_issues"):
            self.app.navigate("home")
            home.action_buttons[route].invoke()
            self.assertEqual(self.app.active_route, route)

    def test_action_buttons_support_keyboard_traversal_and_activation(self) -> None:
        button = self.app.screens["home"].action_buttons["search_saved"]
        self.assertEqual(str(tkinter.Frame.cget(button, "takefocus")), "1")
        self.app.deiconify()
        self.app.update()
        button.tk.call("focus", "-force", button._w)
        tkinter.Frame.event_generate(button, "<Return>")
        self.app.update()
        self.assertEqual(self.app.active_route, "search_saved")
        self.app.navigate("home")
        button.tk.call("focus", "-force", button._w)
        tkinter.Frame.event_generate(button, "<space>")
        self.app.update()
        self.app.withdraw()
        self.assertEqual(self.app.active_route, "search_saved")

    def test_saved_and_issue_queries_leave_loading_visible_until_main_thread_poll(self) -> None:
        saved = self.app.screens["search_saved"]
        saved._search()
        self.assertEqual(saved.status_var.get(), "Loading…")
        self.assertTrue(saved._query_controller.running)
        issues = self.app.screens["view_issues"]
        issues._load()
        self.assertEqual(issues.status_var.get(), "Loading…")
        self.assertTrue(issues._query_controller.running)
        deadline = time.monotonic() + 2
        while (saved._query_controller.running or issues._query_controller.running) and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.005)
        self.assertFalse(saved._query_controller.running)
        self.assertFalse(issues._query_controller.running)

    def test_issue_validation_error_clears_prior_rows_and_preserves_run_zero(self) -> None:
        issues = self.app.screens["view_issues"]
        issues.run_var.set("")
        issues.outcome_var.set("All outcomes")
        issues._load()
        self._drain_query(issues)
        successful_text = self._table_texts(issues.table)
        self.assertIn("Zero Council", successful_text)
        self.assertIn("0", successful_text)

        issues.run_var.set("not-a-run")
        issues._load()
        self._drain_query(issues)
        self.assertIn("whole number", issues.status_var.get())
        self.assertNotIn("Zero Council", self._table_texts(issues.table))

    def test_saved_and_issue_tables_scroll_horizontally_at_minimum_width(self) -> None:
        self.app.geometry("1024x680")
        self.app.deiconify()
        self.app.update()
        try:
            for route in ("search_saved", "view_issues"):
                self.app.navigate(route)
                self.app.update_idletasks()
                table = self.app.screens[route].table
                self.assertTrue(hasattr(table, "horizontal_scrollbar"))
                start_first, start_last = table.xview()
                self.assertLess(start_last - start_first, 1.0)
                table.xview_moveto(1.0)
                self.app.update_idletasks()
                end_first, end_last = table.xview()
                self.assertGreater(end_first, start_first)
                self.assertAlmostEqual(end_last, 1.0, places=4)
        finally:
            self.app.geometry("1280x800")
            self.app.withdraw()

    def test_clear_filters_cannot_mutate_state_during_a_saved_query(self) -> None:
        saved = self.app.screens["search_saved"]
        saved.filter_vars["reference"].set("REF-LOCKED")
        saved._search()
        self.assertTrue(saved._query_controller.running)
        self.assertEqual(saved.clear_button.cget("state"), "disabled")
        saved._clear()
        self.assertEqual(saved.filter_vars["reference"].get(), "REF-LOCKED")
        self.assertEqual(saved.model.filter_values["reference"], "REF-LOCKED")
        deadline = time.monotonic() + 2
        while saved._query_controller.running and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.005)

    def test_search_validation_places_errors_with_the_relevant_field_group(self) -> None:
        screen = self.app.screens["search_new"]
        screen.path_var.set("missing.geojson")
        screen.start_var.set("2026-01-01")
        screen.end_var.set("2026-01-31")
        screen._start_search()
        self.assertIn("existing .geojson", screen.boundary_error_var.get())
        self.assertEqual(screen.date_error_var.get(), "")
        with tempfile.TemporaryDirectory() as directory:
            boundary = Path(directory, "area.geojson")
            boundary.write_text("{}", encoding="utf-8")
            screen.path_var.set(str(boundary))
            screen.start_var.set("2026-02-01")
            screen.end_var.set("2026-01-31")
            screen._start_search()
        self.assertEqual(screen.boundary_error_var.get(), "")
        self.assertIn("on or before", screen.date_error_var.get())

    def test_empty_send_screen_and_unfinished_actions_are_inert(self) -> None:
        screen = self.app.screens["send_applications"]
        self.assertEqual(screen.rows, ())
        starting_route = self.app.active_route
        screen.unfinished_buttons[0].invoke()
        self.assertEqual(screen.status_var.get(), "Not available yet")
        self.assertEqual(self.app.active_route, starting_route)


if __name__ == "__main__":
    unittest.main()
