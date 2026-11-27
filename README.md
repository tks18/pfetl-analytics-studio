# PFETL Analytics Studio

Local-first, read-only analytical companion to **Personal Finance ETL (PFETL)** for visual exploration, operational intelligence, and deterministic presentation-grade reporting.

---

## 1. Product Mission & Core Invariants

PFETL Core calculates canonical financial truth (Bronze → Silver → Gold in DuckDB plus operational Control Plane telemetry in SQLite). **Analytics Studio** turns that truth into interactive dashboards, drill-downs, forensic operational intelligence, ad-hoc exploration, and downloadable reports.

### Architectural Boundaries
- **Strictly Read-Only**: Studio opens PFETL DuckDB with `read_only=True` and Control Plane SQLite via URI `mode=ro`. It **never** writes to PFETL DuckDB, Control Plane/Raw Store SQLite, PFETL `Settings` TOML, or `FinancialRules` TOML.
- **Zero Methodology Reimplementation**: Studio never reimplements FIFO lot matching, tax classification, reconciliation, XIRR, FX attribution, FIRE math, or Monte Carlo simulation. All canonical metrics are read directly from PFETL Gold/Silver/Meta tables and Control Plane snapshots.
- **Lock-Aware Concurrency**: Before opening DuckDB, Studio checks for PFETL's `pipeline.lock`. When PFETL Core is actively writing, Studio pauses warehouse queries while keeping Control Plane run telemetry and logs safely accessible.
- **Local Loopback Binding**: Studio binds to `127.0.0.1` by default and rejects non-loopback hosts.
- **Studio-Owned Persistence**: Only Studio's own local SQLite database (`studio.sqlite` via `platformdirs`) is writable, storing recent profiles, Explorer saved views, custom report definitions (`saved_reports`), and report execution history (`report_runs`).

---

## 2. Profile Model, Drift Detection & Compatibility

Analytics Studio uses **no second configuration universe**. You point Studio at the exact same two TOML files used by PFETL Core:
1. **PFETL Settings TOML** (resolves `target_db_base_path`, DuckDB warehouse, Control Plane SQLite, and `logs_directory`)
2. **FinancialRules TOML** (used strictly for profile context, target/budget reference overlays, and report provenance)

### Startup Safeguards
- **Immutable Snapshot Pinning**: Historical pages and report runs resolve the `cp_settings_snapshots` and `cp_rules_snapshots` linked to the selected PFETL `run_id` rather than assuming current TOML files produced past runs.
- **Profile Drift Check**: Compares explicit TOML leaves against the latest successful PFETL run snapshots and reports `MATCH`, `DRIFTED`, or `UNVERIFIED DEFAULTS`.
- **Catalog Compatibility Check**: Inspects all 82 expected DuckDB/Control Plane surfaces at startup (`COMPATIBLE` / `INCOMPATIBLE`) before analytical queries execute.

---

## 3. Analytical Workspaces

Studio provides 9 dedicated workspaces in a responsive NiceGUI interface:

| Workspace | Route | Purpose & Key Capabilities |
|---|---|---|
| **Overview** | `/overview` | Executive Net Worth, Total Income, Total Expense, Net Cashflow, Savings Rate, Portfolio Value, Indicative Tax Exposure, FIRE readiness, Attribution Waterfall, and Data Health strip. |
| **Household** | `/household` | Income composition (active salary, dividends, interest, non-cash EPF), core vs non-core expense analytics, lifestyle creep & consistency signals, budget guardrails, and cashflow waterfall. |
| **Wealth** | `/wealth` | Market vs Cost Net Worth trajectory, asset-group allocation, liquid vs illiquid breakdown, and savings-vs-market-return wealth attribution. |
| **Investments** | `/investments` | Portfolio summary, XIRR & time-weighted return, benchmark comparison, multi-currency & country FX exposure, sector/industry/instrument allocation, and active holdings. |
| **Investments Evidence** | `/investments-evidence` | Granular lot-level (`fact_investment_lot_analytics`), realized gain/loss, dividend/corporate-action, and cost-basis reconciliation drill-downs. |
| **Tax** | `/tax` | Multi-FY tax position, income-head & sub-head breakdown, TDS/advance tax credits, equity/debt harvesting opportunities, LTCG maturity ladder, and canonical realized/tax events (clearly marked indicative). |
| **FIRE** | `/fire` | Target/Lean/Coast FI milestones, P10/P50/P90 Monte Carlo horizons, success probability, savings gap & velocity, Guyton-Klinger withdrawal guardrails, and `cp_simulation_runs` provenance. |
| **Operations & Forensics** | `/operations` | Control Plane run lifecycle & sync outcomes, source file registry & artifact convergence events, external provider requests, failure domains, warehouse contract/row-count health, Settings/Rules structural snapshot diff, and sequence-ordered JSONL forensic event tree. |
| **Data Explorer** | `/explorer` | Whitelisted guided dataset builder (filter, group, aggregate, sort, CSV export) and read-only SQL workbench (`SELECT`/`WITH` only) with Saved Views persisted to `studio.sqlite`. |
| **Reports** | `/reports` | Searchable, paginated Built-in Templates (15) & Custom Reports gallery, interactive Custom Report Editor, PFETL run selector, provenance diff banner, and PDF/PPTX/XLSX/PNG/SVG exports. |

