"""Production authority adapter dispatch and serial PlanIt reconciliation."""

from __future__ import annotations

import json
from datetime import date, datetime
from threading import Event
from typing import Callable
from urllib.parse import urlsplit

from .adapter_models import PlanningApplication as AdapterApplication
from .adapters import (
    AchieveFormsCouncilConfig,
    AchieveFormsPlanningScraper,
    AgileCouncilConfig,
    AgilePlanningScraper,
    AppSearchServPlanningScraper,
    ArcusCouncilConfig,
    ArcusPlanningScraper,
    AstunPlanningScraper,
    AtriumCouncilConfig,
    AtriumPlanningScraper,
    CcedPlanningScraper,
    CivicaCouncilConfig,
    CivicaPlanningScraper,
    EnterpriseStorePlanningScraper,
    FastwebPlanningScraper,
    HtmlListPlanningScraper,
    IdoxCouncilConfig,
    IdoxPublicAccessScraper,
    LegacyFormsCouncilConfig,
    NorthLincsPlanningScraper,
    NorthgateCouncilConfig,
    NorthgatePlanningScraper,
    OcellaCouncilConfig,
    OcellaPlanningScraper,
    QueryFormPlanningScraper,
    SocrataPlanningScraper,
    StatMapPlanningScraper,
    TascomiPlanningScraper,
    WiltshireCouncilConfig,
    WiltshirePlanningScraper,
    authority_specific_scraper,
)
from .adapters.base import PlanningScraper
from .adapters.base import PortalSearchCompletenessError
from .http import CouncilHttpClient, monitor_council_requests
from .models import ApplicationDocument, Council, PlanningApplication
from .orchestration import AuthoritySearchResult
from .parsing import parse_council_date


class UnsupportedPortalError(RuntimeError):
    """Raised instead of silently routing an unknown portal to a generic parser."""


SUPPORTED_PORTAL_FAMILIES: frozenset[str] = frozenset(
    {
        "achieveforms",
        "agile",
        "appsearchserv",
        "arcus",
        "astun",
        "atrium",
        "cced",
        "civica",
        "custom",
        "enterprisestore",
        "fastweb",
        "idox",
        "northgate",
        "ocella",
        "tascomi",
    }
)


