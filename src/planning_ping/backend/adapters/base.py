from __future__ import annotations

from abc import ABC, abstractmethod

from planning_ping.backend.adapter_models import DiscoveryResult, PlanningApplication


class PortalSearchCompletenessError(RuntimeError):
    """A portal response could not prove that the requested result set is complete."""


class PlanningScraper(ABC):
    def __init__(self, authority: str) -> None:
        self.authority = authority

    @abstractmethod
    def discover_ids(self, **kwargs: object) -> DiscoveryResult:
        """Return application identifiers from a council listing/search page."""

    @abstractmethod
    def fetch_application(
        self,
        uid: str,
        url: str | None = None,
        *,
        include_documents: bool = False,
    ) -> PlanningApplication:
        """Fetch and normalize one application detail page."""

    def discovery_is_detail_complete(self, application: PlanningApplication) -> bool:
        """Return true only for adapters whose search endpoint is their complete record boundary."""

        return False

    def close(self) -> None:
        client = getattr(self, "http", None)
        close = getattr(client, "close", None)
        if callable(close):
            close()
