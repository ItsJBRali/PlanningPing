from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.contracts import AppServices, ApplicationQueryService, IssueQueryService, SearchService
from planning_ping.backend.audit import audit_catalogue
from planning_ping.backend.catalogue import AuthorityCatalogue
from planning_ping.backend.factory import create_services
from planning_ping.backend.models import Council
from planning_ping.backend.production import SUPPORTED_PORTAL_FAMILIES, scraper_for_council


BOUNDARY = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
CHECKED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class CatalogueAuditTests(unittest.TestCase):
    def test_reports_duplicate_codes_bad_country_url_boundary_and_unmapped_family(self) -> None:
        councils = (
            Council("dup", "Alpha", "England", "idox", "Idox", "https://alpha.test", None, "https://alpha.test/search", BOUNDARY),
            Council("dup", "Beta", "France", "unknown", "Custom", "not-a-url", None, "", {"type": "Point", "coordinates": [0, 0]}),
        )

        report = audit_catalogue(councils, supported_families=SUPPORTED_PORTAL_FAMILIES, checked_at=CHECKED_AT)

        self.assertFalse(report.ok)
        self.assertEqual(["Alpha", "Beta"], [row.authority for row in report.rows])
        beta_error = report.rows[1].error or ""
        self.assertIn("duplicate stable code", beta_error)
        self.assertIn("unsupported country", beta_error)
        self.assertIn("invalid endpoint", beta_error)
        self.assertIn("invalid boundary", beta_error)
        self.assertIn("unmapped portal family", beta_error)
        self.assertEqual("2026-01-01T00:00:00+00:00", report.rows[0].checked_at)

    def test_packaged_catalogue_is_complete_mapped_and_dispatchable_without_live_requests(self) -> None:
        catalogue = AuthorityCatalogue.load()
        report = audit_catalogue(
            catalogue.councils,
            supported_families=SUPPORTED_PORTAL_FAMILIES,
            adapter_check=lambda item: scraper_for_council(item).close(),
            checked_at=CHECKED_AT,
        )

        self.assertEqual(399, len(catalogue.councils))
        self.assertTrue(report.ok, [row.error for row in report.rows if row.error])
        self.assertNotIn("unknown", {item.portal_family for item in catalogue.councils})


class FactoryTests(unittest.TestCase):
    def test_create_services_returns_importable_frozen_service_bundle_with_injected_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            services = create_services(Path(directory) / "factory.sql")

            self.assertIsInstance(services, AppServices)
            self.assertIsInstance(services.search, SearchService)
            self.assertIsInstance(services.applications, ApplicationQueryService)
            self.assertIsInstance(services.issues, IssueQueryService)
            services.search._database.close()


if __name__ == "__main__":
    unittest.main()
