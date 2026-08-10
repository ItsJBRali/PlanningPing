from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.backend.adapter_models import DiscoveryResult, PlanningApplication as AdapterApplication, PlanningDocument
from planning_ping.backend.adapters.base import PortalSearchCompletenessError
from planning_ping.backend.adapters.arcus import ArcusCouncilConfig, ArcusPlanningScraper
from planning_ping.backend.adapters.idox import IdoxPublicAccessScraper
from planning_ping.backend.adapters.wiltshire import WiltshireCouncilConfig, WiltshirePlanningScraper
from planning_ping.backend.http import FetchResponse
from planning_ping.backend.http import CouncilHttpClient
from planning_ping.backend.filtering import application_matches_request
from planning_ping.backend.geometry import location_match_quality
from planning_ping.backend.models import Council
from planning_ping.backend.production import ProductionAuthoritySearcher, UnsupportedPortalError, scraper_for_council


BOUNDARY = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}


def council(family: str = "idox", scraper_type: str = "Idox") -> Council:
    return Council("alpha", "Alpha Council", "England", family, scraper_type, "https://alpha.test", None, "https://alpha.test/search", BOUNDARY, {"authority": "Alpha"})


class FakeScraper:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, str | None, bool]] = []

    def discover_ids(self, **kwargs: object) -> DiscoveryResult:
        return DiscoveryResult(
            authority="Alpha",
            source_url="https://alpha.test/search",
            applications=[AdapterApplication("Alpha", "UID1", "https://alpha.test/UID1", reference="24/A")],
        )

    def fetch_application(self, uid: str, url: str | None = None, *, include_documents: bool = False) -> AdapterApplication:
        self.fetches.append((uid, url, include_documents))
        return AdapterApplication(
            authority="Alpha",
            uid=uid,
            url=url or "",
            reference="24/A",
            address="1 High Street EX1 1AA",
            description="Rear extension",
            status="Pending",
            decision="Undecided",
            date_received="2026-01-05",
            applicant_name="A Applicant",
            agent_name="An Agent",
            case_officer="C Officer",
            ward="Central",
            parish="Alpha",
            postcode="EX1 1AA",
            documents=[PlanningDocument("Plan", "https://alpha.test/document/1", "Drawing", "2026-01-06", "10 KB")],
            raw={"longitude": 1.0, "latitude": 1.0},
        )

    def discovery_is_detail_complete(self, application: AdapterApplication) -> bool:
        return False

    def close(self) -> None:
        pass


class DetailCompleteDiscoveryScraper(FakeScraper):
    def discover_ids(self, **kwargs: object) -> DiscoveryResult:
        return DiscoveryResult(
            authority="Alpha",
            source_url="https://alpha.test/search",
            applications=[
                AdapterApplication(
                    "Alpha",
                    "UID1",
                    "https://alpha.test/UID1",
                    reference="24/A",
                    address="Greenwich Meridian EX1 1AA",
                    description="Rear extension",
                    date_received="2026-01-05",
                    documents=[
                        PlanningDocument(
                            "Plan",
                            "https://alpha.test/document/1",
                            "Drawing",
                            "2026-01-06",
                            "10 KB",
                        )
                    ],
                    raw={
                        "detail_complete": True,
                        "date_range_filtered": True,
                        "longitude": 0.0,
                        "latitude": 0.0,
                        "location_x": 9.0,
                        "location_y": 9.0,
                    },
                )
            ],
        )

    def fetch_application(self, uid: str, url: str | None = None, *, include_documents: bool = False) -> AdapterApplication:
        raise AssertionError("detail-complete discovery must not fetch an unavailable detail endpoint")

    def discovery_is_detail_complete(self, application: AdapterApplication) -> bool:
        return (
            application.raw.get("detail_complete") is True
            and application.raw.get("date_range_filtered") is True
        )


class FakePlanItHttp:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads
        self.calls = 0

    def get(self, url: str, params: dict[str, str] | None = None) -> FetchResponse:
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return FetchResponse(url=url, status_code=200, text=json.dumps(payload))


