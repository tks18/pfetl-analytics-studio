# PFETL Analytics Studio — Pre-Phase-9 Foundation Patch

## Purpose

This document defines the **single pre-Phase-9 hardening patch** for `pfetl-analytics-studio` after completing Phases 1–8.

The objective is deliberately narrow:

> Fix the few foundational correctness, performance, semantic, and visualization issues identified in the `0.1.8` archaeology so that every later domain phase can inherit a stronger base.

This is **not** a redesign.

Do not reopen:

- profile architecture;
- read-only database boundaries;
- PFETL isolation;
- catalog scope;
- semantic period architecture;
- metric safety architecture;
- NiceGUI;
- Plotly;
- repository structure;
- the 33-phase program.

At completion:

```text
profile/session foundation
    → stable

analytical query execution
    → non-blocking

historical run semantics
    → correct

catalog/query capability discovery
    → cached and reusable

domain metrics
    → semantically consistent

visual grammar
    → reusable and future-ready

Phase 9+
    → proceed without foundation rework
```

---

# 1. Fix historical-run analytical semantics

## Current issue

Studio can select a historical PFETL run and correctly load historical:

- Settings snapshot;
- FinancialRules snapshot;
- Core version;
- run provenance.

However, analytical services still query the **current DuckDB warehouse**.

PFETL does not currently provide a full run-versioned DuckDB warehouse.

That means the UI can accidentally present:

```text
historical run provenance
+
current warehouse analytics
```

as though both belong to the same historical state.

This is a correctness issue.

## Correct implementation

Restrict historical run selection to surfaces that genuinely have historical truth in the Control Plane:

```text
Operations
Forensics
Run evidence
Configuration comparison
Historical Settings/FinancialRules
```

For ordinary analytical pages such as:

```text
Overview
Household
Wealth
Investments
Tax
FIRE
Reports
```

bind analytical truth to the **latest successful committed PFETL run associated with the current warehouse state**.

The UI must not imply that changing the historical run selector rewinds DuckDB analytics.

## Recommended model

Keep two concepts distinct:

```text
analytical_run_id
historical_inspection_run_id
```

or equivalent state separation.

Do not overload one run selector for two meanings.

## Verification

Test that:

- Overview always uses the latest warehouse-compatible run;
- historical run selection never changes current analytical numbers;
- Operations/Forensics can still inspect older runs;
- provenance shown beside analytical data matches the run used to represent current warehouse state.

---

# 2. Move analytical database work off the NiceGUI event loop

## Current issue

Current page rendering paths call synchronous analytics services directly from NiceGUI render/event handlers.

Examples include:

```text
OverviewService.get_snapshot()
HouseholdAnalytics.income_summary()
HouseholdAnalytics.income_transactions()
```

These services perform DuckDB/SQLite work synchronously.

This is acceptable at today's scale, but later phases add substantially more queries and charts.

## Correct implementation

Keep analytics services synchronous.

Move only the **UI boundary** to `run.io_bound()` or the equivalent NiceGUI thread helper.

Correct pattern:

```text
NiceGUI async handler
        ↓
await run.io_bound(
    service.get_snapshot,
    ...
)
        ↓
immutable result
        ↓
render UI
```

Do not convert the analytics/domain layer to async.

Do not create an asyncio-aware database layer.

## Apply to

At minimum:

```text
Overview
Household Income
future Household tabs
future Wealth
future Investments
future Tax
future FIRE
Operations DB-heavy views
Reports
Explorer queries
```

## UX

While a background query is running:

- keep the page interactive;
- show a loading/skeleton state;
- preserve the previous result where appropriate;
- render the new result atomically on completion;
- show actionable error state on failure.

## Verification

Use deliberately slow fake query services and verify that:

```text
page remains responsive
navigation still works
browser event loop is not blocked
```

---

# 3. Retain catalog discovery as an immutable session capability snapshot

## Current issue

Session hydration already runs catalog discovery and obtains:

```text
surfaces
columns
row counts
compatibility
```

but only the compatibility report is retained.

Later analytics services repeatedly call:

```text
table_exists()
table_has_column()
```

against DuckDB `information_schema`.

## Correct implementation

Retain a lightweight immutable `CatalogSnapshot` in `AppContext`.

Suggested shape:

```python
@dataclass(frozen=True)
class CatalogSnapshot:
    surfaces: frozenset[str]
    columns: Mapping[str, frozenset[str]]
    row_counts: Mapping[str, int | None]
```

Expose helpers such as:

```python
catalog.has_surface("gold.core_monthly_fact")
catalog.has_column(
    "silver.dim_income_subcategory",
    "Taxability",
)
catalog.row_count("gold.core_monthly_fact")
```

## Important

Do not duplicate the catalog authority.

`CatalogSnapshot` is simply the session-time read model of the already-existing catalog discovery.

## Benefit

Future pages avoid repeated schema probes and gain deterministic capability checks.

## Verification

Assert that once session hydration succeeds:

```text
ordinary analytics capability checks
→ no information_schema query required
```

unless the session is explicitly refreshed.

---

# 4. Use one short-lived DuckDB read session per analytical snapshot

## Current issue

Current reader helpers frequently perform:

```text
open connection
query
close connection
```

for every individual `fetch_one()` / `fetch_all()`.

A complex Overview snapshot can therefore open and close DuckDB many times.

## Correct implementation

Preserve the existing principle:

> No long-lived global/shared DuckDB connection across independent UI callbacks.

But support a short-lived read session for one analytical snapshot.

Conceptually:

```python
with duck_reader.session() as db:
    metric_a = query_a(db, ...)
    metric_b = query_b(db, ...)
    metric_c = query_c(db, ...)
```

One service invocation becomes:

```text
background UI task
    ↓
open read-only DuckDB
    ↓
execute all related snapshot queries
    ↓
close
```

## Do not

Do not introduce:

```text
application-global connection
connection pool
shared connection across requests
write-capable connection
```

## Verification

Measure or instrument one Overview load and confirm connection churn drops materially while all queries remain read-only.

---

# 5. Establish lazy domain-tab rendering

## Current risk

Household currently contains:

```text
Income
Expense
Budget & Cashflow
Transactions
```

Only Income is implemented.

If future tabs are implemented eagerly inside each tab panel, opening Household may execute all domain queries and build all charts immediately.

The same risk is even larger for future Investments tabs.

## Correct implementation

Lazy-load tab content on first activation.

Pattern:

```text
open Household
    → load Income only

first click Expense
    → query/render Expense once

first click Budget
    → query/render Budget once

first click Transactions
    → query/render Transactions once
```

Reuse the same pattern later for:

```text
Investments Overview
Performance
Lots
Realized/Reconciliation
Market Evidence
```

## Optional refresh behavior

A tab may reload when:

- profile changes;
- selected period changes;
- relevant filters change;
- latest PFETL run changes;
- user explicitly refreshes.

Do not rerender merely because the user switches away and back if inputs are unchanged.

## Verification

Count service invocations and prove unopened tabs execute zero analytical work.

---

# 6. Detect a new PFETL run and invalidate session analytics cleanly

## Current issue

Cache keys correctly include run identity, but if PFETL executes externally while Studio remains open, Studio can retain the previous latest-run context until a manual refresh.

## Correct implementation

Add a lightweight freshness check at safe analytical boundaries.

Compare:

```text
latest successful committed Control Plane run
```

against:

```text
current analytical provenance run_id
```

When changed:

1. invalidate affected analytical cache;
2. reload latest provenance;
3. rebuild session context where necessary;
4. refresh period/filter context if the available data horizon changed.

## Do not

Do not introduce a heavy filesystem watcher.

Do not poll constantly.

A low-frequency check on route refresh/navigation or explicit refresh is sufficient.

## Verification

Simulate:

```text
Studio session bound to run A
PFETL completes run B
Studio refreshes analytical route
```

and verify Studio cleanly switches to run B without stale cache reuse.

---

# 7. Fix receiving-account share denominator

## Current issue

Household Income ranks receiving accounts with a top-N limit and then calculates share using only those top-N rows.

That causes displayed Top-10 accounts to normalize to 100%.

## Correct implementation

Calculate the total receiving-account amount before applying `LIMIT`.

Preferred SQL approach:

```sql
SUM(SUM(base_amount)) OVER () AS total_amount
```

Then rank/limit rows.

Calculate:

```text
account share
=
account amount / total receiving-account amount
```

## Verification

Create a fixture with more than ten receiving accounts.

Assert:

```text
sum(displayed top-10 shares) < 100%
```

when excluded accounts contain non-zero income.

---

# 8. Fix income-group concentration denominator

## Current issue

Income category concentration uses gross positive income.

Income group concentration currently uses net income after deductions.

This produces inconsistent concentration semantics and can inflate group shares.

## Correct implementation

Use one denominator for positive-income concentration:

```text
gross positive income
```

