# Phase 03 — Production Closure & Core-Integration Readiness

## Objective

Finish Studio as a production-grade standalone local application **and build the Studio-side lifecycle contract required for later PFETL Core integration**, without implementing the Core button itself yet.

After this phase, PFETL Core should eventually need only to:

```text
start Studio child process
wait for ready
open browser
show status
stop child process
```

Studio must own the rest.

---

## 1. Extract server launch configuration from the CLI

### Current state

`cli.py` currently performs:

```text
parse args
validate loopback
load profile
create_app()
ui.run(...)
```

directly, with default `127.0.0.1:8080`.

That is fine for PyPI but not yet a stable lifecycle contract for another application.

### Correct approach

Introduce a small server/runtime module, for example:

```text
server.py
```

with typed configuration such as:

```python
@dataclass(frozen=True)
class StudioServerConfig:
    host: str
    port: int | None
    settings_path: Path | None
    rules_path: Path | None
    open_browser: bool
    reload: bool = False
    runtime_file: Path | None = None
```

and one blocking entrypoint:

```python
run_server(config: StudioServerConfig) -> int
```

The CLI becomes argument parsing + configuration + `run_server()`.

Do not expose NiceGUI internals to PFETL Core.

---

## 2. Add deterministic loopback port resolution

### Problem

A fixed port `8080` can collide with another local service or a second Studio process.

### Correct approach

Support:

- explicit requested port: fail clearly if unavailable;
- automatic port mode: prefer 8080, then find an available loopback port.

Keep binding loopback-only using the existing `validate_loopback_host()` security rule.

Do not bind `0.0.0.0` automatically.

Expose the resolved host/port through runtime metadata.

---

## 3. Add health/readiness endpoint

PFETL Core integration and standalone packaging need a machine-readable readiness check.

Add a minimal local endpoint, for example:

```text
GET /healthz
```

Return a small payload:

```json
{
  "status": "ready",
  "app": "pfetl-analytics-studio",
  "version": "...",
  "host": "127.0.0.1",
  "port": 8080
}
```

The endpoint must not expose financial data, paths, settings, rules, tokens or database details.

A second deep diagnostic endpoint is unnecessary.

The normal UI already contains operational diagnostics.

---

## 4. Add optional runtime metadata file

For child-process orchestration, Studio should optionally write a tiny runtime file after successful startup configuration.

Suggested fields:

```json
{
  "pid": 12345,
  "host": "127.0.0.1",
  "port": 8080,
  "version": "1.0.x",
  "started_at": "...",
  "health_url": "http://127.0.0.1:8080/healthz"
}
```

Requirements:

- stored only in a Studio-owned runtime/temp location;
- written atomically;
- contains no financial/profile secrets;
- removed on normal shutdown where practical;
- stale files are detectable because PID/health check no longer resolves.

This is a transport contract for future PFETL orchestration, not a new state database.

---

## 5. Make the server process-friendly

The later PFETL Core UI will launch Studio as a child process.

Studio must therefore behave well under normal process termination.

Verify:

- `SIGTERM` / normal Windows process termination closes the server cleanly;
- state-store writes are not left half-finished;
- generated temp exports can be cleaned later;
- runtime metadata is cleaned or safely considered stale;
- no non-daemon helper thread prevents shutdown;
- `--no-browser` works reliably.

Do not attempt to embed NiceGUI into the Tkinter process or run its event loop in a Tk thread.

The future architecture remains:

```text
PFETL Core Desktop
        ↓
Studio child process
        ↓
NiceGUI local server
        ↓
default browser
```

---

## 6. Keep profile passing explicit

The current CLI accepts:

```text
--settings
--rules
```

and requires both together.

Preserve this contract.

For future Core integration, PFETL will pass its currently active Settings and FinancialRules paths to Studio at launch.

Studio should:

- validate both paths;
- load the profile normally;
- record it as recent if appropriate;
- remain read-only toward PFETL files.

Do not add an IPC mechanism or shared in-memory state between Core and Studio.

If Core later changes active profile, the simplest safe behavior is to restart/relaunch Studio with the new pair unless the integration spec deliberately chooses otherwise.

---

## 7. Add startup/launch diagnostics

A packaged/local server failure must be understandable.

Before `ui.run()`:

- validate loopback host;
- resolve/check port;
- validate profile pair if supplied;
- validate Studio state directory is writable;
- surface clear errors for unavailable required dependencies.

Return non-zero CLI status on startup failure.

Keep messages suitable for future capture by PFETL Core when it launches the child process.

---

## 8. Add packaging-readiness checks without packaging yet

