"""Application service composition root."""

from __future__ import annotations

from pathlib import Path

from planning_ping.contracts import AppServices

from .catalogue import AuthorityCatalogue
from .orchestration import PlanningSearchService
from .persistence import PlanningDatabase
from .production import ProductionAuthoritySearcher
from .queries import SqliteApplicationQueryService, SqliteIssueQueryService


def create_services(database_path: str | Path | None = None) -> AppServices:
    database = PlanningDatabase(database_path)
    return AppServices(
        search=PlanningSearchService(database, AuthorityCatalogue.load(), ProductionAuthoritySearcher()),
        applications=SqliteApplicationQueryService(database),
        issues=SqliteIssueQueryService(database),
    )
