from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.contracts import ApplicationFilters
from planning_ping.backend.models import ApplicationDocument, Council, PlanningApplication
from planning_ping.backend.persistence import PlanningDatabase, resolve_database_path
from planning_ping.backend.queries import SqliteApplicationQueryService, SqliteIssueQueryService


BOUNDARY = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
FIRST = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
SECOND = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)


def council(code: str = "alpha", name: str = "Alpha Council", country: str = "England") -> Council:
    return Council(code, name, country, "idox", "Idox", f"https://{code}.test", None, f"https://{code}.test/search", BOUNDARY)


def application(
    reference: str,
    *,
    council_code: str = "alpha",
    received: date | None = date(2026, 1, 5),
    validated: date | None = None,
    description: str = "Rear extension",
    address: str = "1 High Street",
    postcode: str = "EX1 1AA",
    status: str = "Pending",
    documents: tuple[ApplicationDocument, ...] = (),
    documents_complete: bool = False,
) -> PlanningApplication:
    return PlanningApplication(
        council_code=council_code,
        reference=reference,
        authority="Alpha Council",
        application_url=f"https://alpha.test/{reference}",
        council_url="https://alpha.test/search",
        received_date=received,
        validated_date=validated,
        description=description,
        address=address,
        postcode=postcode,
        status=status,
        longitude=1.0,
        latitude=1.0,
        location_match_quality="exact",
        scraped_at=FIRST,
        raw={"reference": reference},
        documents=documents,
        documents_complete=documents_complete,
    )


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class PersistenceTests(unittest.TestCase):
    def test_document_pruning_requires_an_explicit_complete_listing(self) -> None:
        run_id = self._begin_run()
        first = ApplicationDocument("First", "https://alpha.test/doc/1")
        second = ApplicationDocument("Second", "https://alpha.test/doc/2")
        self.database.save_council_result(
            run_id, council(), [application("24/001", documents=(first, second), documents_complete=True)], outcome="success"
        )

        self.database.save_council_result(
            run_id, council(), [application("24/001", documents=(first,), documents_complete=False)], outcome="success"
        )
        urls = {row[0] for row in self.database.connection.execute("SELECT document_url FROM application_documents")}
        self.assertEqual({first.document_url, second.document_url}, urls)

        self.database.save_council_result(
            run_id, council(), [application("24/001", documents=(first,), documents_complete=True)], outcome="success"
        )
        urls = {row[0] for row in self.database.connection.execute("SELECT document_url FROM application_documents")}
        self.assertEqual({first.document_url}, urls)

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_directory.name) / "applications.sql"
        self.clock = MutableClock(FIRST)
        self.database = PlanningDatabase(self.path, clock=self.clock)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_directory.cleanup()

    def test_default_path_uses_local_appdata_and_creates_parent(self) -> None:
        root = Path(self.temp_directory.name) / "Local"
        path = resolve_database_path(environ={"LOCALAPPDATA": str(root)})

        self.assertEqual(root / "PlanningPing" / "applications.sql", path)
        self.assertTrue(path.parent.is_dir())

    def test_migration_is_idempotent_and_enables_required_sqlite_pragmas(self) -> None:
        self.database.migrate()
        self.database.migrate()

        tables = {row[0] for row in self.database.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"councils", "applications", "application_documents", "search_runs", "search_run_applications", "council_search_outcomes"} <= tables)
        self.assertEqual(1, self.database.connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(1, self.database.connection.execute("PRAGMA foreign_keys").fetchone()[0])
        self.assertEqual("wal", self.database.connection.execute("PRAGMA journal_mode").fetchone()[0].casefold())
        index_names = {row[1] for row in self.database.connection.execute("PRAGMA index_list(applications)")}
        self.assertTrue({"idx_applications_reference", "idx_applications_effective_date", "idx_applications_ordering"} <= index_names)

    def test_application_upsert_preserves_first_seen_updates_last_seen_and_replaces_document_metadata(self) -> None:
        run_id = self._begin_run()
        original = application(
            "24/001",
            documents=(ApplicationDocument("Plan v1", "https://alpha.test/doc/1", "Drawing", date(2026, 1, 5), "10 KB"),),
        )
        ids = self.database.save_council_result(run_id, council(), [original], outcome="success")

        self.clock.value = SECOND
        changed = application(
            "24/001",
            description="Updated proposal",
            documents=(ApplicationDocument("Plan v2", "https://alpha.test/doc/1", "Drawing", date(2026, 1, 6), "12 KB"),),
        )
        second_ids = self.database.save_council_result(run_id, council(), [changed], outcome="success")

        row = self.database.connection.execute(
            "SELECT id, description, first_seen_at, last_seen_at FROM applications"
        ).fetchone()
        document = self.database.connection.execute(
            "SELECT title, document_date, size FROM application_documents"
        ).fetchone()
        self.assertEqual(ids, second_ids)
        self.assertEqual("Updated proposal", row[1])
        self.assertEqual(FIRST.isoformat(), row[2])
        self.assertEqual(SECOND.isoformat(), row[3])
        self.assertEqual(("Plan v2", "2026-01-06", "12 KB"), tuple(document))
        self.assertEqual(1, self.database.connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0])
        self.assertEqual(1, self.database.connection.execute("SELECT COUNT(*) FROM application_documents").fetchone()[0])

    def test_completed_council_transaction_survives_a_later_rollback(self) -> None:
        run_id = self._begin_run()
        self.database.save_council_result(run_id, council(), [application("24/001")], outcome="success")

        with self.assertRaises(sqlite3.IntegrityError):
            self.database.save_council_result(
                run_id,
                council("beta", "Beta Council"),
                [application("", council_code="beta")],
                outcome="success",
            )

        references = [row[0] for row in self.database.connection.execute("SELECT reference FROM applications")]
        self.assertEqual(["24/001"], references)

    def _begin_run(self) -> int:
        return self.database.begin_search(
            input_path="C:/area.geojson",
            input_hash="abc123",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            exclusions=("shed",),
            total_councils=2,
        )


class QueryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = PlanningDatabase(Path(self.temp_directory.name) / "query.sql", clock=lambda: FIRST)
        self.run_id = self.database.begin_search(
            input_path="area.geojson",
            input_hash="hash",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            exclusions=(),
            total_councils=2,
        )
        self.database.save_council_result(
            self.run_id,
            council(),
            [
                application("24/A", received=date(2026, 1, 2), description="Rear extension 100%", address="1 High Street", postcode="EX1 1AA", status="Pending"),
                application("24/B", received=None, validated=date(2026, 1, 3), description="New dwelling", address="2 Low Road", postcode="EX2 2BB", status="Approved"),
            ],
            outcome="success",
        )
        self.database.save_council_result(
            self.run_id,
            council("beta", "Beta Council", "Wales"),
            [application("25/C", council_code="beta", received=date(2026, 1, 4), description="Solar array", address="3 Farm Lane", postcode="WA1 3CC", status="Pending")],
            outcome="warning",
            exception=RuntimeError("reported total mismatch"),
        )
        self.applications = SqliteApplicationQueryService(self.database)
        self.issues = SqliteIssueQueryService(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_directory.cleanup()

    def test_every_filter_is_parameterized_case_insensitive_and_escaped(self) -> None:
        cases = (
            (ApplicationFilters(reference="24/a"), ["24/A"]),
            (ApplicationFilters(address="low road"), ["24/B"]),
            (ApplicationFilters(postcode="ex1"), ["24/A"]),
            (ApplicationFilters(application_date=date(2026, 1, 3)), ["24/B"]),
            (ApplicationFilters(keywords="100%"), ["24/A"]),
            (ApplicationFilters(council="Beta Council"), ["25/C"]),
            (ApplicationFilters(reference="%' OR 1=1 --"), []),
        )
        for filters, expected in cases:
            with self.subTest(filters=filters):
                self.assertEqual(expected, [row.reference for row in self.applications.search(filters).items])

    def test_every_sort_and_pagination_return_stable_pages(self) -> None:
        expected_first = {
            "reference": "24/A",
            "council": "24/A",
            "application_date": "24/A",
            "received_date": "24/A",
            "validated_date": "24/B",
            "description": "24/B",
            "address": "24/A",
            "postcode": "24/A",
            "status": "24/B",
        }
        for field, expected in expected_first.items():
            with self.subTest(field=field):
                page = self.applications.search(ApplicationFilters(sort_by=field, sort_direction="asc", page=1, page_size=1))
                self.assertEqual(3, page.total_items)
                self.assertEqual(expected, page.items[0].reference)

        second_page = self.applications.search(ApplicationFilters(sort_by="reference", sort_direction="asc", page=2, page_size=1))
        self.assertEqual("24/B", second_page.items[0].reference)

    def test_issue_query_returns_only_problem_outcomes_and_can_scope_by_run(self) -> None:
        issues = self.issues.list_issues()
        scoped = self.issues.list_issues(self.run_id)

        self.assertEqual(1, len(issues))
        self.assertEqual(issues, scoped)
        self.assertEqual("Beta Council", issues[0].council)
        self.assertEqual("warning", issues[0].outcome)
        self.assertEqual("RuntimeError", issues[0].error_type)
        self.assertEqual((), self.issues.list_issues(self.run_id + 99))


if __name__ == "__main__":
    unittest.main()
