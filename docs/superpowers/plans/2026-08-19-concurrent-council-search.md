# Concurrent Council Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process different councils with at most eight concurrent workers, save each council immediately on completion, and defer rather than sleep on HTTP 429 while classifying HTTP 403 safely.

**Architecture:** The UI's existing search thread owns a coordinator backed by a fixed `ThreadPoolExecutor`. A phase scheduler tracks ready, active, deferred, and provider-cooled work; network threads return results or typed HTTP failures, while the coordinator alone advances council state, emits events, and writes SQLite transactions.

**Tech Stack:** Python 3.11, `concurrent.futures`, `threading`, `heapq`, `urllib`, SQLite, `unittest`, and pytest.

**Spec:** `docs/superpowers/specs/2026-08-19-concurrent-council-search-design.md`

## Global Constraints

- Create all implementation commits on `development`, which starts from `origin/main` commit `0e71e6703b4963828a03bc819c3e4aff075a6537`.
- Never execute more than eight council phases concurrently, and never execute two phases for the same council concurrently.
- Execute at most one PlanIt phase at a time.
- A 429 must release its executor thread; valid `Retry-After` values are honored without the existing 20-second truncation.
- PlanIt keeps two retries after the initial attempt; primary searches use their configured `CouncilHttpClient.retries` allowance.
- A 403 is terminal for its source phase and must not trigger an access-control bypass.
- SQLite writes and `SearchEvent` emission occur only on the coordinator thread.
- Save a council exactly once when it reaches a terminal outcome; do not add or migrate database tables.
- Keep the existing public `SearchService.run` signature and existing `SearchEventKind` values.
- Do not add a UI setting for worker count, partial page checkpoints, or cross-restart scheduler recovery.

## File Responsibility Map

- `src/planning_ping/backend/http.py`: turn 429 and 403 responses into structured failures and parse server retry instructions.
- `src/planning_ping/backend/rate_limits.py`: provide shared PlanIt/primary scope names and bounded fallback backoff.
- `src/planning_ping/backend/scheduler.py`: maintain ready, active, deferred, and scope-cooled council phases without performing network or database work.
- `src/planning_ping/backend/production.py`: assign the shared primary scope to each real scraper client and let structured failures reach orchestration.
- `src/planning_ping/backend/orchestration.py`: own the executor, council state transitions, completion-order persistence, events, and cancellation.
- `src/planning_ping/ui/controllers.py`: display coordinator status messages carried by the existing `council_started` event.
- `tests/backend/test_http_scheduler.py`: cover structured HTTP failures, retry parsing/backoff, and phase-queue deadlines.
- `tests/backend/test_production_search.py`: cover production scope assignment and structured-failure propagation.
- `tests/backend/test_orchestration.py`: cover concurrency, persistence timing, retries, fallbacks, and save-once behavior.
- `tests/ui/test_search_controller.py`: cover deferred/retry status presentation without new event kinds.
- `tests/test_shutdown_integration.py`: retain the real UI-worker/database shutdown boundary.

---

### Task 1: Structured HTTP 429 and 403 Failures

**Files:**
- Modify: `src/planning_ping/backend/http.py:42-45,315-349,782-842`
- Test: `tests/backend/test_http_scheduler.py:27-480`

**Interfaces:**
- Produces: `CouncilRateLimitError(url: str, scope: str, retry_after_seconds: float | None, retry_limit: int)`.
- Produces: `CouncilAccessBlockedError(url: str, category: str, reason: str)` where category is `network_filter`, `portal_security`, or `access_denied`.
- Produces: `_retry_after_seconds(exc: HTTPError, *, now: datetime | None = None) -> float | None`.
- Preserves: non-429 transient retry behavior, TLS verification, redirect handling, and adaptive concurrency signals.

- [ ] **Step 1: Write failing tests for 429 metadata, full Retry-After, and no internal sleep**

Add imports for `io`, `datetime`, and the two new exception classes. Add a header fake that can return a supplied `Retry-After`, then add these tests to `HttpBoundaryTests`:

```python
class MappingHeaders(FakeHeaders):
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str, default: object = None) -> object:
        return self.values.get(name, default)


def test_429_returns_retry_metadata_without_retrying_inside_http_client(self) -> None:
    class RateLimitedOpener:
        def __init__(self) -> None:
            self.calls = 0

        def open(self, request: object, timeout: float) -> FakeResponse:
            self.calls += 1
            raise HTTPError(
                "https://www.planit.org.uk/api/applics/json",
                429,
                "Too Many Requests",
                MappingHeaders({"Retry-After": "294"}),
                io.BytesIO(b"rate limited"),
            )

    class Client(CouncilHttpClient):
        def __init__(self) -> None:
            super().__init__(
                min_delay_seconds=0,
                retries=2,
                rate_limit_key="planit",
            )
            self.opener = RateLimitedOpener()

        def _opener(self) -> RateLimitedOpener:
            return self.opener

    client = Client()
    with self.assertRaises(CouncilRateLimitError) as raised:
        client.get("https://www.planit.org.uk/api/applics/json")

    self.assertEqual(1, client.opener.calls)
    self.assertEqual("planit", raised.exception.scope)
    self.assertEqual(294.0, raised.exception.retry_after_seconds)
    self.assertEqual(2, raised.exception.retry_limit)


def test_retry_after_http_date_is_not_truncated_to_twenty_seconds(self) -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)
    error = HTTPError(
        "https://planning.test",
        429,
        "Too Many Requests",
        MappingHeaders({"Retry-After": "Wed, 19 Aug 2026 10:05:00 GMT"}),
        io.BytesIO(),
    )

    self.assertEqual(300.0, planning_http._retry_after_seconds(error, now=now))
```

