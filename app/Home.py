"""Step 1 - New diagnostic: pick the client's files, set the assumptions, run."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import streamlit as st

import theme  # noqa: E402  (sets sys.path for `core`)

theme.apply("New diagnostic")

from core.config import DiagnosticConfig  # noqa: E402
from core.pipeline import run_diagnostic  # noqa: E402

ROOT = theme.ROOT


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
theme.section(
    "Source files",
    "The client's ERP exports. Sales, inventory and suppliers are required; purchase "
    "orders are optional and affect only the avoidable-purchases figure.",
    icon="files",
)

with theme.card():
    # Who the diagnostic is for - it titles the report and names the deliverables.
    name_col, _ = st.columns([1, 3])
    client_name = name_col.text_input("Client name", "Friguarda")

    cols = st.columns(4)
    uploads = {
        "sales": cols[0].file_uploader("Sales (required)", type=["csv"]),
        "inventory": cols[1].file_uploader("Inventory (required)", type=["csv"]),
        "suppliers": cols[2].file_uploader("Suppliers (required)", type=["csv"]),
        "po": cols[3].file_uploader("Purchase orders (optional)", type=["csv"]),
    }

    # Uploads land in one directory that survives reruns, named by content hash. Streamlit
    # reruns the script on every widget change, so a fresh temp dir each time would give
    # the same file a new path and defeat the run cache - every click a full re-run.
    if "_upload_dir" not in st.session_state:
        st.session_state["_upload_dir"] = tempfile.mkdtemp(prefix="wc_uploads_")
    tmp = Path(st.session_state["_upload_dir"])

    paths: dict[str, Path | None] = {}
    for key, up in uploads.items():
        if up is None:
            paths[key] = None
            continue
        data = up.getbuffer()
        dest = tmp / f"{hashlib.sha1(data).hexdigest()[:12]}_{up.name}"
        if not dest.exists():  # same content -> same path -> cache still valid
            dest.write_bytes(data)
        paths[key] = dest

    if paths.get("po") is None:
        st.caption(
            "No purchase-order file: **avoidable purchases** will be reported as N/A "
            "and excluded from the headline. Everything else is unaffected."
        )

# --------------------------------------------------------------------------- #
# Assumptions
# --------------------------------------------------------------------------- #
theme.section(
    "Assumptions",
    "Every figure downstream depends on these. They are exposed so you can flex them "
    "and watch the numbers move.",
    icon="sliders",
)

with theme.card():
    c1, c2, c3 = st.columns(3)
    with c1:
        sl_a = st.slider("Service level - A", 0.80, 0.999, 0.98, 0.005, format="%.3f")
        sl_b = st.slider("Service level - B", 0.80, 0.999, 0.95, 0.005, format="%.3f")
        sl_c = st.slider("Service level - C", 0.80, 0.999, 0.90, 0.005, format="%.3f")
    with c2:
        review = st.selectbox(
            "Review period (how often they order)",
            options=[0.25, 0.5, 1.0, 2.0],
            index=0,
            format_func=lambda v: {
                0.25: "Weekly", 0.5: "Fortnightly", 1.0: "Monthly", 2.0: "Bi-monthly"
            }[v],
        )
        lt_unit = st.selectbox(
            "Lead-time unit in the supplier file", ["months", "weeks", "days"], index=0,
            format_func=str.capitalize,
            help="Lead time is normally expressed in the same unit as the sales grain (months).",
        )
        # An explicit figure beats a checkbox: ~73% of SKUs in a typical export have no
        # lead time, so this one number drives most of the targets.
        default_lt = st.number_input(
            f"Default lead time when missing ({lt_unit})",
            min_value=0.0, value=0.25, step=0.25, format="%.2f",
            help="Applied to every SKU whose supplier row has no lead time. Flagged in the audit.",
        )
    with c3:
        horizon = st.slider("Forecast horizon (months)", 3, 12, 6)
        dead_months = st.slider("Dead stock: no sales for N months", 3, 24, 12)

config = DiagnosticConfig(
    client_name=client_name,
    service_level_a=sl_a,
    service_level_b=sl_b,
    service_level_c=sl_c,
    review_period_months=review,
    lead_time_unit=lt_unit,
    default_lead_time=default_lt,
    # The operator typed a figure, so it must be used - deriving a median instead would
    # silently ignore it.
    default_lead_time_from_median=False,
    forecast_horizon_months=horizon,
    dead_stock_months=dead_months,
)

# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
theme.section(
    "Run",
    "Reconciles the files, segments demand, backtests a model for each SKU, then "
    "forecasts the horizon.",
    icon="play",
)

required_ok = all(paths.get(k) and Path(paths[k]).exists() for k in ("sales", "inventory", "suppliers"))

with theme.card():
    if not required_ok:
        st.warning("Upload the three required files to enable the run.")
    run_clicked = st.button("Run diagnostic", type="primary", disabled=not required_ok)

if run_clicked:
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
