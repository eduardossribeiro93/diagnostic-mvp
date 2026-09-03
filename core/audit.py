"""Data-quality audit.

Runs before the math and is surfaced in the exec output, because a PE partner will
discount a number they cannot see the foundations of. Every check reports how many
SKUs it touches and how much inventory value sits behind them.
"""

from __future__ import annotations

import polars as pl

from .config import DiagnosticConfig

# check_id -> (label, weight toward the score, severity)
CHECKS: dict[str, tuple[str, float, str]] = {
    "lead_time_defaulted": ("Lead time missing - assumed default", 25.0, "high"),
    "cost_proxied": ("Unit cost missing - valued at selling price (upper bound)", 20.0, "high"),
    "insufficient_history": ("Too little sales history to model reliably", 15.0, "medium"),
    "stock_assumed_zero": ("Sold but absent from stock file - assumed zero on-hand", 10.0, "medium"),
    "no_usable_cost": ("No cost or price available - cannot be valued", 10.0, "high"),
    "dead_stock": ("No sales within the dead-stock window", 5.0, "medium"),
    "negative_inventory": ("Negative stock on hand", 5.0, "low"),
    "orphan_po": ("Purchase order for an unknown SKU", 5.0, "low"),
    "duplicate_sku": ("Duplicate SKU rows in a source file", 5.0, "low"),
}


def _flag_frame(df: pl.DataFrame, config: DiagnosticConfig) -> pl.DataFrame:
    return df.with_columns(
        (pl.col("lead_time_source") == "default").alias("f_lead_time_defaulted"),
        (pl.col("cost_source") == "price_proxy").alias("f_cost_proxied"),
        (pl.col("segment") == "insufficient").alias("f_insufficient_history"),
        (pl.col("on_hand_source") == "assumed_zero").alias("f_stock_assumed_zero"),
        (pl.col("unit_cost_effective") <= 0).alias("f_no_usable_cost"),
        pl.col("is_dead").alias("f_dead_stock"),
        (pl.col("on_hand") < 0).alias("f_negative_inventory"),
    )


def run_audit(
    df: pl.DataFrame,
    config: DiagnosticConfig,
    extra_counts: dict[str, int] | None = None,
) -> tuple[pl.DataFrame, float]:
    """Return (checks table, data-quality score 0-100)."""
    flagged = _flag_frame(df, config)
    n = max(flagged.height, 1)
    total_value = float(flagged["on_hand_value"].sum() or 0.0)

    rows = []
    penalty = 0.0
    for check_id, (label, weight, severity) in CHECKS.items():
        col = f"f_{check_id}"
        if col in flagged.columns:
            hit = flagged.filter(pl.col(col))
            count = hit.height
            value = float(hit["on_hand_value"].sum() or 0.0)
        else:
            count = int((extra_counts or {}).get(check_id, 0))
            value = 0.0
        share = count / n
        penalty += weight * share
        rows.append(
            {
                "check": check_id,
                "label": label,
                "severity": severity,
                "skus": count,
                "share_of_skus": share,
                "inventory_value": value,
                "share_of_value": (value / total_value) if total_value > 0 else 0.0,
            }
        )

    score = max(0.0, min(100.0, 100.0 - penalty))
    checks = pl.DataFrame(rows).sort("skus", descending=True)
    return checks, round(score, 1)


def sku_confidence(df: pl.DataFrame) -> pl.Expr:
    """Per-SKU confidence: starts at 1.0 and erodes with each assumption relied upon."""
    penalty = (
        (pl.col("lead_time_source") == "default").cast(pl.Float64) * 0.30
        + (pl.col("cost_source") != "actual").cast(pl.Float64) * 0.20
        + (pl.col("on_hand_source") == "assumed_zero").cast(pl.Float64) * 0.20
        + (pl.col("segment") == "insufficient").cast(pl.Float64) * 0.25
        + (pl.col("unit_cost_effective") <= 0).cast(pl.Float64) * 0.30
    )
    return (1.0 - penalty).clip(0.0, 1.0).alias("confidence")
