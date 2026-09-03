---
title: Working Capital Optimizer — Inventory Cash Diagnostic
emoji: 🚀
colorFrom: red
colorTo: red
sdk: docker
app_port: 8501
tags:
- streamlit
pinned: false
short_description: MVP for Inventory Cash Diagnostic
---

# Working Capital Optimizer — Inventory Cash Diagnostic

An operator-run diagnostic for PE operating partners auditing mid-market portfolio
companies. It answers one question in cash:

> **What inventory should we own, what should we buy, what should we stop buying, and what should we liquidate?**

The analyst runs it, reviews the exceptions, approves, and hands the client an
executive PDF plus an Excel action list. The deliverables are the product; the
Streamlit console exists to produce and defend them.

## Quick start

```bash
.venv/bin/streamlit run app/Home.py
```

Then: **Home** (pick files, set assumptions, run) → **Data Quality** → **Results**
→ **Approve** (generate deliverables).

Run the tests with:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Input files

Three required, one optional. Column names are auto-detected (aliases incl. Portuguese),
and SKU keys are whitespace-trimmed and kept as strings so leading zeros survive.

| File | Columns | Notes |
|---|---|---|
| **Sales** (required) | `Year, Month, SKU, Quantity, Price` | Monthly grain. Revenue is derived (`Quantity × Price`). Duplicate SKU-months collapse with a quantity-weighted price. |
| **Inventory** (required) | `SKU, Units` (+ optional `Location`, `Unit Cost`, `Description`) | Locations aggregate to one pool. Missing `Unit Cost` falls back to the latest selling price — which makes valuations an **upper bound**, and is flagged. |
| **Supplier** (required) | `SKU, Supplier, Lead Time, MOQ` (+ optional `Name`) | Lead time is read in the **same unit as the sales grain** (months). Missing lead times take the median of those present. |
| **Purchase orders** (optional) | `SKU, Ordered Qty, Expected Delivery Date` | Without it, *avoidable purchases* is reported as **N/A**, not a misleading zero. |

## How the numbers are built

1. **Reconcile** — monthly periods, `position = on-hand + open POs`, effective unit cost.
   The incomplete current month is dropped from training and becomes the first
   forecast period.
2. **Classify** — ABC by trailing-12-month revenue; demand segment (regular /
   intermittent / insufficient / dead) routes each SKU to a model family.
3. **Forecast** — Nixtla `statsforecast`, 6 monthly steps. Seasonal/trend models for
   regular demand, the Croston/ADIDA/IMAPA family for sparse demand. One backtest
   both selects each SKU's model and measures its error.
4. **Policy** — `target = demand over (lead time + review period) + safety stock`,
   where safety stock is the SKU's own backtested forecast error scaled to the
   coverage window at its ABC service level.
5. **Diagnose** — the four answers, decomposed so no unit is ever counted twice:

   ```
   excess / liquidate    = max(H − T, 0)
   avoidable / stop      = max(H + P − T, 0) − max(H − T, 0)
   shortfall / buy       = max(T − (H + P), 0)      (MOQ applied to the buy qty)
   ```

   E&O is the dead-SKU slice of excess — a breakdown of it, never an addition.

## Why it is defensible

- **Audit trail** — every € reconstructs from the SKU up: model chosen, forecast
  error, lead time and its source, service level, z-score, safety stock, target,
  unit cost and its source. Visible per SKU in the console.
- **Data-quality score** — each assumption the run leaned on is counted and carried
  into the executive PDF, so caveats travel with the figures.
- **Forecast accuracy log** — every run writes `forecast_accuracy_<ts>.csv` and
  `run_summary_<ts>.json` (MASE, RMSSE, sMAPE, BIAS, RMSE, MAPE, interval coverage).
  MASE is the primary indicator; MAPE is unreliable for intermittent SKUs.
- **Human approval gate** — deliverables are only written after an analyst signs off.

## Layout

```
core/     pure Polars + statsforecast engine, zero UI imports (the portable IP)
app/      Streamlit operator console (a thin view over core)
clients/  per-client runs: <client>/outputs/ + config.yaml
tests/    unit + end-to-end, including headless renders of every app page
```

`core` never imports Streamlit, so the same engine can back a client-facing web app
later without touching the maths.

> **`dataset/` and `clients/` are gitignored.** Real portfolio-company data and the
> deliverables generated from it stay local and never enter version control. Drop the
> client's exports into `dataset/` to run; the test suite uses synthetic fixtures, so
> a fresh clone is fully runnable without any client data.
