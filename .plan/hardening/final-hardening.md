# PFETL Analytics Studio — Final Closure & Standalone Packaging Plan

## Purpose

This document closes the remaining `1.0.3` hardening gaps and packages **PFETL Analytics Studio** as a polished standalone Windows application before the final README rewrite and `1.1.0` release.

The target product experience is:

```text
Launch PFETL Analytics Studio
        ↓
small tray application starts
        ↓
local NiceGUI server starts on loopback
        ↓
health check becomes READY
        ↓
browser opens Studio
        ↓
tray icon remains available for lifecycle control
```

The tray application should behave like a lightweight local service controller, similar in feel to OneDrive/Dropbox tray utilities.

This is **not** a redesign of Studio.

---

# 1. Product identity and logo decision

Use the **same PFETL logo** for Studio.

That is the right choice because Studio is not an unrelated product. It is the analytical companion to PFETL Core.

Recommended identity model:

```text
PFETL
├── Core
└── Analytics Studio
```

Use:

```text
/logo.ico
/logo.png
```

as the source assets for packaging.

The Studio repository may later use its own:

- GitHub social preview;
- README hero/banner;
- screenshots;
- analytical branding imagery.

The application icon itself should remain part of the same PFETL product family.

Do not create a different application logo merely to make Studio look separate.

---

# 2. Final hardening fix — remaining synchronous drill queries

## Issue

The main Studio pages now use `run.io_bound()`, but a few detail interactions still perform database work synchronously from NiceGUI callbacks.

Examples identified in `1.0.3` include:

```text
Wealth asset-detail history
Household transaction detail
Operations snapshot diff
```

## Correct fix

All database-backed UI actions must follow one rule:

```text
UI event
    ↓
loading/disabled state
    ↓
await run.io_bound(...)
    ↓
render result
```

For dialogs:

```text
click item
    ↓
open lightweight loading dialog
    ↓
background query
    ↓
populate dialog
```

Do not introduce async database APIs.

The analytics/data layers remain synchronous.

## Acceptance

No browser-triggered DuckDB/SQLite query should execute directly on the NiceGUI event loop.

---

# 3. Final hardening fix — Household full transaction export

## Issue

The export destination is now file-backed, but the full Household transaction dataset is still first materialized into Python memory before writing the CSV.

Current conceptual path:

```text
query all rows
→ Python result collection
→ presentation dictionaries
→ row tuple collection
→ CSV file
```

Memory therefore still scales with exported row count.

## Correct fix

Add a dedicated full-export path.

Preferred model:

```text
filtered database query
        ↓
chunked iterator / cursor
        ↓
CSV writer
        ↓
temporary Studio export file
```

If the final CSV columns can be expressed directly in SQL, DuckDB `COPY (SELECT ...) TO ...` is acceptable.

If presentation transformation is required, stream rows in bounded chunks.

### Invariant

```text
export memory usage ≠ proportional to total exported rows
```

Do not reuse the paginated UI-query function merely by setting an enormous limit.

---

# 4. Final hardening fix — Investment Lot full export

Apply the same rule to the Lot Explorer.

## Issue

The current full export obtains all lot rows before writing the file.

## Correct fix

Create a dedicated export method such as:

```text
InvestmentAnalytics.export_lots_csv(...)
```

or equivalent.

It should:

- apply the same active filters as the UI;
- preserve the exact export column semantics;
- stream/chunk directly to a Studio-owned temporary CSV;
- avoid constructing one complete Python list of lot rows.

The visible/paginated query remains unchanged.

---

# 5. Final hardening fix — retained source payload download

## Issue

Operations correctly identifies large retained payloads, but the complete SQLite BLOB is currently materialized into `bytes` before the UI decides whether to write it to a temporary file.

For a large payload this defeats the size-aware path.

## Correct fix

Use metadata first:

```text
payload size
    ↓
small?
    ├── yes → existing bytes path
    └── no  → stream BLOB directly to temporary file
```

For large SQLite payloads, use an incremental BLOB/read-stream approach where supported (`sqlite3.Connection.blobopen()` is suitable on modern Python).

Do not hold the complete large payload in memory.

Preserve:

