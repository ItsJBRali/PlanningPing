"""Packaged planning-authority catalogue access and spatial selection."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from .geometry import geometries_intersect, validate_geojson
from .models import Council


_CURRENT_PORTAL_OVERRIDES: dict[str, dict[str, str]] = {
    "E07000219": {
        "portal_family": "tascomi",
        "scraper_type": "Tascomi",
        "base_url": "https://idoxcloud.nuneatonandbedworth.gov.uk",
        "listing_url": "https://idoxcloud.nuneatonandbedworth.gov.uk/planning/index.html?fa=search",
        "planning_url": "https://idoxcloud.nuneatonandbedworth.gov.uk/planning/index.html?fa=search",
    },
}


def stable_council_code(properties: dict[str, Any]) -> str:
    official = str(properties.get("gss_code") or "").strip()
    if official:
        return official.casefold()
    name = str(properties.get("authority") or properties.get("council_name") or "").strip().casefold()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def country_from_properties(properties: dict[str, Any]) -> str:
    country = str(properties.get("country") or "").strip()
    if country:
        return country
    area_type = str(properties.get("area_type") or "").casefold()
    if "welsh" in area_type or "wales" in area_type:
        return "Wales"
    if "scottish" in area_type or "scotland" in area_type:
        return "Scotland"
    return "England"


class AuthorityCatalogue:
    def __init__(self, councils: Iterable[Council]) -> None:
        self.councils = tuple(councils)

    @classmethod
    def load(cls, path: Path | None = None) -> "AuthorityCatalogue":
        if path is None:
            data = resources.files("planning_ping.data").joinpath("planning_authorities.geojson")
            payload = json.loads(data.read_text(encoding="utf-8"))
        else:
            payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return cls.from_geojson(payload)

    @classmethod
    def from_geojson(cls, payload: dict[str, Any]) -> "AuthorityCatalogue":
        if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
            raise ValueError("Authority catalogue must be a GeoJSON FeatureCollection")
        councils: list[Council] = []
        for feature in payload["features"]:
            if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
                raise ValueError("Authority catalogue features require properties")
            properties = dict(feature["properties"])
            properties.update(_CURRENT_PORTAL_OVERRIDES.get(str(properties.get("gss_code") or ""), {}))
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict):
                raise ValueError("Authority catalogue features require boundaries")
            validate_geojson(geometry)
            councils.append(
                Council(
                    code=stable_council_code(properties),
                    name=str(properties.get("council_name") or properties.get("authority") or "").strip(),
                    country=country_from_properties(properties),
                    portal_family=str(properties.get("portal_family") or "unknown").strip().casefold(),
                    scraper_type=str(properties.get("scraper_type") or "").strip(),
                    base_url=str(properties.get("base_url") or "").strip(),
                    listing_url=str(properties.get("listing_url") or "").strip() or None,
                    planning_url=str(properties.get("planning_url") or properties.get("listing_url") or "").strip(),
                    boundary=geometry,
                    metadata=dict(properties),
                )
            )
        return cls(councils)

    def select(self, uploaded_geojson: dict[str, Any]) -> list[Council]:
        uploaded = validate_geojson(uploaded_geojson)
        return [
            council
            for council in self.councils
            if any(geometries_intersect(council.boundary, geometry) for geometry in uploaded)
        ]
