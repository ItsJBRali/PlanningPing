# PlanningPing

PlanningPing is a Windows desktop tool for finding and locally saving planning applications across England, Wales, and Scotland.

## Run from source

Python 3.11 and [uv](https://docs.astral.sh/uv/) are required:

```powershell
uv --system-certs run --link-mode copy --python 3.11 python -m planning_ping
```

The SQLite database is created at `%LOCALAPPDATA%\PlanningPing\applications.sql` and retained between launches.

## Test and audit

Run the test suite with:

```powershell
uv --system-certs run --link-mode copy --python 3.11 python -m unittest discover -s tests -v
```

Generate complete offline authority audits without making council requests:

```powershell
uv --system-certs run --link-mode copy --python 3.11 python tools/coverage_audit.py --format json --report coverage-audit.json
uv --system-certs run --link-mode copy --python 3.11 python tools/coverage_audit.py --format csv --report coverage-audit.csv
```

An optional bounded live reachability smoke is opt-in; it is not an exhaustive council search:

```powershell
uv --system-certs run --link-mode copy --python 3.11 python tools/coverage_audit.py --live-smoke --limit 10 --timeout 10 --report coverage-live-smoke.json
```

## Build the Windows executable

Install the development extra and build the windowed application from the checked-in specification:

```powershell
uv --system-certs run --link-mode copy --python 3.11 --extra dev pyinstaller --clean --noconfirm PlanningPing.spec
```

The executable is written to `dist\PlanningPing.exe`.

## Scope

PlanningPing stores application details and planning-document metadata/links, but never downloads planning-document payloads. Northern Ireland, customer storage, application-sending workflows, authentication, and cloud hosting are outside v1. The Send Applications screen is therefore a non-functional placeholder.