def scraper_for_council(council: Council) -> PlanningScraper:
    authority = str(council.metadata.get("authority") or council.name)
    base_url = council.base_url or _origin(council.planning_url)
    family = council.portal_family.casefold()
    scraper_type = council.scraper_type.casefold()
    portal_key = f"{family} {scraper_type}"
    listing_key = (council.listing_url or council.planning_url).casefold()
    authority_key = authority.casefold()

    if authority_key == "wiltshire":
        return WiltshirePlanningScraper(WiltshireCouncilConfig(authority=authority, base_url=base_url))
    specific = authority_specific_scraper(authority, base_url)
    if specific is not None:
        return specific
    if "arcus" in portal_key:
        return ArcusPlanningScraper(ArcusCouncilConfig(authority=authority, base_url=base_url))
    if "achieveforms" in portal_key or "achieve forms" in portal_key:
        return AchieveFormsPlanningScraper(AchieveFormsCouncilConfig(authority=authority, base_url=base_url))
    if "atrium" in portal_key:
        return AtriumPlanningScraper(AtriumCouncilConfig(authority=authority, base_url=base_url))
    legacy = LegacyFormsCouncilConfig(authority=authority, base_url=base_url)
    if "tascomi" in portal_key:
        return TascomiPlanningScraper(legacy)
    if "enterprisestore" in portal_key or "enterprise store" in portal_key:
        return EnterpriseStorePlanningScraper(legacy)
    if "appsearchserv" in portal_key:
        return AppSearchServPlanningScraper(legacy)
    if "fastweb" in portal_key:
        return FastwebPlanningScraper(legacy)
    if "cced" in portal_key:
        return CcedPlanningScraper(legacy)
    if "astun" in portal_key or "advancedsearchtab.tmplt" in listing_key:
        return AstunPlanningScraper(legacy)
    if "socrata" in portal_key or "opendata.camden.gov.uk" in listing_key:
        return SocrataPlanningScraper(legacy)
    if "statmap" in portal_key or "horizonext" in listing_key:
        return StatMapPlanningScraper(legacy)
    if authority_key == "north lincs":
        return NorthLincsPlanningScraper(legacy)
    if authority_key == "peak district":
        return EnterpriseStorePlanningScraper(
            LegacyFormsCouncilConfig(authority=authority, base_url="https://planning.peakdistrict.gov.uk")
        )
    if authority_key in {"copeland", "scilly isles", "south derbyshire", "amber valley", "stratford on avon", "cumberland"}:
        return HtmlListPlanningScraper(legacy)
    if authority_key in {"east sussex", "kirklees", "nottinghamshire", "preston", "tandridge", "boston", "barrow", "central bedfordshire", "hampshire", "herefordshire", "ipswich", "ribble valley", "sedgemoor", "taunton deane"}:
        return QueryFormPlanningScraper(legacy)
    if "idox" in portal_key:
        return IdoxPublicAccessScraper(
            IdoxCouncilConfig(authority=authority, base_url=base_url, application_root=_idox_application_root(council.listing_url))
        )
    if "ocella" in portal_key:
        return OcellaPlanningScraper(OcellaCouncilConfig(authority=authority, base_url=base_url))
    if "agile" in portal_key:
        return AgilePlanningScraper(AgileCouncilConfig(authority=authority, base_url=base_url))
    if "civica" in portal_key:
        return CivicaPlanningScraper(CivicaCouncilConfig(authority=authority, base_url=base_url))
    if "northgate" in portal_key or "planningexplorer" in portal_key:
        return NorthgatePlanningScraper(NorthgateCouncilConfig(authority=authority, base_url=base_url))
    raise UnsupportedPortalError(f"Unsupported {family or 'unknown'} portal for {authority}")