- [ ] **Step 2: Write failing tests for Streamline3 and generic 403 classification**

```python
def test_403_classifies_local_content_filter_and_never_retries(self) -> None:
    body = (
        b"<title>Content filtering has stopped access to this web page</title>"
        b"<p>Streamline3 support reference BR16</p>"
    )

    class BlockedOpener:
        def __init__(self) -> None:
            self.calls = 0

        def open(self, request: object, timeout: float) -> FakeResponse:
            self.calls += 1
            raise HTTPError(
                "https://ashford.test/search",
                403,
                "Forbidden",
                MappingHeaders({}),
                io.BytesIO(body),
            )

    class Client(CouncilHttpClient):
        def __init__(self) -> None:
            super().__init__(min_delay_seconds=0, retries=6)
            self.opener = BlockedOpener()

        def _opener(self) -> BlockedOpener:
            return self.opener

    client = Client()
    with self.assertRaises(CouncilAccessBlockedError) as raised:
        client.get("https://ashford.test/search")

    self.assertEqual(1, client.opener.calls)
    self.assertEqual("network_filter", raised.exception.category)
    self.assertIn("local network content filter", str(raised.exception).casefold())
```

Add a second case using an empty 403 body and assert category `access_denied`, one opener call, and an `HTTP 403` message.

- [ ] **Step 3: Run the HTTP tests and verify the new imports or assertions fail**

Run: `uv run --with pytest pytest tests/backend/test_http_scheduler.py -q`

Expected: FAIL because `CouncilRateLimitError`, `CouncilAccessBlockedError`, and `_retry_after_seconds` do not yet exist.

- [ ] **Step 4: Implement the typed failures and response conversion**

Add these exception contracts immediately after `CouncilFetchError`:

```python
class CouncilRateLimitError(CouncilFetchError):
    def __init__(
        self,
        *,
        url: str,
        scope: str,
        retry_after_seconds: float | None,
        retry_limit: int,
    ) -> None:
        self.url = url
        self.scope = scope
        self.retry_after_seconds = retry_after_seconds
        self.retry_limit = max(int(retry_limit), 0)
        instruction = (
            f"; retry after {retry_after_seconds:g} seconds"
            if retry_after_seconds is not None
            else ""
        )
        super().__init__(f"HTTP 429 while fetching {url}{instruction}")


class CouncilAccessBlockedError(CouncilFetchError):
    def __init__(self, *, url: str, category: str, reason: str) -> None:
        self.url = url
        self.category = category
        self.reason = reason
        super().__init__(f"HTTP 403 while fetching {url}: {reason}")
```

In `_send_raw`, handle 429 before the retry loop's existing 503 branch and handle 403 before the generic `exc.code < 500` branch. Read at most 12,000 bytes from a 403 response, decode with the response charset or UTF-8, and classify these signatures:

```python
if exc.code == 429:
    raise CouncilRateLimitError(
        url=failure_url,
        scope=self._throttle_key(failure_url),
        retry_after_seconds=_retry_after_seconds(exc),
        retry_limit=self.retries,
    ) from exc
if exc.code == 403:
    body = exc.read(12_000).decode("utf-8", errors="replace")
    category, reason = _access_block_classification(body, "")
    raise CouncilAccessBlockedError(
        url=failure_url,
        category=category,
        reason=reason,
    ) from exc
```

Implement `_retry_after_seconds` so numeric seconds and HTTP dates return their full non-negative value and invalid/missing headers return `None`. Keep `_retry_delay_seconds` for 503 and other existing retries, using `_retry_after_seconds(exc)` when present and otherwise the existing bounded fallback.

Implement `_access_block_classification(text, title) -> tuple[str, str]` with this priority:

```python
if "content filtering has stopped access" in opening or "streamline3" in opening:
    return "network_filter", "Local network content filter blocked this council portal"
waf_tokens = (
    "_incapsula_resource",
    "incapsula incident id",
    "captcha-sdk.awswaf.com",
    "awswaf",
    "cf-chl-",
    "cloudflare ray id",
    "azure waf",
)
if any(token in opening or token in normalized_title for token in waf_tokens):
    return "portal_security", "Web application firewall challenge detected"
if "captcha" in opening or "captcha" in normalized_title:
    return "portal_security", "CAPTCHA challenge detected"
return "access_denied", "The remote service denied access"
```

Make `_blocked_page_reason` reuse the classification helper for non-HTTP browser/page detection. Update `browser_fallback_recommended` to reject `CouncilAccessBlockedError` by type as well as retaining its existing message safeguards.