Therefore:

```text
Top Category Share
Top 3 Category Share
Top Income Group Share
```

all represent concentration within positive income.

Negative deductions remain separate.

## Verification

Create an income fixture with significant negative deductions and confirm group share remains a valid percentage of gross positive income.

---

# 9. Make income composition reconcile to total income

## Current issue

The current stack includes:

```text
Active Income
Passive Income
Non-Cash Income
```

but real data may include positive income that belongs to none of those classifications.

The chart therefore visually appears complete without necessarily reconciling to total income.

## Correct implementation

Calculate:

```text
Other / Unclassified
=
Total Income
- Active Income
- Passive Income
- Non-Cash Income
```

If residual is materially non-zero, render an additional stack.

If the residual is effectively zero within the appropriate numerical tolerance, omit it.

## Rule

A chart presented as a total composition should reconcile to the total.

## Verification

Assert:

```text
sum(composition stacks)
=
total income
```

for every period.

---

# 10. Surface unclassified taxability

## Current state

Taxable share correctly uses:

```text
taxable / classified income
```

and the UI labels it as share of classified income.

However, unclassified taxability amount is already available but not clearly surfaced.

## Correct implementation

Where non-zero, display:

```text
Taxable
Non-Taxable
Unclassified
```

and retain:

```text
Taxable Share = Taxable / Classified Income
```

Do not silently fold unclassified income into non-taxable income.

## Verification

Use a fixture containing all three classifications.

---

# 11. Add explicit metric origin

## Purpose

Studio correctly consumes canonical PFETL financial metrics, but it also derives presentation-only statistics such as:

```text
comparison deltas
concentration shares
account mix
taxability proportions
```

Future domains will introduce more.

## Correct implementation

Extend `MetricSpec` with a small origin field:

```python
class MetricOrigin(StrEnum):
    CORE = "core"
    STUDIO_DERIVED = "studio_derived"
```

Examples:

```text
Portfolio XIRR       → CORE
Tax Exposure         → CORE
FIRE Probability     → CORE
Account Share        → STUDIO_DERIVED
Comparison Delta     → STUDIO_DERIVED
```

## Rule

Studio may derive presentation statistics from canonical data.

Studio must not silently recreate PFETL financial methodology.

## Verification

Ensure the metric registry can distinguish Core truth from Studio-derived presentation metrics.

---

# 12. Freeze Overview scope

Overview is already a broad cross-domain cockpit.

Do not add new Overview panels merely because future domains add new features.

Its purpose should remain:

> What is happening across the complete financial system right now?

Detailed future analytics belong in domain pages.

This is an explicit scope guardrail, not a code change unless required to remove planned duplication.

---

# 13. Generalize Plotly axis handling

## Current limitation

The shared Plotly layout helper is currently optimized mainly for:

```text
date-based X axes
horizontal categorical bars
```

Later phases require:

```text
categorical vertical axes
numeric axes
waterfalls
heatmaps
FY buckets
maturity buckets
```

## Correct implementation

Allow explicit axis semantics.

For example:

```python
x_axis_type: Literal[
    "date",
    "category",
    "linear",
]
```

Default behavior may remain compatible with current charts.

The helper should not guess every future chart from orientation.

## Verification

Add tests for:

```text
date axis
category axis
numeric axis
```

and ensure current charts retain their existing behavior.

---

# 14. Add semantic chart-color roles

## Current issue

The global palette is strong, but chart builders assign semantic meanings individually.

With many future charts, meanings can drift.

## Correct implementation

Add a small semantic mapping layer.

Example roles:

```text
ACTUAL
BENCHMARK
TARGET
POSITIVE
NEGATIVE
NEUTRAL
MARKET
BOOK
ACTIVE
PASSIVE
NON_CASH
FX
```

Map those roles to the existing palette.

## Examples

Use consistent meaning:

```text
BENCHMARK
    → same visual role everywhere

NEGATIVE
    → same visual role everywhere

MARKET vs BOOK
    → same pair everywhere
```

Do not create a theme framework beyond this.

---

# 15. Add a reusable waterfall chart builder

## Why

Waterfall is the major missing financial visual primitive.

It is ideal for attribution and reconciliation.

## Immediate/future uses

### Wealth

```text
Opening Net Worth
+ Net Savings
+ Market Return
+ FX / Other
+ Residual
= Closing Net Worth
```

### Budget / Cashflow

```text
Income
- Core Expense
- Non-Core Expense
± Investing
± Financing
= Net Cash Movement
```

