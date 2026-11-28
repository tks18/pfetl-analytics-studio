# Phase 01 — Runtime & Performance Hardening

## Objective

Make every heavy Studio workspace obey one runtime rule:

> **NiceGUI owns interaction; blocking database/file/export work runs outside the event loop; unopened workspaces perform no analytical work.**

This phase fixes the main performance inconsistencies found in 1.0.0. It does not change financial results or page scope.

---

## 1. Offload remaining analytical I/O from the NiceGUI event loop

### Current issue

The pre-Phase-9 async pattern is applied in Overview, Household, Wealth, and report preview/export, but several completed pages still call synchronous analytics services directly from NiceGUI renderers or event handlers.

Actual 1.0.0 examples include:

- `pages/investments.py`
  - `InvestmentAnalytics.portfolio_snapshot()`
  - `allocation()`
  - `performance()`
  - `lot_rows()`
  - `lot_detail()`
- `pages/investments_evidence.py`
  - `realized_and_reconciliation()`
  - `market_evidence()`
  - lineage lookups
- `pages/tax.py`
  - `TaxAnalytics.fy_summary()`
  - `income_breakdown()`
  - `reconciliation()`
  - `tax_events()`
  - `harvesting_snapshot()`
- `pages/fire.py`
  - `FireAnalytics.summary()`
  - `simulation_history()`
- `pages/operations.py`
  - run history, warehouse health, source/artifact/provider/failure/forensic calls
- `pages/explorer.py` / `explorer_views.py`
  - previews, guided queries, SQL execution, CSV exports
- `pages/reports.py`
  - `_load_available_run_options()` currently calls `OperationsAnalytics.run_history()` synchronously.

### Correct approach

Keep analytics services synchronous. Offload at the presentation boundary:

```python
result = await run.io_bound(service_method, ...)
```

or wrap a small synchronous closure when several arguments/results belong to one refresh.

Use the same pattern already working in:

- `pages/overview.py`
- `pages/household.py`
- `pages/wealth.py`
- report preview/export handlers.

### Requirements

- page navigation must remain responsive during database work;
- disable only the control currently executing where practical;
- preserve the previous successful result while a refresh is running;
- ignore stale results when period/filter/tab inputs change before a background task completes;
- render success/error results back on the NiceGUI event loop;
- do not convert analytics/data layers to `async`.

### Verification

Use slow fake services to prove that browser interactions and route changes continue while work is running.

---

## 2. Make heavy domain tabs truly lazy

### Investments

Current `pages/investments.py` constructs all five tab bodies immediately:

```text
Overview & Allocation
Performance & Benchmarks
Lot Explorer
Realized & Reconciliation
Market Evidence
```

Each body can query data and build Plotly figures even if the user never opens it.

### Tax

`pages/tax.py` creates four panels and `_render_all()` refreshes the FY Overview, breakdown, reconciliation, events and harvesting workspace together.

### Operations

`pages/operations.py` eagerly invokes:

```text
_render_sources_tab()
_render_providers_tab()
_render_forensics_tab()
```

before the active Runs workspace is rendered.

### Explorer

`pages/explorer.py` constructs:

```text
Catalog
Guided Analytics
Saved Views
Advanced SQL
```

together. Guided Analytics can execute an initial query while the user opened only the Catalog.

### Correct approach

Reuse the Household pattern already in the repo:

```python
rendered_tabs: set[str] = set()

def ensure_tab_rendered(tab_id: str) -> None:
    if tab_id in rendered_tabs:
        return
    rendered_tabs.add(tab_id)
    ...
```

Rules:

- render only the initially selected tab;
- render another tab on first activation;
- invalidate/reload when profile, analytical run, period or relevant filters change;
- do not reload merely because the user switches away and back with unchanged inputs;
- preserve the existing selected-tab persistence.

For Tax, FY changes should invalidate only the already-rendered Tax workspaces. Unopened tabs stay unopened.

### Verification

Add call-count tests proving unopened tabs execute **zero** analytics queries.

---

## 3. Add stale-result protection to background page loads

