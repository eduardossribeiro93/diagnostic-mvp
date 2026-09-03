"""Step 1 - New diagnostic: pick the client's files, set the assumptions, run."""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

import theme  # noqa: E402  (sets sys.path for `core`)

theme.apply("New diagnostic")

from core.config import DiagnosticConfig  # noqa: E402
from core.pipeline import run_diagnostic  # noqa: E402

ROOT = theme.ROOT
DEFAULT_DATASET = ROOT / "dataset"


@st.cache_data(show_spinner=False, max_entries=4)
def cached_run(sales: str, inventory: str, suppliers: str, po: str | None,
               cfg_dict: dict, _stamps: tuple):
    """Cache on the inputs so a browser refresh doesn't cost another minute.

    `_stamps` carries the file mtimes so edited exports invalidate the cache.
    """
    return run_diagnostic(sales, inventory, suppliers, po, DiagnosticConfig(**cfg_dict))

st.markdown('<div class="wc-eyebrow">Working Capital Optimizer</div>', unsafe_allow_html=True)
st.markdown('<div class="wc-title">Inventory Cash Diagnostic</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="wc-sub">What should we own, what should we buy, what should we stop '
    "buying, and what should we liquidate?</div>",
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# Source files
# --------------------------------------------------------------------------- #
theme.section("1 - Source files")

mode = st.radio(
    "File source", ["Use a folder on disk", "Upload files"], horizontal=True,
    label_visibility="collapsed",
)

paths: dict[str, Path | None] = {}
if mode == "Use a folder on disk":
    folder = Path(st.text_input("Folder containing the client's exports", str(DEFAULT_DATASET)))
    guesses = {"sales": "Sales", "inventory": "Stock", "suppliers": "Supplier", "po": "Purchase"}
    found = sorted(folder.glob("*.csv")) if folder.exists() else []
    if not folder.exists():
        st.error(f"Folder not found: {folder}")
    cols = st.columns(4)
    labels = {
        "sales": "Sales (required)",
        "inventory": "Inventory / Stocks (required)",
        "suppliers": "Suppliers (required)",
        "po": "Purchase orders (optional)",
    }
    options = ["(none)"] + [f.name for f in found]
    for col, key in zip(cols, labels):
        default = next(
            (i for i, f in enumerate(found) if guesses[key].lower() in f.name.lower()), None
        )
        idx = default + 1 if default is not None else 0
        with col:
            choice = st.selectbox(labels[key], options, index=idx)
        paths[key] = folder / choice if choice != "(none)" else None
else:
    cols = st.columns(4)
    uploads = {
        "sales": cols[0].file_uploader("Sales (required)", type=["csv"]),
        "inventory": cols[1].file_uploader("Inventory (required)", type=["csv"]),
        "suppliers": cols[2].file_uploader("Suppliers (required)", type=["csv"]),
        "po": cols[3].file_uploader("Purchase orders (optional)", type=["csv"]),
    }
    tmp = Path(tempfile.mkdtemp())
    for key, up in uploads.items():
        if up is not None:
            dest = tmp / up.name
            dest.write_bytes(up.getbuffer())
            paths[key] = dest
        else:
            paths[key] = None

if paths.get("po") is None:
    st.caption(
        "No purchase-order file: **avoidable purchases** will be reported as N/A "
        "and excluded from the headline. Everything else is unaffected."
    )

# --------------------------------------------------------------------------- #
# Assumptions
# --------------------------------------------------------------------------- #
theme.section("2 - Assumptions")
st.caption("Every figure downstream depends on these. They are exposed so you can flex them.")

c1, c2, c3, c4 = st.columns(4)
with c1:
    client_name = st.text_input("Client name", "Friguarda")
    currency = st.text_input("Currency", "EUR")
with c2:
    sl_a = st.slider("Service level - A", 0.80, 0.999, 0.98, 0.005, format="%.3f")
    sl_b = st.slider("Service level - B", 0.80, 0.999, 0.95, 0.005, format="%.3f")
    sl_c = st.slider("Service level - C", 0.80, 0.999, 0.90, 0.005, format="%.3f")
with c3:
    review = st.select_slider(
        "Review period (how often they order)",
        options=[0.25, 0.5, 1.0, 2.0],
        value=0.25,
        format_func=lambda v: {0.25: "Weekly", 0.5: "Fortnightly", 1.0: "Monthly", 2.0: "Bi-monthly"}[v],
    )
    lt_unit = st.selectbox(
        "Lead-time unit in the supplier file", ["months", "weeks", "days"], index=0,
        help="Lead time is normally expressed in the same unit as the sales grain (months).",
    )
    default_lt_median = st.checkbox("Default missing lead times to the observed median", True)
with c4:
    horizon = st.slider("Forecast horizon (months)", 3, 12, 6)
    carrying = st.slider("Carrying cost rate (annual)", 0.05, 0.40, 0.20, 0.01)
    dead_months = st.slider("Dead stock: no sales for N months", 3, 24, 12)

config = DiagnosticConfig(
    client_name=client_name,
    currency=currency,
    service_level_a=sl_a,
    service_level_b=sl_b,
    service_level_c=sl_c,
    review_period_months=review,
    lead_time_unit=lt_unit,
    default_lead_time_from_median=default_lt_median,
    forecast_horizon_months=horizon,
    carrying_cost_rate=carrying,
    dead_stock_months=dead_months,
)

# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
theme.section("3 - Run")

required_ok = all(paths.get(k) and Path(paths[k]).exists() for k in ("sales", "inventory", "suppliers"))
if not required_ok:
    st.warning("Select the three required files to enable the run.")

if st.button("Run diagnostic", type="primary", disabled=not required_ok):
    with st.spinner("Reconciling files, segmenting demand, backtesting models, forecasting..."):
        used = [paths["sales"], paths["inventory"], paths["suppliers"], paths.get("po")]
        stamps = tuple(Path(p).stat().st_mtime if p else 0 for p in used)
        result = cached_run(
            str(paths["sales"]), str(paths["inventory"]), str(paths["suppliers"]),
            str(paths["po"]) if paths.get("po") else None,
            config.to_dict(), stamps,
        )
    st.session_state["result"] = result
    st.session_state["config"] = config
    st.session_state["approved"] = False
    st.success(
        f"Done in {result.meta['runtime_seconds']}s - {result.meta['n_skus']:,} SKUs, "
        f"data-quality score {result.quality_score}/100."
    )

result = st.session_state.get("result")
if result is not None:
    ro = result.rollup
    st.markdown(
        f'<div class="wc-headline">{theme.money(ro["opportunity_value"], result.config.currency)} '
        "identified inventory opportunity</div>",
        unsafe_allow_html=True,
    )
    cols = st.columns(4)
    with cols[0]:
        theme.tile("Excess inventory", theme.money(ro["excess_value"], result.config.currency),
                   "cash to release", accent=True)
    with cols[1]:
        theme.tile("Avoidable purchases", theme.money(ro["avoidable_value"], result.config.currency),
                   "cash to save" if ro["has_purchase_orders"] else "no PO file supplied")
    with cols[2]:
        theme.tile("Inventory shortfalls", theme.money(ro["shortfall_value"], result.config.currency),
                   "cash to invest")
    with cols[3]:
        theme.tile("SKUs requiring action", f"{ro['skus_requiring_action']:,}",
                   f"of {ro['n_skus']:,} total")
    st.caption("Review the data quality, then the results, then approve to generate deliverables.")
