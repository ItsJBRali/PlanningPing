# PlanningPing Concurrent Council Search Design

## Objective

Change PlanningPing so a search processes different councils concurrently with
at most eight workers, saves each council as soon as that council reaches a
terminal outcome, and handles HTTP 429 and 403 responses without stalling the
whole search or repeatedly hammering a blocked service.

The work will be developed on `development`, branched directly from the current
`origin/main` tip at commit `0e71e6703b4963828a03bc819c3e4aff075a6537`.

## Current Behaviour

`PlanningSearchService.run` currently performs every council's primary portal
search first, retaining those results in memory. It then performs PlanIt
fallback searches and saves councils in a second pass. Although
`save_council_result` already commits one council in one SQLite transaction,
the two-pass orchestration delays the first durable save until all primary
searches have completed.

Network retries currently happen inside `CouncilHttpClient`. A worker sleeps
while waiting and the client truncates `Retry-After` to 20 seconds. PlanIt has
been observed returning a substantially longer valid `Retry-After`, so this
causes premature repeat requests and further 429 responses. A 403 response is
currently reported as a generic fetch failure. At least one observed primary
portal 403 is a local Streamline3 content-filter denial, which cannot safely be
bypassed by the application.

## Chosen Architecture

The existing UI search worker remains the owner of one search run. Inside that
worker, `PlanningSearchService` becomes a coordinator for a fixed
`ThreadPoolExecutor`. The pool size is `min(8, number_of_councils)`. Threads do
network-bound council phases; the coordinator alone advances council state,
emits progress events, and writes completed results to SQLite.

Each council is represented by independent state containing:

- its catalogue record;
- the next phase to run;
- primary and PlanIt results already obtained;
- warnings and terminal errors;
- rate-limit attempts per phase; and
- whether it has been finalized and saved.

Council work is split into resumable phases rather than submitting a whole
council as one long task:

1. `primary` searches the council's configured portal.
2. `planit` runs the existing PlanIt fallback/complementary search.
3. `finalize` reconciles, filters, records warnings, and saves the council.

Only network phases consume executor threads. Finalization is short and runs on
the coordinator. A phase may resume on a different thread, but two phases for
the same council are never active simultaneously.

This was chosen over whole-council executor jobs because a whole job would hold
a thread while waiting for PlanIt or a retry deadline. An asynchronous rewrite
would remove blocked threads too, but would require replacing or wrapping the
existing urllib and Selenium-based adapters. The phase coordinator delivers
the requested concurrency while retaining the established adapter boundary.

## Scheduling Model

The coordinator maintains four collections:

- a ready queue of council phases that can run now;
- a mapping of active futures to their council and phase;
- a set of councils currently executing, enforcing the distinct-council rule;
- a deadline-ordered heap of deferred phases.

It repeatedly promotes expired deferred phases, fills free worker slots from
the ready queue, and handles completed futures. It submits no more than eight
futures and never submits a phase for a council already executing.

PlanIt is a shared provider and retains its existing one-request-at-a-time
limit. The coordinator therefore submits at most one PlanIt phase concurrently
rather than allowing several executor threads to block on the existing PlanIt
semaphore. Other worker slots remain available for primary searches belonging
to different councils.

If work is ready but temporarily ineligible because of a provider cooldown, the
coordinator skips it and considers other ready councils. If all remaining work
is deferred, it waits only until the nearest deadline or cancellation signal.
Waiting is cancellation-responsive and does not busy-poll.

Completion and progress order may differ from catalogue order. Progress counts
are based on councils finalized and saved, which gives users an accurate view
of durable progress.

## Rate-Limit Handling

An HTTP 429 becomes a typed rate-limit result carrying the retry delay,
rate-limit scope, and configured retry allowance. `CouncilHttpClient` does not
sleep for a 429. Instead, the active phase returns control to the coordinator,
which calculates a monotonic deadline, places the phase in the deferred heap,
and immediately frees the worker for another council.

A valid server `Retry-After` value is honored in full, whether expressed as
seconds or an HTTP date. The current 20-second truncation is removed. If the
header is absent or invalid, the application uses bounded exponential backoff
with jitter. Time comparisons use a monotonic clock after calculating the
delay, so local clock changes cannot make a retry fire early or remain stuck.

Rate limits have a scope:

- a PlanIt 429 sets a provider-wide `planit` cooldown, preventing every pending
  council from immediately repeating the same request;
- a primary portal 429 uses the adapter's existing portal/platform rate key so
  related requests respect the same cooldown without stopping unrelated
  councils.

