# PlanningPing Committed Windows Executable Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, verify, commit, and push the complete portable PlanningPing Windows executable to `dist/PlanningPing.exe` on the repository's newest existing branch.

**Architecture:** Reuse the checked-in `PlanningPing.spec` so PyInstaller freezes the existing `planning_ping.__main__` composition root, front end, back end, reference data, and runtime dependencies into one windowed executable. Validate the source baseline first, then validate the frozen application against an isolated Windows profile before committing only the requested binary.

**Tech Stack:** Python 3.11, uv, unittest, PyInstaller, CustomTkinter, TkinterDnD2, SQLite, Selenium, PowerShell, Git.

## Global Constraints

- Target branch: `claude/planningping-v1-plan-rva65f`.
- User-facing artifact: `dist/PlanningPing.exe` only.
- Build with Python 3.11 and the repository's declared dependencies.
- Commit and push the executable directly to the target branch.
- Stop if the executable exceeds GitHub's 100 MB per-file limit; do not switch to Git LFS, Releases, or Actions-only publication without approval.
- Do not add an installer, auto-updater, code signing, refactor, or new application feature.
- If a source or packaging defect must be fixed, reproduce it with a failing automated test before changing production code or build configuration.

---

## File Map

- Existing build definition: `PlanningPing.spec` packages the application and runtime data.
- Existing composition root: `src/planning_ping/__main__.py` integrates production services and the desktop UI.
- Existing tests: `tests/` verifies backend, UI, integration, shutdown, and packaging behavior.
- Existing design: `docs/superpowers/specs/2026-08-16-dist-executable-design.md` defines the approved requirements.
- Create: `dist/PlanningPing.exe` as the only committed artifact under `dist/`.

### Task 1: Synchronize the Target Branch and Prove the Source Baseline

**Files:**
- Verify: `docs/superpowers/specs/2026-08-16-dist-executable-design.md`
- Verify: `PlanningPing.spec`
- Verify: `src/planning_ping/**/*.py`
- Test: `tests/**/*.py`

**Interfaces:**
- Consumes: Target branch `claude/planningping-v1-plan-rva65f` and its current remote tip.
- Produces: A synchronized, clean source baseline with Python 3.11 dependencies available and the full test suite passing.

- [ ] **Step 1: Fetch and synchronize without discarding the committed specification**

Run:

```powershell
git fetch origin
git branch --show-current
git rebase origin/claude/planningping-v1-plan-rva65f
git status --short --branch
```

Expected: the current branch is `claude/planningping-v1-plan-rva65f`; the local specification commit remains above the fetched remote tip; the worktree is clean. Stop on a rebase conflict rather than resolving unrelated remote changes speculatively.

- [ ] **Step 2: Materialize the locked Python 3.11 development environment**

Run:

```powershell
uv --version
uv --system-certs sync --link-mode copy --python 3.11 --extra dev
uv --system-certs run --link-mode copy --python 3.11 python --version
```

Expected: all commands exit successfully and the interpreter reports Python 3.11.x.

- [ ] **Step 3: Run the complete source test suite**

Run:

```powershell
uv --system-certs run --link-mode copy --python 3.11 python -m unittest discover -s tests -v
```

Expected: exit code 0 with no failed or errored tests. If a test fails, stop and diagnose the baseline before building.

### Task 2: Build and Validate the Frozen Application

**Files:**
- Consume: `PlanningPing.spec`
- Create: `dist/PlanningPing.exe`
- Ignore: `build/` and PyInstaller intermediate files

**Interfaces:**
- Consumes: The passing Python 3.11 source baseline from Task 1.
- Produces: A single frozen Windows executable that starts the real UI and backend composition, initializes its SQLite database, and exits cleanly in smoke mode.

- [ ] **Step 1: Build from the checked-in PyInstaller definition**

Run:

```powershell
uv --system-certs run --link-mode copy --python 3.11 --extra dev pyinstaller --clean --noconfirm PlanningPing.spec
```

Expected: exit code 0 and a newly generated `dist/PlanningPing.exe`.

- [ ] **Step 2: Enforce the artifact path, count, and GitHub size limit**

Run:

```powershell
$planningPingArtifacts = @(Get-ChildItem -LiteralPath dist -File -Recurse)
if ($planningPingArtifacts.Count -ne 1) { throw "Expected one dist artifact; found $($planningPingArtifacts.Count)" }
$planningPingExecutable = Get-Item -LiteralPath dist\PlanningPing.exe
if ($planningPingExecutable.Length -gt 100MB) { throw "PlanningPing.exe is $($planningPingExecutable.Length) bytes; it exceeds GitHub's 100 MB per-file limit" }
$planningPingExecutable | Select-Object FullName, Length, LastWriteTime
```

Expected: exactly one file exists below `dist/`, it is `PlanningPing.exe`, and it does not exceed GitHub's 100 MB per-file limit.

- [ ] **Step 3: Smoke-test the executable against an isolated application-data directory**

Run:

```powershell
$planningPingOriginalLocalAppData = $env:LOCALAPPDATA
$planningPingOriginalSmokeValue = $env:PLANNINGPING_SMOKE_TEST
$planningPingTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$planningPingSmokeRoot = Join-Path $planningPingTempRoot ("PlanningPingSmoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $planningPingSmokeRoot | Out-Null
try {
    $env:LOCALAPPDATA = $planningPingSmokeRoot
    $env:PLANNINGPING_SMOKE_TEST = "1"
    & .\dist\PlanningPing.exe
    if ($LASTEXITCODE -ne 0) { throw "PlanningPing.exe exited with $LASTEXITCODE" }
    $planningPingSmokeDatabase = Join-Path $planningPingSmokeRoot "PlanningPing\applications.sql"
    if (-not (Test-Path -LiteralPath $planningPingSmokeDatabase -PathType Leaf)) {
        throw "Expected smoke database at $planningPingSmokeDatabase"
    }
} finally {
    if ($null -eq $planningPingOriginalLocalAppData) { Remove-Item Env:\LOCALAPPDATA -ErrorAction SilentlyContinue } else { $env:LOCALAPPDATA = $planningPingOriginalLocalAppData }
    if ($null -eq $planningPingOriginalSmokeValue) { Remove-Item Env:\PLANNINGPING_SMOKE_TEST -ErrorAction SilentlyContinue } else { $env:PLANNINGPING_SMOKE_TEST = $planningPingOriginalSmokeValue }
    $planningPingResolvedSmokeRoot = [System.IO.Path]::GetFullPath($planningPingSmokeRoot)
    if (-not $planningPingResolvedSmokeRoot.StartsWith($planningPingTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove smoke directory outside the temporary root"
    }
    Remove-Item -LiteralPath $planningPingResolvedSmokeRoot -Recurse -Force
}
```

Expected: exit code 0, a versioned database is created under the isolated profile, and only the verified temporary directory is removed afterward.

- [ ] **Step 4: Record the artifact identity and stage only the executable**

Run:

```powershell
Get-Item -LiteralPath dist\PlanningPing.exe | Select-Object Length
Get-FileHash -Algorithm SHA256 -LiteralPath dist\PlanningPing.exe
git add -f -- dist/PlanningPing.exe
$planningPingStagedFiles = @(git diff --cached --name-only)
if ($planningPingStagedFiles.Count -ne 1 -or $planningPingStagedFiles[0] -ne "dist/PlanningPing.exe") {
    throw "Unexpected staged files: $($planningPingStagedFiles -join ', ')"
}
git diff --cached --stat
```

Expected: the size and SHA-256 are visible for the handoff, and `dist/PlanningPing.exe` is the only staged file.

### Task 3: Reverify, Commit, Push, and Confirm the Remote Artifact

**Files:**
- Commit: `dist/PlanningPing.exe`
- Preserve: all existing tracked source and documentation

**Interfaces:**
- Consumes: The staged, smoke-tested executable from Task 2.
- Produces: A remote target branch whose tip contains the same executable Git blob as the verified local commit.

- [ ] **Step 1: Run the fresh pre-commit verification gate**

Run:

```powershell
uv --system-certs run --link-mode copy --python 3.11 python -m unittest discover -s tests -v
git diff --cached --check
$planningPingPreCommitFiles = @(git diff --cached --name-only)
if ($planningPingPreCommitFiles.Count -ne 1 -or $planningPingPreCommitFiles[0] -ne "dist/PlanningPing.exe") {
    throw "Pre-commit scope changed: $($planningPingPreCommitFiles -join ', ')"
}
Get-FileHash -Algorithm SHA256 -LiteralPath dist\PlanningPing.exe
```

Expected: the complete suite passes with exit code 0, the staged diff has no integrity errors, the scope remains one executable, and the checksum matches Task 2.

- [ ] **Step 2: Commit the verified executable**

Run:

```powershell
git commit -m "build: add PlanningPing Windows executable"
git status --short --branch
git log -2 --oneline
```

Expected: one new binary commit follows the committed design specification, and no uncommitted tracked changes remain.

- [ ] **Step 3: Push the commits directly to the approved existing branch**

Run:

```powershell
git push origin HEAD:refs/heads/claude/planningping-v1-plan-rva65f
```

Expected: the push succeeds without force and updates `origin/claude/planningping-v1-plan-rva65f`.

- [ ] **Step 4: Verify the remote branch contains the exact committed artifact**

Run:

```powershell
git fetch origin
$planningPingLocalCommit = git rev-parse HEAD
$planningPingRemoteCommit = git rev-parse origin/claude/planningping-v1-plan-rva65f
if ($planningPingLocalCommit -ne $planningPingRemoteCommit) { throw "Remote tip does not match local tip" }
$planningPingLocalBlob = git rev-parse HEAD:dist/PlanningPing.exe
$planningPingRemoteBlob = git rev-parse origin/claude/planningping-v1-plan-rva65f:dist/PlanningPing.exe
if ($planningPingLocalBlob -ne $planningPingRemoteBlob) { throw "Remote executable does not match local executable" }
git ls-tree -l origin/claude/planningping-v1-plan-rva65f dist/PlanningPing.exe
git status --short --branch
```

Expected: local and remote commit IDs match, local and remote executable blob IDs match, the remote tree lists `dist/PlanningPing.exe`, and the branch reports no ahead/behind divergence.
