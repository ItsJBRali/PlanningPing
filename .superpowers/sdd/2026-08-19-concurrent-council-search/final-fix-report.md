# Concurrent Council Search Final Fix Report

Date: 2026-08-19

Branch: `development`

Required starting point: `2e19beb5fb5b176782ea68240b4e45ead8dcc77a`

Implementation commit: `ce5c73f4bacaee1f05e0151d5f23a844966a5280` (`fix: preserve structured failures and prompt followups`)

## Findings-to-fixes mapping

### Important 1: real adapters swallowed structured failures

Root cause:

- Idox caught the `CouncilFetchError` base class around advanced search and converted complete-week 429/403 failures into weekly fallback requests.
- AchieveForms caught `Exception` around detail lookup and converted 429/403 failures into incomplete weekly-row stubs.
- Civica caught `Exception` around its search-criteria probe and converted 429/403 failures into default date fields.

Fix:

- `idox.py`, `achieveforms.py`, and `civica.py` now re-raise `CouncilRateLimitError` and `CouncilAccessBlockedError` before their existing broad fallbacks.
- Ordinary council fetch, parsing, and detail failures still take the established fallback paths.
- `tests/backend/test_adapter_structured_failures.py` exercises both structured types through each real adapter path. The Idox complete-week case asserts the original exception object and exactly one request, proving that no weekly fallback request occurs.

### Important 2: newly unlocked PlanIt phases starved behind the primary backlog

Root cause:

- `CouncilPhaseScheduler` had one ready FIFO. Every primary was enqueued at startup, so a PlanIt phase unlocked by an early primary was appended behind every untouched primary.

Fix:

- The scheduler now has a dedicated FIFO prompt-follow-up queue in addition to the unchanged ordinary ready FIFO.
- Only PlanIt phases newly unlocked by orchestration enter the prompt queue. Due PlanIt retries return to it after their cooldown; ordinary and deferred primary order remains FIFO.
- Acquisition scans prompt follow-ups first, then the ordinary FIFO. Per-council exclusion, PlanIt single-flight, scope cooldowns, cancellation, and the maximum of eight workers are unchanged.
- Primary starvation is bounded: only one PlanIt can be active, and every prompt PlanIt is unlocked by one completed primary. With one worker the order can alternate primary/PlanIt, while PlanIt retries remain capped at two deferrals.
- Cancellation finalization now persists an untouched council as cancelled when prompt execution makes that state reachable. Deferred rate-limited councils remain intentionally unsaved, preserving the established deferred-cancellation contract.

Regression coverage:

- The scheduler boundary test proves prompt FIFO ordering and proves a queued PlanIt cannot block primary progress while another PlanIt is active.
- The deterministic 10-council orchestration test blocks the first eight primaries, releases only council 0, and proves council 0 reaches PlanIt and is saved before council 9's untouched primary begins. Later primaries wait on explicit events rather than timeouts.
- The one-worker cancellation test now proves gamma's primary never starts but gamma still receives a durable cancelled outcome.
- All existing cooldown, retry-cap, cancellation, liveness, immediate-save, eight-worker, per-council exclusion, and PlanIt single-flight tests pass.

### Important 3: established bot-check block signature was lost

Root cause:

- The structured access-classification refactor moved WAF signatures into `_access_block_classification` but omitted `checking you're not a bot`.

Fix:

- The established signature is restored to the fixed WAF token list.
- Direct HTTP 403 and browser-page tests prove it classifies as portal security / a web application firewall challenge.
- Both tests include unique body markers and assert those markers are absent from raised error text, proving raw page content is not exposed.

### Minor: production cleanup was not asserted directly

Fix:

- Production structured-failure coverage now retains each constructed primary scraper.
- Both 429 and 403 primary propagation assert the original exception identity and `close()` directly.
- Both structured types are also checked in the PlanIt direction.

This was a coverage gap rather than a production cleanup defect: the existing `finally` already closed the scraper, so these assertions passed when added.

## TDD evidence

### Initial RED

Command:

```text
uv --system-certs run --with pytest pytest \
  tests/backend/test_adapter_structured_failures.py \
  tests/backend/test_http_scheduler.py::HttpBoundaryTests::test_403_checking_not_a_bot_page_is_safely_classified_without_body_exposure \
  tests/backend/test_http_scheduler.py::HttpBoundaryTests::test_browser_rejects_checking_not_a_bot_page_without_exposing_body \
  tests/backend/test_http_scheduler.py::SchedulerBoundaryTests::test_prompt_followups_are_fifo_and_do_not_block_primary_progress_during_planit_single_flight \
  tests/backend/test_orchestration.py::OrchestrationTests::test_unlocked_planit_is_saved_before_untouched_primary_backlog_drains \
  tests/backend/test_production_search.py::ProductionAuthoritySearchTests::test_production_search_preserves_structured_failures_and_closes_primary_scrapers -q
```

