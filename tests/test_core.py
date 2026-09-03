"""Unit tests for the calculation core.

The decomposition tests matter most: if excess / avoidable / shortfall ever
double-count a unit, the headline number is wrong and indefensible.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from core import diagnose, metrics, reconcile
from core.config import DiagnosticConfig
from core.policy import z_score


def make_frame(rows: list[dict]) -> pl.DataFrame:
    """Minimal SKU frame with the fields diagnose() needs."""
    defaults = {
        "sku": "X",
        "on_hand": 0.0,
        "open_po": 0.0,
        "target": 0.0,
        "unit_cost_effective": 1.0,
        "moq": 0.0,
        "is_dead": False,
    }
    return pl.DataFrame([{**defaults, **r} for r in rows])


CFG = DiagnosticConfig()


# --------------------------------------------------------------------------- #
# Decomposition integrity
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "on_hand,open_po,target",
    [(100, 0, 40), (100, 50, 40), (10, 5, 40), (0, 0, 25), (40, 0, 40), (0, 80, 30)],
)
def test_buckets_never_double_count(on_hand, open_po, target):
    """excess + avoidable must equal the total position above target, exactly."""
    df = diagnose.diagnose(
        make_frame([{"on_hand": on_hand, "open_po": open_po, "target": target}]), CFG, has_pos=True
    )
    row = df.row(0, named=True)
    above = max(on_hand + open_po - target, 0)
    assert row["excess_units"] + row["avoidable_units"] == pytest.approx(above)


def test_excess_never_exceeds_owned_stock():
    df = diagnose.diagnose(
        make_frame([{"on_hand": 30, "open_po": 500, "target": 10}]), CFG, has_pos=True
    )
    row = df.row(0, named=True)
    assert row["excess_units"] <= 30
    assert row["excess_units"] == pytest.approx(20)
    assert row["avoidable_units"] == pytest.approx(500)


def test_shortfall_is_net_of_inbound():
    """Stock already on order must not be re-ordered."""
    df = diagnose.diagnose(
        make_frame([{"on_hand": 10, "open_po": 25, "target": 30}]), CFG, has_pos=True
    )
    assert df.row(0, named=True)["shortfall_units"] == pytest.approx(0)


def test_no_negative_buys():
    df = diagnose.diagnose(
        make_frame([{"on_hand": 999, "open_po": 0, "target": 5}]), CFG, has_pos=True
    )
    row = df.row(0, named=True)
    assert row["shortfall_units"] == 0
    assert row["buy_qty"] == 0


def test_excess_and_shortfall_are_mutually_exclusive():
    for on_hand, target in [(100, 40), (10, 40), (40, 40)]:
        row = diagnose.diagnose(
            make_frame([{"on_hand": on_hand, "target": target}]), CFG, has_pos=False
        ).row(0, named=True)
        assert row["excess_units"] == 0 or row["shortfall_units"] == 0


# --------------------------------------------------------------------------- #
# Optional purchase orders
# --------------------------------------------------------------------------- #

def test_avoidable_is_null_without_po_file():
    """No PO export must yield N/A, never a misleading zero."""
    df = diagnose.diagnose(make_frame([{"on_hand": 100, "target": 40}]), CFG, has_pos=False)
    assert df.row(0, named=True)["avoidable_value"] is None
    assert diagnose.rollup(df, CFG, has_pos=False)["avoidable_value"] is None


def test_headline_excludes_avoidable_when_no_pos():
    df = diagnose.diagnose(make_frame([{"on_hand": 100, "target": 40}]), CFG, has_pos=False)
    ro = diagnose.rollup(df, CFG, has_pos=False)
    assert ro["opportunity_value"] == pytest.approx(ro["excess_value"] + ro["shortfall_value"])


# --------------------------------------------------------------------------- #
# MOQ and dead stock
# --------------------------------------------------------------------------- #

def test_moq_is_respected():
    """You cannot order 7 units against a minimum of 100."""
    row = diagnose.diagnose(
        make_frame([{"on_hand": 0, "target": 7, "moq": 100}]), CFG, has_pos=False
    ).row(0, named=True)
    assert row["shortfall_units"] == pytest.approx(7)
    assert row["buy_qty"] == pytest.approx(100)


def test_dead_stock_is_flagged_and_is_subset_of_excess():
    row = diagnose.diagnose(
        make_frame([{"on_hand": 60, "target": 0, "is_dead": True, "unit_cost_effective": 2.0}]),
        CFG,
        has_pos=False,
    ).row(0, named=True)
    assert row["eo_units"] == pytest.approx(60)
    assert row["eo_value"] == pytest.approx(row["excess_value"])  # subset, not an addition
    assert row["recommendation"] == "Liquidate (dead stock)"


# --------------------------------------------------------------------------- #
# Policy helpers
# --------------------------------------------------------------------------- #

def test_z_score_increases_with_service_level():
    assert z_score(0.90) < z_score(0.95) < z_score(0.98)
    assert z_score(0.50) == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# Ingest / reconcile behaviour
# --------------------------------------------------------------------------- #

def test_partial_current_month_is_dropped_but_becomes_forecast_start():
    sales = pl.DataFrame(
        {
            "sku": ["A"] * 3,
            "year": [2026, 2026, 2026],
            "month": [7, 8, 9],
            "quantity": [10.0, 12.0, 1.0],
            "price": [2.0, 2.0, 2.0],
        }
    )
    monthly, meta = reconcile.build_monthly(sales, CFG, today=date(2026, 9, 3))
    assert meta["dropped_partial_period"] == date(2026, 9, 1)
    assert meta["last_training_period"] == date(2026, 8, 1)
    # The dropped month is exactly where the forecast horizon begins.
    assert meta["forecast_start"] == date(2026, 9, 1)
    assert monthly["period"].max() == date(2026, 8, 1)


def test_complete_last_month_is_kept():
    sales = pl.DataFrame(
        {"sku": ["A", "A"], "year": [2026, 2026], "month": [6, 7],
         "quantity": [10.0, 12.0], "price": [2.0, 2.0]}
    )
    monthly, meta = reconcile.build_monthly(sales, CFG, today=date(2026, 9, 3))
    assert meta["dropped_partial_period"] is None
    assert meta["last_training_period"] == date(2026, 7, 1)


def test_duplicate_sku_months_collapse_with_weighted_price():
    """Two rows in one month must preserve total revenue exactly."""
    sales = pl.DataFrame(
        {"sku": ["A", "A"], "year": [2026, 2026], "month": [6, 6],
         "quantity": [10.0, 30.0], "price": [1.0, 5.0]}
    )
    monthly, _ = reconcile.build_monthly(sales, CFG, today=date(2026, 9, 3))
    row = monthly.row(0, named=True)
    assert row["quantity"] == pytest.approx(40.0)
    assert row["revenue"] == pytest.approx(10 * 1.0 + 30 * 5.0)
    assert row["price"] == pytest.approx(160.0 / 40.0)  # quantity-weighted, not 3.0


def test_panel_fills_missing_months_with_zero():
    sales = pl.DataFrame(
        {"sku": ["A", "A"], "year": [2026, 2026], "month": [1, 4],
         "quantity": [5.0, 7.0], "price": [1.0, 1.0]}
    )
    monthly, meta = reconcile.build_monthly(sales, CFG, today=date(2026, 9, 3))
    panel = reconcile.build_panel(monthly, meta)
    # Runs from the SKU's first sale to the last training month: Jan..Apr.
    assert panel.height == 4
    assert panel.filter(pl.col("quantity") == 0).height == 2  # Feb and Mar
    assert panel["period"].to_list() == [date(2026, m, 1) for m in (1, 2, 3, 4)]


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

def test_dio_and_turns_are_consistent():
    df = make_frame([{"on_hand": 100, "target": 40, "unit_cost_effective": 1.0}]).with_columns(
        pl.lit(1200.0).alias("qty_12m")
    )
    df = diagnose.diagnose(df, CFG, has_pos=False)
    m = metrics.compute(df, CFG)
    assert m["annual_cogs"] == pytest.approx(1200.0)
    assert m["dio_before"] == pytest.approx(100 / 1200 * 365)
    assert m["turns_before"] == pytest.approx(12.0)
    # Acting on the recommendation releases exactly the excess.
    assert m["inventory_value_released"] == pytest.approx(60.0)
