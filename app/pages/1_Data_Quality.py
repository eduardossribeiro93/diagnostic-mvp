"""Step 2 - Data quality: what the numbers rest on, before anyone trusts them."""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.express as px
import polars as pl
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import theme  # noqa: E402

theme.apply("Data quality")
result = theme.require_result()

st.markdown('<div class="wc-eyebrow">Step 2</div>', unsafe_allow_html=True)
st.markdown('<div class="wc-title">Data quality</div>', unsafe_allow_html=True)

score = result.quality_score
verdict = "Strong" if score >= 80 else "Workable" if score >= 55 else "Heavily assumption-led"
c1, c2, c3, c4 = st.columns(4)
with c1:
    theme.tile("Data-quality score", f"{score}/100", verdict, accent=score >= 55)
with c2:
    theme.tile("SKUs analysed", f"{result.meta['n_skus']:,}")
with c3:
    theme.tile("Months of history", f"{result.meta['n_periods']}",
               f"to {result.meta['last_training_period']}")
with c4:
    dropped = result.meta.get("dropped_partial_period")
    theme.tile("Partial month excluded", str(dropped) if dropped else "none",
               "kept out of training, forecast instead")

theme.section("Checks")
st.caption(
    "Each check shows how many SKUs it touches. These caveats travel with the € figures "
    "into the executive summary - they are what stops the number being waved away."
)

checks = result.checks.filter(pl.col("skus") > 0)
if checks.height:
    fig = px.bar(
        checks.sort("share_of_skus").to_pandas(),
        x="share_of_skus", y="label", orientation="h",
        text="skus", height=max(260, 42 * checks.height),
    )
    fig.update_traces(texttemplate="%{text:,} SKUs", textposition="outside", cliponaxis=False)
    fig.update_layout(xaxis_tickformat=".0%", xaxis_title=None, yaxis_title=None,
                      xaxis_range=[0, 1.15])
    st.plotly_chart(fig, width="stretch")

    st.dataframe(
        checks.select(
            pl.col("label").alias("Check"),
            pl.col("severity").alias("Severity"),
            pl.col("skus").alias("SKUs"),
            (pl.col("share_of_skus") * 100).round(1).alias("% of SKUs"),
            pl.col("inventory_value").round(0).alias("Stock value affected"),
        ).to_pandas(),
        width="stretch", hide_index=True,
    )
else:
    st.success("No data-quality issues detected.")

theme.section("What would most improve this diagnostic")
lead = result.checks.filter(pl.col("check") == "lead_time_defaulted")
cost = result.checks.filter(pl.col("check") == "cost_proxied")
asks = []
if lead.height and lead["skus"][0]:
    asks.append(
        f"**Supplier lead times** for the {lead['skus'][0]:,} SKUs without one "
        f"({lead['share_of_skus'][0]:.0%} of the range). Lead time drives the target directly, "
        "so this is the single highest-value thing to request - even just for the A-class SKUs."
    )
if cost.height and cost["skus"][0]:
    asks.append(
        f"**Unit costs** for the {cost['skus'][0]:,} SKUs valued at selling price. "
        "Valuing stock at price rather than cost makes every € figure an upper bound; "
        "real costs would tighten the headline."
    )
for a in asks:
    st.markdown(f"- {a}")
