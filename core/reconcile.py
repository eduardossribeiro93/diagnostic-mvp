"""Reconcile the source files into a monthly demand series and a one-row-per-SKU master.

Handles the awkward realities: SKUs that sell but aren't in the stock file, missing
lead times, absent unit costs, and the incomplete current month.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from .config import DiagnosticConfig


@dataclass
class Normalized:
    monthly: pl.DataFrame  # sku, period, quantity, price, revenue  (training only)
    sku_master: pl.DataFrame  # one row per SKU
    meta: dict = field(default_factory=dict)


def _month_start(today: date | None = None) -> date:
    today = today or date.today()
    return date(today.year, today.month, 1)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def build_monthly(sales: pl.DataFrame, config: DiagnosticConfig, today: date | None = None):
    """Collapse to one row per SKU-month, then drop the incomplete current month.

    Duplicate SKU-months are summed on quantity with a quantity-weighted price, so
    derived revenue stays exact.
    """
    monthly = (
        sales.with_columns(
            pl.date(pl.col("year"), pl.col("month"), 1).alias("period"),
            (pl.col("quantity") * pl.col("price")).alias("_rev"),
        )
        .group_by(["sku", "period"])
        .agg(
            pl.col("quantity").sum().alias("quantity"),
            pl.col("_rev").sum().alias("revenue"),
        )
        .with_columns(
            pl.when(pl.col("quantity") != 0)
            .then(pl.col("revenue") / pl.col("quantity"))
            .otherwise(None)
            .alias("price")
        )
        .sort(["sku", "period"])
    )

    dropped = None
    if monthly.height:
        last_period = monthly["period"].max()
        current = _month_start(today)
        # The current calendar month is incomplete by definition.
        if config.drop_partial_last_month and last_period == current:
            dropped = last_period
            monthly = monthly.filter(pl.col("period") < current)

    last_training = monthly["period"].max() if monthly.height else None
    meta = {
        "dropped_partial_period": dropped,
        "last_training_period": last_training,
        # Horizon begins the month after training - i.e. the dropped partial month.
        "forecast_start": _add_months(last_training, 1) if last_training else None,
        "n_periods": monthly["period"].n_unique() if monthly.height else 0,
        "first_period": monthly["period"].min() if monthly.height else None,
    }
    return monthly, meta


def _effective_cost(monthly: pl.DataFrame, inventory: pl.DataFrame) -> pl.DataFrame:
    """Unit cost, else latest positive selling price, else quantity-weighted average price.

    Falling back to selling price makes valuations an upper bound - flagged downstream.
    """
    priced = monthly.filter((pl.col("price") > 0) & (pl.col("quantity") > 0))
    latest = (
        priced.sort(["sku", "period"])
        .group_by("sku")
        .agg(pl.col("price").last().alias("latest_price"))
    )
    weighted = (
        priced.group_by("sku").agg(
            (pl.col("revenue").sum() / pl.col("quantity").sum()).alias("avg_price")
        )
    )
    inv_cost = inventory.select("sku", "unit_cost").unique(subset=["sku"], keep="first")

    return (
        latest.join(weighted, on="sku", how="full", coalesce=True)
        .join(inv_cost, on="sku", how="full", coalesce=True)
        .with_columns(
            pl.coalesce(
                pl.when(pl.col("unit_cost") > 0).then(pl.col("unit_cost")),
                pl.col("latest_price"),
                pl.col("avg_price"),
            ).alias("unit_cost_effective"),
            pl.when(pl.col("unit_cost") > 0)
            .then(pl.lit("actual"))
            .when(pl.col("latest_price").is_not_null() | pl.col("avg_price").is_not_null())
            .then(pl.lit("price_proxy"))
            .otherwise(pl.lit("missing"))
            .alias("cost_source"),
        )
        .select("sku", "unit_cost_effective", "cost_source")
    )


def build_sku_master(
    monthly: pl.DataFrame,
    inventory: pl.DataFrame,
    suppliers: pl.DataFrame,
    purchase_orders: pl.DataFrame,
    config: DiagnosticConfig,
    meta: dict,
) -> pl.DataFrame:
    """One row per SKU: stock position, cost, supplier terms, and history stats."""
    last_period = meta.get("last_training_period")

    history = monthly.group_by("sku").agg(
        pl.col("quantity").sum().alias("total_qty"),
        pl.col("revenue").sum().alias("total_revenue"),
        pl.col("period").n_unique().alias("months_with_sales"),
        pl.col("period").max().alias("last_sale_period"),
        pl.col("period").min().alias("first_sale_period"),
    )

    # Trailing 12 complete months drive DIO / turns (history, not forecast).
    if last_period is not None:
        cutoff = _add_months(last_period, -11)
        trailing = (
            monthly.filter(pl.col("period") >= cutoff)
            .group_by("sku")
            .agg(
                pl.col("quantity").sum().alias("qty_12m"),
                pl.col("revenue").sum().alias("revenue_12m"),
            )
        )
    else:
        trailing = pl.DataFrame(
            schema={"sku": pl.String, "qty_12m": pl.Float64, "revenue_12m": pl.Float64}
        )

    stock = inventory.group_by("sku").agg(
        pl.col("units").sum().alias("on_hand"),
        pl.col("description").drop_nulls().first().alias("description"),
    )

    po = (
        purchase_orders.group_by("sku").agg(pl.col("ordered_qty").sum().alias("open_po"))
        if purchase_orders.height
        else pl.DataFrame(schema={"sku": pl.String, "open_po": pl.Float64})
    )

    cost = _effective_cost(monthly, inventory)

    # A default drawn from the lead times that ARE present beats an arbitrary constant.
    default_lt = config.default_lead_time
    if config.default_lead_time_from_median and "lead_time" in suppliers.columns:
        observed = suppliers["lead_time"].drop_nulls()
        if observed.len():
            default_lt = float(observed.median())
    meta["default_lead_time_used"] = default_lt

    # Universe = everything that sells or is held.
    universe = (
        pl.concat([monthly.select("sku"), inventory.select("sku")]).unique().sort("sku")
    )

    master = (
        universe.join(history, on="sku", how="left")
        .join(trailing, on="sku", how="left")
        .join(stock, on="sku", how="left")
        .join(suppliers, on="sku", how="left")
        .join(po, on="sku", how="left")
        .join(cost, on="sku", how="left")
        .with_columns(
            # Absent from the stock file -> treated as zero on-hand, and tagged.
            pl.col("on_hand").is_null().alias("_no_stock_row"),
            pl.col("on_hand").fill_null(0.0).alias("on_hand"),
            pl.col("open_po").fill_null(0.0).alias("open_po"),
            pl.col("total_qty").fill_null(0.0).alias("total_qty"),
            pl.col("total_revenue").fill_null(0.0).alias("total_revenue"),
            pl.col("qty_12m").fill_null(0.0).alias("qty_12m"),
            pl.col("revenue_12m").fill_null(0.0).alias("revenue_12m"),
            pl.col("months_with_sales").fill_null(0).alias("months_with_sales"),
            pl.col("unit_cost_effective").fill_null(0.0).alias("unit_cost_effective"),
            pl.col("cost_source").fill_null("missing").alias("cost_source"),
            # Missing lead time -> configured default, flagged.
            pl.col("lead_time").is_null().alias("_lead_time_defaulted"),
            pl.col("lead_time").fill_null(default_lt).alias("lead_time"),
            pl.col("moq").fill_null(0.0).alias("moq"),
        )
        .with_columns(
            pl.when(pl.col("_no_stock_row"))
            .then(pl.lit("assumed_zero"))
            .otherwise(pl.lit("stock_file"))
            .alias("on_hand_source"),
            pl.when(pl.col("_lead_time_defaulted"))
            .then(pl.lit("default"))
            .otherwise(pl.lit("actual"))
            .alias("lead_time_source"),
            (pl.col("on_hand") + pl.col("open_po")).alias("position"),
            (pl.col("on_hand") * pl.col("unit_cost_effective")).alias("on_hand_value"),
            pl.coalesce(pl.col("name"), pl.col("supplier")).alias("supplier_label"),
        )
        .drop(["_no_stock_row", "_lead_time_defaulted"])
    )

    if last_period is not None:
        master = master.with_columns(
            pl.when(pl.col("last_sale_period").is_null())
            .then(None)
            .otherwise(
                (pl.lit(last_period).dt.year() - pl.col("last_sale_period").dt.year()) * 12
                + (pl.lit(last_period).dt.month() - pl.col("last_sale_period").dt.month())
            )
            .alias("months_since_last_sale")
        )
    else:
        master = master.with_columns(pl.lit(None, dtype=pl.Int32).alias("months_since_last_sale"))

    return master


def build_panel(monthly: pl.DataFrame, meta: dict) -> pl.DataFrame:
    """Zero-filled monthly grid per SKU, from its first sale to the last training month.

    Months with no sales are real zeros, not gaps - the intermittent-demand models
    and the zero-share segmentation both depend on them being present.
    """
    last = meta.get("last_training_period")
    if last is None or not monthly.height:
        return pl.DataFrame(schema={"sku": pl.String, "period": pl.Date, "quantity": pl.Float64})

    grid = (
        monthly.group_by("sku")
        .agg(pl.col("period").min().alias("start"))
        .with_columns(
            pl.date_ranges(pl.col("start"), pl.lit(last), interval="1mo").alias("period")
        )
        .explode("period")
        .drop_nulls("period")  # explicit, so the Polars 2.0 default change is a no-op
        .select("sku", "period")
    )
    return (
        grid.join(monthly.select("sku", "period", "quantity"), on=["sku", "period"], how="left")
        .with_columns(pl.col("quantity").fill_null(0.0))
        .sort(["sku", "period"])
    )


def reconcile(
    sales: pl.DataFrame,
    inventory: pl.DataFrame,
    suppliers: pl.DataFrame,
    purchase_orders: pl.DataFrame,
    config: DiagnosticConfig,
    today: date | None = None,
) -> Normalized:
    monthly, meta = build_monthly(sales, config, today=today)
    master = build_sku_master(monthly, inventory, suppliers, purchase_orders, config, meta)
    meta["has_purchase_orders"] = bool(purchase_orders.height)
    meta["n_skus"] = master.height
    return Normalized(monthly=monthly, sku_master=master, meta=meta)
