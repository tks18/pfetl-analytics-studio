# Phase 02 — UX, Domain & Confidence Hardening

## Objective

Raise the completed 1.0.0 product from “feature-rich” to “predictable, efficient and easy to operate” without adding new domains.

The focus is:

```text
dense workflows
→ clearer hierarchy

domain presentation
→ consistent semantics

tests
→ confidence in real analytical invariants
```

---

## 1. Preserve the current domain architecture

The current domain coverage is already mature:

- Overview;
- Household;
- Wealth;
- Investments;
- Tax;
- FIRE;
- Operations;
- Explorer;
- Reports.

Do not add more top-level routes or new financial engines.

PFETL remains the source of canonical finance methodology.

Studio may derive presentation statistics, comparison deltas, shares and visual transformations only where `MetricOrigin` makes that ownership clear.

---

## 2. Improve control hierarchy on dense pages

The archaeology found three particularly dense workflows:

```text
Operations
Explorer
Reports
```

They combine global context, sub-tabs and local controls.

### Correct approach

Use a consistent visual hierarchy:

```text
Global context
    ↓
Domain/workspace navigation
    ↓
Local controls
    ↓
Results
```

Specific improvements:

### Operations
- make the active workspace title and current run/source context visually explicit;
- keep filters closest to the table/chart they control;
- avoid repeating global profile/run information inside each panel;
- preserve drill-back paths from forensic/source/provider details.

### Explorer
- distinguish “Catalog”, “Guided”, “Saved”, and “SQL” as separate tools rather than one large form;
- make starter recipes prominent in Guided/SQL;
- keep dangerous-looking advanced SQL visually separated despite read-only enforcement;
- keep result row count, truncation/limit state and export scope obvious.

### Reports
Present the workflow clearly as:

```text
Choose
→ Configure
→ Preview
→ Export
```

Do not remove advanced capability. Improve hierarchy, labels and progressive disclosure.

---

## 3. Standardize loading, refreshing and error behavior

After Phase 01 moves more work into background tasks, every heavy workspace should behave consistently.

Use shared presentation conventions:

- initial skeleton/loading panel;
- “Refreshing…” without clearing last good data;
- error panel with concise failure + retry where useful;
- empty state that explains why no data exists;
- locked/incompatible state using existing shared components;
- disable duplicate action buttons while that action is running.

Avoid page-specific spinners and wording where an existing component can express the state.

---

## 4. Tighten chart usability without adding chart families

The chart library is already broad: line, area, bars, stacked/diverging bars, waterfall, treemap, heatmap, scatter/bubble, donut, dual-axis and percentile bands.

Do not add new chart types for variety.

### Improve consistency

Audit existing builders for:

- semantic color-role consistency;
- currency vs percent vs count axes;
- `cockpit` vs `analytical` interaction mode;
- readable hover content;
- reference lines only where domain-defined;
- title/subtitle/source-caption consistency;
- stable click/drill identity where a chart is drillable.

### Detailed analytical pages

Prefer separation over overly dense dual-axis charts when the user is doing analysis rather than scanning a cockpit.

Do not change Overview simply to make it more elaborate; its scope remains frozen.

---

## 5. Verify domain presentation invariants

Add or strengthen tests around what the UI claims, not just whether a figure exists.

High-value examples:

### Household
- income composition reconciles to total including residual/unclassified;
- top-N account/category shares use full denominators;
- budget/cashflow bridge reconciles;
- transaction totals match applied filters.

### Wealth
- opening net worth + attribution components reconcile to closing net worth where canonical data supports the bridge;
- market/book and asset/FX measures stay on correct units/axes.

### Investments
- allocation weights reconcile to expected total;
- portfolio value and P&L decomposition remain internally consistent;
- benchmark/active-return visuals use percentage semantics;
- lot filters/pagination do not alter total-summary meaning;
- realized/reconciliation links preserve identifiers.

### Tax
- FY summary equals the displayed breakdown/reconciliation universe;
- taxable/non-taxable/unclassified semantics remain distinct;
- harvesting views remain indicative and do not imply statutory filing output.

