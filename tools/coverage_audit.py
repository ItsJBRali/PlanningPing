"""Validate authority coverage; optionally perform a bounded live reachability smoke."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from planning_ping.backend.audit import AuditReport, audit_catalogue
from planning_ping.backend.catalogue import AuthorityCatalogue
from planning_ping.backend.http import CouncilHttpClient
from planning_ping.backend.production import SUPPORTED_PORTAL_FAMILIES, scraper_for_council


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, help="Alternative authority catalogue")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--report", type=Path, default=Path("coverage-audit.json"))
    parser.add_argument("--live-smoke", action="store_true", help="Opt in to bounded endpoint requests")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--limit", type=int, default=10, help="Maximum live endpoints (default 10)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    catalogue = AuthorityCatalogue.load(arguments.catalogue)
    report = audit_catalogue(
        catalogue.councils,
        supported_families=SUPPORTED_PORTAL_FAMILIES,
        adapter_check=lambda council: scraper_for_council(council).close(),
    )
    if arguments.live_smoke:
        report = live_smoke(
            report,
            timeout_seconds=max(0.1, arguments.timeout),
            limit=max(1, arguments.limit),
        )
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    content = report.to_csv() if arguments.format == "csv" else report.to_json()
    arguments.report.write_text(content, encoding="utf-8", newline="")
    return 0 if report.ok else 1


def live_smoke(report: AuditReport, *, timeout_seconds: float, limit: int) -> AuditReport:
    """Perform explicit, bounded GET reachability checks without bypassing blocks."""

    client = CouncilHttpClient(timeout_seconds=timeout_seconds, min_delay_seconds=0.25, retries=0)
    updated = []
    contacted = 0
    for row in report.rows:
        if row.result != "pass":
            updated.append(row)
            continue
        if contacted >= limit:
            updated.append(replace(row, result="not_checked", error="live reachability not checked (bounded limit)"))
            continue
        contacted += 1
        try:
            response = client.get(row.endpoint)
            updated.append(replace(row, result="pass", error=None))
            del response
        except Exception as exc:
            updated.append(replace(row, result="fail", error=f"live reachability failed: {type(exc).__name__}: {exc}"))
    return AuditReport(tuple(updated))


if __name__ == "__main__":
    raise SystemExit(main())