class ProductionAuthoritySearchTests(unittest.TestCase):
    def test_detail_fetch_preserves_kensington_discovery_coordinates_and_documents(self) -> None:
        discovery_document = PlanningDocument("Map", "https://alpha.test/map")

        class KensingtonLikeScraper(FakeScraper):
            def discover_ids(self, **kwargs: object) -> DiscoveryResult:
                return DiscoveryResult(
                    "Alpha",
                    "https://alpha.test/search",
                    [AdapterApplication(
                        "Alpha", "UID1", "https://alpha.test/UID1", reference="24/A",
                        address="Discovery address", documents=[discovery_document],
                        raw={"record": {"longitude": 8.0, "latitude": 8.0, "uprn": "1"}, "docs_url": "https://alpha.test/docs"},
                    )],
                )

            def fetch_application(self, uid: str, url: str | None = None, *, include_documents: bool = False) -> AdapterApplication:
                return AdapterApplication(
                    "Alpha", uid, url or "", reference="24/A", address="Detail address",
                    description="Detail description", date_received="2026-01-05",
                    raw={"record": {"current_stage": "Pending"}},
                )

        application = ProductionAuthoritySearcher(scraper_factory=lambda _: KensingtonLikeScraper()).search_primary(
            council(), date(2026, 1, 1), date(2026, 1, 31), Event()
        ).applications[0]
        self.assertEqual("Detail address", application.address)
        self.assertEqual((8.0, 8.0), (application.longitude, application.latitude))
        self.assertEqual("1", application.raw["record"]["uprn"])
        self.assertEqual("https://alpha.test/map", application.documents[0].document_url)
        self.assertIsNone(location_match_quality(application.longitude, application.latitude, [BOUNDARY]))

    def test_dispatches_supported_family_and_never_routes_unknown_generically(self) -> None:
        self.assertIsInstance(scraper_for_council(council()), IdoxPublicAccessScraper)
        with self.assertRaisesRegex(UnsupportedPortalError, "unknown"):
            scraper_for_council(council("unknown", "Custom"))

    def test_primary_search_fetches_details_and_document_metadata_without_payload_downloads(self) -> None:
        scraper = FakeScraper()
        searcher = ProductionAuthoritySearcher(scraper_factory=lambda _: scraper)

        result = searcher.search_primary(council(), date(2026, 1, 1), date(2026, 1, 31), Event())

        self.assertEqual([("UID1", "https://alpha.test/UID1", True)], scraper.fetches)
        application = result.applications[0]
        self.assertEqual("24/A", application.reference)
        self.assertEqual(date(2026, 1, 5), application.received_date)
        self.assertEqual("https://alpha.test/document/1", application.documents[0].document_url)
        self.assertEqual(1.0, application.longitude)

    def test_primary_search_accepts_explicitly_detail_complete_discovery_records(self) -> None:
        searcher = ProductionAuthoritySearcher(scraper_factory=lambda _: DetailCompleteDiscoveryScraper())

        result = searcher.search_primary(council(), date(2026, 1, 1), date(2026, 1, 31), Event())

        application = result.applications[0]
        self.assertEqual("24/A", application.reference)
        self.assertEqual(date(2026, 1, 5), application.received_date)
        self.assertEqual("https://alpha.test/document/1", application.documents[0].document_url)
        self.assertEqual((0.0, 0.0), (application.longitude, application.latitude))
        self.assertEqual("exact", location_match_quality(application.longitude, application.latitude, [BOUNDARY]))

    def test_arcus_and_wiltshire_nonempty_discovery_do_not_call_unavailable_detail_endpoints(self) -> None:
        discovered = AdapterApplication(
            "Alpha",
            "UID1",
            "https://alpha.test/UID1",
            reference="24/A",
            description="Rear extension",
            address="1 High Street EX1 1AA",
            date_received="2026-01-05",
            raw={"detail_complete": True, "date_range_filtered": True},
        )

        class NonemptyArcus(ArcusPlanningScraper):
            def discover_ids(self, **kwargs: object) -> DiscoveryResult:
                return DiscoveryResult("Alpha", "https://alpha.test/search", [discovered])

        class NonemptyWiltshire(WiltshirePlanningScraper):
            def discover_ids(self, **kwargs: object) -> DiscoveryResult:
                return DiscoveryResult("Alpha", "https://alpha.test/search", [discovered])

        scrapers = (
            NonemptyArcus(ArcusCouncilConfig("Alpha", "https://alpha.test")),
            NonemptyWiltshire(WiltshireCouncilConfig("Alpha", "https://alpha.test")),
        )
        for scraper in scrapers:
            with self.subTest(scraper=type(scraper).__name__):
                result = ProductionAuthoritySearcher(scraper_factory=lambda _, value=scraper: value).search_primary(
                    council(), date(2026, 1, 1), date(2026, 1, 31), Event()
                )
                self.assertEqual(["24/A"], [application.reference for application in result.applications])

    def test_primary_search_does_not_silently_use_incomplete_discovery_after_detail_failure(self) -> None:
        class BrokenDetailScraper(FakeScraper):
            def fetch_application(self, uid: str, url: str | None = None, *, include_documents: bool = False) -> AdapterApplication:
                raise ValueError("detail retrieval failed")

        with self.assertRaisesRegex(ValueError, "detail retrieval failed"):
            ProductionAuthoritySearcher(scraper_factory=lambda _: BrokenDetailScraper()).search_primary(
                council(), date(2026, 1, 1), date(2026, 1, 31), Event()
            )

    def test_primary_search_rejects_details_outside_requested_date_range(self) -> None:
        class IgnoredDateRangeScraper(FakeScraper):
            def fetch_application(self, uid: str, url: str | None = None, *, include_documents: bool = False) -> AdapterApplication:
                application = super().fetch_application(uid, url, include_documents=include_documents)
                application.date_received = "2025-12-31"
                return application

        with self.assertRaisesRegex(PortalSearchCompletenessError, "ignored"):
            ProductionAuthoritySearcher(scraper_factory=lambda _: IgnoredDateRangeScraper()).search_primary(
                council(), date(2026, 1, 1), date(2026, 1, 31), Event()
            )

    def test_production_assigns_an_adaptive_platform_key_to_unkeyed_http_clients(self) -> None:
        scraper = FakeScraper()
        scraper.http = CouncilHttpClient(min_delay_seconds=0)

        ProductionAuthoritySearcher(scraper_factory=lambda _: scraper).search_primary(
            council(), date(2026, 1, 1), date(2026, 1, 31), Event()
        )

        self.assertEqual("portal:idox", scraper.http.concurrency_key)

    def test_inferred_request_dates_are_rejected_and_undated_records_do_not_match(self) -> None:
        inferred = AdapterApplication(
            "Alpha",
            "UID1",
            "https://alpha.test/UID1",
            reference="24/A",
            description="Rear extension",
            address="1 High Street EX1 1AA",
            date_received="2026-01-01",
            raw={
                "detail_complete": True,
                "date_range_filtered": True,
                "date_inferred_from_search_window": True,
            },
        )

        class InferredDiscoveryScraper(DetailCompleteDiscoveryScraper):
            def discover_ids(self, **kwargs: object) -> DiscoveryResult:
                return DiscoveryResult("Alpha", "https://alpha.test/search", [inferred])

        with self.assertRaisesRegex(PortalSearchCompletenessError, "inferred"):
            ProductionAuthoritySearcher(scraper_factory=lambda _: InferredDiscoveryScraper()).search_primary(
                council(), date(2026, 1, 1), date(2026, 1, 31), Event()
            )

        undated = AdapterApplication(
            "Alpha",
            "UID2",
            "https://alpha.test/UID2",
            reference="24/B",
            description="Rear extension",
            address="1 High Street EX1 1AA",
            raw={"detail_complete": True, "date_range_filtered": True},
        )

        class UndatedDiscoveryScraper(DetailCompleteDiscoveryScraper):
            def discover_ids(self, **kwargs: object) -> DiscoveryResult:
                return DiscoveryResult("Alpha", "https://alpha.test/search", [undated])

        result = ProductionAuthoritySearcher(scraper_factory=lambda _: UndatedDiscoveryScraper()).search_primary(
            council(), date(2026, 1, 1), date(2026, 1, 31), Event()
        )
        self.assertIsNone(result.applications[0].application_date)
        self.assertFalse(
            application_matches_request(result.applications[0], date(2026, 1, 1), date(2026, 1, 31), ())
        )

    def test_planit_rejects_repeated_pages_and_reported_total_mismatches(self) -> None:
        repeated = {"total": 2, "records": [{"uid": "24/A", "name": "24/A", "start_date": "2026-01-05"}]}
        searcher = ProductionAuthoritySearcher(planit_http=FakePlanItHttp([repeated, repeated]), planit_page_size=1)
        with self.assertRaisesRegex(RuntimeError, "repeated"):
            searcher.search_planit(council(), date(2026, 1, 1), date(2026, 1, 31), Event())

        incomplete = ProductionAuthoritySearcher(
            planit_http=FakePlanItHttp([{"total": 2, "records": [{"uid": "24/A", "name": "24/A"}]}, {"total": 2, "records": []}]),
            planit_page_size=1,
        )
        with self.assertRaisesRegex(RuntimeError, "advertised total"):
            incomplete.search_planit(council(), date(2026, 1, 1), date(2026, 1, 31), Event())

    def test_planit_honors_cancellation_before_request(self) -> None:
        cancel = Event()
        cancel.set()
        http = FakePlanItHttp([{"total": 0, "records": []}])
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            ProductionAuthoritySearcher(planit_http=http).search_planit(council(), date(2026, 1, 1), date(2026, 1, 31), cancel)
        self.assertEqual(0, http.calls)

    def test_planit_rejects_ignored_dates_and_page_caps(self) -> None:
        ignored = ProductionAuthoritySearcher(
            planit_http=FakePlanItHttp([{"total": 1, "records": [{"uid": "24/A", "start_date": "2025-12-01"}]}])
        )
        with self.assertRaisesRegex(RuntimeError, "ignored"):
            ignored.search_planit(council(), date(2026, 1, 1), date(2026, 1, 31), Event())

        capped = ProductionAuthoritySearcher(
            planit_http=FakePlanItHttp([{"total": 2, "records": [{"uid": "24/A", "start_date": "2026-01-01"}]}]),
            planit_page_size=1,
            planit_max_pages=1,
        )
        with self.assertRaisesRegex(RuntimeError, "page cap"):
            capped.search_planit(council(), date(2026, 1, 1), date(2026, 1, 31), Event())


if __name__ == "__main__":
    unittest.main()