class ProductionAuthoritySearcher:
    def __init__(
        self,
        *,
        scraper_factory: Callable[[Council], PlanningScraper] = scraper_for_council,
        planit_http: CouncilHttpClient | None = None,
        planit_page_size: int = 100,
        planit_max_pages: int = 100,
    ) -> None:
        self._scraper_factory = scraper_factory
        self._planit_http = planit_http or CouncilHttpClient(
            timeout_seconds=20,
            min_delay_seconds=1.5,
            retries=2,
            rate_limit_key="planit",
            concurrency_key="planit",
            concurrency_limit=1,
        )
        self._planit_page_size = max(1, planit_page_size)
        self._planit_max_pages = max(1, planit_max_pages)

    def search_primary(
        self,
        council: Council,
        start_date: date,
        end_date: date,
        cancel_event: Event,
    ) -> AuthoritySearchResult:
        scraper = self._scraper_factory(council)
        client = getattr(scraper, "http", None)
        if isinstance(client, CouncilHttpClient) and not client.concurrency_key:
            platform = council.portal_family.casefold().strip()
            if platform in {"", "custom", "unknown"}:
                platform = council.scraper_type.casefold().strip() or "custom"
            client.concurrency_key = f"portal:{platform}"
        try:
            with monitor_council_requests(lambda: None, should_cancel=cancel_event.is_set):
                discovery = scraper.discover_ids(
                    listing_url=council.listing_url or council.planning_url,
                    start_date=start_date,
                    end_date=end_date,
                )
                details: list[PlanningApplication] = []
                for discovered in discovery.applications:
                    if cancel_event.is_set():
                        raise RuntimeError(f"Search cancelled while fetching details for {council.name}")
                    if discovered.raw.get("date_inferred_from_search_window") is True:
                        raise PortalSearchCompletenessError(
                            f"{council.name} returned an inferred request date instead of an application date"
                        )
                    if scraper.discovery_is_detail_complete(discovered):
                        complete = discovered
                    else:
                        complete = _merge_discovery_details(
                            discovered,
                            scraper.fetch_application(
                                discovered.uid,
                                discovered.url,
                                include_documents=True,
                            ),
                        )
                    application = _from_adapter(council, complete)
                    if (
                        application.application_date is not None
                        and not start_date <= application.application_date <= end_date
                    ):
                        raise PortalSearchCompletenessError(
                            f"{council.name} ignored the requested application date range"
                        )
                    details.append(application)
            return AuthoritySearchResult(tuple(details))
        finally:
            scraper.close()

    def search_planit(
        self,
        council: Council,
        start_date: date,
        end_date: date,
        cancel_event: Event,
    ) -> AuthoritySearchResult:
        records: list[dict[str, object]] = []
        seen_pages: set[tuple[str, ...]] = set()
        seen_references: set[str] = set()
        expected_total: int | None = None
        authority = str(council.metadata.get("authority") or council.name)
        for page in range(1, self._planit_max_pages + 1):
            if cancel_event.is_set():
                raise RuntimeError(f"PlanIt reconciliation cancelled for {council.name}")
            response = self._planit_http.get(
                "https://www.planit.org.uk/api/applics/json",
                {
                    "auth": authority,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "pg_sz": str(self._planit_page_size),
                    "page": str(page),
                },
            )
            try:
                payload = json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("PlanIt returned invalid JSON") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
                raise RuntimeError("PlanIt returned an invalid records collection")
            batch = payload["records"]
            if not all(isinstance(record, dict) for record in batch):
                raise RuntimeError("PlanIt returned invalid records")
            page_total = _planit_total(payload)
            if expected_total is None:
                expected_total = page_total
            elif page_total != expected_total:
                raise RuntimeError("PlanIt changed or omitted its advertised total")

            references = [_planit_reference(record) for record in batch]
            if any(not reference for reference in references):
                raise RuntimeError("PlanIt returned a record without a usable reference")
            signature = tuple(reference.casefold() for reference in references)
            if batch and signature in seen_pages:
                raise RuntimeError("PlanIt returned a repeated pagination page")
            if batch:
                seen_pages.add(signature)
            for record, reference in zip(batch, references):
                key = reference.casefold()
                if key not in seen_references:
                    application = _from_planit(council, record, reference)
                    if application.application_date and not start_date <= application.application_date <= end_date:
                        raise RuntimeError("PlanIt ignored the requested application dates")
                    seen_references.add(key)
                    records.append(record)
            if expected_total is not None and len(seen_references) == expected_total:
                break
            if expected_total is not None and not batch:
                raise RuntimeError("PlanIt pagination ended before the advertised total")
            if expected_total is None and len(batch) < self._planit_page_size:
                break
        else:
            raise RuntimeError("PlanIt pagination exceeded the maximum page cap")

        if expected_total is not None and len(seen_references) != expected_total:
            raise RuntimeError("PlanIt result count did not match the advertised total")
        return AuthoritySearchResult(
            tuple(_from_planit(council, record, _planit_reference(record)) for record in records)
        )


def _from_adapter(council: Council, source: AdapterApplication) -> PlanningApplication:
    raw = dict(source.raw)
    return PlanningApplication(
        council_code=council.code,
        reference=(source.reference or source.uid).strip(),
        authority=council.name,
        application_url=source.url,
        council_url=council.planning_url,
        received_date=_parse_date(source.date_received),
        validated_date=_parse_date(source.date_validated),
        description=source.description or "",
        address=source.address or "",
        postcode=source.postcode or "",
        status=source.status,
        decision=source.decision,
        applicant=source.applicant_name,
        agent=source.agent_name,
        case_officer=source.case_officer,
        ward=source.ward,
        parish=source.parish,
        longitude=_float_value(_adapter_coordinate(raw, "longitude", "location_x")),
        latitude=_float_value(_adapter_coordinate(raw, "latitude", "location_y")),
        scraped_at=_parse_datetime(source.date_scraped),
        raw=raw,
        documents=tuple(
            ApplicationDocument(
                title=document.title,
                document_url=document.url,
                document_type=document.document_type,
                date=_parse_date(document.date_published),
                size=document.file_size,
                description=document.description,
                source_url=document.source_url,
            )
            for document in source.documents
        ),
        documents_complete=source.documents_complete,
    )


