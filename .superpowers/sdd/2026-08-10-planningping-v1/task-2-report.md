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

## Review Fix Round 1

Status: DONE. All eight review findings were reproduced with focused regressions and fixed. The frozen runtime literal validation remains covered by positive tests for all seven event kinds and all three summary statuses plus negative tests for invalid values.

### RED/GREEN evidence and finding dispositions

1. **Arcus/Wiltshire nonempty discovery — fixed.** RED: a detail-complete discovery record called the adapters' intentionally unavailable `fetch_application` method and failed; an out-of-range primary detail was silently returned. GREEN: production now uses an explicit adapter capability that only Arcus and Wiltshire opt into after verifying both completeness/date-range markers, preserves the discovered normalized record/document metadata, still fetches every ordinary adapter detail, propagates ordinary detail failures, and raises `PortalSearchCompletenessError` for off-range details. The real Arcus and Wiltshire subclasses are covered with nonempty discoveries.
2. **Automation evasion/WAF-CAPTCHA behavior — fixed.** RED: Selenium options suppressed automation signals, injected a `navigator.webdriver` override, WAF/CAPTCHA pages were waited out, and access-control/server errors recommended browser fallback. GREEN: all evasion switches and CDP injection are removed; browser rendering leaves WebDriver truth intact; authentication, WAF, CAPTCHA, and server-error pages terminate immediately as visible `CouncilFetchError`s; fallback is limited to ordinary rendering/method cases. A source audit reports zero evasion patterns.
3. **Silent completeness gaps — fixed.** RED: Idox returned at its page cap and on repeated/no-progress pages, Atrium sliced pagination, Northgate returned below its advertised total, Arcus/Ocella returned unsplittable capped windows, Agile/Power Pages truncated at fixed bounds, and the production boundary accepted off-range primary details. GREEN: Idox, Atrium, Northgate, Agile, Tascomi, Colchester Power Pages, Arcus, and Ocella now raise explicit completeness exceptions for applicable caps, repeated pages, no unique progress, changed/reported-total mismatches, and unsplittable threshold windows. Existing CCED, StatMap, Fastweb/Socrata guards were audited and retained. PlanIt and the production primary boundary retain date/total/repeat/cap guards.
4. **Global one-request-per-host gate — fixed.** RED: two clients with different portal-family keys entered the same hostname concurrently. GREEN: every HTTP request first acquires a process-wide hostname semaphore, then its optional platform gate; the cross-client concurrency regression proves serialization. The adaptive scheduler's host/rate-limit behavior remains covered.
5. **TLS verification — fixed.** RED: certificate failure retried after mutating `verify_tls=False`, and Arcus/Wiltshire/Bath plus Agile contained unverified fallback paths. GREEN: verification cannot be disabled, certificate failures terminate visibly without an unverified retry, automatic cipher/security downgrades are gone, every adapter uses trusted default/configured CAs, and source audit finds no unverified-context pattern.
6. **Greenwich coordinates — fixed.** RED: truthiness coalescing replaced valid longitude/latitude `0.0` with fallback coordinates. GREEN: fallback occurs only for `None`/blank; `(0.0, 0.0)` is retained and verified as an exact boundary match.
7. **Cancellation aggregates — fixed.** RED: cancelled councils incremented searched/completed and sometimes empty/failed counts, with `council_finished` events for unfinished councils. GREEN: unfinished states persist usable primary records under a distinct `cancelled` outcome without incrementing searched, empty, or failed counts and without completion events; already reconciled councils and their counts remain durable.
8. **Council start timestamp — fixed.** RED: the persisted outcome start was the later PlanIt-phase time. GREEN: `_CouncilState` captures the primary-phase start and supplies it to both normal and cancelled outcome persistence.

Focused RED examples produced the expected failures: two production-boundary failures; seven HTTP/security/concurrency failures across five tests; five adapter-completeness failures; and three cancellation/timestamp failures. Focused GREEN results: production 9/9, HTTP/scheduler 8/8, adapter completeness 6/6, and orchestration 5/5.

### Fix-round files

- Production/adapter boundary: `src/planning_ping/backend/production.py`, `src/planning_ping/backend/adapters/base.py`, `arcus.py`, and `wiltshire.py`.
- Completeness: `agile.py`, `atrium.py`, `idox.py`, `northgate.py`, `ocella.py`, `bespoke_portals.py`, and `legacy_forms.py`.
- HTTP/security/concurrency: `src/planning_ping/backend/http.py`.
- Cancellation/timestamps: `src/planning_ping/backend/orchestration.py`.
- Regressions: `tests/backend/test_adapter_completeness.py`, `test_http_scheduler.py`, `test_orchestration.py`, and `test_production_search.py`.

### Fresh fix-round verification and audits

- Full suite: `uv --system-certs run --link-mode copy --python 3.11 python -m unittest discover -s tests -v` — **64 tests, 0 failures**.
- Compile: `uv --system-certs run --link-mode copy --python 3.11 python -m compileall -q src tools` — exit 0.
- Offline catalogue audit: **399 rows, 399 pass, 0 fail**; no live council requests.
- `git diff --check` — exit 0.
- Security-evasion/TLS audit — 0 occurrences of unverified contexts, disabled certificate checks, WebDriver suppression, `AutomationControlled`, or automation-switch hiding.
- Document payload audit — 0 backend filesystem payload writes. The unused Selenium binary-download path was removed. The retained `CouncilHttpClient.get_bytes` is only called by Kensington's binary primary-record search API; document-looking URLs in adapters are stored as metadata only.
- Fixed-cap audit — all production fixed page bounds located by source search now terminate with visible completeness exceptions; date parsing/year iteration and single-option selection slices are not pagination truncation.
- Reusable source repository remains clean; no `src/planning_ping/ui/` change; nothing pushed.

