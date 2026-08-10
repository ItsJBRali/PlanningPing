# PlanningPing Task 2 Report: Backend Search and Persistence

## Status

DONE. Backend search, normalized persistence, frozen query services, authority catalogue/audit, production adapter dispatch, serial PlanIt reconciliation, packaging data, and the Windows PyInstaller specification are implemented on `search`.

The separate prerequisite contract correction was committed first as `a0fde576ee6ab141f2cf785feaa7f0917556d5d0` (`fix: align frozen service contracts`) and sent to the integration owner before Task 2 continued.

## TDD Evidence

All new behavior followed focused RED/GREEN cycles:

- Contract alignment RED: five constructor errors exposed the missing approved fields. GREEN: 13/13 contract/filter/request tests.
- Runtime literal validation RED: invalid event kind did not raise. GREEN: all seven event kinds, all three summary statuses, and both invalid branches pass.
- Domain/geometry/catalogue RED: `planning_ping.backend` did not exist. GREEN: 7/7 covering effective-date fallback, undated exclusion, description-only exclusions, normalized reference precedence, malformed coordinates/shapes, Polygon/MultiPolygon intersection, exact/overlap location, and zero overlap.
- Persistence/query RED: persistence module did not exist. GREEN: 7/7 covering default path, idempotent migration and PRAGMAs, indexes, transactional council durability, application/document upserts, first/last seen history, escaped parameterized filters, every allowed sort, pagination, and issues.
- Orchestration RED: orchestration module did not exist. GREEN: 4/4 covering primary-before-PlanIt sequencing, primary precedence, warnings/errors/empty data, emitted events, zero-council success, primary-phase cancellation, and PlanIt-phase cancellation. The latter test first reproduced a wrong `warning` outcome before the cancellation branch was corrected.
- Ported-adapter characterization RED: the PlanningPing adapter namespace did not exist. GREEN: 3/3 proving all primary adapter families import under `planning_ping`, inherited Idox fixture behavior is retained, and shared parsing is unchanged.
- Production boundary RED: production search module did not exist. GREEN: 5/5 covering explicit dispatch, refusal to route `unknown` generically, detail/document-metadata discovery, cancellation, repeated pages, total mismatch, ignored dates, and page caps.
- Audit/factory RED: audit module did not exist. GREEN: 3/3 covering invalid catalogue diagnostics, all packaged mappings, and the service factory.
- Audit CLI RED: `tools.coverage_audit` did not exist. GREEN: offline JSON and CSV reports both succeed without network access.
- Retry/throttle characterization: bounded empty-response retry, WAF rejection, pre-request cancellation, one active request per host, and adaptive platform throttling pass.

Fresh final verification:

- `uv --system-certs run --link-mode copy --python 3.11 python -m unittest discover -s tests -v`: 48 tests, 0 failures.
- `uv --system-certs run --link-mode copy --python 3.11 python -m compileall -q src tools`: exit 0.
- Offline `tools/coverage_audit.py --format json`: exit 0.
- Import smoke: backend factory importable; packaged catalogue loads.

## Implementation and Files

### Domain, geometry, filtering, and catalogue

- `src/planning_ping/backend/models.py`
- `src/planning_ping/backend/filtering.py`
- `src/planning_ping/backend/geometry.py`
- `src/planning_ping/backend/catalogue.py`
- `src/planning_ping/data/planning_authorities.geojson`

The inherited 399-authority catalogue is packaged. Stable codes prefer GSS codes, countries derive from explicit data/area type, and inherited `unknown` families were converted to explicit `custom` authority-specific mappings. Geometry is dependency-free and validates GeoJSON structures and finite numeric positions before selection.

### Persistence and frozen query services

- `src/planning_ping/backend/persistence.py`
- `src/planning_ping/backend/queries.py`

Schema version 1 creates all six required tables, foreign keys, WAL and busy-timeout settings, unique identities, outcome constraints, and filter/order indexes. The shared connection is guarded for UI/query and worker-thread use. Each council save uses its own immediate transaction. Query ordering is allowlisted and all filter values are escaped parameters.