- original filename;
- provenance;
- read-only upstream behavior.

---

# 6. Strengthen runtime liveness detection

## Current state

`is_runtime_metadata_live()` validates approximately:

```text
PID exists
+
port accepts connection
```

## Better definition

A Studio instance is live only when:

```text
PID exists
+
GET /healthz succeeds
+
app == "pfetl-analytics-studio"
+
status == "ready"
```

The helper should use a short timeout.

This strengthens:

- standalone tray status;
- stale runtime-file detection;
- future PFETL Core integration.

---

# 7. Standalone Windows packaging architecture

Do **not** turn the NiceGUI server itself into the visible desktop shell.

Use a small tray controller as the user-facing process.

Recommended runtime model:

```text
PFETL Analytics Studio Tray
        │
        ├── resolves Settings / FinancialRules
        ├── resolves/free port
        ├── starts Studio server child process
        ├── polls /healthz
        ├── opens browser
        ├── monitors child
        └── stops/restarts child
```

Server:

```text
Studio server child process
        ↓
NiceGUI
        ↓
127.0.0.1:<resolved_port>
```

This process boundary is also exactly what future PFETL Core integration needs.

---

# 8. Add a dedicated tray entrypoint

Add a small module such as:

```text
tray.py
```

or:

```text
desktop_tray.py
```

with a dedicated entrypoint:

```text
pfetl-studio-tray
```

For the packaged Windows application, this becomes the primary executable.

The existing CLI/server entrypoint remains available for:

```text
PyPI
development
automation
direct server launch
```

Do not remove the current CLI.

---

# 9. Recommended tray technology

Use a lightweight Windows-compatible tray library such as:

```text
pystray
+
Pillow
```

This is sufficient for the required lifecycle UI and avoids introducing a second desktop framework.

Do not add:

```text
PyQt
PySide
Electron
Tauri
```

just for the tray icon.

The tray host should remain tiny.

---

# 10. Tray menu design

Recommended right-click menu:

```text
PFETL Analytics Studio
────────────────────────
Open Studio
Status: Ready                [disabled informational row]
────────────────────────
Start Server                 [when stopped]
Stop Server                  [when running]
Restart Server
────────────────────────
Open Studio Data Folder
Open Logs / Runtime Folder
────────────────────────
Diagnostics
────────────────────────
Exit
```

## Open Studio

Open:

```text
http://127.0.0.1:<resolved_port>/
```

using the default browser.

If the server is stopped, either:

- start it and wait for readiness, then open; or
- disable Open Studio until ready.

Starting automatically is preferable.

## Status

Use simple states:

```text
Starting
Ready
Stopping
Stopped
Failed
```

The tray tooltip should also reflect status where the library allows it.

## Start Server

Start the child server using the normal server runtime contract.

## Stop Server

Gracefully terminate the owned child process.

Do not kill unrelated processes merely because they happen to use the same port.

## Restart Server

Stop the owned server, wait for exit, start again, health-check, then restore Ready.

## Open Studio Data Folder

Open the Studio `platformdirs` data directory.

Useful for:

```text
studio.sqlite
runtime metadata
exports
temporary artifacts
```

## Open Logs / Runtime Folder

If a dedicated logs/runtime directory exists, expose it.

If not, use the Studio data folder and avoid creating another directory only for this menu item.

## Diagnostics

Show a compact native/tray notification or browser diagnostic view containing:

```text
Studio version
server state
host
port
health status
active profile status
static-export capability
runtime directory
```

Do not display financial values.

## Exit

Stop the server child process owned by the tray application, then exit the tray host.

---

# 11. Tray startup behavior

Default packaged behavior:

```text
double-click executable
        ↓
tray appears
        ↓
server starts automatically
        ↓
wait for /healthz
        ↓
browser opens
```

Allow a harmless preference such as:

```text
open_browser_on_start = true
```

in Studio-owned preferences if useful.

The tray should continue running after the browser is closed.

Closing the browser must **not** stop Studio.

---

# 12. Prevent duplicate tray/server instances

Before starting a new server:

1. inspect runtime metadata;
2. verify it using `/healthz`;
3. determine whether the existing process belongs to Studio.

If a valid Studio server already exists:

```text
do not start another one
→ adopt/open the existing instance where safe
```

For the tray process itself, use a small single-instance guard on Windows if practical.

At minimum:

- prevent one tray instance from accidentally spawning multiple owned servers;
- avoid relying only on a fixed port.

---

# 13. Child-process launch contract

The tray must launch Studio through the same stable server contract built in `server.py`.

Pass explicit arguments/config for:

```text
host
port / auto-port
Settings path
FinancialRules path
no-browser
runtime metadata path
```

The server child should not know that the tray exists.

This keeps future PFETL Core integration simple.

---

# 14. Graceful shutdown and orphan handling

The tray owns the child server it launches.

On Stop/Exit:

```text
request graceful termination
        ↓
wait bounded period
        ↓
force terminate only if necessary
```

On next startup:

- detect stale runtime metadata;
- validate PID + `/healthz`;
- remove stale metadata safely.

If the tray crashes but the Studio server remains alive, the next tray launch should detect and recover/adopt or explicitly restart it.

Do not terminate arbitrary processes by PID without validating Studio identity.

---

# 15. Package resource handling

The source repository may keep:

```text
/logo.ico
/logo.png
```

at the root.

However, packaged runtime code must not assume the current working directory contains these files.

Add one resource resolver that works for:

```text
normal source checkout
installed wheel
PyInstaller bundle
```

For PyInstaller, support bundled resources through the generated runtime location (`sys._MEIPASS` or the appropriate packaged resource helper).

Use:

```text
logo.ico
```

for:

- executable icon;
- tray icon where appropriate.

Use:

```text
logo.png
```

for:

- tray libraries requiring raster input;
- future Studio README/UI branding where needed.

---

# 16. Packaging format recommendation

For the first enterprise-grade standalone package, prefer:

> **PyInstaller one-folder (`onedir`) distribution**

rather than `onefile`.

Why:

- faster startup;
- no large self-extraction on every launch;
- easier NiceGUI/runtime resource handling;
- easier debugging;
- easier static-export/browser dependency management;
- fewer antivirus false-positive patterns;
- cleaner server child-process launch.

A later installer can wrap the `onedir` distribution.

Do not optimize for “one EXE file” at the cost of reliability.

---

# 17. Executable layout

Recommended packaged output:

```text
PFETL Analytics Studio/
├── PFETL Analytics Studio.exe      # tray host
├── pfetl-studio-server.exe         # server child, if separate executable chosen
├── _internal/
│   └── packaged dependencies/resources
└── ...
```

Two acceptable approaches:

## Preferred

Two executables:

```text
Tray executable
Server executable
```

Advantages:

- very clear process ownership;
- server can be launched independently;
- future PFETL Core can launch the server executable directly;
- tray remains optional.

## Acceptable alternative

One executable with internal modes:

```text
PFETL Analytics Studio.exe --tray
PFETL Analytics Studio.exe --server ...
```

This reduces files but complicates startup/process semantics slightly.

For future Core integration, **separate tray + server executable is cleaner**.

---

# 18. PyInstaller specifications

Create explicit `.spec` files rather than relying on a long CLI command.

Suggested:

```text
packaging/
├── studio_server.spec
└── studio_tray.spec
```

The specs should:

- set the PFETL `logo.ico`;
- include `logo.png` where needed;
- collect required NiceGUI/static resources;
- include Plotly/Kaleido/python-pptx/ReportLab/XlsxWriter runtime modules as required;
- exclude development-only tooling;
- produce windowed/no-console tray executable;
- decide intentionally whether the server executable keeps a console.

For production user experience, both can be no-console if startup errors are persisted to a Studio-owned log.

---

# 19. Startup diagnostics for packaged builds

Because no console may be visible, packaging startup errors must not disappear.

Create a small Studio startup log under the user data/runtime directory.

Capture at minimum:

```text
timestamp
Studio version
startup mode
resolved host/port
profile validation result
server startup result
health readiness result
unexpected exception traceback
```

Do not log financial values or full configuration contents.

The tray's **Diagnostics** action can open or summarize this information.

---

# 20. Profile selection for standalone package

Studio currently accepts explicit:

```text
--settings
--rules
```