### Fix-round self-review and concerns

- Completeness/YAGNI: fixes are confined to the reviewed production boundaries and shared infrastructure; no UI, document payload, OCR, lead-output, or unrelated feature code was added.
- Test realism: orchestration uses real SQLite transactions; host concurrency uses two real threads and independent HTTP clients; adapter tests exercise production parser/pagination methods with deterministic response doubles; real Arcus/Wiltshire types cover the detail-complete capability.
- Remaining concern: exhaustive live council searches remain intentionally unrun. Real portal markup, availability, and certificates can change externally, so bounded opt-in live smoke remains an integration activity.

## Review Fix Round 2

Status: DONE. The remaining hostname/redirect/adaptive-throttling gap and inferred-date regression were reproduced and fixed with focused RED/GREEN cycles.

### RED/GREEN evidence and dispositions

#### A. Host normalization, redirects, and production adaptive limits — fixed

- **Root causes:** the global host semaphore used `urlsplit(url).netloc`, so an implicit HTTPS port and `:443` created different gates; urllib followed redirects internally while only the original URL's gate was held; and the production search boundary did not assign a platform key to otherwise unkeyed adapter clients, leaving the standalone scheduler disconnected from those requests.
- **RED:** default-port and explicit-port requests entered concurrently; a returned cross-host 302 never acquired the target-host gate; HTTP 429 did not reduce platform concurrency; two successes did not demonstrate capacity restoration; and an unkeyed production scraper remained unkeyed. All four focused tests failed for those expected reasons.
- **GREEN:** hostname and rate-limit identities now use normalized `urlsplit(...).hostname`, independent of port. Automatic urllib redirects are disabled and followed manually with a ten-hop/repeat/scheme bound; each hop releases its source gate before acquiring the target hostname, preventing source/target lock inversion. Cross-host sensitive headers are not forwarded. A local two-server smoke also confirmed a real urllib 302 reaches the final response.
- A process-wide adaptive platform gate now classifies rate-limited (429), blocked/auth (401/403 and WAF/CAPTCHA body), and service-unavailable (502/503/504) signals, reduces the affected platform to a minimum of one request, and restores one slot after two successful requests. All adapter HTTP traffic has an existing family key or receives one from `ProductionAuthoritySearcher` before network activity, so production uses the adaptive limit. The hostname/redirect/adaptive/production-key focused tests pass 4/4.

#### B. Arcus/Wiltshire inferred request dates — fixed

- **Root cause:** both Salesforce adapters copied `start_date` into received/validated when the portal record lacked a real date, then marked the synthetic value with `date_inferred_from_search_window=True`; this made an actually undated record pass the inclusive application-date filter.
- **RED:** public discovery for both adapters normalized `2026-01-01` from the request into the application, and production accepted a deliberately inferred complete-discovery record. Both focused tests failed.
- **GREEN:** neither adapter accepts or copies a fallback request date; missing portal dates remain `None`. Their detail-complete capability explicitly excludes inferred markers, and production independently raises `PortalSearchCompletenessError` if an inferred marker reaches the boundary. A genuinely undated complete record remains undated and `application_matches_request` rejects it. Adapter/production focused tests pass 2/2.

### Round-2 files changed

- `src/planning_ping/backend/http.py`
- `src/planning_ping/backend/production.py`
- `src/planning_ping/backend/adapters/arcus.py`
- `src/planning_ping/backend/adapters/wiltshire.py`
- `tests/backend/test_http_scheduler.py`
- `tests/backend/test_production_search.py`
- `tests/backend/test_adapter_completeness.py`

### Fresh round-2 verification and audits

- Full suite: `uv --system-certs run --link-mode copy --python 3.11 python -m unittest discover -s tests -v` — **69 tests, 0 failures**.
- Focused HTTP/production/completeness modules — **28 tests, 0 failures**.
- Compile: `uv --system-certs run --link-mode copy --python 3.11 python -m compileall -q src tools` — exit 0.
- Offline catalogue audit — **399 rows, 399 pass, 0 fail**; no live council searches.
- `git diff --check` — exit 0.
- Security-evasion/TLS audit — 0 hits.
- Document-payload-write audit — 0 hits.
- Reusable source repository clean; no `src/planning_ping/ui/` changes; nothing pushed.

### Round-2 self-review and concerns

- Redirect handling is bounded, preserves POST bodies only for 307/308, converts 301/302/303 to GET, rejects unsupported/repeated targets, and strips credentials/cookies on hostname changes.
- Adaptive limits remain deliberately small and process-local; they do not introduce a new persistence/configuration surface. Host concurrency remains one regardless of port or adapter family.
- The full frozen contract literal tests remain green.
- Remaining concern unchanged: exhaustive live council searches were intentionally not run; external portal behavior can change independently.
