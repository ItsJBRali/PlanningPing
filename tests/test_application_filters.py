from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_ping import contracts


class ApplicationFiltersTests(unittest.TestCase):
    """Break caught: invalid pagination or ordering could reach the query service."""

    def test_accepts_supported_application_ordering(self) -> None:
        filters = contracts.ApplicationFilters(
            page=2,
            page_size=100,
            sort_by="local_authority",
            sort_direction="asc",
        )

        self.assertEqual(2, filters.page)
        self.assertEqual(100, filters.page_size)
        self.assertEqual("local_authority", filters.sort_by)
        self.assertEqual("asc", filters.sort_direction)

    def test_rejects_page_before_first_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "page"):
            contracts.ApplicationFilters(page=0)

    def test_rejects_page_size_outside_supported_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "page_size"):
            contracts.ApplicationFilters(page_size=501)

    def test_rejects_unknown_sort_field_and_direction(self) -> None:
        with self.assertRaisesRegex(ValueError, "sort_by"):
            contracts.ApplicationFilters(sort_by="postcode")
        with self.assertRaisesRegex(ValueError, "sort_direction"):
            contracts.ApplicationFilters(sort_direction="sideways")


if __name__ == "__main__":
    unittest.main()