- [ ] **Step 5: Run focused and existing HTTP boundary tests**

Run: `uv run --with pytest pytest tests/backend/test_http_scheduler.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the HTTP boundary**

```bash
git add src/planning_ping/backend/http.py tests/backend/test_http_scheduler.py
git commit -m "fix: expose council rate limits and access blocks"
```

---

### Task 2: Council Phase Deadlines and Shared Rate-Limit Scopes

**Files:**
- Create: `src/planning_ping/backend/rate_limits.py`
- Modify: `src/planning_ping/backend/scheduler.py:1-225`
- Test: `tests/backend/test_http_scheduler.py:482-506`

**Interfaces:**
- Produces: `PLANIT_RATE_LIMIT_SCOPE = "planit"`.
- Produces: `primary_rate_limit_scope(council: Council) -> str`.
- Produces: `fallback_retry_delay(retry_number: int, jitter: Callable[[float, float], float]) -> float`.
- Produces: `CouncilPhaseTask(council_code: str, phase: Literal["primary", "planit"], scope: str)`.
- Produces: `CouncilPhaseScheduler.enqueue`, `defer`, `set_scope_cooldown`, `acquire`, `release`, `has_pending`, and `next_ready_at`.

- [ ] **Step 1: Write failing pure tests for scopes and bounded exponential backoff**

Add imports from `rate_limits.py` and these tests:

```python
def test_primary_scopes_are_stable_and_fallback_backoff_is_bounded(self) -> None:
    council = Council(
        "alpha",
        "Alpha Council",
        "England",
        "idox",
        "Idox",
        "https://alpha.test",
        None,
        "https://alpha.test/search",
        {},
    )

    self.assertEqual("portal:idox", primary_rate_limit_scope(council))
    self.assertEqual(
        [2.0, 4.0, 8.0, 10.0, 10.0],
        [fallback_retry_delay(number, lambda low, high: 0.0) for number in range(1, 6)],
    )
```

Also create a `custom` council and assert its scope uses the normalized scraper type rather than the generic word `custom`.

- [ ] **Step 2: Write failing scheduler tests for distinct councils, PlanIt single-flight, deadlines, and shared cooldowns**

```python
def test_council_phase_scheduler_defers_without_occupying_an_active_slot(self) -> None:
    scheduler = CouncilPhaseScheduler()
    alpha_primary = CouncilPhaseTask("alpha", "primary", "portal:idox")
    alpha_planit = CouncilPhaseTask("alpha", "planit", PLANIT_RATE_LIMIT_SCOPE)
    beta_planit = CouncilPhaseTask("beta", "planit", PLANIT_RATE_LIMIT_SCOPE)
    gamma_primary = CouncilPhaseTask("gamma", "primary", "portal:arcus")
    for task in (alpha_primary, alpha_planit, beta_planit, gamma_primary):
        scheduler.enqueue(task)

    self.assertEqual(alpha_primary, scheduler.acquire(now=0.0))
    self.assertEqual(beta_planit, scheduler.acquire(now=0.0))
    self.assertEqual(gamma_primary, scheduler.acquire(now=0.0))
    self.assertIsNone(scheduler.acquire(now=0.0))

    scheduler.release(alpha_primary)
    scheduler.defer(alpha_primary, ready_at=30.0)
    scheduler.release(beta_planit)
    scheduler.set_scope_cooldown(PLANIT_RATE_LIMIT_SCOPE, ready_at=20.0)
    scheduler.release(gamma_primary)

    self.assertIsNone(scheduler.acquire(now=10.0))
    self.assertEqual(20.0, scheduler.next_ready_at(now=10.0))
    self.assertEqual(alpha_planit, scheduler.acquire(now=20.0))
    scheduler.release(alpha_planit)
    self.assertIsNone(scheduler.acquire(now=29.0))
    self.assertEqual(alpha_primary, scheduler.acquire(now=30.0))
```

- [ ] **Step 3: Run the scheduler tests and verify they fail on missing APIs**

Run: `uv run --with pytest pytest tests/backend/test_http_scheduler.py::SchedulerBoundaryTests -q`

Expected: FAIL because the scope/backoff functions and phase scheduler do not exist.

- [ ] **Step 4: Implement the pure rate-limit helpers**

Create `rate_limits.py` with these exact rules:

```python
from __future__ import annotations

from typing import Callable

from .models import Council


PLANIT_RATE_LIMIT_SCOPE = "planit"


def primary_rate_limit_scope(council: Council) -> str:
    platform = council.portal_family.casefold().strip()
    if platform in {"", "custom", "unknown"}:
        platform = council.scraper_type.casefold().strip() or "custom"
    return f"portal:{platform}"


def fallback_retry_delay(
    retry_number: int,
    jitter: Callable[[float, float], float],
) -> float:
    exponent = max(int(retry_number) - 1, 0)
    base = min(2.0 * (2**exponent), 10.0)
    return base + jitter(0.0, min(base * 0.25, 1.0))
