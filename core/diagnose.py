"""Turn targets into the four answers: own / buy / stop buying / liquidate.

The decomposition is deliberately built so the buckets never double-count a unit:

    total position above target = max(H + P - T, 0)
        of which owned stock    = max(H - T, 0)              -> excess / liquidate
        of which inbound POs    = the remainder              -> avoidable purchases
    still short after inbound   = max(T - (H + P), 0)        -> shortfall / buy

E&O is the dead-SKU slice of excess (a breakdown of it, never an addition to it).
"""

from __future__ import annotations

import polars as pl

from .config import DiagnosticConfig

ACTION_THRESHOLD = 1e-6


def diagnose(master: pl.DataFrame, config: DiagnosticConfig, has_pos: bool) -> pl.DataFrame:
    H, P, T = pl.col("on_hand"), pl.col("open_po"), pl.col("target")
    cost = pl.col("unit_cost_effective")

    df = master.with_columns(
        (H - T).clip(lower_bound=0.0).alias("excess_units"),
        ((H + P - T).clip(lower_bound=0.0) - (H - T).clip(lower_bound=0.0)).alias("avoidable_units"),
        (T - (H + P)).clip(lower_bound=0.0).alias("shortfall_units"),
    ).with_columns(
        (pl.col("excess_units") * cost).alias("excess_value"),
        (pl.col("avoidable_units") * cost).alias("avoidable_value"),
        (pl.col("shortfall_units") * cost).alias("shortfall_value"),
        # Respect the supplier minimum: you cannot order 7 units against an MOQ of 100.
        pl.when(pl.col("shortfall_units") > 0)
        .then(pl.max_horizontal(pl.col("shortfall_units"), pl.col("moq")))
        .otherwise(0.0)
        .alias("buy_qty"),
    ).with_columns(
        (pl.col("buy_qty") * cost).alias("buy_value"),
        # E&O: dead SKUs have target 0, so their excess is their entire holding.
        pl.when(pl.col("is_dead")).then(pl.col("excess_units")).otherwise(0.0).alias("eo_units"),
        pl.when(pl.col("is_dead")).then(pl.col("excess_value")).otherwise(0.0).alias("eo_value"),
    )

    if not has_pos:
        # Without a PO file there is no inbound to stop - report N/A, not a false zero.
        df = df.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("avoidable_units"),
            pl.lit(None, dtype=pl.Float64).alias("avoidable_value"),
        )

    return df.with_columns(_recommendation(has_pos)).with_columns(
        (
            (pl.col("excess_value") > ACTION_THRESHOLD)
            | (pl.col("shortfall_value") > ACTION_THRESHOLD)
            | (pl.col("avoidable_value").fill_null(0.0) > ACTION_THRESHOLD)
        ).alias("needs_action")
    )


def _recommendation(has_pos: bool) -> pl.Expr:
    """Primary action = the bucket with the most money in it."""
    excess, short = pl.col("excess_value"), pl.col("shortfall_value")
    avoid = pl.col("avoidable_value").fill_null(0.0) if has_pos else pl.lit(0.0)
    biggest = pl.max_horizontal(excess, short, avoid)

    return (
        pl.when(pl.col("is_dead") & (pl.col("on_hand") > 0))
        .then(pl.lit("Liquidate (dead stock)"))
        .when(biggest <= ACTION_THRESHOLD)
        .then(pl.lit("Hold"))
        .when(excess >= biggest)
        .then(pl.lit("Liquidate"))
        .when(short >= biggest)
        .then(pl.lit("Buy"))
        .otherwise(pl.lit("Stop inbound"))
        .alias("recommendation")
    )


def rollup(df: pl.DataFrame, config: DiagnosticConfig, has_pos: bool) -> dict:
    """Company-level headline figures."""
    excess = float(df["excess_value"].sum() or 0.0)
    shortfall = float(df["shortfall_value"].sum() or 0.0)
    eo = float(df["eo_value"].sum() or 0.0)
    avoidable = float(df["avoidable_value"].sum() or 0.0) if has_pos else None

    opportunity = excess + shortfall + (avoidable or 0.0)
    return {
        "excess_value": excess,
        "avoidable_value": avoidable,  # None when no PO file was supplied
        "shortfall_value": shortfall,
        "eo_value": eo,  # a component of excess, not an addition
        "opportunity_value": opportunity,
        "skus_requiring_action": int(df["needs_action"].sum()),
        "n_skus": df.height,
        "has_purchase_orders": has_pos,
        "currency": config.currency,
    }
