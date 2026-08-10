from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.backend.adapter_models import PlanningApplication
from planning_ping.backend.adapters.agile import AgileCouncilConfig, AgilePlanningScraper
from planning_ping.backend.adapters.arcus import ArcusCouncilConfig, ArcusPlanningScraper
from planning_ping.backend.adapters.atrium import AtriumCouncilConfig, AtriumPlanningScraper
from planning_ping.backend.adapters.base import PortalSearchCompletenessError
from planning_ping.backend.adapters.idox import IdoxCouncilConfig, IdoxPublicAccessScraper
from planning_ping.backend.adapters.northgate import NorthgateCouncilConfig, NorthgatePlanningScraper
from planning_ping.backend.adapters.ocella import OcellaCouncilConfig, OcellaPlanningScraper
from planning_ping.backend.adapters.wiltshire import WiltshireCouncilConfig, WiltshirePlanningScraper
from planning_ping.backend.adapters.bespoke_portals import ColchesterPlanningScraper, TelfordPlanningScraper
from planning_ping.backend.adapters.legacy_forms import LegacyFormsCouncilConfig
from planning_ping.backend.http import BinaryFetchResponse, CouncilFetchError, CouncilHttpClient, FetchResponse, monitor_council_requests


def application(uid: str = "UID1") -> PlanningApplication:
    return PlanningApplication(
        authority="Example",
        uid=uid,
        url=f"https://planning.example.test/detail/{uid}",
        reference=f"24/{uid}",
    )


class MappingHttp:
    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = pages or {}
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> FetchResponse:
        self.calls.append(url)
        page = (parse_qs(urlsplit(url).query).get("searchCriteria.page") or [""])[0]
        return FetchResponse(url=url, status_code=200, text=self.pages.get(page, self.pages.get(url, "<html></html>")))

    def close(self) -> None:
        pass


