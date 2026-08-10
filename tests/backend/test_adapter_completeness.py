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
from planning_ping.backend.adapters.bespoke_portals import ColchesterPlanningScraper
from planning_ping.backend.adapters.legacy_forms import LegacyFormsCouncilConfig
from planning_ping.backend.http import FetchResponse


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
