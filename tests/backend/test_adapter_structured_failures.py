from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.backend.adapters.achieveforms import (
    AchieveFormsCouncilConfig,
    AchieveFormsMetadata,
    AchieveFormsPlanningScraper,
)
from planning_ping.backend.adapters.civica import (
    CivicaCouncilConfig,
    CivicaPlanningScraper,
)
from planning_ping.backend.adapters.idox import (
    IdoxCouncilConfig,
    IdoxPublicAccessScraper,
)
from planning_ping.backend.http import CouncilAccessBlockedError, CouncilRateLimitError


def structured_failures() -> tuple[Exception, ...]:
    return (
        CouncilRateLimitError(
            url="https://planning.example.test/search",
            scope="portal:idox",
            retry_after_seconds=60.0,
            retry_limit=6,
        ),
        CouncilAccessBlockedError(
            url="https://planning.example.test/search",
            category="portal_security",
            reason="Web application firewall challenge detected",
        ),
    )


class AdapterStructuredFailureTests(unittest.TestCase):
    def test_idox_complete_week_search_propagates_structured_failure_without_weekly_fallback(self) -> None:
        class FailingHttp:
            def __init__(self, error: Exception) -> None:
                self.error = error
                self.calls: list[str] = []

            def get(self, url: str, **_: object):
                self.calls.append(url)
                if len(self.calls) > 1:
                    raise AssertionError("Idox must not request a weekly fallback after a structured failure")
                raise self.error

        for error in structured_failures():
            with self.subTest(error=type(error).__name__):
                http = FailingHttp(error)
                scraper = IdoxPublicAccessScraper(
                    IdoxCouncilConfig("Example", "https://planning.example.test"),
                    http_client=http,  # type: ignore[arg-type]
                )

                with self.assertRaises(type(error)) as raised:
                    scraper.discover_ids(
                        listing_url="https://planning.example.test/advanced-search",
                        start_date=date(2026, 8, 10),
                        end_date=date(2026, 8, 16),
                    )

                self.assertIs(error, raised.exception)
                self.assertEqual(
                    ["https://planning.example.test/advanced-search"],
                    http.calls,
                )

    def test_achieveforms_detail_lookup_propagates_structured_failure_without_stub_fallback(self) -> None:
        metadata = AchieveFormsMetadata(
            listing_url="https://planning.example.test/form",
            form_uri="planning-form",
            form_id="form-id",
            form_name="Planning search",
            weekly_lookup_id="weekly",
            detail_lookup_id="detail",
            documents_lookup_id="documents",
        )

        class FailingAchieveForms(AchieveFormsPlanningScraper):
            def __init__(self, error: Exception) -> None:
                super().__init__(
                    AchieveFormsCouncilConfig("Example", "https://planning.example.test")
                )
                self.error = error
                self.lookup_calls: list[str] = []

            def _metadata(self, listing_url: str) -> AchieveFormsMetadata:
                return metadata

            def _lookup_rows(
                self,
                metadata: AchieveFormsMetadata,
                lookup_id: str,
                tokens: dict[str, str],
            ) -> list[dict[str, str]]:
                self.lookup_calls.append(lookup_id)
                if lookup_id == metadata.weekly_lookup_id:
                    return [{"referenceNumber": "24/0001"}]
                raise self.error

        for error in structured_failures():
            with self.subTest(error=type(error).__name__):
                scraper = FailingAchieveForms(error)

                with self.assertRaises(type(error)) as raised:
                    scraper.discover_ids(listing_url=metadata.listing_url)

                self.assertIs(error, raised.exception)
                self.assertEqual(["weekly", "detail"], scraper.lookup_calls)

    def test_civica_criteria_probe_propagates_structured_failure_without_default_field_fallback(self) -> None:
        class FailingHttp:
            def __init__(self, error: Exception) -> None:
                self.error = error
                self.calls: list[str] = []

            def get(self, url: str, **_: object):
                self.calls.append(url)
                raise self.error

        for error in structured_failures():
            with self.subTest(error=type(error).__name__):
                http = FailingHttp(error)
                scraper = CivicaPlanningScraper(
                    CivicaCouncilConfig("Example", "https://planning.example.test"),
                    http_client=http,  # type: ignore[arg-type]
                )

                with self.assertRaises(type(error)) as raised:
                    scraper._preferred_date_search_fields(
                        "https://planning.example.test/api/",
                        "GFPlanning",
                    )

                self.assertIs(error, raised.exception)
                self.assertEqual(1, len(http.calls))


if __name__ == "__main__":
    unittest.main()
