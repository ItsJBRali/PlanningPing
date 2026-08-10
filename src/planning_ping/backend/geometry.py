"""Small dependency-free GeoJSON validation and geometry operations."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Iterator

Geometry = dict[str, Any]
Point = tuple[float, float]
Ring = list[list[float]]
Polygon = list[Ring]


def load_geojson(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read valid GeoJSON: {exc}") from exc
    validate_geojson(value)
    return value


def validate_geojson(value: object) -> list[Geometry]:
    if not isinstance(value, dict):
        raise ValueError("GeoJSON must be an object")
    geometries = list(iter_geometries(value))
    if not geometries:
        raise ValueError("GeoJSON must contain a Polygon or MultiPolygon geometry")
    for geometry in geometries:
        _validate_geometry(geometry)
    return geometries


def iter_geometries(value: dict[str, Any]) -> Iterator[Geometry]:
    kind = value.get("type")
    if kind in {"Polygon", "MultiPolygon"}:
        yield value
    elif kind == "Feature":
        geometry = value.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError("GeoJSON Feature must contain a geometry object")
        yield from iter_geometries(geometry)
    elif kind == "FeatureCollection":
        features = value.get("features")
        if not isinstance(features, list):
            raise ValueError("GeoJSON FeatureCollection features must be a list")
        for feature in features:
            if not isinstance(feature, dict):
                raise ValueError("GeoJSON features must be objects")
            yield from iter_geometries(feature)
    elif kind == "GeometryCollection":
        geometries = value.get("geometries")
        if not isinstance(geometries, list):
            raise ValueError("GeoJSON GeometryCollection geometries must be a list")
        for geometry in geometries:
            if not isinstance(geometry, dict):
                raise ValueError("GeoJSON geometries must be objects")
            yield from iter_geometries(geometry)
    else:
        raise ValueError("GeoJSON geometry type must be Polygon or MultiPolygon")


def _validate_geometry(geometry: Geometry) -> None:
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("GeoJSON coordinates must be a non-empty list")
    polygons = coordinates if geometry["type"] == "MultiPolygon" else [coordinates]
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("GeoJSON Polygon must contain at least one ring")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4:
                raise ValueError("GeoJSON polygon rings must contain at least four positions")
            for point in ring:
                if not isinstance(point, list) or len(point) < 2:
                    raise ValueError("GeoJSON positions must contain longitude and latitude")
                if any(type(number) not in (int, float) or not math.isfinite(number) for number in point[:2]):
                    raise ValueError("GeoJSON coordinates must be finite numeric values")


def geometry_polygons(geometry: Geometry) -> list[Polygon]:
    validate_geojson(geometry)
    coordinates = geometry["coordinates"]
    return coordinates if geometry["type"] == "MultiPolygon" else [coordinates]


def point_in_geometry(point: Point, geometry: Geometry) -> bool:
    return any(point_in_polygon(point, polygon) for polygon in geometry_polygons(geometry))


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    return bool(polygon) and point_in_ring(point, polygon[0]) and not any(
        point_in_ring(point, hole) for hole in polygon[1:]
    )


def point_in_ring(point: Point, ring: Ring) -> bool:
    if len(ring) < 3:
        return False
    x, y = point
    inside = False
    previous = ring[-1]
    for current in ring:
        if _point_on_segment(point, previous, current):
            return True
        x1, y1 = previous[:2]
        x2, y2 = current[:2]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def geometries_intersect(left: Geometry, right: Geometry) -> bool:
    for left_polygon in geometry_polygons(left):
        for right_polygon in geometry_polygons(right):
            if _polygons_intersect(left_polygon, right_polygon):
                return True
    return False


def _polygons_intersect(left: Polygon, right: Polygon) -> bool:
    if not _bboxes_intersect(_polygon_bbox(left), _polygon_bbox(right)):
        return False
    if any(point_in_polygon((p[0], p[1]), right) for p in left[0]):
        return True
    if any(point_in_polygon((p[0], p[1]), left) for p in right[0]):
        return True
    return any(
        _segments_intersect(a1, a2, b1, b2)
        for a1, a2 in _ring_edges(left[0])
        for b1, b2 in _ring_edges(right[0])
    )


def location_match_quality(
    longitude: float | None,
    latitude: float | None,
    uploaded_geometries: Iterable[Geometry],
) -> str | None:
    if longitude is None or latitude is None:
        return "council_overlap"
    point = (longitude, latitude)
    return "exact" if any(point_in_geometry(point, geometry) for geometry in uploaded_geometries) else None


def _ring_edges(ring: Ring) -> Iterator[tuple[list[float], list[float]]]:
    for index, point in enumerate(ring):
        yield point, ring[(index + 1) % len(ring)]


def _point_on_segment(point: Point, start: list[float], end: list[float]) -> bool:
    px, py = point
    sx, sy = start[:2]
    ex, ey = end[:2]
    cross = (py - sy) * (ex - sx) - (px - sx) * (ey - sy)
    return abs(cross) <= 1e-12 and min(sx, ex) <= px <= max(sx, ex) and min(sy, ey) <= py <= max(sy, ey)


def _segments_intersect(a1: list[float], a2: list[float], b1: list[float], b2: list[float]) -> bool:
    def orientation(p: list[float], q: list[float], r: list[float]) -> float:
        return (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])

    values = (orientation(a1, a2, b1), orientation(a1, a2, b2), orientation(b1, b2, a1), orientation(b1, b2, a2))
    if (values[0] > 0) != (values[1] > 0) and (values[2] > 0) != (values[3] > 0):
        return True
    return any(
        abs(value) <= 1e-12 and _point_on_segment((point[0], point[1]), start, end)
        for value, point, start, end in (
            (values[0], b1, a1, a2),
            (values[1], b2, a1, a2),
            (values[2], a1, b1, b2),
            (values[3], a2, b1, b2),
        )
    )


def _polygon_bbox(polygon: Polygon) -> tuple[float, float, float, float]:
    points = [point for ring in polygon for point in ring]
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def _bboxes_intersect(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    return not (left[2] < right[0] or right[2] < left[0] or left[3] < right[1] or right[3] < left[1])