### Tax / Reconciliation

Use only where canonical data supports an additive bridge.

## Requirements

The builder should support:

- relative steps;
- totals;
- positive/negative semantic colors;
- currency formatting;
- optional source caption;
- deterministic ordering.

## Verification

Assert that the waterfall mathematically reconciles to the final total.

---

# 16. Add a reusable treemap builder

## Why

Future domains contain hierarchical composition that becomes unreadable as repeated bar charts.

## Good uses

```text
Investments:
Asset Class → Instrument Type → Subtype/ISIN

Investments:
Sector → Industry

Wealth:
Asset Group → Asset Subcategory

Expenses:
Expense Group → Category
```

## Rule

Use treemap for:

```text
composition at one point in time
```

Do not use it for trends, performance, or time series.

## Requirements

Support:

- hierarchy path;
- value;
- optional percentage/hover metrics;
- semantic tooltip formatting;
- stable domain identity for future drill.

---

# 17. Add a reusable heatmap builder

## Good uses

### Expenses

```text
month × category
→ spend intensity
```

### Operations

```text
provider × time period
→ failures / retries
```

### Investments

Only where the underlying metric and aggregation are semantically valid.

## Rule

Heatmaps answer:

> Where is the pattern concentrated?

Do not use them as decorative alternatives to ordinary tables.

## Requirements

Support:

- categorical X/Y;
- numeric intensity;
- compact hover;
- missing-value handling;
- stable axis ordering.

---

# 18. Add chart interaction modes

Current Overview charts correctly hide the Plotly mode bar.

Detailed future analytical pages need richer interaction.

## Correct implementation

Support two simple configurations:

```text
cockpit
analytical
```

### Cockpit

For:

```text
Overview
compact cards
summary views
```

Use:

```text
mode bar off
compact margins
limited interaction
```

### Analytical

For:

```text
Investments
FIRE
Market Evidence
Operations
detailed trend analysis
```

Enable a minimal mode bar with:

```text
zoom
pan
reset
```

Image export can remain a later reporting/export concern.

Do not expose the full Plotly toolbox.

---

# 19. Establish a chart-point drill convention

## Current limitation

Charts are visually strong but do not yet have a consistent stable point identity for drill interactions.

Future phases need:

```text
click category
→ transactions

click country
→ holdings

click ISIN
→ instrument details

click tax head
→ tax events

click failure domain
→ failures
```

## Correct implementation

Each drillable chart point should carry:

```text
stable domain key
+
display metadata
```

Do not rely on formatted hover strings as the identity.

Add optional support to `chart_panel()` such as:

```python
on_point_click = ...
```

or the equivalent NiceGUI Plotly event integration.

## Important

Do not build a generalized chart-action framework.

Just standardize the identity and callback pattern.

---

# 20. Strengthen semantic visualization tests

Current chart tests mainly verify that a figure exists and contains the expected number of traces.

Add tests for chart meaning.

Examples:

```text
income composition reconciles to total
redeemed investment bars are negative
benchmark metrics use percentage axis
market/book metrics use currency axis
waterfall reconciles to closing value
date chart uses date axis
category chart uses category axis
unclassified income appears when required
top-N shares use full denominator
```

Tests should verify important data/axis semantics, not pixel appearance.

---

# 21. Add FI target reference line

The FIRE Overview chart currently shows:

```text
FI Coverage %
MC Success %
```

Add a reference line at:

```text
FI Coverage = 100%
```

because 100% has explicit domain meaning.

Do not invent a Monte Carlo target threshold unless FinancialRules defines one.

---

# 22. Detailed Investment performance visual rule

Keep the current combined Portfolio Value + XIRR chart on Overview if it remains readable.

For the later detailed Investments Performance page, split:

### Valuation

```text
Market Value
Cost Basis
```

### Performance

```text
Portfolio XIRR
Benchmark XIRR
Active Return
```

This is a future implementation rule, not an immediate page rewrite.

---

# 23. Optional percentile/range-band primitive

This is optional before Phase 9.

Only implement it now if the canonical PFETL FIRE data already exposes a time-series shape such as:

```text
P10
P50
P90
by horizon
```

If available, use:

```text
P50 line
+
P10–P90 shaded band
```

If only terminal percentile values exist, do not invent a trajectory.

This primitive can safely wait until the FIRE phase.

---

# 24. Visualization semantic rules for all future phases

Use the following chart grammar consistently.

## Stocks / state over time

Use line charts.

Examples:

```text
net worth
portfolio value
liquidity
```

## Period flows

Use bars.

Examples:

```text
income
expense
investment deployment
realized gains
```

## Cumulative flows

Use line/area.

Examples:

```text
YTD income
cumulative savings
```

## Composition

Prefer:

```text
ranked horizontal bars
or
treemap
```

depending on cardinality/hierarchy.

## Attribution

Use:

```text
waterfall
```

## Actual vs target

Use:

```text
grouped bars
or
line + target/reference line
```

## Ratios / probabilities

Use:

```text
KPI progress
or
line chart
```

with reference lines only where the domain defines a meaningful threshold.

## Positive vs negative movements

Use:

```text
diverging bars
```

## Pattern density

Use:

```text
heatmap
```

Do not use pie charts, gauges, Sankey diagrams, or 3D charts as default primitives.

---

# 25. Do not add chart downsampling yet

Current monthly and analytical series are small.

Even daily market series are manageable at current scale.

Do not silently drop financial observations for theoretical rendering performance.

Add downsampling only if actual future Market Evidence profiling proves it necessary.

---

# 26. Future domain file-size guardrail

The current domain architecture is good:

```text
models
queries
service
page
chart builders
```

Keep it.

However, future Investments should not become one giant 3,000–4,000 line service/query module.

Split only when actual domain complexity arrives.

Good future boundaries include:

```text
investments_overview
investments_performance
investment_lots
investment_reconciliation
```

Do not pre-create empty architecture.

---

# 27. Explicit non-goals

Do not:

```text
rewrite NiceGUI
replace Plotly
introduce React
create a visualization DSL
create a dashboard framework
create chart plugins
build a generic query framework
build all future domain pages early
add dozens of chart types
introduce pie/gauge/3D charts by default
change PFETL Core methodology
write to PFETL databases
rework the 82-surface catalog
replan the 33 phases
```

This patch strengthens existing foundations only.

---

# 28. Expected implementation areas

Likely files include the existing equivalents of:

```text
app/session/context
profile/run selection
catalog read models
DuckDB gateway / reader
analytics/overview
analytics/household
analytics/household_queries
semantic/metrics
ui/charts
ui/chart panels/components
pages/overview
pages/household
tests for session/catalog/cache
tests for overview
tests for household
tests for chart semantics
```

Potential small new helpers are acceptable where they reduce repeated chart/session logic.

Do not create a new architectural layer unless one of the fixes genuinely requires it.

---

# 29. Final verification checklist

Before starting Phase 9:

## Correctness

- historical analytical pages cannot display current warehouse data as an older run;
- receiving-account shares use the full denominator;
- income-group concentration uses gross positive income;
- income composition reconciles to total income;
- unclassified taxability is visible when present;
- Core vs Studio-derived metrics are distinguishable.

## Performance

- analytical DB work does not block the NiceGUI event loop;
- unopened domain tabs perform zero analytical work;
- session catalog capability checks avoid repeated schema probing;
- one analytical snapshot can reuse one short-lived read-only DuckDB session;
- external PFETL run changes invalidate stale analytical context/cache cleanly.

## Visualization

- shared layout supports date/category/numeric axes;
- semantic color roles are consistent;
- waterfall builder exists and reconciles correctly;
- treemap builder exists;
- heatmap builder exists;
- chart interaction supports cockpit vs analytical modes;
- drillable charts can carry stable domain identity;
- chart tests verify semantic data/axis behavior;
- FI coverage includes a 100% reference line;
- no unnecessary chart families are introduced.

## Regression

- existing Overview remains functionally equivalent except for intentional fixes;
- existing Household Income remains functionally equivalent except for denominator/reconciliation fixes;
- existing read-only guarantees remain intact;
- PFETL package imports remain absent from Studio production code;
- catalog still covers all 82 upstream surfaces;
- existing period/filter/metric safety remains intact;
- existing tests remain green.

---

# Closure rule

Once this patch is green:

> **The PFETL Analytics Studio foundation is frozen through Phase 8.**

From Phase 9 onward, implementation should build domain capability on top of the existing foundation rather than reopening:

```text
profile architecture
database gateway design
catalog design
period semantics
metric safety
query execution model
visual grammar
```

Future foundation changes should require an observed correctness/performance problem, not speculative redesign.

Then proceed cleanly to:

```text
Phase 9
Expense Analytics
```

with the expectation that later Wealth, Investments, Tax, FIRE, Operations, Explorer, and Reports all inherit these strengthened foundations.