```

- [ ] **Step 5: Implement the non-blocking council phase scheduler**

Add `CouncilPhaseTask` and `CouncilPhaseScheduler` to `scheduler.py`. Use a `deque` for ready work, a `heapq` entry `(ready_at, sequence, task)` for deferred work, a set of active council codes, a PlanIt-active flag, and a scope-to-deadline mapping.

`acquire(now)` must promote all due heap entries, rotate once through the ready queue, and select the first task whose council is inactive, whose scope cooldown has expired, and whose PlanIt phase does not conflict with an active PlanIt task. It marks the selected task active before returning it. `release(task)` clears those active markers. `next_ready_at(now)` returns the earliest future deferred deadline or cooldown affecting queued work, ignoring expired deadlines.

Use these signatures:

```python
CouncilPhase = Literal["primary", "planit"]


@dataclass(frozen=True, slots=True)
class CouncilPhaseTask:
    council_code: str
    phase: CouncilPhase
    scope: str


class CouncilPhaseScheduler:
    def enqueue(self, task: CouncilPhaseTask) -> None:
        self._ready.append(task)

    def defer(self, task: CouncilPhaseTask, *, ready_at: float) -> None:
        heapq.heappush(self._deferred, (ready_at, next(self._sequence), task))

    def set_scope_cooldown(self, scope: str, *, ready_at: float) -> None:
        self._scope_cooldowns[scope] = max(self._scope_cooldowns.get(scope, 0.0), ready_at)

    def acquire(self, *, now: float) -> CouncilPhaseTask | None:
        self._promote_due(now)
        for _candidate in range(len(self._ready)):
            task = self._ready.popleft()
            if self._eligible(task, now):
                self._activate(task)
                return task
            self._ready.append(task)
        return None

    def release(self, task: CouncilPhaseTask) -> None:
        self._active_councils.discard(task.council_code)
        if task.phase == "planit":
            self._planit_active = False
```

Keep the existing `PlatformAwareScheduler` behavior and tests unchanged.

- [ ] **Step 6: Run scheduler and HTTP tests**

Run: `uv run --with pytest pytest tests/backend/test_http_scheduler.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the scheduling primitives**

```bash
git add src/planning_ping/backend/rate_limits.py src/planning_ping/backend/scheduler.py tests/backend/test_http_scheduler.py
git commit -m "feat: add deferred council phase scheduling"
```

---

### Task 3: Production Search Rate-Limit Scope Propagation

**Files:**
- Modify: `src/planning_ping/backend/production.py:35-45,161-210`
- Test: `tests/backend/test_production_search.py:14-260`

**Interfaces:**
- Consumes: `CouncilRateLimitError`, `CouncilAccessBlockedError`, and `primary_rate_limit_scope` from Tasks 1-2.
- Produces: every real primary `CouncilHttpClient.rate_limit_key` matches `primary_rate_limit_scope(council)`.
- Preserves: `ProductionAuthoritySearcher.search_primary` and `search_planit` signatures and scraper cleanup.

- [ ] **Step 1: Extend the production client-scope test and add structured-failure propagation tests**

Extend `test_production_assigns_an_adaptive_platform_key_to_unkeyed_http_clients`:

```python
self.assertEqual("portal:idox", scraper.http.concurrency_key)
self.assertEqual("portal:idox", scraper.http.rate_limit_key)
```

Add a scraper and PlanIt HTTP fake that raise one preconstructed rate-limit exception, and prove the same object reaches orchestration's boundary:

```python
def test_production_search_preserves_structured_rate_limit_failures(self) -> None:
    primary_error = CouncilRateLimitError(
        url="https://alpha.test/search",
        scope="portal:idox",
        retry_after_seconds=60.0,
        retry_limit=6,
    )

    class RateLimitedScraper(FakeScraper):
        def discover_ids(self, **kwargs: object) -> DiscoveryResult:
            raise primary_error

    with self.assertRaises(CouncilRateLimitError) as primary_raised:
        ProductionAuthoritySearcher(
            scraper_factory=lambda _council: RateLimitedScraper()
        ).search_primary(council(), date(2026, 1, 1), date(2026, 1, 31), Event())
    self.assertIs(primary_error, primary_raised.exception)

    class RateLimitedPlanIt:
        def get(self, url: str, params: dict[str, str] | None = None) -> FetchResponse:
            raise primary_error

    with self.assertRaises(CouncilRateLimitError) as planit_raised:
        ProductionAuthoritySearcher(planit_http=RateLimitedPlanIt()).search_planit(
            council(), date(2026, 1, 1), date(2026, 1, 31), Event()
        )
    self.assertIs(primary_error, planit_raised.exception)
```

Add the corresponding imports and use `_council` rather than `value` for the unused lambda parameter to satisfy style checks.

- [ ] **Step 2: Run the production tests and verify the rate-limit key assertion fails**

Run: `uv run --with pytest pytest tests/backend/test_production_search.py -q`

Expected: FAIL because primary clients do not yet receive a shared `rate_limit_key`.

- [ ] **Step 3: Assign the deterministic primary scope without changing adapter APIs**

In `search_primary`, compute the scope before entering `monitor_council_requests`:

