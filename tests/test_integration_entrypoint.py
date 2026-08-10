from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import tkinter
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_ping.backend.factory import create_services
from planning_ping.contracts import AppServices


class ServiceCompositionIntegrationTests(unittest.TestCase):
    """Integration coverage for the executable composition boundary."""

    def test_real_services_create_versioned_default_database_and_persist_across_recreation(self) -> None:
        try:
            from planning_ping.__main__ import close_services
        except ModuleNotFoundError:
            self.fail("the application composition entrypoint is missing")

        with tempfile.TemporaryDirectory() as local_appdata:
            database_path = Path(local_appdata) / "PlanningPing" / "applications.sql"
            with patch.dict(os.environ, {"LOCALAPPDATA": local_appdata}):
                services = create_services()
                try:
                    database = services.search._database
                    run_id = database.begin_search(
                        input_path="boundary.geojson",
                        input_hash="integration-marker",
                        start_date=date(2026, 1, 1),
                        end_date=date(2026, 1, 31),
                        exclusions=(),
                        total_councils=0,
                    )
                finally:
                    close_services(services)

                self.assertTrue(database_path.is_file())
                with closing(sqlite3.connect(database_path)) as connection:
                    self.assertEqual(1, connection.execute("PRAGMA user_version").fetchone()[0])

                recreated = create_services()
                try:
                    row = recreated.search._database.connection.execute(
                        "SELECT input_hash FROM search_runs WHERE id=?", (run_id,)
                    ).fetchone()
                    self.assertEqual("integration-marker", row[0])
                finally:
                    close_services(recreated)

    def test_normal_entrypoint_passes_real_services_to_ui_runner_and_closes_database(self) -> None:
        import planning_ping.__main__ as entrypoint

        self.assertTrue(hasattr(entrypoint, "main"), "the executable main function is missing")
        received: list[AppServices] = []

        def record_ui_launch(services: AppServices) -> None:
            received.append(services)

        with tempfile.TemporaryDirectory() as local_appdata:
            database_path = Path(local_appdata) / "PlanningPing" / "applications.sql"
            with patch.dict(os.environ, {"LOCALAPPDATA": local_appdata}):
                os.environ.pop("PLANNINGPING_SMOKE_TEST", None)
                with patch.object(entrypoint, "run_app", record_ui_launch):
                    self.assertEqual(0, entrypoint.main())

            self.assertEqual(1, len(received))
            self.assertIsInstance(received[0], AppServices)
            self.assertTrue(database_path.is_file())
            database_path.unlink()
            self.assertFalse(database_path.exists())

    def test_smoke_mode_constructs_real_services_and_ui_then_exits_cleanly(self) -> None:
        import planning_ping.__main__ as entrypoint

        self.assertTrue(hasattr(entrypoint, "_run_smoke"), "the packaged startup smoke path is missing")
        try:
            display_probe = tkinter.Tk()
            display_probe.update_idletasks()
            display_probe.destroy()
        except tkinter.TclError as error:
            raise unittest.SkipTest(f"Tk display is unavailable: {error}") from error
        with tempfile.TemporaryDirectory() as local_appdata:
            database_path = Path(local_appdata) / "PlanningPing" / "applications.sql"
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": local_appdata, "PLANNINGPING_SMOKE_TEST": "1"},
            ):
                result = entrypoint.main()

            self.assertEqual(0, result)
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(1, connection.execute("PRAGMA user_version").fetchone()[0])
            database_path.unlink()

    def test_smoke_mode_cancels_pending_tk_callbacks_before_destroy(self) -> None:
        import planning_ping.__main__ as entrypoint

        class ObservedPlanningPingApp(entrypoint.PlanningPingApp):
            pending_at_destroy: tuple[str, ...] = ()

            def destroy(self) -> None:
                type(self).pending_at_destroy = tuple(self.tk.call("after", "info"))
                super().destroy()

        with tempfile.TemporaryDirectory() as local_appdata:
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": local_appdata, "PLANNINGPING_SMOKE_TEST": "1"},
            ), patch.object(entrypoint, "PlanningPingApp", ObservedPlanningPingApp):
                entrypoint.main()

        self.assertEqual((), ObservedPlanningPingApp.pending_at_destroy)


if __name__ == "__main__":
    unittest.main()
