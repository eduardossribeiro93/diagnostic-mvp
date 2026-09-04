"""Step 4 - Approve and generate: the human gate before anything reaches the client.

Selling confidence in the answer, not just automation - so the deliverables are only
produced once an analyst has signed off.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import polars as pl
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import theme  # noqa: E402

theme.apply("Approve")
result = theme.require_result()

from core import report  # noqa: E402

cur = result.config.currency
ro = result.rollup

st.markdown('<div class="wc-eyebrow">Step 4</div>', unsafe_allow_html=True)
st.markdown('<div class="wc-title">Approve &amp; generate</div>', unsafe_allow_html=True)

theme.section("Forecast accuracy (this run)")
acc = result.accuracy.filter(pl.col("n_holdout") > 0)
c1, c2, c3, c4 = st.columns(4)
if acc.height:
    with c1:
        theme.tile("Median MASE", f"{acc['mase'].median():.2f}",
                   "below 1.0 beats a naive forecast", accent=float(acc["mase"].median() or 9) < 1)
    with c2:
        theme.tile("Median MAPE", f"{acc['mape'].median():.0%}", "where actual > 0")
    with c3:
        theme.tile("Mean bias", f"{acc['bias'].mean():+,.1f}", "units/month; + = under-forecast")
    with c4:
        theme.tile("SKUs backtested", f"{acc.height:,}", f"of {result.meta['n_skus']:,}")
    st.caption(
        "MASE is the primary indicator: MAPE is unreliable for intermittent SKUs because it "
        "divides by near-zero actuals. Full per-SKU metrics ship in the run log below."
    )
else:
    st.info("No SKU had enough history to backtest.")

theme.section("Review before approving")
checks = result.checks.filter(pl.col("skus") > 0).head(4)
for r in checks.iter_rows(named=True):
    st.markdown(f"- {r['label']}: **{r['skus']:,} SKUs** ({r['share_of_skus']:.0%})")
st.markdown(
    f"- Headline: **{theme.money(ro['opportunity_value'], cur)}** "
    f"= excess {theme.money(ro['excess_value'], cur)} "
    f"+ shortfall {theme.money(ro['shortfall_value'], cur)}"
    + ("" if ro["has_purchase_orders"] else "  (avoidable purchases N/A - no PO file)")
)

theme.section("Approve")
approved = st.checkbox(
    "I have reviewed the data-quality caveats and the top opportunities, and I approve "
    "this diagnostic for release to the client.",
    value=st.session_state.get("approved", False),
)
st.session_state["approved"] = approved

client_dir = theme.ROOT / "clients" / result.config.client_name.lower().replace(" ", "_")
out_dir = client_dir / "outputs"

if st.button("Generate deliverables", type="primary", disabled=not approved):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe = result.config.client_name.replace(" ", "_")
    with st.spinner("Writing PDF, Excel and the run log..."):
        pdf = report.write_pdf(result, out_dir / f"{safe}_Diagnostic.pdf")
        xlsx = report.write_excel(result, out_dir / f"{safe}_SKU_Actions.xlsx")
        acc_path = out_dir / f"forecast_accuracy_{stamp}.csv"
        result.forecast_export().write_csv(acc_path)
        summary_path = out_dir / f"run_summary_{stamp}.json"
        import json

        summary_path.write_text(
            json.dumps(
                {
                    "client": result.config.client_name,
                    "generated": stamp,
                    "rollup": result.rollup,
                    "metrics": result.metrics,
                    "quality_score": result.quality_score,
                    "config": result.config.to_dict(),
                    "meta": {k: str(v) for k, v in result.meta.items()},
                    "accuracy_aggregates": {
                        "median_mase": float(acc["mase"].median()) if acc.height else None,
                        "median_mape": float(acc["mape"].median()) if acc.height else None,
                        "mean_bias": float(acc["bias"].mean()) if acc.height else None,
                        "skus_backtested": acc.height,
                    },
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        result.config.save(client_dir / "config.yaml")
    st.success(f"Written to `{out_dir}`")
    st.session_state["generated"] = [pdf, xlsx, acc_path, summary_path]

for path in st.session_state.get("generated", []):
    path = Path(path)
    if path.exists():
        st.download_button(
            f"Download {path.name}", path.read_bytes(), file_name=path.name, key=str(path)
        )