Keep this.

Standalone tray startup should use this preference order:

1. explicit launch arguments, if provided;
2. most recent valid Studio profile from `studio.sqlite`;
3. no active profile, then open Studio profile-selection UI normally.

Do not hardcode PFETL Core paths.

This preserves standalone independence.

Later PFETL Core integration will pass its active profile paths explicitly.

---

# 21. Static export / Chrome capability

Kaleido 1.x may depend on Chrome/Chromium.

Do not make server startup depend on this.

At startup or Diagnostics:

```text
check_static_export_capability()
```

Then:

```text
available
→ PDF/PPTX/PNG/SVG chart rendering enabled

unavailable
→ Studio remains fully usable
→ affected export actions clearly explain requirement
```

The packaging certification should test both states.

Do not decide in this closure document to bundle Chromium unless packaging proves it necessary.

---

# 22. Temporary/export directory policy

Use Studio-owned `platformdirs` locations for:

```text
runtime metadata
temporary CSV exports
report artifacts
temporary payload materialization
startup logs
```

Rules:

- no writes into PFETL database/source directories;
- no dependency on repository CWD;
- collision-safe names;
- stale temp cleanup;
- never delete explicit user-selected final export destinations.

---

# 23. Packaging test matrix

Before calling standalone packaging complete, test:

## Clean machine / clean venv equivalent

- no repository checkout required;
- launch tray executable;
- tray icon appears;
- server becomes Ready;
- browser opens;
- profile can be selected;
- Studio loads.

## Server lifecycle

- Start;
- Stop;
- Restart;
- browser closed/reopened;
- occupied 8080 fallback;
- stale runtime metadata;
- second launch while server already running.

## Core functionality

Smoke:

```text
Overview
Household
Wealth
Investments
Tax
FIRE
Operations
Explorer
Reports
```

## Exports

Representative:

```text
CSV
XLSX
PDF
PPTX
PNG/SVG
```

with static-export capability both available and unavailable where practical.

## Large-data behavior

- large Household CSV;
- large lot CSV;
- large retained source payload;
- confirm memory remains bounded.

## Shutdown

- Exit tray stops owned server;
- no orphaned Studio server remains;
- stale runtime metadata is handled safely next launch.

---

# 24. Version and release target

Recommended closure sequence:

```text
1.0.3
    ↓
final closure fixes + tray/package implementation
    ↓
1.1.0
```

`1.1.0` is justified because standalone packaged/tray operation is a genuine new product capability, not merely a bugfix.

The PyPI package remains supported.

The standalone Windows package becomes an additional distribution surface.

---

# 25. What not to implement now

Do not add:

```text
PFETL Core integration button
shared-memory IPC
Windows service
system startup auto-run
auto updater
remote hosting
authentication
cloud deployment
new analytics domains
new charts
new report families
```

The tray application is lifecycle control only.

---

# 26. Exit criteria for Studio repository closure

The repository is ready for the final README and `1.1.0` publish when:

## Hardening

- all DB-backed UI queries are off the NiceGUI event loop;
- Household full transaction export is truly streaming/chunked;
- Investment Lot full export is truly streaming/chunked;
- large retained payload downloads do not require full BLOB materialization;
- runtime liveness uses `/healthz`.

## Standalone package

- tray executable launches reliably;
- server child lifecycle is deterministic;
- tray supports Open / Start / Stop / Restart / Diagnostics / Exit;
- port collisions are handled;
- duplicate instances are safe;
- packaged resources resolve correctly;
- user-state/temp paths are package-safe;
- no console is required for normal use;
- startup failures are diagnosable;
- representative exports work.

## Certification

Run locally:

```text
pytest
ruff
mypy
pyright
pylint
```

plus the packaging smoke matrix and real-data Studio smoke.

Once green:

> **Freeze Studio architecture and functionality.**

Then proceed to:

```text
final Studio README rewrite
        ↓
1.1.0 CI / PyPI / standalone package publish
        ↓
Studio repository closed
        ↓
PFETL Core integration implementation document
        ↓
Core integration
        ↓
PFETL Core README final pass
        ↓
tks18 profile README rewrite
```

That is the final closure path.