### Search orchestration and production search

- `src/planning_ping/backend/orchestration.py`
- `src/planning_ping/backend/production.py`
- `src/planning_ping/backend/http.py`
- `src/planning_ping/backend/scheduler.py`
- `src/planning_ping/backend/portals.py`
- `src/planning_ping/backend/parsing.py`
- `src/planning_ping/backend/adapter_models.py`
- `src/planning_ping/backend/adapters/`

Primary discovery/details run before serial PlanIt reconciliation. Primary references win. Per-source failures become the specified warning/error outcomes, completed council transactions survive later problems, cancellation is checked before requests and at both phases, and terminal counts/statuses are persisted and returned. HTTP behavior retains bounded retry/timeout, host/platform gating, cancellation, WAF detection, and no authentication/CAPTCHA bypass.

### Audit, factory, and packaging

- `src/planning_ping/backend/audit.py`
- `src/planning_ping/backend/factory.py`
- `tools/coverage_audit.py`
- `PlanningPing.spec`
- `tools/pyinstaller_hooks/`
- `pyproject.toml`

`create_services(database_path=None)` returns the frozen `AppServices` bundle. The audit is deterministic by authority/code order and reports JSON/CSV-friendly rows. Live reachability is opt-in, timeout-bounded, and limited by default. Package data and the spec include the authority catalogue and tkinter/tkinterdnd hooks/data; no downloaded planning-file path is bundled.

### Tests and fixtures

- `tests/backend/test_domain_geometry_catalogue.py`
- `tests/backend/test_persistence_queries.py`
- `tests/backend/test_orchestration.py`
- `tests/backend/test_adapter_characterization.py`
- `tests/backend/test_production_search.py`
- `tests/backend/test_http_scheduler.py`
- `tests/backend/test_audit_factory.py`
- `tests/backend/test_coverage_audit_cli.py`
- `tests/backend/fixtures/`
- corrected shared contract tests in `tests/test_contract_models.py`

## Catalogue and Audit Results

- Active packaged authorities: 399.
- Supported countries: England, Wales, Scotland only.
- Duplicate stable codes: 0.
- Invalid packaged boundaries/endpoints: 0.
- `unknown` portal families: 0.
- Authorities without a concrete adapter mapping: 0.
- Offline audit result: pass.
- Exhaustive live requests: deliberately not run. The explicit `--live-smoke --timeout ... --limit ... --report ...` path is available.

## Self-Review

- Completeness: checked the binding brief line by line against schema, events, source precedence, cancellation, audit, package data, and tests.
- Code quality: domain/filter/geometry/catalogue/orchestration/persistence/query/production/audit/factory responsibilities remain separate; SQL order fields are allowlisted and values are parameterized.
- YAGNI: no UI, legacy GUI, lead folders, CSV lead output, cloud/auth/customer workflow, or generic fallback for unknown portals was added.
- Test realism: SQLite tests use real file databases and transactions; orchestration tests use the real service/database with only the external authority boundary replaced; inherited HTML verifies a real ported parser.
- Document audit: backend code performs no planning-document filesystem writes and contains no PDF/OCR/enrichment/drawing/lead-folder code. `include_documents=True` fetches metadata/listing pages only and persists titles/types/dates/sizes/descriptions/source/document URLs. The retained binary HTTP path is used by Kensington's planning-record API, not planning-document download or storage.
- Source repository audit: the reusable `lead-generation` checkout remains clean and unmodified.
- Workspace audit: no files under `src/planning_ping/ui/` were changed, and nothing was pushed.

## Deferred Integration Checks / Concerns

- Exhaustive live council searches were intentionally not run, per the brief. Portal reachability can change independently of this build.
- The PyInstaller executable build/startup smoke is an integration-task check because the UI-owned entrypoint does not exist on the `search` branch alone. The spec is ready for the frontend merge.
- A separate reviewer agent could not be allocated because the collaboration thread was at its agent limit; the line-by-line self-review and fresh verification above were completed instead.
