"""End-to-end tests: the pipeline on synthetic data, the deliverables, and the app pages."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import report  # noqa: E402
from core.config import DiagnosticConfig  # noqa: E402
from core.pipeline import run_diagnostic  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app"


# --------------------------------------------------------------------------- #
# Pipeline behaviour on realistic-but-awkward data
# --------------------------------------------------------------------------- #

def test_pipeline_runs_and_covers_every_sku(result):
    df = result.sku_frame
    # 8 stocked + 1 sold-but-never-stocked
    assert df.height == 9
    assert df["sku"].n_unique() == 9
    assert result.rollup["n_skus"] == 9


def test_padded_sku_codes_reconcile(result):
    """Trailing whitespace in the stock file must not break the join."""
    row = result.sku_frame.filter(pl.col("sku") == "0001").row(0, named=True)
    assert row["description"] == "OVERSTOCKED ITEM"  # joined despite the padding
    assert row["on_hand"] == 5000
    assert row["months_with_sales"] > 0  # matched to sales too


def test_partial_month_excluded_and_forecast_starts_there(result):
    assert result.meta["dropped_partial_period"] == date(2026, 9, 1)
    assert result.meta["last_training_period"] == date(2026, 8, 1)
    assert result.meta["forecast_start"] == date(2026, 9, 1)


def test_overstocked_sku_is_liquidate(result):
    row = result.sku_frame.filter(pl.col("sku") == "0001").row(0, named=True)
    assert row["excess_units"] > 0
    assert row["shortfall_units"] == 0
    assert row["recommendation"].startswith("Liquidate")


def test_understocked_sku_is_buy_and_respects_moq(result):
    row = result.sku_frame.filter(pl.col("sku") == "0002").row(0, named=True)
    assert row["shortfall_units"] > 0
    assert row["buy_qty"] >= 50  # MOQ for this SKU
    assert row["recommendation"] == "Buy"


def test_sold_but_not_stocked_is_zero_on_hand_and_tagged(result):
    row = result.sku_frame.filter(pl.col("sku") == "0009").row(0, named=True)
    assert row["on_hand"] == 0
    assert row["on_hand_source"] == "assumed_zero"


def test_never_sold_stock_is_dead(result):
    row = result.sku_frame.filter(pl.col("sku") == "0006").row(0, named=True)
    assert row["is_dead"]
    assert row["target"] == 0


def test_missing_lead_time_uses_default_and_is_flagged(result):
    row = result.sku_frame.filter(pl.col("sku") == "0003").row(0, named=True)
    assert row["lead_time_source"] == "default"
    assert row["lead_time"] > 0


def test_cost_is_proxied_from_price_when_absent(result):
    row = result.sku_frame.filter(pl.col("sku") == "0001").row(0, named=True)
    assert row["cost_source"] == "price_proxy"
    assert row["unit_cost_effective"] == pytest.approx(2.50)


def test_real_unit_cost_is_used_when_present(dataset):
    cfg = DiagnosticConfig(client_name="Fixture Co", min_history_months=4)
    r = run_diagnostic(
        dataset["sales"], dataset["stocks_with_cost"], dataset["suppliers"], None, cfg,
        today=date(2026, 9, 3),
    )
    row = r.sku_frame.filter(pl.col("sku") == "0001").row(0, named=True)
    assert row["cost_source"] == "actual"
    assert row["unit_cost_effective"] == pytest.approx(1.75)


def test_quality_score_and_checks_present(result):
    assert 0 <= result.quality_score <= 100
    flagged = {r["check"] for r in result.checks.iter_rows(named=True) if r["skus"]}
    assert {"lead_time_defaulted", "cost_proxied", "negative_inventory"} <= flagged


# --------------------------------------------------------------------------- #
# Optional purchase orders
# --------------------------------------------------------------------------- #

def test_without_pos_avoidable_is_na(result):
    assert result.rollup["avoidable_value"] is None
    assert result.rollup["has_purchase_orders"] is False


def test_with_pos_avoidable_appears_and_orphans_are_flagged(result_with_pos):
    ro = result_with_pos.rollup
    assert ro["has_purchase_orders"] is True
    assert ro["avoidable_value"] is not None and ro["avoidable_value"] > 0
    orphan = result_with_pos.checks.filter(pl.col("check") == "orphan_po")
    assert orphan["skus"][0] == 1  # SKU 9999


def test_pos_reduce_shortfall_and_never_double_count(result, result_with_pos):
    """Inbound stock offsets the need; buckets still reconcile exactly."""
    before = result.sku_frame.filter(pl.col("sku") == "0002").row(0, named=True)
    after = result_with_pos.sku_frame.filter(pl.col("sku") == "0002").row(0, named=True)
    assert after["shortfall_units"] < before["shortfall_units"]

    for r in result_with_pos.sku_frame.iter_rows(named=True):
        above = max(r["on_hand"] + r["open_po"] - r["target"], 0)
        assert r["excess_units"] + r["avoidable_units"] == pytest.approx(above)


# --------------------------------------------------------------------------- #
# Deliverables
# --------------------------------------------------------------------------- #

def test_pdf_and_excel_are_written(result, tmp_path):
    pdf = report.write_pdf(result, tmp_path / "d.pdf")
    xlsx = report.write_excel(result, tmp_path / "d.xlsx")
    assert pdf.exists() and pdf.stat().st_size > 1000
    assert xlsx.exists() and xlsx.stat().st_size > 1000

    from openpyxl import load_workbook

    wb = load_workbook(xlsx)
    assert "SKU Actions" in wb.sheetnames
    headers = [c.value for c in wb["SKU Actions"][1]]
    for required in ["SKU", "Description", "Stock", "Target", "Excess", "€ Excess",
                     "Recommendation", "Priority (ABC)"]:
        assert required in headers


def test_pdf_states_na_when_no_po_file(result, tmp_path):
    pytest.importorskip("pypdf")
    from pypdf import PdfReader

    pdf = report.write_pdf(result, tmp_path / "d.pdf")
    text = "\n".join(p.extract_text() or "" for p in PdfReader(str(pdf)).pages)
    assert "No purchase-order file supplied" in text
    assert "Methodology" in text


# --------------------------------------------------------------------------- #
# Streamlit pages render without raising
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "page", ["Home.py", "pages/1_Data_Quality.py", "pages/2_Results.py", "pages/3_Approve.py"]
)
def test_app_pages_render(page, result):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP / page), default_timeout=90)
    at.session_state["result"] = result
    at.session_state["config"] = result.config
    at.run()
    assert not at.exception, f"{page} raised: {at.exception}"


def test_pages_guard_when_no_run_yet():
    """Every page except Home must degrade gracefully with no result in session."""
    from streamlit.testing.v1 import AppTest

    for page in ("pages/1_Data_Quality.py", "pages/2_Results.py", "pages/3_Approve.py"):
        at = AppTest.from_file(str(APP / page), default_timeout=60)
        at.run()
        assert not at.exception
        assert any("No diagnostic has been run" in i.value for i in at.info)
