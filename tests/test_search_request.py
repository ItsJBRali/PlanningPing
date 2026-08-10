from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_ping import contracts


class SearchRequestTests(unittest.TestCase):
    """Break caught: search requests could accept invalid boundary/date inputs."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.boundary_path = Path(self.temp_directory.name) / "area.geojson"
        self.boundary_path.write_text('{"type": "FeatureCollection", "features": []}')

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_normalizes_phrases_and_preserves_inclusive_date_bounds(self) -> None:
        request = contracts.SearchRequest(
            boundary_geojson_path=self.boundary_path,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            exclusion_phrases=("  Loft conversion  ", "", "LOFT CONVERSION", "Solar"),
        )

        self.assertEqual(("Loft conversion", "Solar"), request.exclusion_phrases)
        self.assertEqual(date(2026, 1, 1), request.start_date)
        self.assertEqual(date(2026, 1, 31), request.end_date)

    def test_rejects_inverted_date_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "start_date"):
            contracts.SearchRequest(
                boundary_geojson_path=self.boundary_path,
                start_date=date(2026, 2, 1),
                end_date=date(2026, 1, 31),
            )

    def test_requires_an_existing_geojson_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "existing .geojson"):
            contracts.SearchRequest(
                boundary_geojson_path=Path(self.temp_directory.name) / "missing.geojson",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
            )


if __name__ == "__main__":
    unittest.main()
