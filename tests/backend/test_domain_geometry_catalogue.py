from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.backend.catalogue import AuthorityCatalogue
from planning_ping.backend.filtering import (
    application_matches_request,
    reconcile_applications,
)
from planning_ping.backend.geometry import (
    geometry_polygons,
    geometries_intersect,
    location_match_quality,
    validate_geojson,
)
from planning_ping.backend.models import Council, PlanningApplication


SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]],
}


class DomainFilteringTests(unittest.TestCase):
    def test_effective_date_uses_received_then_validated_and_undated_never_matches(self) -> None:
        received = PlanningApplication(
            council_code="c1", reference="R1", received_date=date(2026, 1, 4), validated_date=date(2026, 1, 5)
        )
        validated = PlanningApplication(
            council_code="c1", reference="R2", validated_date=date(2026, 1, 5)
        )
        undated = PlanningApplication(council_code="c1", reference="R3")

        self.assertEqual(date(2026, 1, 4), received.application_date)
        self.assertTrue(application_matches_request(received, date(2026, 1, 4), date(2026, 1, 4), ()))
        self.assertTrue(application_matches_request(validated, date(2026, 1, 5), date(2026, 1, 5), ()))
        self.assertFalse(application_matches_request(undated, date(2026, 1, 1), date(2026, 1, 31), ()))

    def test_exclusions_match_description_only_case_insensitively(self) -> None:
        excluded = PlanningApplication(
            council_code="loft-council",
            reference="SOLAR/1",
            description="Proposed LOFT Conversion",
            address="Solar House",
            received_date=date(2026, 1, 5),
        )
        retained = PlanningApplication(
            council_code="loft-council",
            reference="LOFT/2",
            description="Rear extension",
            address="Loft Lane",
            received_date=date(2026, 1, 5),
        )

        self.assertFalse(application_matches_request(excluded, date(2026, 1, 1), date(2026, 1, 31), ("loft conversion",)))
        self.assertTrue(application_matches_request(retained, date(2026, 1, 1), date(2026, 1, 31), ("loft",)))

    def test_primary_records_win_by_normalized_reference(self) -> None:
        primary = PlanningApplication(council_code="c1", reference=" 24/ABC ", description="Primary")
        planit_duplicate = PlanningApplication(council_code="c1", reference="24/abc", description="PlanIt")
        planit_new = PlanningApplication(council_code="c1", reference="24/DEF", description="PlanIt new")

        merged = reconcile_applications([primary], [planit_duplicate, planit_new])

        self.assertEqual(["Primary", "PlanIt new"], [item.description for item in merged])


class GeometryTests(unittest.TestCase):
    def test_validates_polygons_and_multipolygons_and_rejects_malformed_coordinates(self) -> None:
        multi = {"type": "MultiPolygon", "coordinates": [SQUARE["coordinates"], [[[10, 10], [11, 10], [11, 11], [10, 11], [10, 10]]]]}
        self.assertEqual(1, len(validate_geojson(SQUARE)))
        self.assertEqual(2, len(geometry_polygons(multi)))
        with self.assertRaisesRegex(ValueError, "numeric"):
            validate_geojson({"type": "Polygon", "coordinates": [[[0, 0], ["x", 0], [0, 1], [0, 0]]]})
        with self.assertRaisesRegex(ValueError, "Polygon or MultiPolygon"):
            validate_geojson({"type": "Point", "coordinates": [0, 0]})

    def test_detects_partial_polygon_overlap(self) -> None:
        overlapping = {"type": "Polygon", "coordinates": [[[3, 3], [5, 3], [5, 5], [3, 5], [3, 3]]]}
        separate = {"type": "Polygon", "coordinates": [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]]}
        self.assertTrue(geometries_intersect(SQUARE, overlapping))
        self.assertFalse(geometries_intersect(SQUARE, separate))

    def test_coordinates_require_exact_match_and_missing_coordinates_use_council_overlap(self) -> None:
        self.assertEqual("exact", location_match_quality(2, 2, [SQUARE]))
        self.assertIsNone(location_match_quality(8, 8, [SQUARE]))
        self.assertEqual("council_overlap", location_match_quality(None, None, [SQUARE]))


class CatalogueTests(unittest.TestCase):
    def test_selects_every_authority_intersecting_any_uploaded_geometry(self) -> None:
        councils = (
            Council("alpha", "Alpha", "England", "idox", "Idox", "https://alpha.test", None, "https://alpha.test/search", SQUARE),
            Council("beta", "Beta", "Wales", "custom", "Custom", "https://beta.test", None, "https://beta.test/search", {"type": "Polygon", "coordinates": [[[9, 9], [12, 9], [12, 12], [9, 12], [9, 9]]]}),
        )
        catalogue = AuthorityCatalogue(councils)
        user = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": [SQUARE["coordinates"]]}}]}

        self.assertEqual(["alpha"], [item.code for item in catalogue.select(user)])
        self.assertEqual([], catalogue.select({"type": "Polygon", "coordinates": [[[20, 20], [21, 20], [21, 21], [20, 21], [20, 20]]]}))


if __name__ == "__main__":
    unittest.main()