Once more pages use `run.io_bound`, rapid user changes can create races:

```text
request A starts
user changes filter
request B starts
B finishes
A finishes later
```

A must not overwrite B.

### Correct approach

Use a simple generation/request token scoped to the page/tab:

```text
load_generation += 1
capture generation
run background work
render only if captured generation == current generation
```

Apply to page refreshes, heavy drills, forensic operations and Explorer query execution where inputs can change quickly.

Do not build a generic task framework unless repeated code clearly justifies one small helper.

---

## 4. Eliminate unbounded CSV materialization

### Current issue

Explorer full exports can currently do:

```text
DuckDB / SQLite
→ Polars DataFrame
→ CSV string
→ encoded bytes
→ ui.download(...)
```

Actual paths include `analytics/explorer_queries.py` and `analytics/explorer.py`.

This can hold several copies of a large result in memory.

### Correct approach

Classify exports:

**Bounded result**
- small paginated tables / current visible result;
- in-memory CSV is acceptable.

**Potentially large result**
- full surface export;
- full Guided Analytics export;
- unrestricted Advanced SQL export.

For potentially large exports:

```text
database/query
→ temporary CSV file
→ NiceGUI file download
→ cleanup
```

Prefer DuckDB `COPY (SELECT ...) TO ... (HEADER, DELIMITER ',')` for DuckDB-backed exports where safe and compatible with the existing read-only policy; otherwise stream/write rows to a temporary file without building one giant bytes object.

Control Plane exports must remain read-only.

### Safety

- enforce SQL read-only guard before export;
- use a Studio-owned temp/export directory;
- sanitize filenames;
- clean old temporary exports;
- never write into PFETL stores.

---

## 5. Stop rereading completed report/export files into memory

### Current issue

`pages/reports.py` exports in background correctly, then uses `res.path.read_bytes()` before `ui.download`.

This duplicates potentially large PDF/XLSX/PPTX content in process memory.

### Correct approach

Serve/download the generated file by path or a temporary static/file response supported by NiceGUI rather than loading the entire file into bytes.

Apply the same principle to other already-materialized file exports.

Keep `read_bytes()` only for genuinely tiny assets.

---

## 6. Add size-aware retained-payload download behavior

`pages/operations.py` can expose retained source payloads as `file_bytes`.

For normal source files this may be fine, but large payloads should not be silently duplicated in memory.

### Correct approach

- expose payload byte size before download;
- define a practical in-memory threshold;
- above the threshold, materialize to a temporary Studio-owned file and download by path;
- keep provenance and original filename;
- never mutate the Control Plane payload.

---

## 7. Review repeated catalog discovery in Explorer

The session already owns `catalog_snapshot`, but some Explorer paths still call fresh `PFETLCatalog(...).discover()` where richer metadata is needed.

### Correct approach

For each call:

- use `AppContext.catalog_snapshot` when its stored surface/column/row-count data is sufficient;
- keep fresh discovery only when Explorer genuinely needs metadata not carried by the snapshot;
- do not expand `CatalogSnapshot` merely to remove one cheap call unless the metadata is broadly useful.

This is an optimization, not a redesign.

---

## 8. Performance acceptance

Before Phase 02:

- opening Investments loads only the selected tab;
- opening Operations does not parse forensic data or load providers/sources unless selected;
- opening Explorer Catalog does not execute Guided Analytics or SQL;
- Tax FY changes do not eagerly query unopened tabs;
- no heavy domain query runs directly on the NiceGUI event loop;
- large CSV exports do not materialize the entire dataset as both DataFrame and bytes;
- report downloads do not reread generated files into memory;
- existing charts, metrics and financial results remain unchanged.

## Expected files

Primarily:

```text
pages/investments.py
pages/investments_evidence.py
pages/tax.py
pages/fire.py
pages/operations.py
pages/explorer.py
pages/explorer_views.py
pages/reports.py
analytics/explorer.py
analytics/explorer_queries.py
data/* only if a small export/session helper is genuinely needed
tests/*
```

Do not change PFETL financial methodology or report definitions in this phase.
