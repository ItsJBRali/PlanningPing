# PlanningPing v1 Implementation Plan

> **For agentic workers:** Implement test-first. Backend and frontend work on separate branches rooted at the shared foundation commit.

**Goal:** Build a Windows desktop application that searches planning applications across England, Wales, and Scotland, persists normalized results to local SQLite, and provides the four specified CustomTkinter screens.

**Architecture:** One Python 3.11 process with dependency-injected service protocols. The backend owns authority selection, adapters, filtering, orchestration, and SQLite. The UI owns presentation and consumes only the frozen contracts.

**Tech Stack:** Python 3.11, CustomTkinter 6.0.0, tkinterdnd2 0.6.2, sqlite3, lxml, Selenium, unittest, PyInstaller.

## Global Constraints

- Windows-only v1; database path defaults to `%LOCALAPPDATA%/PlanningPing/applications.sql`.
- England, Wales, and Scotland are in scope; Northern Ireland is excluded.
- Search dates are inclusive and use received date, falling back to validated date.
- Nonblank exclusion phrases match only the application description, case-insensitively.
- Never download planning documents; retain document metadata and URLs.
- Primary portal data wins over serial PlanIt reconciliation data.
- No real customer storage, sending workflow, authentication, or cloud hosting.

### Task 1: Foundation and Frozen Contracts

Create the package skeleton, dependency metadata, ignore rules, test runner foundation, packaging skeleton, and `src/planning_ping/contracts.py`. Define `SearchRequest`, `SearchEvent`, `SearchSummary`, `ApplicationFilters`, `ApplicationRow`, `IssueRow`, `Page[T]`, the three service protocols, and `AppServices`. Normalize and validate contract inputs. Add contract tests first, prove RED, implement, prove GREEN, and commit on `search`.

### Task 2: Backend Search and Persistence

On `search`, reuse only relevant code and fixtures from `ItsJBRali/lead-generation` branch `codex/search-completeness`. Build focused catalogue, domain model, filtering, geometry, adapter, orchestration, persistence, query-service, and service-factory modules. Implement the specified schema and migrations, default LocalAppData path, upserts, documents, run history, outcomes, saved filters, cancellation/events, bounded completeness errors, respectful throttling, and serial PlanIt reconciliation. Remove lead-only inclusion and hard-coded exclusions. Add fixture and behavior tests test-first. Produce a coverage-audit command/report and packaging specification; do not run exhaustive live searches during unit tests.

### Task 3: CustomTkinter Frontend

On `frontend` rooted at Task 1's commit, change only `src/planning_ping/ui/`, `tests/ui/`, and the tkinterdnd2 hook. Build a resizable monochrome application with a sidebar, top bar, code-drawn sonar logo, home action cards, Search New Applications, Search Saved Applications, Send Applications placeholder, and View Issues. Use dependency injection, a worker thread, queue-based `SearchEvent` handling, cancellation, Tcl-safe drag/drop parsing, loading/error/empty states, pagination, sorting, and clickable URLs. Production customer data is empty; test fakes may contain sample data. Add deterministic tests first and commit.

### Task 4: Integration, Release Checks, and Publishing

Create `integration` from completed `search`, merge `frontend`, wire `create_services()` to the UI entrypoint, and resolve no contract divergence. Run the full unit suite, compile/import checks, coverage audit, PyInstaller build, packaged executable startup smoke test, LocalAppData persistence check, and no-document-download check. Run final review and fix blocking findings. Push `search`, `frontend`, and `integration`; leave `main` unchanged.