```python
scope = primary_rate_limit_scope(council)
client = getattr(scraper, "http", None)
if isinstance(client, CouncilHttpClient):
    client.rate_limit_key = scope
    if not client.concurrency_key:
        client.concurrency_key = scope
```

Do not catch `CouncilRateLimitError` or `CouncilAccessBlockedError`; the existing `finally: scraper.close()` must still execute while the structured exception propagates.

- [ ] **Step 4: Run production and HTTP tests**

Run: `uv run --with pytest pytest tests/backend/test_production_search.py tests/backend/test_http_scheduler.py -q`

Expected: PASS.

- [ ] **Step 5: Commit production scope propagation**

```bash
git add src/planning_ping/backend/production.py tests/backend/test_production_search.py
git commit -m "fix: share portal rate limit scopes"
```

---

### Task 4: Eight-Worker Phase Coordinator and Immediate Council Saves

**Files:**
- Modify: `src/planning_ping/backend/orchestration.py:1-350`
- Modify: `tests/backend/test_orchestration.py:1-190`

**Interfaces:**
- Consumes: `CouncilPhaseScheduler`, `CouncilPhaseTask`, `PLANIT_RATE_LIMIT_SCOPE`, and `primary_rate_limit_scope`.
- Produces: `PlanningSearchService` keyword parameters `worker_limit: int = 8`, `monotonic_clock: Callable[[], float] = monotonic`, and `jitter: Callable[[float, float], float] = random.uniform`, with `worker_limit` clamped to `1..8` for internal tests only.
- Produces: `_CouncilState` containing primary/PlanIt results, errors, per-phase rate-limit attempt counts, and a saved flag.
- Preserves: `PlanningSearchService.run(request, emit, cancel_event) -> SearchSummary`.

- [ ] **Step 1: Replace the serial-order assertion with per-council phase-order assertions**

Protect `FakeAuthoritySearcher.calls` with a `Lock`, then replace the assertion that all primary calls precede every PlanIt call:

```python
for code in ("alpha", "beta", "gamma"):
    with self.subTest(code=code):
        self.assertLess(
            searcher.calls.index(("primary", code)),
            searcher.calls.index(("planit", code)),
        )
```

Keep the existing result, filter, warning, and persistence assertions.

- [ ] **Step 2: Write a failing concurrency-ceiling and PlanIt single-flight test**

Add a thread-safe searcher that blocks all primary calls until eight distinct councils have entered. It must track `active_councils`, `max_active`, `duplicate_council`, `planit_active`, and `max_planit_active` under one lock:

```python
class BlockingConcurrentSearcher:
    def __init__(self) -> None:
        self.lock = Lock()
        self.release_primary = Event()
        self.eight_started = Event()
        self.active_councils: set[str] = set()
        self.max_active = 0
        self.duplicate_council = False
        self.planit_active = 0
        self.max_planit_active = 0
        self.first_planit_started = Event()
        self.planit_overlap = Event()
        self.release_planit = Event()

    def search_primary(self, council, start_date, end_date, cancel_event):
        with self.lock:
            if council.code in self.active_councils:
                self.duplicate_council = True
            self.active_councils.add(council.code)
            self.max_active = max(self.max_active, len(self.active_councils))
            if len(self.active_councils) == 8:
                self.eight_started.set()
        self.release_primary.wait(2)
        with self.lock:
            self.active_councils.remove(council.code)
        return AuthoritySearchResult()

    def search_planit(self, council, start_date, end_date, cancel_event):
        with self.lock:
            self.planit_active += 1
            self.max_planit_active = max(self.max_planit_active, self.planit_active)
            self.first_planit_started.set()
            if self.planit_active > 1:
                self.planit_overlap.set()
        self.release_planit.wait(2)
        with self.lock:
            self.planit_active -= 1
        return AuthoritySearchResult()
```

Run a ten-council service in a background test thread, wait for `eight_started`, assert `max_active == 8`, and release the primary searches. Wait for `first_planit_started`, assert `planit_overlap.wait(0.1)` is false, release PlanIt, join the thread, and assert `max_planit_active == 1`, `duplicate_council is False`, and all ten councils were searched.

- [ ] **Step 3: Write a failing durability test that observes the database before the run finishes**

Use two councils. Alpha returns from both phases immediately; beta blocks in primary on an event. Run the service in a test thread and set `alpha_finished` from the emitted `council_finished` event:

```python
self.assertTrue(alpha_finished.wait(2))
self.assertTrue(beta_searcher.beta_entered.is_set())
self.assertEqual(
    [("alpha", "success")],
    list(
        self.database.connection.execute(
            "SELECT c.code, o.outcome_status "
            "FROM council_search_outcomes o "
            "JOIN councils c ON c.id=o.council_id"
        )
    ),
)
self.assertTrue(search_thread.is_alive())
beta_searcher.release_beta.set()
search_thread.join(2)
```

This assertion must occur while beta is still blocked, proving that alpha was not retained for an end-of-run batch.

Wrap `save_council_result` and `emit` in this test to record
`threading.get_ident()`, and record the thread identifiers inside both searcher
methods. After joining, assert every save/event identifier equals the service
coordinator thread identifier and no save/event identifier appears in the set
of network-phase thread identifiers. This proves worker threads never write
SQLite or publish UI events.

