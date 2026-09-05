# Working notes for Claude

The README explains *what* this is and how the numbers are built. This file records the
decisions behind it, the traps already hit, and the conventions to follow — so a fresh
session doesn't re-litigate settled choices or rediscover the same bugs.

Full plan: `docs/plan.md`. The working copy Claude Code edits lives at
`~/.claude/plans/hey-as-context-i-virtual-waterfall.md` — if the plan changes, re-copy it so
the tracked version doesn't drift.

## What this is, and what it deliberately is not

An **operator-run** diagnostic for PE operating partners auditing mid-market portcos. The
analyst runs it, reviews exceptions, approves, and hands the client an executive PDF plus
an Excel action list. **The deliverables are the product**; the Streamlit console exists to
produce and defend them. It is architected for a client-facing app *later* — hence `core/`
never imports Streamlit — but v1 is not self-serve.

Commercial hypothesis: *will a PE partner hand over portco data in exchange for a credible,
defensible cash-release estimate?* That is why auditability beats automation here.

Scope is the **inventory** lever of working capital only. Not AR/AP. Say so plainly rather
than implying a full working-capital audit.

## Settled decisions (don't reopen without a reason)

- **Target = order-up-to level.** Demand over (lead time + review period) + safety stock,
  where safety stock is the SKU's own backtested forecast error at its ABC service level.
- **Service levels are ABC-tiered and operator-set**, not hardcoded.
- **Monthly grain, 6-month horizon**, starting the month after training — i.e. the dropped
  partial current month. Sales arrive as `Year|Month`, so no date parsing.
- **All locations aggregate to one pool.** Sales carry no location, so per-location targets
  would be false precision.
- **Nixtla `statsforecast`**, routed by demand segment. Statistical and explainable, not ML
  — the audit trail records each SKU's chosen model. Do not swap in neural forecasting.
- **Currency is fixed at EUR** (the config default). It is not asked for per run.
- **Carrying cost was removed on purpose.** Opportunity is measured as *stock value*, not
  opportunity cost. Don't reintroduce a carrying-cost metric.
- **Client name is not an assumption** — it identifies the run and names the deliverables,
  so it lives in the Source files section.
- **Optional inputs degrade honestly.** No PO file → avoidable purchases is reported as
  **N/A**, never a misleading zero. No unit cost → falls back to selling price, which makes
  valuations an **upper bound**, and that is flagged.

## Client data must never be committed

`dataset/` and `clients/` are gitignored (`.gitkeep` placeholders keep the structure). Real
portfolio-company data and the deliverables generated from it stay local. **Always check
`git status` for stray CSV/XLSX/PDF before committing.** The test suite builds its own
synthetic fixtures, so a fresh clone is fully runnable with no client data.

## Traps already hit — don't rediscover these

- **Streamlit does not reload imported modules.** Editing `app/theme.py` needs a *server
  restart*; a browser refresh reruns only `Home.py`. Symptom: some edits appear, CSS doesn't.
- **Don't style Streamlit internals by CSS class.** Bordered containers carry an unstable
  `st-emotion-cache-*` hash, and `stVerticalBlockBorderWrapper` doesn't exist in this
  version. Use `theme.borderColor` etc. in `.streamlit/config.toml`.
- **Uploads need content-hashed, session-stable paths.** Streamlit reruns on every widget
  change; a fresh `mkdtemp()` per rerun gives the same file a new path, defeating the run
  cache and turning every click into a full re-run.
- **`default_lead_time_from_median=True` overrides `default_lead_time`** in `reconcile.py`.
  When the operator types a figure, the flag must be `False` or the input is silently
  discarded. ~73% of stocked SKUs depend on this number.
- **SKU is always a string.** Leading zeros matter (`0001`), some are non-numeric (`PEUR`),
  and every Stocks SKU has trailing whitespace that must be trimmed or all joins fail.
- **`autoPort` doesn't work for Streamlit** — it ignores the harness `PORT` and picks its
  own, so the two disagree. Keep the explicit port in `.claude/launch.json`.
- **Browser automation can't drive Streamlit's virtualized selectbox.** Synthetic events
  don't reach its React state. Use `streamlit.testing.v1.AppTest` to test widget behaviour.

## The client dataset (Friguarda), and its limits

~1,921 SKUs, 26 complete months (2024-07 → 2026-08); the partial current month is dropped
from training and becomes the first forecast period. Data-quality score lands around 55.

- **Only ~27% of stocked SKUs have a real lead time.** This is the single biggest accuracy
  caveat and the highest-value thing to request from the client.
- No `Unit Cost` and no `Location` in the stock file → price-proxy valuations, single pool.
- POs: 154 rows / 133 SKUs, zero orphans, 16 SKUs with 2–3 batches, and **122 (79%) already
  overdue**. Overdue count as inbound (client's call) but are flagged — if they were in fact
  already received, those units also sit in on-hand and would be double-counted.
- ~531 SKUs sell but are absent from the stock file → treated as zero on-hand, and tagged.

## Conventions

```bash
.venv/bin/python -m pytest tests/ -q     # 52 tests, all should pass
.venv/bin/streamlit run app/Home.py      # console on :8501
```

- Keep `core/` free of UI imports — that separation is the point.
- New behaviour gets a test that pins *why*, not just that it runs.
- Design language follows OpenRouter: near-black ground, one lime accent used sparingly,
  hairline borders, section = icon + title + muted comment, then content in a card.
- Verify browser-visible changes by actually looking at them, and measure rather than trust
  a screenshot — several "fixes" above looked right while doing nothing.
- Commit messages explain the reasoning, not just the change.

## Open threads

- Ask the client for lead times (biggest single improvement to the numbers).
- The section icon/description/card pattern is applied on Home only; Data Quality, Results
  and Approve still need it.
- Deliberately out of v1: client self-serve, ERP APIs, ML forecasting, multi-echelon,
  per-SKU months-of-cover override, achieved service-level baseline, AR/AP, auth/cloud.
- Repo is private: `github.com/eduardossribeiro93/diagnostic-mvp`. A GitHub Actions
  workflow syncs to a Hugging Face Space (`sdk: streamlit`, `app_file: app/Home.py`).
