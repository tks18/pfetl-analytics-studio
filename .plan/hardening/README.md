# PFETL Analytics Studio 1.0 — Production Hardening & Closure

## Purpose

This program hardens the completed `pfetl-analytics-studio 1.0.0` implementation before standalone packaging and later PFETL Core integration.

It is grounded in the actual 1.0.0 source. The application is already feature-complete; this is **not a redesign** and does not reopen the 33-phase product plan.

The goal is to bring Studio closer to PFETL Core's operating rigor:

```text
feature-complete Studio
    ↓
runtime discipline
    ↓
UX / domain polish
    ↓
confidence tests
    ↓
server lifecycle + integration readiness
    ↓
certification
    ↓
packaging + PFETL Core integration planning
```

## Authority

For this closure program:

> **Current 1.0.0 source + this README + the active phase file = implementation authority.**

Do not infer new scope from old planning documents when the current source already defines the product.

## What must remain unchanged

Do not redesign:

- `PFETLProfile` and the Settings + FinancialRules input contract;
- read-only DuckDB and Control Plane access;
- the 82-surface catalog;
- `AppContext`;
- period and filter semantics;
- `DatasetSpec` / `MetricSpec` safety;
- `MetricOrigin`;
- report registry/block architecture;
- Studio-owned SQLite;
- NiceGUI;
- Plotly;
- the PFETL boundary;
- canonical PFETL finance methodology.

Studio remains a **read-only analytical consumer**. It may derive presentation statistics, but it must not reproduce PFETL's financial accounting methodology.

## Phase map

| Phase | Goal |
|---|---|
| **01 — Runtime & Performance Hardening** | Remove event-loop blocking, make heavy domain tabs genuinely lazy, and eliminate avoidable large-memory export/download paths. |
| **02 — UX, Domain & Confidence Hardening** | Improve dense workflows, tighten domain presentation semantics, and raise domain/report/explorer test confidence without expanding scope. |
| **03 — Production Closure & Integration Readiness** | Add a stable Studio server lifecycle contract, health/readiness and port/runtime metadata, complete production checks, and leave Studio ready for later PFETL Core orchestration and packaging. |

## Closure rule

After all three phases are green, stop modifying architecture and perform a fresh certification archaeology.

Only after that certification should we design:

1. standalone packaging;
2. PFETL Core start/stop integration;
3. combined distribution;
4. the final public README and branding pass.