- [ ] **Step 4: Run orchestration tests and verify serial orchestration fails the concurrency tests**

Run: `uv run --with pytest pytest tests/backend/test_orchestration.py -q`

Expected: FAIL because the current service runs one primary at a time and delays alpha's save.

- [ ] **Step 5: Implement council state and the executor-owned phase loop**

Expand `_CouncilState` to hold these exact fields:

```python
@dataclass(slots=True)
class _CouncilState:
    council: Council
    started_at: datetime | None = None
    primary: AuthoritySearchResult | None = None
    planit: AuthoritySearchResult | None = None
    primary_error: Exception | None = None
    planit_error: Exception | None = None
    rate_limit_attempts: dict[str, int] = field(default_factory=dict)
    saved: bool = False
```

Add one coordinator-owned counter object so helper methods update a single
source of aggregate truth:

```python
@dataclass(slots=True)
class _RunCounters:
    completed: int = 0
    saved: int = 0
    empty: int = 0
    failed: int = 0
```

Initialize one state per selected council and enqueue one primary `CouncilPhaseTask` per state. Construct the executor with:

```python
worker_count = min(self._worker_limit, len(councils))
in_flight: dict[Future[AuthoritySearchResult], CouncilPhaseTask] = {}
with ThreadPoolExecutor(
    max_workers=worker_count,
    thread_name_prefix="planning-council",
) as executor:
    while scheduler.has_pending() or in_flight:
        self._submit_ready_phases(
            executor,
            scheduler,
            in_flight,
            states,
            request,
            emit,
            run_id,
            completed,
            len(councils),
            cancel_event,
        )
        completed_futures = self._wait_for_progress(
            tuple(in_flight), scheduler, cancel_event
        )
        for future in completed_futures:
            task = in_flight.pop(future)
            scheduler.release(task)
            self._handle_phase_result(
                task,
                future,
                scheduler,
                states,
                request,
                uploaded_geometries,
                emit,
                run_id,
                counters,
                cancel_event,
            )
```

If `councils` is empty, skip executor construction and finish the run through
the existing successful zero-council path; `ThreadPoolExecutor` must never be
constructed with zero workers.

Implement the named helpers with these boundaries:

- `_run_phase(task, state, request, cancel_event) -> AuthoritySearchResult` calls only the selected searcher method.
- `_submit_ready_phases` runs only on the coordinator and emits the first `council_started` event before submitting a primary phase.
- `_handle_phase_result` stores the result/error, queues PlanIt after primary, and calls `_finalize_council` after PlanIt.
- `_finalize_council` performs reconciliation/filtering, calls `save_council_result` once, updates a mutable `_RunCounters`, and emits `application_saved`, terminal `warning`, and `council_finished` in that order.
- `_wait_for_progress` calls `wait(tuple(in_flight), timeout=timeout, return_when=FIRST_COMPLETED)` when futures exist. It does not perform database or UI work.

Use the scheduler's PlanIt eligibility rule instead of relying on the HTTP semaphore, so PlanIt tasks never occupy multiple executor threads. Keep `_problem_for_sources`, `_matching_applications`, `_run_has_warnings`, and the existing outcome rules, adapting them to the expanded state.

For the existing cancellation tests, construct the service with
`worker_limit=1` to keep their deliberately serial fixtures deterministic. In
the coordinator, cancellation stops new submissions, drains active futures,
does not enqueue another phase, and saves each started unsaved state once as
`cancelled`. Task 6 adds the multi-worker distinction between active, ready,
and rate-limit-deferred states.

- [ ] **Step 6: Run orchestration and persistence tests**

Run: `uv run --with pytest pytest tests/backend/test_orchestration.py tests/backend/test_persistence_queries.py -q`

Expected: PASS.

- [ ] **Step 7: Commit concurrent per-council persistence**

```bash
git add src/planning_ping/backend/orchestration.py tests/backend/test_orchestration.py
git commit -m "feat: search up to eight councils concurrently"
```

---

### Task 5: Deferred Rate-Limit Retries and Existing-Event UI Status

**Files:**
- Modify: `src/planning_ping/backend/orchestration.py`
- Modify: `src/planning_ping/ui/controllers.py:126-136`
- Test: `tests/backend/test_orchestration.py`
- Test: `tests/ui/test_search_controller.py:21-90`

**Interfaces:**
- Consumes: `CouncilRateLimitError`, `fallback_retry_delay`, and scheduler deadline/cooldown APIs.
- Produces: 429 phase retry with an exact monotonic `ready_at`, scoped provider cooldown, configured attempt exhaustion, and no executor-thread sleep.
- Produces: existing `council_started` events whose optional `message` carries deferred/retrying status.
- Preserves: the `SearchEventKind` literal set; no new contract value is introduced.

- [ ] **Step 1: Write a failing test proving a 429 frees a worker for another council**

Create a fake searcher with `worker_limit=2`: alpha primary succeeds, beta primary blocks, alpha PlanIt raises one `CouncilRateLimitError(retry_after_seconds=0.0, retry_limit=2)`, and gamma records entry into primary. Assert gamma enters before beta is released, then release beta and assert alpha PlanIt is called twice and completes successfully.

