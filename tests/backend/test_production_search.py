from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.backend.adapter_models import DiscoveryResult, PlanningApplication as AdapterApplication, PlanningDocument
from planning_ping.backend.adapters.idox import IdoxPublicAccessScraper
from planning_ping.backend.http import FetchResponse
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

    def close(self) -> None:
        pass


class FakePlanItHttp:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads
        self.calls = 0

    def get(self, url: str, params: dict[str, str] | None = None) -> FetchResponse:
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return FetchResponse(url=url, status_code=200, text=json.dumps(payload))


class ProductionAuthoritySearchTests(unittest.TestCase):
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
