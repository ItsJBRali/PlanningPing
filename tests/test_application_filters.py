from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_ping import contracts


class ApplicationFiltersTests(unittest.TestCase):
    """Break caught: query filters could diverge from visible application fields."""

    def test_accepts_all_application_filters_and_ordering(self) -> None:
        filters = contracts.ApplicationFilters(
            reference="24/00001",
            address="High Street",
            postcode="EX1 1AA",
            application_date=date(2026, 1, 7),
            keywords="rear extension",
            council="Example Council",
            page=2,
            page_size=100,
            sort_by="council",
            sort_direction="asc",
        )

        self.assertEqual("24/00001", filters.reference)
        self.assertEqual("High Street", filters.address)
        self.assertEqual("EX1 1AA", filters.postcode)
        self.assertEqual(date(2026, 1, 7), filters.application_date)
        self.assertEqual("rear extension", filters.keywords)
        self.assertEqual("Example Council", filters.council)
        self.assertEqual("council", filters.sort_by)

    def test_rejects_page_before_first_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "page"):
            contracts.ApplicationFilters(page=0)

    def test_rejects_page_size_outside_supported_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "page_size"):
            contracts.ApplicationFilters(page_size=501)

    def test_rejects_fractional_and_boolean_pagination_values(self) -> None:
        for field_name, value in (
            ("page", 1.5),
            ("page", True),
            ("page_size", 10.5),
            ("page_size", True),
        ):
            with self.subTest(field_name=field_name, value=value):
                with self.assertRaisesRegex(ValueError, field_name):
                    contracts.ApplicationFilters(**{field_name: value})

    def test_rejects_unknown_sort_field_and_direction(self) -> None:
        with self.assertRaisesRegex(ValueError, "sort_by"):
            contracts.ApplicationFilters(sort_by="agent")
        with self.assertRaisesRegex(ValueError, "sort_direction"):
            contracts.ApplicationFilters(sort_direction="sideways")


if __name__ == "__main__":
    unittest.main()