PlanIt retains its configured allowance of two retries after its initial
attempt. Primary adapters retain their existing configured retry allowances,
with the coordinator counting rate-limit deferrals against the applicable
phase. When that allowance is exhausted, the phase becomes a warning rather
than blocking the whole run. If primary search is exhausted, the council still
proceeds to PlanIt. If PlanIt is exhausted, the council finalizes with any
primary results already obtained.

PlanIt retries resume only the PlanIt phase and retain completed primary data.
The current primary adapter interface returns a council-level result rather
than a page-level checkpoint, so a rate-limited primary phase restarts that
phase. Database upserts and reconciliation prevent duplicate saved
applications. Page-level adapter checkpointing is outside this change.

The UI receives a concise deferred status including the council, provider, and
remaining wait at the time of deferral, followed by a retry status when the
phase becomes ready. A per-second UI animation is not required for scheduling;
the monotonic deadline remains authoritative.

## HTTP 403 Handling

HTTP 403 is not treated as a transient rate limit and is not blindly retried.
The HTTP layer classifies known block-page signatures, including the observed
Streamline3 content-filter response, separately from a generic access-denied
response. The recorded warning explains whether access appears to be blocked
by a local network filter, portal security/WAF, or an unspecified permission
denial.

A primary 403 ends only the primary phase. The council continues to PlanIt and
then saves any available applications with the warning attached. A PlanIt 403
ends only the PlanIt phase and finalizes with any primary result. The
application will not attempt CAPTCHA avoidance, proxy changes, or automated
bypass of access controls.

## Per-Council Persistence

When both applicable search phases have succeeded or reached terminal failure,
the coordinator reconciles and filters their results and calls the existing
`save_council_result` exactly once for that council. The existing transaction
continues to atomically upsert the council and applications, associate them
with the search run, and store the council outcome.

The coordinator performs SQLite writes serially. Worker threads never write to
the database or emit Tk/UI calls, avoiding database lock contention and UI
thread violations. Once a save commits, later council failures, cancellation,
or application shutdown cannot roll that completed council back.

Cancellation stops new scheduling and signals active adapters through the
existing cancellation event. Futures are drained before shared services close.
Any council that reaches a terminal outcome during cooperative cancellation is
saved once with the data and cancellation status available at that point.
Deferred councils that never resumed remain unfinished; already committed
councils remain durable. Persisting in-progress scheduler queues across an app
restart is outside this change.

## Component Changes

`src/planning_ping/backend/orchestration.py` will own the coordinator, council
phase state, ready/deferred queues, worker limit, phase transitions, and
single-threaded finalization. The existing public search-service entry point
and UI-facing event contract remain compatible, with additional status details
for deferred work.

`src/planning_ping/backend/http.py` will expose typed rate-limit and access-block
failures, parse `Retry-After` without the current truncation, and return 429
control to the coordinator instead of sleeping. Existing host and adaptive
concurrency gates remain defensive limits beneath the scheduler.

`src/planning_ping/backend/production.py` will preserve the primary and PlanIt
adapter APIs while ensuring typed deferral/access failures are not converted
into ordinary warnings before reaching the coordinator. PlanIt remains
globally single-flight.

`src/planning_ping/backend/persistence.py` requires no schema change. Its
existing per-council transaction is reused from the coordinator thread.

Tests will be extended primarily in `tests/backend/test_orchestration.py` and
the HTTP-client tests. Existing integration and persistence tests will protect
the public composition and durability boundaries.

## Verification

Deterministic tests will use controlled searchers, events, and clocks rather
than real network calls or long sleeps. They will prove that:

1. no more than eight council phases execute concurrently;
2. active workers always belong to different councils;
3. at most one PlanIt phase executes at a time;
4. a 429 releases its worker slot and other councils continue;
5. a deferred phase runs only after its deadline expires;
6. a PlanIt 429 holds other PlanIt work while primary work continues;
7. a full valid `Retry-After` value is honored and fallback backoff is bounded;
8. retry exhaustion degrades to a council warning and does not stop the run;
9. 403 block pages are classified without blind retry or bypass;
10. each council is saved immediately after finalization, before overall run
    completion;
11. each council is saved at most once despite retries or cancellation;
12. cancellation drains workers and preserves every committed council.

The complete repository suite will be run after the targeted tests. The known
baseline shutdown/Tk integration test is intermittent in this environment; a
failure there must be rerun alone and distinguished from any scheduler-related
regression.

## Scope Boundaries

This change does not add a user-configurable worker count, exceed eight council
workers, run multiple PlanIt requests concurrently, bypass 403 access controls,
persist partial phase checkpoints, or resume an unfinished scheduler after the
application restarts. It also does not change the SQLite schema or the meaning
of saved application records.
