from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.backend.adapters import (
    AchieveFormsPlanningScraper,
    AgilePlanningScraper,
    ArcusPlanningScraper,
    AtriumPlanningScraper,
    CivicaPlanningScraper,
    IdoxCouncilConfig,
    IdoxPublicAccessScraper,
    NorthgatePlanningScraper,
    OcellaPlanningScraper,
    WiltshirePlanningScraper,
)
from planning_ping.backend.parsing import clean_text, extract_postcode, parse_council_date


FIXTURES = Path(__file__).parent / "fixtures"


class AdapterNamespaceCharacterizationTests(unittest.TestCase):
    """Break caught: ported adapters could change parsing behavior or retain old imports."""

    def test_every_primary_adapter_family_imports_from_planning_ping_namespace(self) -> None:
        classes = (
            AchieveFormsPlanningScraper,
            AgilePlanningScraper,
            ArcusPlanningScraper,
            AtriumPlanningScraper,
            CivicaPlanningScraper,
            IdoxPublicAccessScraper,
            NorthgatePlanningScraper,
            OcellaPlanningScraper,
            WiltshirePlanningScraper,
        )
        self.assertTrue(all(item.__module__.startswith("planning_ping.backend.adapters") for item in classes))

    def test_idox_fixture_preserves_listing_reference_and_detail_url(self) -> None:
        scraper = IdoxPublicAccessScraper(
            IdoxCouncilConfig(authority="Example Council", base_url="https://planning.example.gov.uk")
        )
        html = (FIXTURES / "idox_listing.html").read_text(encoding="utf-8")

        applications = scraper.parse_listing(
            html,
            "https://planning.example.gov.uk/online-applications/weeklyListResults.do?action=firstPage",
        )

        self.assertEqual("24/01234/FUL", applications[0].reference)
        self.assertEqual("ABC123XYZ", applications[0].uid)
        self.assertEqual(
            "https://planning.example.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=ABC123XYZ",
            applications[0].url,
        )

    def test_shared_parsing_normalizes_inherited_portal_values(self) -> None:
        self.assertEqual("A & B", clean_text(" A&nbsp;&amp;&nbsp; B "))
        self.assertEqual("2026-07-15", parse_council_date("15 July 2026"))
        self.assertEqual("SW1A 1AA", extract_postcode("London sw1a1aa"))


if __name__ == "__main__":
    unittest.main()
