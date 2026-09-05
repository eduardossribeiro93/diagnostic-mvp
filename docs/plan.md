# Working Capital Optimizer — MVP Plan (v1: "Inventory Cash Diagnostic", operator-run)

## Context
PE operating partners auditing traditional mid-market portfolio companies need a **fast, standardized, defensible** answer to one question: *"What inventory should we own, what should we buy, what should we stop buying, and what should we liquidate?"* — in cash. This plan is the output of a discovery discussion (and a cross-check against a second model's recommendations).

v1 is an **analyst-operated diagnostic**, not client self-serve: the operator runs it, reviews exceptions, **approves**, then hands over deliverables (executive PDF + Excel action list). The Streamlit app is the operator's internal console; the *deliverables are the product*. It's architected so the same engine later exposes a self-serve client app ("diagnostic now, optimizer later"). Scope is the **inventory** lever of working capital only — position it honestly as an *Inventory Cash Diagnostic*.

Commercial hypothesis being tested: *"Will a PE operating partner hand over portfolio-company data in exchange for a credible, defensible cash-release estimate?"* — hence operator-run + auditability over automation.

## Product decisions (settled in discovery)
- **Positioning:** operator-run console + human-approval gate now; self-serve later. Local-first (no cloud/auth/multi-tenancy in v1).
- **Audience/output:** layered — exec summary (PDF export) drilling into a comprehensive SKU worklist (Excel export), both operated by the analyst.
- **Data in:** CSV/Excel ERP exports — **3 required files** (Sales, Inventory, Supplier) + **optional Purchase Orders** (column-map + SKU-key reconciliation on load).
- **Location:** aggregate all locations to one company-level pool (Sales carry no location).
- **Aggregation:** monthly buckets (default).
- **Forecasting:** Nixtla `statsforecast` — segment → route to model family → backtest-select → forecast **6 months** + prediction intervals, the horizon starting at the first month after training (= the dropped partial current month). (Statistical, explainable models; every SKU's chosen model is recorded in the audit trail.)
- **Target ("optimal"):** order-up-to level (future: per-SKU months-of-cover override column).
- **Service level:** ABC-tiered, parameterized at ingestion with defaults, exposed with live sensitivity.
- **Stack:** Streamlit thin console over a **decoupled, pure-Python calc core** (zero UI imports → swappable to a client web app later).

## Data contract (ingestion)
Three **required** uploads + an **optional** fourth (Purchase Orders); column-mapping + **SKU-key reconciliation across files** (e.g. sales "Material Number" ↔ inventory "SKU"), reporting orphans:
- **Sales** *(required)*: `Year | Month | SKU | Quantity | Price`  (monthly grain = our bucketing, no date parsing needed. **Revenue is derived = Quantity × Price**; Price = avg unit price → ASP/margin. Handle negative/return rows; sum duplicate SKU-months with a quantity-weighted Price.)
- **Inventory** *(required)*: `SKU | Units` (+ **optional** `Location`, `Unit Cost`, `Description`) → aggregate Units across any Location → on-hand `H`. If `Unit Cost` is absent/zero for a SKU, fall back to that SKU's latest Sales `Price` as a cost proxy (weighted-avg Price as secondary fallback) — flagged in data quality, since it makes valuations an upper bound.
- **Supplier** *(required)*: `SKU | Supplier | Lead Time | MOQ` (+ optional `Name`) — `Supplier` is the system code; `Name` is the display description, used in the supplier-exposure rollup and falling back to the code when absent.
- **Purchase Orders** *(optional)*: `SKU | Ordered Qty | Expected Delivery Date` → open PO qty `P`; **position = H + P**. **Multiple batches per SKU are summed** into one `open_po`. `Expected Delivery Date` **splits `P` by arrival**: `P_in` = batches landing within the coverage window (lead time + review period), which cover near-term demand; `P_beyond` = later batches, still committed spend but not covering the window. **Overdue POs (ETA in the past) count as inbound** — assumed late, so they land inside the window — with the overdue count flagged in data quality, since if they were in fact already received their units also sit in on-hand. Missing ETA → treated as in-window. If no PO file, `P = 0` (position = on-hand) and *avoidable purchases* is reported as **N/A** and dropped from the headline.

**Sample dataset** (`dataset/`: `Sales.csv`, `Stocks.csv`, `Suppliers.csv`, `PurchaseOrders.csv` — 26 complete months, ~1,880 SKUs). POs: 154 rows / 133 SKUs, **zero orphans** (all reconcile to Stocks), **16 SKUs carry 2–3 batches** at different dates, and **122 of 154 (79%) are already overdue** — counted as inbound per decision, but flagged, since any already received would also sit in on-hand. Real-world quirks handled on ingest: **trim trailing whitespace** on Stocks SKUs; SKU is a string (leading zeros + non-numeric like `PEUR`); ignore the redundant first-of-month `Date` (use Year+Month); **drop the trailing partial month**; Stocks has no Location (single pool) and no Unit Cost (Price proxy); SKUs **sold but absent from Stocks → on-hand 0, tagged** "sold, not in stock file"; only ~27% of stocked SKUs have a real lead time → default lead time applied to the rest (flagged as the top accuracy caveat — worth requesting from the client).

## Configuration (assumptions, defaults editable)
ABC service levels (98/95/90) · ABC revenue thresholds (80/15/5) · review period · forecast horizon (6 mo) · **minimum history** (below which a SKU can't be forecast → flag/fallback) · **carrying-cost rate** (e.g. 20%/yr, for carrying-cost-freed metric) · dead-stock window N (no sales in N months → E&O) · **default lead time** (applied to SKUs with no supplier lead time, flagged). Persisted per client in `config.yaml`.

## Data audit + data-quality score
Runs before the math AND surfaced in the exec output as a confidence caveat. Checks: unmapped/duplicate SKU, no supplier lead time (→ default lead-time parameter applied, flagged), negative inventory, **unit cost missing/zero → proxied from selling Price (valuations become an upper bound)**, orphan POs and **overdue POs** (if provided — overdue count as inbound but are flagged), sales-but-no-stock (censored demand), stock-but-no-sales (dead), SKUs below minimum history. Emits per-check coverage %, a per-SKU confidence, and a single **data-quality score (0–100)** for the diagnostic.

## Calc core pipeline (pure Polars/statsforecast, zero UI imports)
1. **Ingest + reconcile + normalize:** build a month-start `period` (`YYYY-MM-01`) from Year+Month; aggregate duplicate SKU-months (sum Qty, quantity-weighted Price, derive Revenue); resolve **effective unit cost** per SKU (Unit Cost, else latest Sales Price proxy); net returns; `position = H + P`, splitting `P` into `P_in` / `P_beyond` by ETA (`P = 0` when POs not provided); run audit + quality score.
2. **Classify:** ABC by revenue; **XYZ**-style demand pattern (regular vs intermittent) to route the forecaster; E&O flag (no sales in N months).
3. **Forecast (`statsforecast`, 6 monthly steps + intervals; the horizon begins the month after training — i.e. the dropped partial current month):** intermittent family (Croston/TSB/ADIDA/IMAPA) for sparse SKUs; AutoETS/AutoARIMA/AutoTheta for regular ones; select per-SKU by a single holdout backtest (MASE/RMSSE). Below minimum history → fallback (category proxy / non-seasonal). The selection backtest already produces holdout forecasts vs actuals, so per-SKU accuracy metrics are a near-free byproduct (persisted per run — see step 8). *Caveat: monthly + ~2yr ≈ 24 pts is the floor for annual seasonality — noted in data quality.*
4. **Policy — target `T` = order-up-to level:** demand over lead time + review period + safety stock (from the forecast prediction interval at the SKU's ABC service level).
5. **Diagnose (consistent, non-double-counting decomposition; all € use the effective unit cost):**
   - **Excess / liquidate (€):** `max(H − T, 0) × cost`
   - **Avoidable purchases / stop buying (€):** `max(H + P − T, 0) − max(H − T, 0)` × cost (portion of **all** open POs above target — later batches are still cancellable committed spend). **Requires POs** — reported as N/A and excluded from the headline when not provided.
   - **Shortfall / buy (€):** `max(T − (H + P_in), 0) × cost` (respect MOQ on buy qty) — only POs **arriving within the coverage window** offset near-term demand; a batch landing after it does not.
   - **E&O / dead-stock (€):** separate first-class bucket — full remaining stock of no-demand SKUs (usually the biggest quick win)
   - Per-SKU **Recommendation** (Liquidate / Buy / Stop inbound / Hold) + **ABC priority** + confidence.
6. **Roll-ups:** company headline; Top-20 cash-release + Top-20 supply risks; ABC summary; **supplier-exposure rollup** (cash & lead-time locked per supplier); **PE metrics** — inventory value, DIO, turns, and **carrying-cost freed**, shown *before → target-state* (DIO/turns computed on **trailing-12-month actual COGS** from history, so they're unaffected by the forecast horizon).
7. **Audit trail:** for every € the engine records the full chain `€ total → bucket → SKU → excess/short units → target → service level → lead time → history → unit cost → chosen model`, so any figure is defensible in a management meeting.
8. **Forecast accuracy log (per run):** from the same selection backtest, persist per-SKU metrics — MASE, RMSSE, sMAPE, **BIAS**, RMSE, MAPE (only where actual > 0), and prediction-interval coverage — plus chosen model, n_obs, ABC class, demand segment. Write `outputs/forecast_accuracy_<ts>.csv` — which also carries **each horizon month's forecast quantity as its own column**, pivoted from the per-SKU/per-month forecast the engine already produces, so the client can read expected sales per SKU per month (export only; not surfaced in the app) — plus a `run_summary_<ts>.json` of aggregates (overall & by segment). Near-free (backtest already runs); an aggregate line also appears in the exec Methodology. *MAPE is unreliable for intermittent/zero-demand SKUs — MASE/RMSSE/BIAS are the primary indicators there.*

## Operator workflow (screens + approval gate)
1. **New diagnostic:** select/create client project → upload 3 required files (+ optional POs) → map columns → set config → Run.
2. **Data quality & review:** quality score + flags; analyst investigates exceptions.
3. **Results (exec + ops):** exec summary drilling into the SKU worklist; each figure expands to its audit trail; analyst can **override/exclude** SKUs.
4. **Approve & generate:** approval gate → emit `Company_Diagnostic.pdf` + `Company_SKU_Actions.xlsx` into the client's `outputs/`.

## Exec summary (adopted layout) → PDF export
```
Working Capital Diagnostic — Company X
€3.4m identified inventory opportunity            ← excess + avoidable(if POs) + shortfall (labeled release vs invest)
  €1.8m excess inventory        (cash to release)
  €0.9m avoidable purchases     (cash to save)    ← shown only when POs provided; else N/A
  €0.7m inventory shortfalls    (cash to invest)
  183 SKUs requiring action
  DIO / turns / carrying-cost freed:  before → target
  Top 10 cash-release opportunities · Top 10 supply risks · Supplier exposure
  Methodology / Assumptions / Data-quality score
```

## Excel export (all SKUs)
`SKU | Description | Stock | Target | Excess | € Excess | Recommendation | Priority (ABC)` (+ confidence / audit fields).

## Design language (OpenRouter-inspired)
Professional but modern — calm, dense-but-airy, restrained color.
- **Palette:** near-black canvas (#0D0D0F), lifted panels (#151518) with hairline borders (#26262B); grayscale text hierarchy (white values, muted gray body, tiny UPPERCASE micro-labels); a single **lime/chartreuse accent** (~#C6F24E) used sparingly for CTAs, active states, primary chart series.
- **Layout:** top bar + left vertical section nav (icon+label, active highlighted); rounded cards; **stat tiles** for the exec KPIs; sortable tables; sub-tabs underline-active.
- **Charts (Plotly):** one shared dark template (transparent bg, faint gridlines, lime accent, Inter-like font).
- **Streamlit:** `.streamlit/config.toml` base theme + custom CSS (`st.markdown`) for cards/micro-labels/tables/nav + a single Plotly template. Won't be pixel-identical, but gets convincingly close.

## Explicitly OUT of v1
Client self-serve web app; live ERP integration/APIs; ML/neural forecasting; per-location targets / multi-echelon; per-SKU months-of-cover override; current achieved-service-level baseline (phase-2); AR/AP levers; supplier returns/price negotiation; scenario simulation beyond the config parameters; auth / multi-tenancy / cloud.

## Architecture & dependencies
```
diagnostic-mvp/
  core/          # pure Polars/statsforecast, zero UI imports — the portable engine (the IP)
    ingest.py reconcile.py audit.py classify.py forecast.py policy.py diagnose.py metrics.py audit_trail.py report.py
  app/           # Streamlit operator console (thin view layer)
    Home.py  pages/1_DataQuality.py  pages/2_Results.py  pages/3_Approve.py
  clients/       # per-client "diagnostic factory": company_x/{raw,processed,outputs}/config.yaml
  tests/
  requirements.txt / pyproject.toml
```
- **polars** (data pipeline; wins on multi-million-row sales), **statsforecast** + **utilsforecast** (Nixtla; accept Polars), **streamlit**, **plotly**, **openpyxl** (Excel), **WeasyPrint** (HTML→PDF; `reportlab`/`fpdf2` as dep-free fallback), **PyYAML** (config). **DuckDB** optional for very large files. Convert Polars→pandas only at UI/export boundaries, on the small result table.

## Verification
- Build a small **synthetic fixture** in the 4-file shape, plus variants **without POs** and **without Unit Cost** to exercise the optional paths (real dataset arrives later; `../industrial-data` unused).
- Unit tests on `policy.py` (target/safety stock), `diagnose.py` (excess/avoidable/shortfall never double-count; reconcile to roll-ups; no negative buys; excess ≤ on-hand; avoidable = N/A when no POs), `metrics.py` (DIO/turns/carry), and `audit_trail.py` (each € reconstructs from its chain).
- End-to-end via the console: run on the fixture → review data quality → drill an SKU to its audit trail → approve → confirm PDF + Excel emit with the specified fields; confirm a `forecast_accuracy_*.csv` + `run_summary_*.json` are written per run; flex ABC service levels and confirm the headline € (and DIO/turns) move (sensitivity works).
