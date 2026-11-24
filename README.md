# PFETL Analytics Studio

Local-first, read-only analytical companion to Personal Finance ETL (PFETL) for visual exploration, reporting, and operational intelligence.

---

## 1. Product Mission & Invariants

PFETL Core calculates financial truth; Analytics Studio turns that truth into interactive exploration, visual drill-downs, operational intelligence, and downloadable reports.

### Hard Boundary
- **Read-Only**: Studio **never** writes to PFETL DuckDB, Control Plane SQLite, Settings TOML, or FinancialRules TOML.
- **Zero Reimplementation**: Studio never reimplements FIFO, tax classification, reconciliation, XIRR, or Monte Carlo methodology.
- **Local Loopback**: Studio binds to `127.0.0.1` by default and rejects non-loopback hosts.
- **No Direct Vendor Coupling**: Studio queries warehouse tables via DuckDB SQL and SQLite URIs (`mode=ro`).

---

## 2. Quickstart

### Prerequisites
- Python >=3.13
- [uv](https://docs.astral.sh/uv/) package manager

### Launch Application
```bash
# Start Analytics Studio on localhost:8080
uv run pfetl-studio

# Start in development mode without opening a browser
uv run poe dev
```

---

## 3. Engineering Workflows & Quality Gate

Orchestrated using **Project Forge** (`pforge`):

```bash
# Run Fast Quality Gate (< 2s: Ruff Lint, Format, Mypy)
uv run poe audit-fast

# Run Full Quality Gate (Ruff, Mypy, Pyright, Pytest)
uv run poe audit-full

# Automated Conventional Commits
uv run poe commit:preview
uv run poe commit:run

# GitHub PR and Release Automation
uv run poe pr:create
uv run poe release:pipeline
```

---

## 4. Architecture Overview

```text
src/pfetl_analytics_studio/
├── __init__.py           # Package version & metadata
├── __main__.py           # Module executable
├── cli.py                # Command-line entrypoint (pfetl-studio)
├── app.py                # NiceGUI application factory
├── profile.py            # Settings/Rules resolution & drift check
├── state.py              # Studio-owned SQLite state persistence
├── data/                 # DuckDB, Control Plane & Log I/O
├── semantic/             # Periods, filters, metrics, datasets metadata
├── analytics/            # Typed analytical read services
├── pages/                # NiceGUI presentation pages
├── reports/              # PDF & Excel report generators
└── ui/                   # Reusable UI components & formatting
```

