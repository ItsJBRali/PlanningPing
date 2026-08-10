"""SQLite schema, migrations, and transactional search persistence."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping

from .models import Council, PlanningApplication

SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def resolve_database_path(
    database_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    if database_path is not None:
        path = Path(database_path)
    else:
        environment = os.environ if environ is None else environ
        root = environment.get("LOCALAPPDATA")
        if not root:
            root = str(Path.home() / "AppData" / "Local")
        path = Path(root) / "PlanningPing" / "applications.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class PlanningDatabase:
    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.path = resolve_database_path(database_path)
        self._clock = clock
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute(f"PRAGMA busy_timeout={max(0, int(busy_timeout_ms))}")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                yield self.connection
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def migrate(self) -> None:
        with self._lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS councils (
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    country TEXT NOT NULL,
                    portal_family TEXT NOT NULL,
                    scraper_type TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    listing_url TEXT,
                    planning_url TEXT NOT NULL,
                    catalogue_metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY,
                    council_id INTEGER NOT NULL REFERENCES councils(id),
                    reference TEXT NOT NULL COLLATE NOCASE CHECK(length(trim(reference)) > 0),
                    received_date TEXT,
                    validated_date TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    postcode TEXT NOT NULL DEFAULT '',
                    status TEXT,
                    decision TEXT,
                    applicant TEXT,
                    agent TEXT,
                    case_officer TEXT,
                    ward TEXT,
                    parish TEXT,
                    longitude REAL,
                    latitude REAL,
                    location_match_quality TEXT NOT NULL CHECK(location_match_quality IN ('exact', 'council_overlap')),
                    council_url TEXT NOT NULL DEFAULT '',
                    application_url TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    scraped_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(council_id, reference)
                );

                CREATE TABLE IF NOT EXISTS application_documents (
                    id INTEGER PRIMARY KEY,
                    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    document_type TEXT,
                    document_date TEXT,
                    size TEXT,
                    description TEXT,
                    source_url TEXT,
                    document_url TEXT NOT NULL,
                    UNIQUE(application_id, document_url)
                );

                CREATE TABLE IF NOT EXISTS search_runs (
                    id INTEGER PRIMARY KEY,
                    input_path TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    exclusions_json TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    total_councils INTEGER NOT NULL DEFAULT 0,
                    searched_councils INTEGER NOT NULL DEFAULT 0,
                    saved_applications INTEGER NOT NULL DEFAULT 0,
                    empty_councils INTEGER NOT NULL DEFAULT 0,
                    failed_councils INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS search_run_applications (
                    run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
                    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    PRIMARY KEY(run_id, application_id)
                );

                CREATE TABLE IF NOT EXISTS council_search_outcomes (
                    id INTEGER PRIMARY KEY,
                    run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
                    council_id INTEGER NOT NULL REFERENCES councils(id),
                    portal_family TEXT NOT NULL,
                    outcome_status TEXT NOT NULL CHECK(outcome_status IN ('success', 'empty', 'warning', 'error', 'cancelled')),
                    application_count INTEGER NOT NULL DEFAULT 0,
                    exception_type TEXT,
                    exception_message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    UNIQUE(run_id, council_id)
                );

                CREATE INDEX IF NOT EXISTS idx_applications_reference ON applications(reference);
                CREATE INDEX IF NOT EXISTS idx_applications_address ON applications(address);
                CREATE INDEX IF NOT EXISTS idx_applications_postcode ON applications(postcode);
                CREATE INDEX IF NOT EXISTS idx_applications_effective_date ON applications(COALESCE(received_date, validated_date));
                CREATE INDEX IF NOT EXISTS idx_applications_description ON applications(description);
                CREATE INDEX IF NOT EXISTS idx_applications_council ON applications(council_id);
                CREATE INDEX IF NOT EXISTS idx_applications_ordering ON applications(last_seen_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_outcomes_run_status ON council_search_outcomes(run_id, outcome_status);
                PRAGMA user_version=1;
                """
            )

    def begin_search(
        self,
        *,
        input_path: str,
        input_hash: str,
        start_date: date,
        end_date: date,
        exclusions: tuple[str, ...],
        total_councils: int,
    ) -> int:
        now = self._clock().isoformat()
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO search_runs(
                    input_path, input_hash, start_date, end_date, exclusions_json,
                    started_at, status, total_councils
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)""",
                (input_path, input_hash, start_date.isoformat(), end_date.isoformat(), json.dumps(exclusions), now, total_councils),
            )
            return int(cursor.lastrowid)

    def save_council_result(
        self,
        run_id: int,
        council: Council,
        applications: Iterable[PlanningApplication],
        *,
        outcome: str,
        exception: Exception | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> list[int]:
        items = list(applications)
        now = self._clock()
        started = started_at or now
        finished = finished_at or now
        with self.transaction() as connection:
            council_id = self._upsert_council(connection, council)
            application_ids = [self._upsert_application(connection, council_id, item, now) for item in items]
            for application_id in application_ids:
                connection.execute(
                    "INSERT OR IGNORE INTO search_run_applications(run_id, application_id) VALUES (?, ?)",
                    (run_id, application_id),
                )
            connection.execute(
                """INSERT INTO council_search_outcomes(
                    run_id, council_id, portal_family, outcome_status, application_count,
                    exception_type, exception_message, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, council_id) DO UPDATE SET
                    portal_family=excluded.portal_family,
                    outcome_status=excluded.outcome_status,
                    application_count=excluded.application_count,
                    exception_type=excluded.exception_type,
                    exception_message=excluded.exception_message,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at""",
                (
                    run_id,
                    council_id,
                    council.portal_family,
                    outcome,
                    len(items),
                    type(exception).__name__ if exception else None,
                    str(exception) if exception else None,
                    started.isoformat(),
                    finished.isoformat(),
                ),
            )
        return application_ids

    def finish_search(
        self,
        run_id: int,
        *,
        status: str,
        searched_councils: int,
        saved_applications: int,
        empty_councils: int,
        failed_councils: int,
        finished_at: datetime | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE search_runs SET finished_at=?, status=?, searched_councils=?,
                    saved_applications=?, empty_councils=?, failed_councils=? WHERE id=?""",
                (
                    (finished_at or self._clock()).isoformat(),
                    status,
                    searched_councils,
                    saved_applications,
                    empty_councils,
                    failed_councils,
                    run_id,
                ),
            )

    def _upsert_council(self, connection: sqlite3.Connection, council: Council) -> int:
        connection.execute(
            """INSERT INTO councils(
                code, name, country, portal_family, scraper_type, base_url,
                listing_url, planning_url, catalogue_metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name=excluded.name, country=excluded.country,
                portal_family=excluded.portal_family, scraper_type=excluded.scraper_type,
                base_url=excluded.base_url, listing_url=excluded.listing_url,
                planning_url=excluded.planning_url,
                catalogue_metadata_json=excluded.catalogue_metadata_json""",
            (
                council.code,
                council.name,
                council.country,
                council.portal_family,
                council.scraper_type,
                council.base_url,
                council.listing_url,
                council.planning_url,
                json.dumps(council.metadata, sort_keys=True),
            ),
        )
        row = connection.execute("SELECT id FROM councils WHERE code=?", (council.code,)).fetchone()
        assert row is not None
        return int(row[0])

    def _upsert_application(
        self,
        connection: sqlite3.Connection,
        council_id: int,
        application: PlanningApplication,
        now: datetime,
    ) -> int:
        reference = application.reference.strip()
        values = (
            council_id,
            reference,
            _date_text(application.received_date),
            _date_text(application.validated_date),
            application.description,
            application.address,
            application.postcode,
            application.status,
            application.decision,
            application.applicant,
            application.agent,
            application.case_officer,
            application.ward,
            application.parish,
            application.longitude,
            application.latitude,
            application.location_match_quality,
            application.council_url,
            application.application_url,
            now.isoformat(),
            now.isoformat(),
            application.scraped_at.isoformat(),
            json.dumps(application.raw, sort_keys=True),
        )
        connection.execute(
            """INSERT INTO applications(
                council_id, reference, received_date, validated_date, description,
                address, postcode, status, decision, applicant, agent, case_officer,
                ward, parish, longitude, latitude, location_match_quality, council_url,
                application_url, first_seen_at, last_seen_at, scraped_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(council_id, reference) DO UPDATE SET
                received_date=excluded.received_date, validated_date=excluded.validated_date,
                description=excluded.description, address=excluded.address,
                postcode=excluded.postcode, status=excluded.status, decision=excluded.decision,
                applicant=excluded.applicant, agent=excluded.agent, case_officer=excluded.case_officer,
                ward=excluded.ward, parish=excluded.parish, longitude=excluded.longitude,
                latitude=excluded.latitude, location_match_quality=excluded.location_match_quality,
                council_url=excluded.council_url, application_url=excluded.application_url,
                last_seen_at=excluded.last_seen_at, scraped_at=excluded.scraped_at,
                raw_json=excluded.raw_json""",
            values,
        )
        row = connection.execute(
            "SELECT id FROM applications WHERE council_id=? AND reference=? COLLATE NOCASE",
            (council_id, reference),
        ).fetchone()
        assert row is not None
        application_id = int(row[0])
        urls = [document.document_url for document in application.documents]
        if application.documents_complete:
            if urls:
                placeholders = ",".join("?" for _ in urls)
                connection.execute(
                    f"DELETE FROM application_documents WHERE application_id=? AND document_url NOT IN ({placeholders})",
                    (application_id, *urls),
                )
            else:
                connection.execute("DELETE FROM application_documents WHERE application_id=?", (application_id,))
        for document in application.documents:
            connection.execute(
                """INSERT INTO application_documents(
                    application_id, title, document_type, document_date, size,
                    description, source_url, document_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(application_id, document_url) DO UPDATE SET
                    title=excluded.title, document_type=excluded.document_type,
                    document_date=excluded.document_date, size=excluded.size,
                    description=excluded.description, source_url=excluded.source_url""",
                (
                    application_id,
                    document.title,
                    document.document_type,
                    _date_text(document.date),
                    document.size,
                    document.description,
                    document.source_url,
                    document.document_url,
                ),
            )
        return application_id


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None
