"""Step 3 - Results: the exec summary, drilling into the SKU worklist and its audit trail."""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.express as px
import polars as pl
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import theme  # noqa: E402

theme.apply("Results")
result = theme.require_result()

from core import audit_trail  # noqa: E402

cur = result.config.currency
ro, mt = result.rollup, result.metrics
df = result.sku_frame

st.markdown('<div class="wc-eyebrow">Step 3</div>', unsafe_allow_html=True)
st.markdown(f'<div class="wc-title">{result.config.client_name}</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="wc-headline">{theme.money(ro["opportunity_value"], cur)} '
    "identified inventory opportunity</div>",
    unsafe_allow_html=True,
)
basis = "excess + avoidable + shortfall" if ro["has_purchase_orders"] else "excess + shortfall"
st.markdown(f'<div class="wc-sub">Sum of {basis}.</div>', unsafe_allow_html=True)

exec_tab, ops_tab, trail_tab = st.tabs(["Executive summary", "SKU worklist", "Audit trail"])

# --------------------------------------------------------------------------- #
with exec_tab:
    cols = st.columns(4)
    with cols[0]:
        theme.tile("Excess inventory", theme.money(ro["excess_value"], cur), "cash to release", accent=True)
    with cols[1]:
        theme.tile("Avoidable purchases", theme.money(ro["avoidable_value"], cur),
                   "cash to save" if ro["has_purchase_orders"] else "no PO file supplied")
    with cols[2]:
        theme.tile("Inventory shortfalls", theme.money(ro["shortfall_value"], cur), "cash to invest")
    with cols[3]:
        theme.tile("SKUs requiring action", f"{ro['skus_requiring_action']:,}", f"of {ro['n_skus']:,}")

    st.write("")
    cols = st.columns(4)
    def fmt(v, s="", dp=0):
        return "n/a" if v is None else f"{v:,.{dp}f}{s}"
    with cols[0]:
        theme.tile("Inventory value", theme.money(mt["inventory_value_before"], cur),
                   f"target {theme.money(mt['inventory_value_target'], cur)}")
    with cols[1]:
        theme.tile("DIO", f"{fmt(mt['dio_before'],'d')} → {fmt(mt['dio_target'],'d')}", "before → target")
    with cols[2]:
        theme.tile("Inventory turns", f"{fmt(mt['turns_before'],'',2)} → {fmt(mt['turns_target'],'',2)}",
                   "before → target")
    with cols[3]:
        theme.tile("Stock released", theme.money(mt["inventory_value_released"], cur),
                   "held stock above target")

    if ro["eo_value"]:
        st.caption(
            f"Of the excess, {theme.money(ro['eo_value'], cur)} is dead / obsolete stock "
            "(no sales within the dead-stock window) - a component of excess, not an addition."
        )

    theme.section("Where the value sits")
    c1, c2 = st.columns(2)
    abc = result.abc_summary().to_pandas()
    with c1:
        fig = px.bar(abc, x="abc", y=["excess_value", "shortfall_value"], barmode="group", height=300)
        fig.update_layout(xaxis_title="ABC class", yaxis_title=cur, legend_title=None)
        st.plotly_chart(fig, width="stretch")
    with c2:
        sup = result.supplier_exposure.head(10).to_pandas()
        fig = px.bar(sup.sort_values("excess_value"), x="excess_value", y="supplier_label",
                     orientation="h", height=300)
        fig.update_layout(xaxis_title=f"Excess ({cur})", yaxis_title=None)
        st.plotly_chart(fig, width="stretch")

    show = ["sku", "description", "abc", "on_hand", "target", "excess_units", "excess_value"]
    theme.section("Top 10 cash-release opportunities")
    st.dataframe(result.top_cash_release(10).select(show).to_pandas(),
                 width="stretch", hide_index=True)

    theme.section("Top 10 supply risks")
    st.dataframe(
        result.top_supply_risks(10)
        .select(["sku", "description", "abc", "on_hand", "target", "shortfall_units",
                 "buy_qty", "shortfall_value"])
        .to_pandas(),
        width="stretch", hide_index=True,
    )

# --------------------------------------------------------------------------- #
with ops_tab:
    c1, c2, c3, c4 = st.columns(4)
    actions = c1.multiselect("Recommendation", sorted(df["recommendation"].unique().to_list()))
    abcs = c2.multiselect("ABC", ["A", "B", "C"])
    segs = c3.multiselect("Demand segment", sorted(df["segment"].unique().to_list()))
    only_action = c4.checkbox("Only SKUs requiring action", True)

    view = df
    if actions:
        view = view.filter(pl.col("recommendation").is_in(actions))
    if abcs:
        view = view.filter(pl.col("abc").is_in(abcs))
    if segs:
        view = view.filter(pl.col("segment").is_in(segs))
    if only_action:
        view = view.filter(pl.col("needs_action"))

    st.caption(f"{view.height:,} SKUs - excess {theme.money(float(view['excess_value'].sum()), cur)}, "
               f"shortfall {theme.money(float(view['shortfall_value'].sum()), cur)}")
    st.dataframe(
        view.select(
            "sku", "description", "abc", "segment", "on_hand", "target",
            "excess_units", "excess_value", "shortfall_units", "buy_qty", "shortfall_value",
            "recommendation", "model", "confidence",
        ).sort("excess_value", descending=True).to_pandas(),
        width="stretch", hide_index=True, height=520,
    )

# --------------------------------------------------------------------------- #
with trail_tab:
    st.caption(
        "Every € traces back to the SKU it came from. This is what lets the number be "
        "defended in a management meeting rather than taken on faith."
    )
    ranked = df.sort("excess_value", descending=True)["sku"].to_list()
    sku = st.selectbox("SKU", ranked, index=0)
    chain = audit_trail.explain(df, sku)
    left, right = st.columns(2)
    half = (len(chain) + 1) // 2
    for col, items in ((left, chain[:half]), (right, chain[half:])):
        with col:
            for label, value in items:
                if isinstance(value, float):
                    value = f"{value:,.2f}"
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;gap:1rem;'
                    f'padding:.28rem 0;border-bottom:1px solid {theme.BORDER};">'
                    f'<span style="color:{theme.MUTED};font-size:.78rem">{label}</span>'
                    f'<span style="font-size:.82rem;text-align:right">{value}</span></div>',
                    unsafe_allow_html=True,
                )
