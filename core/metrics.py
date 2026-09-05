"""Working-capital metrics in the language a PE operating partner uses.

DIO and turns are computed on trailing-12-month *actual* COGS from history, so they
are unaffected by the forecast horizon.
"""

from __future__ import annotations

import polars as pl

from .config import DiagnosticConfig

DAYS_PER_YEAR = 365.0


def compute(df: pl.DataFrame, config: DiagnosticConfig) -> dict:
    cost = pl.col("unit_cost_effective")

    agg = df.select(
        (pl.col("on_hand") * cost).sum().alias("inv_before"),
        # After acting: hold the target, never more than you already own.
        (pl.min_horizontal(pl.col("on_hand"), pl.col("target")) * cost).sum().alias("inv_target"),
        (pl.col("qty_12m") * cost).sum().alias("annual_cogs"),
    ).row(0, named=True)

    inv_before = float(agg["inv_before"] or 0.0)
    inv_target = float(agg["inv_target"] or 0.0)
    cogs = float(agg["annual_cogs"] or 0.0)

    def dio(value: float) -> float | None:
        return (value / cogs) * DAYS_PER_YEAR if cogs > 0 else None

    def turns(value: float) -> float | None:
        return cogs / value if value > 0 else None

    return {
        "inventory_value_before": inv_before,
        "inventory_value_target": inv_target,
        "inventory_value_released": inv_before - inv_target,
        "annual_cogs": cogs,
        "dio_before": dio(inv_before),
        "dio_target": dio(inv_target),
        "turns_before": turns(inv_before),
        "turns_target": turns(inv_target),
    }