Do not build PyInstaller/installer artifacts in this phase.

But remove avoidable surprises:

### Resource ownership
Verify every runtime asset is resolved from package-owned paths or installed-package resources, not repository-relative working-directory assumptions.

### Writable paths
All mutable state must use platform/user directories (`platformdirs`) or explicit Studio-owned temp/export paths.

### Browser/static export dependency
Kaleido 1.x may require Chrome/Chromium. Add a lightweight capability check used by export UI/diagnostics so static-image/PDF/PPTX chart export can report availability before a long export fails.

Do **not** decide yet whether a packaged distribution will bundle Chromium. That belongs in the packaging plan after certification.

### Optional export degradation
If the required browser engine is unavailable:

- normal Studio analytics must still run;
- formats that require static chart rendering should fail clearly or disable themselves;
- do not make Studio startup depend on Chrome.

---

## 9. Bound and clean temporary/export files

Create one clear Studio-owned temp/export policy for:

- large CSV exports;
- report artifacts;
- static chart images;
- retained-payload materialization;
- server runtime metadata.

Requirements:

- no writes into PFETL source/database directories;
- collision-safe filenames;
- cleanup of stale temporary files;
- keep explicit user exports when the user intentionally chose a destination;
- cleanup behavior must be safe after crashes.

---

## 10. Add lifecycle tests

Add focused tests for the new integration-ready server contract.

### Host/security
- loopback hosts accepted;
- remote hosts rejected unless the existing development override is set.

### Port
- explicit available port accepted;
- explicit occupied port fails clearly;
- auto-port resolves a free loopback port.

### Runtime metadata
- contains only allowed fields;
- written atomically;
- stale metadata is detectable;
- cleanup is safe.

### Health
- `/healthz` returns ready/version information;
- contains no profile/financial secrets.

### CLI
- `--settings` and `--rules` must still be paired;
- `--no-browser` works;
- startup failure returns non-zero.

### Process smoke
Where practical in CI/local tests:

```text
spawn Studio
→ poll /healthz
→ terminate
→ process exits
```

Use a temporary profile/fixture and a free loopback port.

---

## 11. Final production certification suite

Before declaring the program complete, run:

```text
ruff
mypy
pyright
pylint
pytest
```

plus a real-data manual smoke:

```text
load real PFETL profile
Overview
Household
Wealth
Investments
Tax
FIRE
Operations
Explorer
Reports

run representative filters/drills
generate representative exports
verify read-only PFETL files
```

Also verify:

- PFETL running/locked state is handled safely;
- PFETL completes a newer run while Studio is open and freshness refresh works;
- Studio restart preserves only intended local preferences/state;
- no page introduces a hidden write to PFETL storage.

---

## 12. Performance certification

Measure representative user flows rather than microbenchmarking every function.

Record at least:

- cold Studio startup to usable shell;
- Overview first load;
- Household Income first load;
- Investments Overview first load;
- Lot Explorer first load;
- Operations Runs first load;
- Forensics first load for a representative large JSONL;
- Explorer Catalog first load;
- Guided query execution;
- representative report preview;
- representative PDF/XLSX/PPTX export.

Acceptance is primarily behavioral:

- no visible event-loop freeze;
- tab switches remain responsive;
- unopened tabs do no work;
- memory does not spike because of unnecessary full-result byte copies;
- repeated/cached navigation is materially faster than first load.

Do not invent performance optimization after this unless profiling identifies a real bottleneck.

---

## 13. Explicit non-goals

Do not implement yet:

- PyInstaller/Nuitka/installer design;
- combined PFETL + Studio distribution;
- PFETL Tkinter Start/Stop Studio button;
- cross-process IPC beyond launch arguments/runtime health metadata;
- auto-updater;
- Windows service;
- system tray app;
- authentication;
- remote-network hosting;
- new analytics pages;
- README/branding overhaul.

Those are later steps after certification.

---

## 14. Exit criteria

Phase 03 is complete when:

- Studio can be launched through a typed server runtime entrypoint;
- port collisions are handled;
- `/healthz` reliably indicates readiness;
- optional runtime metadata makes child-process orchestration deterministic;
- `--no-browser` and explicit profile launch are stable;
- shutdown is process-friendly;
- temporary/export paths are bounded and package-safe;
- static-export browser capability is diagnosable;
- all quality gates and domain/report tests are green;
- real-data smoke confirms usability and read-only behavior;
- the application is ready for a fresh certification archaeology.

At that point stop.

The next work is **certification first**, then a separate packaging plan and a separate PFETL Core integration specification.