### FIRE
- canonical PFETL outputs remain visually distinct from Studio what-if scenarios;
- percentile bands use canonical P10/P50/P90 values where available;
- FI 100% reference line remains domain-defined;
- simulation provenance points to the correct Core run/rules.

### Operations
- historical run context never changes current analytical warehouse truth;
- forensic events, operation tree and failure/source/provider drills remain tied to the selected historical run.

---

## 6. Raise domain test rigor to PFETL-style confidence

The existing test suite is respectable but uneven across final domains.

Add focused golden/integration tests rather than many tiny structural tests.

### Wealth golden case
Assert a small hand-checkable:

```text
opening net worth
+ savings
+ market/FX movement
= closing net worth
```

with the exact Studio query/result model used by the page.

### Investments golden case
Use a compact fixture that asserts:

- allocation totals;
- market value / cost basis;
- P&L decomposition;
- benchmark and active return;
- lot totals;
- realized-event linkage;
- reconciliation identifiers.

Do not re-test FIFO methodology itself; PFETL owns that.

### Tax golden case
Assert:

- FY totals;
- head/subhead breakdown;
- reconciliation;
- event counts/status;
- harvesting exposure.

### FIRE golden case
Assert:

- canonical summary values;
- simulation provenance;
- what-if calculations are clearly Studio-derived and deterministic.

### Operations integration case
Assert one synthetic run across:

```text
cp_runs
sync summary
artifact events
provider request
failure
settings/rules snapshot
forensic JSONL
operation tree
```

### Explorer safety case
Assert:

- read-only SQL guard;
- bounded preview;
- large export path writes a temp file rather than building giant bytes;
- saved-view replay retains period/filter/query semantics.

---

## 7. Strengthen report confidence

Reports are a major product surface and deserve representative end-to-end coverage.

### Built-ins

For each built-in definition, at minimum verify:

- registration;
- rendering succeeds against the fixture database;
- required provenance is present;
- block ordering and required block types are valid.

### Representative export matrix

Do not export every report to every format in every test.

Use a focused matrix covering:

- PDF;
- XLSX;
- PPTX;
- CSV;
- PNG/SVG where chart export applies.

Ensure at least:

- one finance review;
- one Investments report;
- one Tax/FIRE report;
- one Operations/forensic report;
- one custom report

complete through the real exporter.

### PPTX

Verify native editable chart generation for supported chart types and image fallback for unsupported complex visuals.

---

## 8. Add product-level smoke flows

Create a small set of high-value smoke tests around the real session/context:

```text
load profile
→ compatibility/catalog
→ Overview
→ Household
→ Wealth
→ Investments
→ Tax
→ FIRE
→ Operations
→ Explorer
→ Reports
```

The test does not need browser pixel automation for every page.

It should prove each major service can build its primary immutable snapshot from the same realistic fixture and that no domain accidentally writes upstream.

---

## 9. Maintainability cleanup only where touched

Several page/query modules exceed 1,000 lines.

Do **not** run a LOC-reduction refactor.

If Phase 01/02 changes make one file harder to reason about, split by existing workspace ownership, for example:

```text
operations_runs.py
operations_sources.py
operations_forensics.py

investments_overview.py
investments_performance.py
investments_lots.py
```

Only split when there is a real independent state/query/event lifecycle.

Do not add repositories/interactors/controllers merely to reduce file length.

---

## 10. UX acceptance

Before Phase 03:

- every heavy page provides consistent loading/refresh/error states;
- unopened sub-workspaces remain unloaded;
- dense pages have an obvious visual workflow;
- charts use consistent units/colors/interactions;
- no page implies Studio owns canonical PFETL methodology;
- representative domain goldens pass;
- report render/export confidence is materially stronger;
- no new top-level functionality has been introduced.

## Expected files

Likely:

```text
pages/operations.py
pages/explorer.py
pages/explorer_views.py
pages/reports.py
pages/investments.py
pages/tax.py
pages/fire.py
ui/charts*.py
ui/cards.py
ui/tables.py
reports/*
tests/analytics/*
tests/test_operations_forensics.py
tests/test_explorer.py
tests/test_reports.py
tests/pages/*
tests/ui/*
```

Keep the scope product-hardening, not feature expansion.