Use events rather than sleeps for the ordering assertion:

```python
self.assertTrue(searcher.alpha_rate_limited.wait(2))
self.assertTrue(
    searcher.gamma_primary_started.wait(2),
    "the deferred alpha retry kept a worker from processing gamma",
)
self.assertTrue(search_thread.is_alive())
searcher.release_beta.set()
search_thread.join(2)
self.assertEqual(2, searcher.planit_calls["alpha"])
```

- [ ] **Step 2: Write failing tests for retry exhaustion and saved primary data**

Make PlanIt always raise a zero-delay `CouncilRateLimitError` with `retry_limit=2`, while primary returns application `A1`. Assert:

```python
self.assertEqual(3, searcher.planit_calls["alpha"])
self.assertEqual("completed_with_issues", summary.status)
self.assertEqual(1, summary.saved_applications)
self.assertEqual(
    ("warning", "A1"),
    self.database.connection.execute(
        "SELECT o.outcome_status, a.reference "
        "FROM council_search_outcomes o "
        "JOIN search_run_applications sra ON sra.run_id=o.run_id "
        "JOIN applications a ON a.id=sra.application_id"
    ).fetchone(),
)
```

Assert emitted `council_started` messages contain both `retrying in` and `Retrying PlanIt`, and that only one `council_finished` event exists for alpha.

Add a companion primary-phase case: the first primary call raises a zero-delay
rate limit, the second returns `A1`, and PlanIt returns empty. Assert two primary
calls, one PlanIt call, one saved `A1`, and one council outcome. This proves a
primary retry restarts only its phase and remains idempotent at persistence.

- [ ] **Step 3: Write a failing UI test for coordinator-supplied status text**

```python
def test_council_started_uses_coordinator_status_message_when_present(self) -> None:
    controller = SearchController(FakeSearchService(summary()), lambda state: None)
    controller._apply_event(
        SearchEvent(
            "council_started",
            council="Alpha",
            completed=1,
            total=3,
            message="Alpha paused by PlanIt rate limit; retrying in 294 seconds",
        )
    )

    self.assertEqual(
        "Alpha paused by PlanIt rate limit; retrying in 294 seconds",
        controller.state.status_message,
    )
```

- [ ] **Step 4: Run the focused orchestration and UI tests and verify they fail**

Run: `uv run --with pytest pytest tests/backend/test_orchestration.py tests/ui/test_search_controller.py -q`

Expected: FAIL because rate limits are currently treated as terminal phase errors and the UI ignores `event.message`.

- [ ] **Step 5: Implement coordinator-owned deferral, cooldown, and retry exhaustion**

In `_handle_phase_result`, catch `CouncilRateLimitError` separately before generic exceptions. Release the scheduler's active marker before calling this handler. Count attempts by phase, and use this exact rule:

```python
attempt = state.rate_limit_attempts.get(task.phase, 0) + 1
state.rate_limit_attempts[task.phase] = attempt
if attempt > error.retry_limit:
    self._record_phase_error(state, task.phase, error)
    self._queue_next_or_finalize(task, state, scheduler)
    return

delay = (
    error.retry_after_seconds
    if error.retry_after_seconds is not None
    else fallback_retry_delay(attempt, self._jitter)
)
ready_at = self._monotonic_clock() + max(delay, 0.0)
scheduler.set_scope_cooldown(error.scope, ready_at=ready_at)
scheduler.defer(task, ready_at=ready_at)
state.deferred_phase = task.phase
emit(
    SearchEvent(
        kind="council_started",
        run_id=run_id,
        council=state.council.name,
        completed=counters.completed,
        total=total,
        saved_count=counters.saved,
        message=(
            f"{state.council.name} paused by {self._phase_label(task.phase)} "
            f"rate limit; retrying in {ceil(delay)} seconds"
        ),
    )
)
```

Add `deferred_phase: str | None = None` to `_CouncilState`. Clear it when that
phase is submitted again. When a deferred phase becomes eligible, emit
`Retrying Primary portal for Alpha Council` or `Retrying PlanIt for Alpha
Council` through the same existing event kind, substituting the current council
name. Do not clear completed primary/PlanIt results when retrying the other
phase.

Update `_wait_for_progress`:

- if futures exist, wait for the first completion with a timeout no greater than 0.25 seconds or the nearest scheduler deadline;
- if only deferred/cooldown work remains, call `cancel_event.wait(timeout)` so cancellation wakes the coordinator immediately;
- after every return, promote/check scheduler work using the injected monotonic clock;
- never call `sleep` for a rate-limit deadline.

- [ ] **Step 6: Display supplied council status without adding a new event kind**

Change the `council_started` controller branch to:

```python
status_message = event.message or f"Searching {event.council or 'council'}…"
changes.update(
    current_council=event.council or "",
    completed_councils=event.completed,
    total_councils=event.total,
    status_message=status_message,
)
```

- [ ] **Step 7: Run the focused tests**

