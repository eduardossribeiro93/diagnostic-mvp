"""Traceability: every euro reconstructs from the SKU up.

This is what lets an operating partner defend a number in a management meeting
instead of pointing at a black box.
"""

from __future__ import annotations

import polars as pl

# Fields that make up a SKU's calculation chain, in the order they are derived.
CHAIN = [
    ("Description", "description"),
    ("ABC class", "abc"),
    ("Demand segment", "segment"),
    ("Months of sales history", "months_with_sales"),
    ("Months since last sale", "months_since_last_sale"),
    ("Forecast model chosen", "model"),
    ("Forecast error (sigma, monthly)", "sigma_month"),
    ("Forecast demand / month", "demand_month"),
    ("Lead time (source units)", "lead_time"),
    ("Lead time source", "lead_time_source"),
    ("Lead time (months)", "lead_time_months"),
    ("Coverage window = lead time + review (months)", "coverage_months"),
    ("Demand over coverage window", "demand_coverage"),
    ("Service level", "service_level"),
    ("z-score", "z"),
    ("Safety stock", "safety_stock"),
    ("TARGET (order-up-to)", "target"),
    ("On hand", "on_hand"),
    ("On-hand source", "on_hand_source"),
    ("Open POs", "open_po"),
    ("Position (on hand + PO)", "position"),
    ("Excess units", "excess_units"),
    ("Shortfall units", "shortfall_units"),
    ("MOQ", "moq"),
    ("Buy qty (MOQ applied)", "buy_qty"),
    ("Unit cost", "unit_cost_effective"),
    ("Unit cost source", "cost_source"),
    ("EXCESS VALUE", "excess_value"),
    ("SHORTFALL VALUE", "shortfall_value"),
    ("Recommendation", "recommendation"),
    ("Confidence", "confidence"),
]


def explain(sku_frame: pl.DataFrame, sku: str) -> list[tuple[str, object]]:
    """Ordered calculation chain for one SKU."""
    row = sku_frame.filter(pl.col("sku") == sku)
    if not row.height:
        return []
    data = row.row(0, named=True)
    return [(label, data.get(col)) for label, col in CHAIN if col in data]


def explain_text(sku_frame: pl.DataFrame, sku: str) -> str:
    lines = [f"Audit trail - SKU {sku}", "=" * 40]
    for label, value in explain(sku_frame, sku):
        if isinstance(value, float):
            value = f"{value:,.2f}"
        lines.append(f"{label:.<38} {value}")
    return "\n".join(lines)
