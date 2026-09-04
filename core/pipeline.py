"""Orchestrates the whole diagnostic: files in -> one SKU-level result table + roll-ups.

Pure computation, no UI imports - the Streamlit console and any future client app are
just views over what this returns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

from . import audit, classify, diagnose, forecast, ingest, metrics, reconcile
from .config import DiagnosticConfig


@dataclass
class DiagnosticResult:
    sku_frame: pl.DataFrame
    rollup: dict
    metrics: dict
    checks: pl.DataFrame
    quality_score: float
    accuracy: pl.DataFrame
    supplier_exposure: pl.DataFrame
    forecast: pl.DataFrame = field(default_factory=pl.DataFrame)
    meta: dict = field(default_factory=dict)
    config: DiagnosticConfig | None = None

    # --- convenience views used by the console and the reports ---
    def top_cash_release(self, n: int = 10) -> pl.DataFrame:
        return self.sku_frame.sort("excess_value", descending=True).head(n)

    def top_supply_risks(self, n: int = 10) -> pl.DataFrame:
        return self.sku_frame.sort("shortfall_value", descending=True).head(n)

    def forecast_export(self) -> pl.DataFrame:
        """Accuracy metrics plus each horizon month's forecast quantity as its own column.

        Export only - it lets the client read how much of each SKU we expect to sell in
        any given month of the horizon, without adding anything to the console.
        """
        if not self.forecast.height:
            return self.accuracy
        wide = (
            self.forecast.with_columns(
                pl.col("period").dt.strftime("forecast_%Y-%m").alias("_m"),
                pl.col("demand").round(2),
            )
            .pivot(on="_m", index="sku", values="demand", aggregate_function="first")
        )
        months = sorted(c for c in wide.columns if c != "sku")
        return (
            self.accuracy.join(wide, on="sku", how="left")
            .select(*self.accuracy.columns, *months)
        )

    def abc_summary(self) -> pl.DataFrame:
        return (
            self.sku_frame.group_by("abc")
            .agg(
                pl.len().alias("skus"),
                pl.col("on_hand_value").sum().alias("stock_value"),
                pl.col("excess_value").sum().alias("excess_value"),
                pl.col("shortfall_value").sum().alias("shortfall_value"),
            )
            .sort("abc")
        )


def _supplier_exposure(df: pl.DataFrame) -> pl.DataFrame:
    return (
        df.with_columns(pl.col("supplier_label").fill_null("(unknown)"))
        .group_by("supplier_label")
        .agg(
            pl.len().alias("skus"),
            pl.col("on_hand_value").sum().alias("stock_value"),
            pl.col("excess_value").sum().alias("excess_value"),
            pl.col("shortfall_value").sum().alias("shortfall_value"),
            pl.col("lead_time").mean().alias("avg_lead_time"),
        )
        .sort("excess_value", descending=True)
    )


def run_diagnostic(
    sales_path: str | Path,
    inventory_path: str | Path,
    supplier_path: str | Path,
    po_path: str | Path | None = None,
    config: DiagnosticConfig | None = None,
    mappings: dict[str, dict[str, str]] | None = None,
    today: date | None = None,
) -> DiagnosticResult:
    config = config or DiagnosticConfig()
    mappings = mappings or {}
    started = time.time()

    sales = ingest.load_sales(sales_path, mappings.get("sales"))
    inventory = ingest.load_inventory(inventory_path, mappings.get("inventory"))
    suppliers = ingest.load_suppliers(supplier_path, mappings.get("suppliers"))
    pos = (
        ingest.load_purchase_orders(po_path, mappings.get("purchase_orders"))
        if po_path
        else ingest.empty_purchase_orders()
    )
    has_pos = bool(pos.height)

    norm = reconcile.reconcile(sales, inventory, suppliers, pos, config, today=today)
    panel = reconcile.build_panel(norm.monthly, norm.meta)

    classified = classify.classify(norm.sku_master, panel, config)
    fc = forecast.run_forecast(panel, classified, config, norm.meta)
    with_policy = policy_step(classified, fc, config)
    diagnosed = diagnose.diagnose(with_policy, config, has_pos)
    diagnosed = diagnosed.with_columns(audit.sku_confidence(diagnosed))

    orphans = (
        pos.join(norm.sku_master.select("sku"), on="sku", how="anti")["sku"].n_unique()
        if has_pos
        else 0
    )
    checks, score = audit.run_audit(
        diagnosed,
        config,
        extra_counts={
            "orphan_po": orphans,
            "overdue_po": int(norm.meta.get("skus_with_overdue_po", 0)),
        },
    )

    result = DiagnosticResult(
        sku_frame=diagnosed,
        rollup=diagnose.rollup(diagnosed, config, has_pos),
        metrics=metrics.compute(diagnosed, config),
        checks=checks,
        quality_score=score,
        accuracy=fc.accuracy,
        supplier_exposure=_supplier_exposure(diagnosed),
        forecast=fc.forecast,
        meta={**norm.meta, "runtime_seconds": round(time.time() - started, 1)},
        config=config,
    )
    return result


def policy_step(classified: pl.DataFrame, fc, config: DiagnosticConfig) -> pl.DataFrame:
    from .policy import apply_policy

    return apply_policy(classified, fc.forecast, fc.summary, config)