Run: `uv run --with pytest pytest tests/backend/test_orchestration.py tests/backend/test_http_scheduler.py tests/ui/test_search_controller.py -q`

Expected: PASS.

- [ ] **Step 8: Commit deferred retry behavior**

```bash
git add src/planning_ping/backend/orchestration.py src/planning_ping/ui/controllers.py tests/backend/test_orchestration.py tests/ui/test_search_controller.py
git commit -m "fix: defer rate limited councils without blocking workers"
```

---

### Task 6: 403 Fallback, Cancellation Durability, and Full Regression Verification

**Files:**
- Modify: `src/planning_ping/backend/orchestration.py`
- Modify: `tests/backend/test_orchestration.py`
- Modify only if a regression is exposed: `tests/test_shutdown_integration.py`

**Interfaces:**
- Consumes: `CouncilAccessBlockedError` from Task 1 and all coordinator interfaces from Tasks 4-5.
- Produces: primary 403 to PlanIt fallback, PlanIt 403 to primary-only finalization, cancellation that drains active futures, and save-at-most-once persistence.
- Preserves: completed council rows across later failures/cancellation and the existing shutdown service boundary.

- [ ] **Step 1: Write a failing 403 fallback integration test**

Raise this primary error for alpha and return `A1` from PlanIt:

```python
CouncilAccessBlockedError(
    url="https://alpha.test/search",
    category="network_filter",
    reason="Local network content filter blocked this council portal",
)
```

Assert the search completes with issues, saves `A1`, records a `warning` council outcome rather than `error`, sets `failed_councils == 0`, emits one warning containing `local network content filter`, and makes exactly one primary and one PlanIt call.

Add the reverse case: primary returns `A1`, PlanIt raises an access block, and the same warning outcome retains `A1`.

- [ ] **Step 2: Write a failing cancellation/save-once test around active and deferred states**

Wrap `database.save_council_result` with a recording function before starting the service. Arrange alpha to complete normally, beta to return from an active primary only after cancellation, and gamma to be deferred on a future retry deadline. Set cancellation after alpha's `council_finished` event and gamma's deferral event.

Assert after join:

```python
self.assertEqual("cancelled", summary.status)
self.assertEqual(1, saved_codes.count("alpha"))
self.assertLessEqual(saved_codes.count("beta"), 1)
self.assertNotIn("gamma", saved_codes)
self.assertEqual(
    "success",
    self.database.connection.execute(
        "SELECT o.outcome_status FROM council_search_outcomes o "
        "JOIN councils c ON c.id=o.council_id WHERE c.code='alpha'"
    ).fetchone()[0],
)
```

Beta may be saved once as `cancelled` only if its active phase returns a result during cooperative cancellation. Gamma remains unfinished because its deferred phase never resumes. Also assert the terminal search row has `status='cancelled'` and a non-null `finished_at`.

- [ ] **Step 3: Run orchestration and shutdown integration tests**

Run: `uv run --with pytest pytest tests/backend/test_orchestration.py tests/test_shutdown_integration.py -q`

Expected before cancellation handling is complete: the new save-once/deferred assertion fails. Existing shutdown tests must remain passing or skip only for unavailable Tk.

- [ ] **Step 4: Finalize cancellation and terminal-source handling**

On cancellation, stop calling `scheduler.acquire`, leave never-started states
and states whose `deferred_phase` is still set unsaved, and continue draining
futures that were active when cancellation was observed. A council that had
started and was merely ready for its next non-deferred phase may retain the
existing cancelled-outcome behavior. For each drained future:

- retain a returned result or exception in its council state;
- finalize that active council once with outcome `cancelled` when it has usable partial state;
- never enqueue its next phase;
- skip any state whose `saved` flag is already true.

After active futures drain, call `finish_search` with status `cancelled`, preserving counters from councils already finalized. The executor context must exit before `run` returns so application shutdown cannot close SQLite while a council worker is still active.

Structured access-block errors require no retry-specific branch: record them as the applicable source error, continue to the other source when primary fails, and let `_problem_for_sources` preserve the actionable message in the saved warning.

- [ ] **Step 5: Run the entire repository suite**

Run: `uv run --with pytest pytest -q`

Expected: 121 tests plus all newly added tests pass. If the known Tk/shutdown integration test fails in the full run, rerun that exact node alone; accept it as environmental only when the isolated rerun passes and every scheduler-related test remains green.

- [ ] **Step 6: Verify source constraints and branch cleanliness**

Run:

```bash
git diff --check
git status --short
git log --oneline origin/main..HEAD
```

Expected: no whitespace errors; only intentional implementation/test changes are present before the final commit; history contains the design, plan, and focused implementation commits.

- [ ] **Step 7: Commit the integration boundary**

```bash
git add src/planning_ping/backend/orchestration.py tests/backend/test_orchestration.py tests/test_shutdown_integration.py
git commit -m "test: verify durable concurrent council searches"
```

- [ ] **Step 8: Push and verify the remote development branch**

```bash
git push origin development
git ls-remote --heads origin development
```

Expected: the remote `development` SHA matches local `HEAD`. Do not merge `development` into `main` until the implementation has been reviewed and explicitly approved.