Observed: `10 failed, 4 passed, 4 subtests passed`.

The failures were the expected missing behaviors:

- Idox made a second weekly request after both structured errors.
- AchieveForms returned a stub for both structured errors.
- Civica returned default fields for both structured errors.
- HTTP classified the bot-check page as generic access denial.
- Browser classification did not reject the page.
- The scheduler had no prompt-follow-up operation.
- The early council's PlanIt timed out behind the untouched primary backlog.

The production cleanup/propagation test passed at RED because it strengthens coverage for an already-correct `finally` path.

### Initial GREEN

After the minimal exception-ordering, WAF-token, and scheduler changes, the same command reported:

```text
8 passed, 10 subtests passed in 0.91s
```

### Cancellation regression RED/GREEN

The expanded focused run exposed a newly reachable cancellation state:

```text
1 failed, 77 passed, 22 subtests passed
```

The failure showed an untouched gamma primary had no durable cancelled outcome. The focused test was strengthened to prove gamma's primary was never called and still failed on the missing outcome. After removing only the `started_at is None` cancellation skip (while retaining the deferred-phase skip), the focused test reported `1 passed`.

The full focused matrix then reported:

```text
78 passed, 22 subtests passed in 1.97s
```

That matrix covered adapter characterization/completeness, structured adapter failures, HTTP/scheduler, orchestration, production search, and the UI search controller.

## Full verification

The canonical full suite was run with explicit Tcl/Tk library paths because this uv-managed Python did not reliably discover `init.tcl` without them:

```text
uv --system-certs run python -m unittest discover -s tests
```

Fresh completion result before commit:

```text
Ran 150 tests in 25.565s
OK
```

Static checks:

- Ruff fatal-error rules (`E9`, `F63`, `F7`, `F82`) on every changed source and test file: `All checks passed!`
- `python -m compileall -q src tests`: exit 0.
- `git diff --check`: exit 0.

A diagnostic default Ruff scan of the entire repository reports 142 broader style findings (legacy import ordering, timezone style, broad catches, and similar rules). Ruff is not configured as a repository gate. No unrelated mass-formatting or lint refactor was included in this fix pass.

Baseline note: an initial pytest run on the untouched starting commit reached `141 passed, 47 subtests passed` but reported two Tk shutdown failures after uv/Tk discovery errors and a cleanup-time SQLite handle leak. Pinning `TCL_LIBRARY` and `TK_LIBRARY` to the installed Python 3.11 Tcl/Tk directories made the canonical full suite pass, including all shutdown tests.

## Remote verification

Implementation push checkpoint:

```text
HEAD=ce5c73f4bacaee1f05e0151d5f23a844966a5280
ORIGIN_DEVELOPMENT=ce5c73f4bacaee1f05e0151d5f23a844966a5280
```

The report is committed separately after this checkpoint. Its own commit cannot truthfully contain its final self-referential SHA; final `HEAD` / `origin/development` equality and the report commit SHA are recorded in the task handoff.

No merge to `main` was performed and no branch was deleted.

## Self-review

- Broad exception ordering: both structured subclasses are re-raised before every implicated parent/broad catch; Idox's outer browser failure catch still closes and re-raises.
- Duplicate requests: the Idox test proves the weekly fallback is never requested after 429/403; AchieveForms and Civica stop at the failing lookup/probe.
- Queue fairness/liveness: prompt and ordinary queues are independently FIFO; PlanIt single-flight prevents prompt work from consuming more than one worker; one-worker alternation is bounded by completed primaries and retry caps.
- Cooldown: due PlanIt retries re-enter the prompt FIFO only after scheduler promotion; scope cooldown eligibility remains authoritative.
- Immediate save order: the 10-council test observes PlanIt and persistence before the untouched backlog drains; persistence remains on the coordinator thread.
- Cancellation: active work drains once, untouched councils receive durable cancelled outcomes, and deferred retries remain unsaved.
- Safe errors: block-page classification returns fixed category/reason strings and never interpolates the raw body.
- Cleanup: retained primary scraper instances directly prove `close()` after both structured failure types.

## Concerns

- No product-behavior concerns remain from this review pass.
- Environment-only concern: the uv Python needed explicit `TCL_LIBRARY` / `TK_LIBRARY` values for a reliable full GUI test run.
- Repository-maintenance concern: a default full Ruff scan is not currently green (142 broad style findings); this pass verified fatal rules and compilation without expanding scope.