class AdapterCompletenessTests(unittest.TestCase):
    def test_modern_agile_api_uses_shared_binary_transport_and_honours_cancellation(self) -> None:
        class BinaryHttp:
            user_agent = "test-agent"

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

            def get_bytes(self, url: str, params: dict[str, str], headers: dict[str, str]) -> BinaryFetchResponse:
                self.calls.append((url, params, headers))
                return BinaryFetchResponse(url=f"{url}?status=registered", status_code=200, body=b'{"results": []}')

        http = BinaryHttp()
        agile = AgilePlanningScraper(AgileCouncilConfig("Example", "https://planning.example.test"), http_client=http)  # type: ignore[arg-type]
        text, final_url = agile._api_get("application/search", {"status": "registered"}, "EX")
        self.assertEqual('{"results": []}', text)
        self.assertEqual("https://planningapi.agileapplications.co.uk//api/application/search", http.calls[0][0])
        self.assertEqual("registered", http.calls[0][1]["status"])
        self.assertEqual("EX", http.calls[0][2]["x-client"])
        self.assertIn("status=registered", final_url)

        class CancelledClient(CouncilHttpClient):
            def _opener(self):
                raise AssertionError("cancelled Agile request reached the network")

        cancelled = AgilePlanningScraper(
            AgileCouncilConfig("Example", "https://planning.example.test"),
            http_client=CancelledClient(min_delay_seconds=0),
        )
        with monitor_council_requests(lambda: None, should_cancel=lambda: True):
            with self.assertRaisesRegex(CouncilFetchError, "cancelled"):
                cancelled._api_get("application/search", {}, "EX")

    def test_telford_rejects_an_unproven_exact_ten_cap_but_accepts_below_cap(self) -> None:
        class TelfordHttp:
            def __init__(self, count: int) -> None:
                self.count = count

            def get(self, url: str, **_: object) -> FetchResponse:
                return FetchResponse(
                    url=url,
                    status_code=200,
                    text='<form><input name="ctl00$ContentPlaceHolder1$DCdatefrom"></form>',
                )

            def post_form(self, url: str, data: object, **_: object) -> FetchResponse:
                rows = "".join(
                    f'<tr><td><a href="PA-ApplicationSummary.aspx?id={index}">24/{index:04d}</a></td>'
                    '<td>01/01/2026</td><td>1 High Street</td><td>Extension</td></tr>'
                    for index in range(self.count)
                )
                return FetchResponse(url=url, status_code=200, text=f"<table>{rows}</table>")

        exact = TelfordPlanningScraper(
            LegacyFormsCouncilConfig("Telford", "https://planning.example.test"),
            http_client=TelfordHttp(10),  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(PortalSearchCompletenessError, "10|cap|complete"):
            exact._search_day("https://planning.example.test/search", date(2026, 1, 1))

        below = TelfordPlanningScraper(
            LegacyFormsCouncilConfig("Telford", "https://planning.example.test"),
            http_client=TelfordHttp(9),  # type: ignore[arg-type]
        )
        self.assertEqual(9, len(below._search_day("https://planning.example.test/search", date(2026, 1, 1))))
    def test_idox_raises_at_page_cap_instead_of_returning_a_prefix(self) -> None:
        first = FetchResponse(
            url="https://planning.example.test/search",
            status_code=200,
            text='<a href="?action=page&amp;searchCriteria.page=2">2</a>',
        )
        scraper = IdoxPublicAccessScraper(
            IdoxCouncilConfig("Example", "https://planning.example.test"),
            http_client=MappingHttp(),
        )
        scraper.MAX_PAGED_RESULT_PAGES = 1

        with self.assertRaisesRegex(PortalSearchCompletenessError, "maximum|cap"):
            scraper._parse_listing_pages(first)

    def test_idox_rejects_no_progress_and_reported_total_mismatch(self) -> None:
        listing = '<a href="/online-applications/applicationDetails.do?keyVal=UID1">24/0001</a>'
        first = FetchResponse(
            url="https://planning.example.test/search",
            status_code=200,
            text=listing + '<a href="?action=page&amp;searchCriteria.page=2">2</a>',
        )
        http = MappingHttp(
            {
                "2": listing + '<a href="?action=page&amp;searchCriteria.page=3">3</a>',
                "3": listing,
            }
        )
        scraper = IdoxPublicAccessScraper(
            IdoxCouncilConfig("Example", "https://planning.example.test"),
            http_client=http,
        )
        with self.assertRaisesRegex(PortalSearchCompletenessError, "progress|repeated"):
            scraper._parse_listing_pages(first)

        incomplete = FetchResponse(
            url="https://planning.example.test/search",
            status_code=200,
            text="<p>Displaying 1 - 1 of 2</p>" + listing,
        )
        with self.assertRaisesRegex(PortalSearchCompletenessError, "reported total"):
            scraper._parse_listing_pages(incomplete)

    def test_atrium_raises_when_pagination_exceeds_its_bound(self) -> None:
        page_links = "".join(
            f'<a href="/Search/ResultsPage/{number}">{number}</a>' for number in range(2, 5)
        )
        http = MappingHttp({"https://planning.example.test/search": f"<html>{page_links}</html>"})
        scraper = AtriumPlanningScraper(
            AtriumCouncilConfig("Example", "https://planning.example.test"),
            http_client=http,
        )
        scraper.MAX_PAGED_RESULT_PAGES = 2

        with self.assertRaisesRegex(PortalSearchCompletenessError, "maximum|cap"):
            scraper._fetch_listing("https://planning.example.test/search")

    def test_northgate_rejects_exhaustion_below_advertised_total(self) -> None:
        class IncompleteNorthgate(NorthgatePlanningScraper):
            def _fetch_listing(self, *args: object, **kwargs: object) -> FetchResponse:
                return FetchResponse(
                    url="https://planning.example.test/results",
                    status_code=200,
                    text="<html><body>Records 1 to 1 of 2</body></html>",
                )

            def parse_listing(self, html_text: str, page_url: str) -> list[PlanningApplication]:
                return [application()]

            def _pagination_urls(self, html_text: str, page_url: str) -> list[str]:
                return []

        scraper = IncompleteNorthgate(
            NorthgateCouncilConfig("Example", "https://planning.example.test"),
            http_client=MappingHttp(),
        )

        with self.assertRaisesRegex(PortalSearchCompletenessError, "advertised total"):
            scraper._discover_ids(listing_url="https://planning.example.test/search")

    def test_unsplittable_arcus_and_ocella_caps_are_visible(self) -> None:
        class CappedArcus(ArcusPlanningScraper):
            def _fetch_search_records(self, *args: object, **kwargs: object):
                return ([{"Id": "UID1", "Name": "24/0001"}], True)

        arcus = CappedArcus(
            ArcusCouncilConfig("Example", "https://planning.example.test"),
            http_client=MappingHttp(),
        )
        with self.assertRaisesRegex(PortalSearchCompletenessError, "threshold|complete"):
            arcus._search_records_window(
                "https://planning.example.test/search",
                {},
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 1),
            )

        class CappedOcella(OcellaPlanningScraper):
            def _post_received_date_search(self, *args: object, **kwargs: object) -> FetchResponse:
                return FetchResponse(
                    url="https://planning.example.test/results",
                    status_code=200,
                    text="<html><body>First 100 results shown, there are 150 in total</body></html>",
                )

        ocella = CappedOcella(
            OcellaCouncilConfig("Example", "https://planning.example.test"),
            http_client=MappingHttp(),
        )
        with self.assertRaisesRegex(PortalSearchCompletenessError, "cap|complete"):
            ocella._fetch_received_date_pages(
                "https://planning.example.test/search",
                {},
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 1),
            )

    def test_arcus_and_wiltshire_never_normalize_the_requested_start_as_an_application_date(self) -> None:
        class UndatedArcus(ArcusPlanningScraper):
            def _aura_context(self, html_text: str) -> dict[str, object]:
                return {}

            def _search_records(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
                return [{"Id": "UID1", "Name": "24/0001"}]

        class UndatedWiltshire(WiltshirePlanningScraper):
            def _aura_context(self, html_text: str) -> dict[str, object]:
                return {}

            def _search_records(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
                return [{"Id": "UID2", "Name": "24/0002"}]

        arcus = UndatedArcus(
            ArcusCouncilConfig("Example", "https://planning.example.test"),
            http_client=MappingHttp(),
        )
        wiltshire_scraper = UndatedWiltshire(
            WiltshireCouncilConfig("Example", "https://planning.example.test"),
            http_client=MappingHttp(),
        )
        requested_start = date(2026, 1, 1)

        arcus_application = arcus.discover_ids(
            listing_url="https://planning.example.test/s/register-view",
            start_date=requested_start,
            end_date=date(2026, 1, 31),
        ).applications[0]
        wiltshire_application = wiltshire_scraper.discover_ids(
            listing_url="https://planning.example.test/s/register-view",
            start_date=requested_start,
            end_date=date(2026, 1, 31),
        ).applications[0]

        self.assertIsNone(arcus_application.date_received)
        self.assertIsNone(wiltshire_application.date_validated)
        self.assertFalse(arcus_application.raw["date_inferred_from_search_window"])
        self.assertFalse(wiltshire_application.raw["date_inferred_from_search_window"])

    def test_agile_and_power_pages_fixed_bounds_raise_instead_of_truncating(self) -> None:
        agile_links = "".join(
            f'<a href="WPHAPPSEARCHRES.DisplayResultsUrl?StartIndex={number}">{number}</a>'
            for number in range(2, 5)
        )
        agile = AgilePlanningScraper(
            AgileCouncilConfig("Example", "https://planning.example.test"),
            http_client=MappingHttp(),
        )
        agile.MAX_PAGED_RESULT_PAGES = 2
        with self.assertRaisesRegex(PortalSearchCompletenessError, "maximum|cap"):
            agile._with_legacy_apas_pages(
                FetchResponse(
                    url="https://planning.example.test/results",
                    status_code=200,
                    text=f"<html>{agile_links}</html>",
                )
            )

        class PowerPagesHttp:
            def __init__(self) -> None:
                self.posts = 0

            def get(self, url: str, **_: object) -> FetchResponse:
                if "tokenhtml" in url:
                    text = '<input name="__RequestVerificationToken" value="token">'
                else:
                    text = '<div data-get-url="/grid" data-view-layouts="ignored"></div>'
                return FetchResponse(url=url, status_code=200, text=text)

            def post_json(self, url: str, data: object, **_: object) -> FetchResponse:
                import json

                self.posts += 1
                return FetchResponse(
                    url=url,
                    status_code=200,
                    text=json.dumps(
                        {
                            "Records": [{"Id": str(self.posts)}],
                            "MoreRecords": True,
                            "NextPagePagingCookie": str(self.posts),
                        }
                    ),
                )

            def close(self) -> None:
                pass

        class CappedPowerPages(ColchesterPlanningScraper):
            def _secure_configuration(self, encoded_layouts: str) -> str:
                return "configuration"

            def _application_from_record(self, record: dict[str, object], listing_url: str) -> PlanningApplication:
                return application(str(record["Id"]))

        colchester = CappedPowerPages(
            LegacyFormsCouncilConfig("Colchester", "https://planning.example.test"),
            http_client=PowerPagesHttp(),
        )
        colchester.MAX_PAGED_RESULT_PAGES = 2
        with self.assertRaisesRegex(PortalSearchCompletenessError, "maximum|cap"):
            colchester.search(
                "https://planning.example.test/search",
                start_date=None,
                end_date=None,
                limit=None,
            )


if __name__ == "__main__":
    unittest.main()
