# PlanningPing Committed Windows Executable Design

## Objective

Build the complete PlanningPing Windows desktop application from the newest
branch tip and commit the resulting portable executable directly to that same
branch at `dist/PlanningPing.exe`.

The target branch is `claude/planningping-v1-plan-rva65f`, whose newest observed
tip before this specification was written was commit `993aec5`. The build must
use the source and packaging configuration from the checked-out branch tip so
that the executable contains every feature currently present there.

## Existing Architecture

PlanningPing is a Python 3.11 desktop application. The CustomTkinter front end
and the planning-search, persistence, scheduling, filtering, and portal-adapter
back end are composed by `src/planning_ping/__main__.py`. The production service
factory creates the SQLite-backed services, passes them to the UI, and closes
them after the UI drains its workers.

`PlanningPing.spec` is the existing PyInstaller definition for a single,
windowed Windows executable. It includes the application composition root, the
planning-authority GeoJSON data, Tk/TkinterDnD runtime assets, and Selenium
browser imports. Reusing this definition preserves the current application
boundary without introducing a second packaging path.

## Build and Artifact

The build runs on Windows with Python 3.11 and the repository's declared project
and development dependencies. PyInstaller builds `PlanningPing.spec` in clean,
non-interactive mode and writes one user-facing artifact:

`dist/PlanningPing.exe`

The committed `dist` folder contains only that executable. PyInstaller's
intermediate `build` directory and other generated files remain uncommitted.
Because `dist/` is intentionally ignored for ordinary development, the verified
executable is explicitly force-added to Git for this requested publication.

No installer, auto-updater, code signing, source refactor, or new application
feature is included. The executable remains the repository's current portable,
Windows-first distribution format.

## Runtime Data Flow

Starting `PlanningPing.exe` constructs the real production services and opens
the existing desktop UI. Searches flow from the UI controllers into the backend
search orchestration and council portal adapters. Saved applications, search
runs, and issues persist in `%LOCALAPPDATA%\PlanningPing\applications.sql`, so
the executable can remain self-contained while user data survives app upgrades.

The build bundles planning-authority reference data and runtime libraries, but
does not bundle or download planning-document payloads. Browser-assisted portal
access continues to rely on the app's existing Selenium behavior and an
available supported browser on the user's Windows system.

## Verification

Before committing the binary:

1. Run the complete repository test suite from the target branch.
2. Build the executable from `PlanningPing.spec` and require a successful exit.
3. Run `dist/PlanningPing.exe` with `PLANNINGPING_SMOKE_TEST=1` and an isolated
   temporary `LOCALAPPDATA` directory.
4. Require the executable to exit successfully and create
   `PlanningPing/applications.sql` in that isolated directory.
5. Confirm `dist/PlanningPing.exe` is the only committed file under `dist/`.
6. Record the executable's byte size and SHA-256 checksum in the final handoff.

The smoke test exercises the frozen front-end startup, packaged runtime assets,
production service composition, database initialization, and orderly shutdown.
The automated test suite covers the current backend adapters, persistence,
queries, UI controllers and models, packaging hooks, and integration boundary.

## Failure Handling

If tests, the PyInstaller build, or the frozen smoke test fail, the executable is
not committed. Any packaging defect is reproduced with a failing automated test
before a source or build-definition fix is made.

If `PlanningPing.exe` exceeds GitHub's 100 MB per-file limit, work stops and the
constraint is reported. The artifact is not silently moved to Git LFS, a GitHub
Release, or an Actions-only download because the approved requirement is to
commit it directly to the branch.

## Publication

After verification, commit `dist/PlanningPing.exe` directly on
`claude/planningping-v1-plan-rva65f` and push that branch to `origin`. The remote
branch is then checked to confirm that its new tip contains the executable at
the requested path.