def _merge_discovery_details(discovery: AdapterApplication, details: AdapterApplication) -> AdapterApplication:
    for name in (
        "authority", "uid", "url", "reference", "address", "description", "status", "decision",
        "date_received", "date_validated", "applicant_name", "agent_name", "case_officer", "ward",
        "parish", "postcode", "source_url",
    ):
        detail_value = getattr(details, name)
        if detail_value is None or isinstance(detail_value, str) and not detail_value.strip():
            setattr(details, name, getattr(discovery, name))
    details.raw = _merge_raw(discovery.raw, details.raw)
    if not details.documents:
        details.documents = list(discovery.documents)
        details.documents_complete = discovery.documents_complete
    return details


def _merge_raw(discovery: dict[str, object], details: dict[str, object]) -> dict[str, object]:
    merged = dict(discovery)
    for key, value in details.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_raw(existing, value)
        elif value not in (None, ""):
            merged[key] = value
    return merged


def _adapter_coordinate(raw: dict[str, object], primary: str, alternate: str) -> object:
    direct = _first_present(raw.get(primary), raw.get(alternate))
    if direct is not None:
        return direct
    record = raw.get("record")
    if isinstance(record, dict):
        return _first_present(record.get(primary), record.get(alternate))
    return None


def _from_planit(council: Council, record: dict[str, object], reference: str) -> PlanningApplication:
    other = record.get("other_fields") if isinstance(record.get("other_fields"), dict) else {}
    return PlanningApplication(
        council_code=council.code,
        reference=reference,
        authority=council.name,
        application_url=_string(record, "url") or _string(record, "link") or "",
        council_url=council.planning_url,
        received_date=_parse_date(_string(other, "date_received") or _string(record, "start_date")),
        validated_date=_parse_date(_string(other, "date_validated")),
        description=_string(record, "description") or "",
        address=_string(record, "address") or "",
        postcode=_string(record, "postcode") or "",
        status=_string(other, "status") or _string(record, "app_state"),
        applicant=_string(other, "applicant_name"),
        agent=_string(other, "agent_name"),
        case_officer=_string(other, "case_officer"),
        parish=_string(other, "parish"),
        longitude=_float_value(record.get("location_x")),
        latitude=_float_value(record.get("location_y")),
        raw={"source": "planit", **record},
    )


def _planit_total(payload: dict[str, object]) -> int | None:
    if "total" not in payload or payload["total"] in (None, ""):
        return None
    value = payload["total"]
    if isinstance(value, bool) or not (isinstance(value, int) or isinstance(value, str) and value.strip().isdigit()):
        raise RuntimeError("PlanIt returned an invalid advertised total")
    total = int(value)
    if total < 0:
        raise RuntimeError("PlanIt returned an invalid advertised total")
    return total


def _planit_reference(record: dict[str, object]) -> str:
    return _string(record, "uid") or _string(record, "reference") or _string(record, "name") or ""


def _string(mapping: dict[str, object], key: str) -> str | None:
    value = mapping.get(key)
    return str(value).strip() if value not in (None, "") else None


def _parse_date(value: str | None) -> date | None:
    normalized = parse_council_date(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now().astimezone()


def _float_value(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _first_present(*values: object) -> object | None:
    for value in values:
        if value is not None and (not isinstance(value, str) or value.strip()):
            return value
    return None


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _idox_application_root(listing_url: str | None) -> str | None:
    if not listing_url or "/online-applications/" not in listing_url:
        return None
    return listing_url.split("/online-applications/", 1)[0] + "/online-applications"
