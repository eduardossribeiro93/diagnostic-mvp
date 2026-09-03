"""ABC value classification and demand-pattern segmentation.

The segment decides which forecast family a SKU is routed to: you cannot fit a
seasonal ETS to an item that sells four times a year.
"""

from __future__ import annotations

import polars as pl

from .config import DiagnosticConfig

SEGMENTS = ("regular", "intermittent", "insufficient", "dead")


def assign_abc(master: pl.DataFrame, config: DiagnosticConfig) -> pl.DataFrame:
    """Pareto classification on trailing-12-month revenue (falls back to full history)."""
    base = (
        pl.when(pl.col("revenue_12m") > 0)
        .then(pl.col("revenue_12m"))
        .otherwise(pl.col("total_revenue"))
        .alias("_abc_base")
    )
    df = master.with_columns(base).sort("_abc_base", descending=True)
    total = df["_abc_base"].sum()

    if not total or total <= 0:
        return df.with_columns(pl.lit("C").alias("abc")).drop("_abc_base")

    cut_a = config.abc_threshold_a
    cut_b = config.abc_threshold_a + config.abc_threshold_b
    return (
        df.with_columns((pl.col("_abc_base").cum_sum() / total).alias("_cum"))
        .with_columns(
            pl.when(pl.col("_abc_base") <= 0)
            .then(pl.lit("C"))
            .when(pl.col("_cum") <= cut_a)
            .then(pl.lit("A"))
            .when(pl.col("_cum") <= cut_b)
            .then(pl.lit("B"))
            .otherwise(pl.lit("C"))
            .alias("abc")
        )
        .drop(["_abc_base", "_cum"])
    )


def assign_segment(
    master: pl.DataFrame, panel: pl.DataFrame, config: DiagnosticConfig
) -> pl.DataFrame:
    """Label each SKU dead / insufficient / intermittent / regular."""
    if panel.height:
        zero_share = panel.group_by("sku").agg(
            (pl.col("quantity") <= 0).mean().alias("zero_share"),
            pl.col("quantity").len().alias("n_obs"),
        )
    else:
        zero_share = pl.DataFrame(
            schema={"sku": pl.String, "zero_share": pl.Float64, "n_obs": pl.UInt32}
        )

    return (
        master.join(zero_share, on="sku", how="left")
        .with_columns(
            pl.col("zero_share").fill_null(1.0),
            pl.col("n_obs").fill_null(0),
        )
        .with_columns(
            pl.when(
                (pl.col("months_with_sales") == 0)
                | (pl.col("months_since_last_sale") >= config.dead_stock_months)
            )
            .then(pl.lit("dead"))
            .when(pl.col("months_with_sales") < config.min_history_months)
            .then(pl.lit("insufficient"))
            .when(pl.col("zero_share") >= config.intermittent_zero_share)
            .then(pl.lit("intermittent"))
            .otherwise(pl.lit("regular"))
            .alias("segment")
        )
        .with_columns((pl.col("segment") == "dead").alias("is_dead"))
    )


def classify(
    master: pl.DataFrame, panel: pl.DataFrame, config: DiagnosticConfig
) -> pl.DataFrame:
    """ABC + segment + the SKU's service-level target."""
    out = assign_segment(assign_abc(master, config), panel, config)
    return out.with_columns(
        pl.col("abc")
        .replace_strict(
            {
                "A": config.service_level_a,
                "B": config.service_level_b,
                "C": config.service_level_c,
            },
            default=config.service_level_c,
        )
        .alias("service_level")
    )