---

## 4. Built-in & Custom Reports

### 15 Certified Built-in Templates
All built-in templates use presentation-deck pacing (`PageBreakBlock` slide boundaries, 4-up KPI cards, dark-themed Plotly charts, and bounded tables):

1. **Financial & Wealth Reviews**: `monthly_financial_review`, `financial_year_review`, `net_worth_wealth_attribution`
2. **Household, Cashflow & Income**: `cashflow_budget_review`, `expense_lifestyle_review`, `income_resilience_review`
3. **Portfolio, Performance & FX**: `portfolio_review`, `investment_performance_benchmark`, `multi_currency_exposure_review`
4. **Tax & FIRE Planning**: `investment_tax_harvesting`, `tax_position_review`, `fire_planning_review`
5. **Operations, Forensics & Evidence**: `data_health_operations`, `run_forensic`, `source_evidence_change`

### Custom Report Editor & Rerun Lifecycle
- **Compose & Clone**: Create blank custom reports or clone any of the 15 built-in templates without mutating `DEFAULT_REPORT_REGISTRY`.
- **71 Registered Block Sources + Saved Views**: Add grouped KPI, Chart, Table, Explorer `SavedViewBlock` (table-only), `TitleBlock`, `SubtitleBlock`, `NarrativeBlock`, `ProvenanceBlock`, `DividerBlock`, and `PageBreakBlock` blocks.
- **Reorder, Filter & Validate**: Move blocks up/down, configure global and per-block `FilterSet` overrides, and validate broken `source_id` / deleted `SavedView` references with actionable remediation messages.
- **Deterministic Rerun & Provenance Diff**: Preview or rerun any built-in or saved custom report against the active run, latest successful run, or any historical PFETL run, with an automatic **Provenance Changes Since Prior Run** diff banner.

### Export Formats
- **Landscape PDF** (`ReportLab` + `Kaleido`): Deterministic multi-page presentation deck with provenance stamp, KPI grids, embedded vector/raster charts, and formatted tables.
- **Editable 16:9 PPTX** (`python-pptx`): Native editable slide deck with styled KPI cards, chart slides, and tables.
- **Excel Workbook** (`XlsxWriter`): Multi-sheet `.xlsx` with formatted data tables, embedded chart images, and a dedicated Provenance sheet.
- **Chart Assets & Tables**: Individual Plotly charts export to **PNG** and **SVG**; Explorer & UI tables export to **CSV**.

---

## 5. Quickstart & CLI

### Prerequisites
- **Python >= 3.13**
- **[uv](https://docs.astral.sh/uv/)** package manager
- **Chrome / Chromium** (required by Kaleido for PNG/SVG/PDF chart rendering)

### Installation & Launch
```bash
# Sync environment from uv.lock
uv sync

# Launch Analytics Studio on http://127.0.0.1:8080
uv run pfetl-studio

# Launch directly with explicit PFETL profile TOMLs
uv run pfetl-studio --settings /path/to/settings.toml --rules /path/to/financial_rules.toml

# Development mode (no automatic browser open)
uv run poe dev
```

---

## 6. Engineering Workflows & Quality Gates

Tasks are orchestrated with `poe` (`poethepoet`):

```bash
# Fast Quality Gate (Ruff Lint, Ruff Format Check, Mypy)
uv run poe audit-fast

# Full Quality Gate (Ruff, Mypy, Pyright, Pytest)
uv run poe audit-full

# Pylint Check
uv run pylint src tests

# Automated Conventional Commits & Release Helpers
uv run poe commit:preview
uv run poe commit:run
uv run poe pr:create
uv run poe release:pipeline
```

---

## 7. Repository Structure

```text
pfetl-analytics-studio/
├── pyproject.toml                   # Package metadata, dependencies & poe tasks
├── uv.lock                          # Locked Python >=3.13 environment
├── ruff.toml / mypy.ini             # Static analysis configuration
├── src/pfetl_analytics_studio/
│   ├── __init__.py                  # Package version & metadata
│   ├── __main__.py                  # Module executable
│   ├── cli.py                       # pfetl-studio CLI entrypoint
│   ├── app.py                       # NiceGUI application & routing shell
│   ├── context.py                   # AppSessionManager, AppContext & routes
│   ├── profile.py                   # Settings/Rules TOML loader & drift check
│   ├── state.py                     # Studio-owned SQLite persistence
│   ├── data/                        # Read-only DuckDB, Control Plane & JSONL log readers
│   ├── semantic/                    # Periods, filters, metrics & dataset catalog
│   ├── analytics/                   # Typed read-only domain analytical services
│   ├── pages/                       # NiceGUI workspace pages & Custom Report Editor UI
│   ├── reports/                     # Report runtime, registry, editor, templates & exporters
│   └── ui/                          # Shared theme, formatting, tables & Plotly builders
└── tests/                           # Unit, integration, UI & report export tests
```
