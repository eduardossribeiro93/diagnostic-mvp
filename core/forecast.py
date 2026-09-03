"""Demand forecasting via Nixtla statsforecast, with per-SKU model routing and selection.

Design notes
------------
* SKUs are routed to a model family by demand segment - seasonal/trend models for
  regular demand, the intermittent family (Croston/TSB/ADIDA/IMAPA) for sparse ones.
  Fitting a seasonal ETS to an item that sells four times a year produces a
  confident-looking wrong number.
* One backtest pass picks each SKU's model *and* measures its error. Safety stock is
  derived from that empirical error (the std of holdout residuals), which works
  uniformly across every model family - including the intermittent models, which have
  no closed-form prediction interval. It is also the most defensible story for an
  auditor: "your own historical forecast error for this SKU".
* The horizon starts the month after training, i.e. the dropped partial month.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl
from statsforecast import StatsForecast
from statsforecast.models import (
    ADIDA,
    IMAPA,
    AutoETS,
    AutoTheta,
    CrostonClassic,
    CrostonOptimized,
    HistoricAverage,
    SeasonalNaive,
    WindowAverage,
)

from .config import DiagnosticConfig


@dataclass
class ForecastResult:
    forecast: pl.DataFrame  # sku, period, demand
    summary: pl.DataFrame  # sku, model, demand_month, sigma_month
    accuracy: pl.DataFrame  # per-SKU backtest metrics (the run log)


def _models(kind: str, config: DiagnosticConfig):
    if kind == "regular":
        return [
            AutoETS(season_length=config.season_length),
            AutoTheta(season_length=config.season_length),
            SeasonalNaive(season_length=config.season_length),
            WindowAverage(window_size=3),
        ]
    if kind == "intermittent":
        return [
            CrostonClassic(),
            CrostonOptimized(),
            ADIDA(),
            IMAPA(),
            WindowAverage(window_size=6),
        ]
    return [WindowAverage(window_size=3), HistoricAverage()]


def _to_sf(panel: pl.DataFrame) -> pl.DataFrame:
    return panel.select(
        pl.col("sku").alias("unique_id"),
        pl.col("period").alias("ds"),
        pl.col("quantity").cast(pl.Float64).alias("y"),
    ).sort(["unique_id", "ds"])


def _naive_scale(panel: pl.DataFrame) -> pl.DataFrame:
    """In-sample mean absolute month-over-month change - the denominator for MASE."""
    return (
        panel.sort(["sku", "period"])
        .with_columns((pl.col("quantity") - pl.col("quantity").shift(1).over("sku")).abs().alias("d"))
        .group_by("sku")
        .agg(pl.col("d").drop_nulls().mean().alias("naive_scale"))
        .with_columns(
            pl.when(pl.col("naive_scale") > 0)
            .then(pl.col("naive_scale"))
            .otherwise(None)
            .alias("naive_scale")
        )
    )


def _metrics_from_cv(cv: pl.DataFrame, model_cols: list[str]) -> pl.DataFrame:
    """Long-form error table: one row per SKU x model with the raw error aggregates."""
    long = cv.unpivot(
        on=model_cols, index=["unique_id", "ds", "y"], variable_name="model", value_name="yhat"
    ).with_columns(
        (pl.col("y") - pl.col("yhat")).alias("err"),
    )
    return long.group_by(["unique_id", "model"]).agg(
        pl.col("err").abs().mean().alias("mae"),
        (pl.col("err") ** 2).mean().sqrt().alias("rmse"),
        pl.col("err").mean().alias("bias"),
        pl.col("err").std().alias("resid_sd"),
        (
            pl.when(pl.col("y") > 0)
            .then((pl.col("err") / pl.col("y")).abs())
            .otherwise(None)
        )
        .mean()
        .alias("mape"),
        (
            2
            * pl.col("err").abs()
            / (pl.col("y").abs() + pl.col("yhat").abs()).replace(0.0, None)
        )
        .mean()
        .alias("smape"),
        pl.len().alias("n_holdout"),
    )


def _run_group(
    skus: list[str], kind: str, panel: pl.DataFrame, config: DiagnosticConfig, horizon: int
):
    """Backtest -> pick best model per SKU -> refit on full history -> forecast."""
    sub = panel.filter(pl.col("sku").is_in(skus))
    if not sub.height:
        return None, None

    sf_df = _to_sf(sub)
    models = _models(kind, config)
    model_cols = [m.alias for m in models]

    # fallback_model keeps a single awkward series (too short for ETS, all zeros, ...)
    # from aborting the whole run.
    sf = StatsForecast(
        models=models, freq="1mo", n_jobs=1, fallback_model=HistoricAverage()
    )
    cv = sf.cross_validation(
        df=sf_df, h=config.cv_horizon, n_windows=config.cv_windows, step_size=config.cv_horizon
    )
    errors = _metrics_from_cv(cv, model_cols)

    # Best model per SKU = lowest MAE on the holdout (ties broken by model order).
    best = (
        errors.sort(["unique_id", "mae"])
        .group_by("unique_id", maintain_order=True)
        .first()
        .rename({"unique_id": "sku"})
    )

    fc = sf.forecast(df=sf_df, h=horizon)
    fc_long = fc.unpivot(
        on=model_cols, index=["unique_id", "ds"], variable_name="model", value_name="demand"
    ).rename({"unique_id": "sku", "ds": "period"})

    chosen = fc_long.join(best.select("sku", "model"), on=["sku", "model"], how="inner").select(
        "sku", "period", "demand", "model"
    )
    return chosen, best


def _fallback(skus: list[str], panel: pl.DataFrame, horizon: int, periods: list) -> tuple:
    """Too little history to backtest: flat mean of what exists, sigma from its spread."""
    sub = panel.filter(pl.col("sku").is_in(skus))
    stats = sub.group_by("sku").agg(
        pl.col("quantity").mean().alias("demand"),
        pl.col("quantity").std().fill_null(0.0).alias("resid_sd"),
        pl.len().alias("n_obs"),
    )
    if not stats.height:
        return None, None
    fc = (
        stats.select("sku", "demand")
        .join(pl.DataFrame({"period": periods}), how="cross")
        .with_columns(pl.lit("Fallback:Mean").alias("model"))
        .select("sku", "period", "demand", "model")
    )
    summary = stats.select(
        "sku",
        pl.lit("Fallback:Mean").alias("model"),
        pl.col("resid_sd"),
        pl.lit(None, dtype=pl.Float64).alias("mae"),
        pl.lit(None, dtype=pl.Float64).alias("rmse"),
        pl.lit(None, dtype=pl.Float64).alias("bias"),
        pl.lit(None, dtype=pl.Float64).alias("mape"),
        pl.lit(None, dtype=pl.Float64).alias("smape"),
        pl.lit(0, dtype=pl.UInt32).alias("n_holdout"),
    )
    return fc, summary


def _horizon_periods(meta: dict, horizon: int) -> list:
    start = meta["forecast_start"]
    out, cur = [], start
    for _ in range(horizon):
        out.append(cur)
        m = cur.month
        cur = cur.replace(year=cur.year + (m // 12), month=(m % 12) + 1)
    return out


def run_forecast(
    panel: pl.DataFrame, master: pl.DataFrame, config: DiagnosticConfig, meta: dict
) -> ForecastResult:
    horizon = config.forecast_horizon_months
    periods = _horizon_periods(meta, horizon)
    min_obs = config.cv_windows * config.cv_horizon + config.min_train_months

    obs = panel.group_by("sku").agg(pl.len().alias("n_obs"))
    routing = master.select("sku", "segment").join(obs, on="sku", how="left").with_columns(
        pl.col("n_obs").fill_null(0)
    )

    groups: dict[str, list[str]] = {}
    for kind in ("regular", "intermittent"):
        groups[kind] = routing.filter(
            (pl.col("segment") == kind) & (pl.col("n_obs") >= min_obs)
        )["sku"].to_list()
    # Short-history SKUs (any segment) and the "insufficient" bucket use the fallback.
    fallback_skus = routing.filter(
        (pl.col("segment").is_in(["regular", "intermittent", "insufficient"]))
        & (pl.col("n_obs") < min_obs)
        | (pl.col("segment") == "insufficient")
    )["sku"].to_list()
    dead_skus = routing.filter(pl.col("segment") == "dead")["sku"].to_list()

    fcs, summaries = [], []
    for kind in ("regular", "intermittent"):
        if groups[kind]:
            fc, summ = _run_group(groups[kind], kind, panel, config, horizon)
            if fc is not None:
                fcs.append(fc)
                summaries.append(summ)

    fallback_skus = sorted(set(fallback_skus) - set(groups["regular"]) - set(groups["intermittent"]))
    if fallback_skus:
        fc, summ = _fallback(fallback_skus, panel, horizon, periods)
        if fc is not None:
            fcs.append(fc)
            summaries.append(summ)

    if dead_skus:
        fcs.append(
            pl.DataFrame({"sku": dead_skus})
            .join(pl.DataFrame({"period": periods}), how="cross")
            .with_columns(
                pl.lit(0.0).alias("demand"), pl.lit("Dead:Zero").alias("model")
            )
            .select("sku", "period", "demand", "model")
        )
        summaries.append(
            pl.DataFrame({"sku": dead_skus}).with_columns(
                pl.lit("Dead:Zero").alias("model"),
                pl.lit(0.0).alias("resid_sd"),
                *[pl.lit(None, dtype=pl.Float64).alias(c) for c in ("mae", "rmse", "bias", "mape", "smape")],
                pl.lit(0, dtype=pl.UInt32).alias("n_holdout"),
            )
        )

    cols = ["sku", "model", "resid_sd", "mae", "rmse", "bias", "mape", "smape", "n_holdout"]
    forecast = (
        pl.concat(fcs, how="vertical_relaxed")
        .with_columns(pl.col("demand").clip(lower_bound=0.0))  # negative demand is meaningless
        if fcs
        else pl.DataFrame(schema={"sku": pl.String, "period": pl.Date, "demand": pl.Float64, "model": pl.String})
    )
    summary = (
        pl.concat([s.select(cols) for s in summaries], how="vertical_relaxed")
        if summaries
        else pl.DataFrame(schema={c: pl.Float64 for c in cols})
    )

    per_sku = forecast.group_by("sku").agg(
        pl.col("demand").mean().alias("demand_month"),
        pl.col("demand").sum().alias("demand_horizon"),
    )
    summary = (
        summary.join(per_sku, on="sku", how="full", coalesce=True)
        .join(_naive_scale(panel), on="sku", how="left")
        .with_columns(
            pl.col("resid_sd").fill_null(0.0).alias("sigma_month"),
            pl.when(pl.col("naive_scale").is_not_null() & (pl.col("naive_scale") > 0))
            .then(pl.col("mae") / pl.col("naive_scale"))
            .otherwise(None)
            .alias("mase"),
        )
    )
    accuracy = summary.select(
        "sku", "model", "n_holdout", "mae", "rmse", "mase", "mape", "smape", "bias",
        "sigma_month", "demand_month", "demand_horizon",
    )
    return ForecastResult(forecast=forecast, summary=summary, accuracy=accuracy)
