"""Inventory policy: the order-up-to level each SKU *should* hold.

target = expected demand over (lead time + review period) + safety stock

Safety stock uses the SKU's own empirical forecast error (sigma from the backtest),
scaled to the coverage window and multiplied by the z-score for its ABC service level.
"""

from __future__ import annotations

from statistics import NormalDist

import polars as pl

from .config import DiagnosticConfig

DAYS_PER_MONTH = 30.44


def z_score(service_level: float) -> float:
    """Normal quantile for a service level, clamped to a sane range."""
    sl = min(max(service_level, 0.50), 0.9999)
    return NormalDist().inv_cdf(sl)


def apply_policy(
    master: pl.DataFrame,
    forecast: pl.DataFrame,
    fsummary: pl.DataFrame,
    config: DiagnosticConfig,
) -> pl.DataFrame:
    """Attach coverage demand, safety stock and target to the SKU master."""
    horizon = config.forecast_horizon_months

    df = master.join(
        fsummary.select("sku", "model", "sigma_month", "demand_month", "demand_horizon"),
        on="sku",
        how="left",
    ).with_columns(
        pl.col("sigma_month").fill_null(0.0),
        pl.col("demand_month").fill_null(0.0),
        # Lead time is converted from whatever unit the client's file uses.
        (pl.col("lead_time") * config.lead_time_to_months).alias("lead_time_months"),
    ).with_columns(
        (pl.col("lead_time_months") + config.review_period_months).alias("coverage_months")
    )

    # Demand across the coverage window: whole forecast months plus a prorated tail.
    ranked = forecast.sort(["sku", "period"]).with_columns(
        pl.col("period").cum_count().over("sku").alias("h_idx")
    )
    weighted = (
        ranked.join(df.select("sku", "coverage_months"), on="sku", how="inner")
        .with_columns(
            (pl.col("coverage_months") - (pl.col("h_idx") - 1))
            .clip(0.0, 1.0)
            .alias("w")
        )
        .group_by("sku")
        .agg((pl.col("demand") * pl.col("w")).sum().alias("demand_coverage_in_horizon"))
    )

    df = (
        df.join(weighted, on="sku", how="left")
        .with_columns(pl.col("demand_coverage_in_horizon").fill_null(0.0))
        .with_columns(
            # If coverage runs past the forecast horizon, extend at the monthly mean.
            (
                pl.col("demand_coverage_in_horizon")
                + (pl.col("coverage_months") - horizon).clip(lower_bound=0.0)
                * pl.col("demand_month")
            ).alias("demand_coverage")
        )
        .with_columns(
            pl.col("service_level")
            .map_elements(z_score, return_dtype=pl.Float64)
            .alias("z"),
            (pl.col("sigma_month") * pl.col("coverage_months").sqrt()).alias("sigma_coverage"),
        )
        .with_columns(
            (pl.col("z") * pl.col("sigma_coverage")).clip(lower_bound=0.0).alias("safety_stock")
        )
        .with_columns(
            (pl.col("demand_coverage") + pl.col("safety_stock")).clip(lower_bound=0.0).alias("target")
        )
    )

    # A dead SKU has no future demand, so its target is zero - all stock is excess.
    return df.with_columns(
        pl.when(pl.col("is_dead")).then(0.0).otherwise(pl.col("target")).alias("target"),
        pl.when(pl.col("is_dead")).then(0.0).otherwise(pl.col("safety_stock")).alias("safety_stock"),
    )
