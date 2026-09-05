"""Deliverables: the executive PDF and the Excel action list.

These are the product - the console exists to produce and defend them.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
from fpdf import FPDF

from .config import DiagnosticConfig

# Palette mirrors the console: near-black canvas, hairline borders, one lime accent.
INK = (13, 13, 15)
PANEL = (21, 21, 24)
BORDER = (56, 56, 62)
TEXT = (240, 240, 242)
MUTED = (150, 150, 158)
ACCENT = (198, 242, 78)

EXCEL_COLUMNS = [
    ("sku", "SKU"),
    ("description", "Description"),
    ("on_hand", "Stock"),
    ("target", "Target"),
    ("excess_units", "Excess"),
    ("excess_value", "€ Excess"),
    ("recommendation", "Recommendation"),
    ("abc", "Priority (ABC)"),
    # Supporting / audit fields
    ("shortfall_units", "Shortfall"),
    ("buy_qty", "Buy Qty (MOQ applied)"),
    ("shortfall_value", "€ Shortfall"),
    ("segment", "Demand segment"),
    ("model", "Forecast model"),
    ("lead_time", "Lead time"),
    ("lead_time_source", "Lead time source"),
    ("unit_cost_effective", "Unit cost"),
    ("cost_source", "Cost source"),
    ("on_hand_source", "Stock source"),
    ("confidence", "Confidence"),
]


def _money(value: float | None, currency: str = "EUR") -> str:
    if value is None:
        return "n/a"
    for unit, div in (("m", 1e6), ("k", 1e3)):
        if abs(value) >= div:
            return f"{currency} {value / div:,.1f}{unit}"
    return f"{currency} {value:,.0f}"


# --------------------------------------------------------------------------- #
# Excel action list
# --------------------------------------------------------------------------- #

def write_excel(result, path: str | Path) -> Path:
    """Every SKU, with the action columns first and the audit fields behind them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    cols = [(c, label) for c, label in EXCEL_COLUMNS if c in result.sku_frame.columns]
    actions = (
        result.sku_frame.select([pl.col(c).alias(label) for c, label in cols])
        .sort("€ Excess", descending=True)
        .to_pandas()
    )

    with pd_writer(path) as writer:
        actions.to_excel(writer, sheet_name="SKU Actions", index=False)
        result.checks.to_pandas().to_excel(writer, sheet_name="Data Quality", index=False)
        result.supplier_exposure.to_pandas().to_excel(
            writer, sheet_name="Supplier Exposure", index=False
        )
        result.accuracy.to_pandas().to_excel(writer, sheet_name="Forecast Accuracy", index=False)
    return path


def pd_writer(path: Path):
    import pandas as pd

    return pd.ExcelWriter(path, engine="openpyxl")


# --------------------------------------------------------------------------- #
# Executive PDF
# --------------------------------------------------------------------------- #

class _Deck(FPDF):
    def header(self):  # noqa: D102 - fpdf hook
        pass

    def footer(self):  # noqa: D102 - fpdf hook
        self.set_y(-12)
        self.set_font("Helvetica", size=7)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Page {self.page_no()}", align="R")


def _panel(pdf: _Deck, x: float, y: float, w: float, h: float) -> None:
    pdf.set_fill_color(*PANEL)
    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.2)
    pdf.rect(x, y, w, h, style="DF")


def _stat_tile(pdf: _Deck, x: float, y: float, w: float, label: str, value: str, accent=False):
    h = 20.0
    _panel(pdf, x, y, w, h)
    pdf.set_xy(x + 4, y + 3.5)
    pdf.set_font("Helvetica", size=6.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(w - 8, 3, label.upper())
    pdf.set_xy(x + 4, y + 9)
    pdf.set_font("Helvetica", "B", size=13)
    pdf.set_text_color(*(ACCENT if accent else TEXT))
    pdf.cell(w - 8, 7, value)


def _section(pdf: _Deck, title: str) -> None:
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", size=9)
    pdf.set_text_color(*TEXT)
    pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*BORDER)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(1.5)


def _table(pdf: _Deck, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
    pdf.set_font("Helvetica", size=6.5)
    pdf.set_text_color(*MUTED)
    for h, w in zip(headers, widths):
        pdf.cell(w, 5, h.upper())
    pdf.ln(5)
    pdf.set_font("Helvetica", size=7.5)
    pdf.set_text_color(*TEXT)
    for row in rows:
        for cell, w in zip(row, widths):
            text = str(cell)
            limit = int(w / 1.7)
            pdf.cell(w, 4.6, text[:limit])
        pdf.ln(4.6)


def write_pdf(result, path: str | Path) -> Path:
    """One-page executive summary in the layout the operating partner expects."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg: DiagnosticConfig = result.config or DiagnosticConfig()
    ro, mt = result.rollup, result.metrics
    cur = cfg.currency

    pdf = _Deck(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_fill_color(*INK)
    pdf.rect(0, 0, pdf.w, pdf.h, style="F")

    # --- Headline -----------------------------------------------------------
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, "WORKING CAPITAL DIAGNOSTIC", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", size=17)
    pdf.set_text_color(*TEXT)
    pdf.cell(0, 9, cfg.client_name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", size=22)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 12, f"{_money(ro['opportunity_value'], cur)} identified inventory opportunity",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=7)
    pdf.set_text_color(*MUTED)
    basis = "excess + shortfall" if not ro["has_purchase_orders"] else "excess + avoidable + shortfall"
    pdf.cell(0, 4, f"Sum of {basis}. Cash to release and cash to invest are shown separately below.",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # --- KPI tiles ----------------------------------------------------------
    y = pdf.get_y()
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    w = (usable - 3 * 3) / 4
    _stat_tile(pdf, pdf.l_margin, y, w, "Excess inventory (release)", _money(ro["excess_value"], cur), accent=True)
    _stat_tile(pdf, pdf.l_margin + (w + 3), y, w, "Avoidable purchases (save)", _money(ro["avoidable_value"], cur))
    _stat_tile(pdf, pdf.l_margin + 2 * (w + 3), y, w, "Inventory shortfalls (invest)", _money(ro["shortfall_value"], cur))
    _stat_tile(pdf, pdf.l_margin + 3 * (w + 3), y, w, "SKUs requiring action", f"{ro['skus_requiring_action']:,}")
    pdf.set_y(y + 20 + 3)

    # --- Working capital metrics -------------------------------------------
    y = pdf.get_y()
    def fmt(v, suffix="", dp=0):
        return "n/a" if v is None else f"{v:,.{dp}f}{suffix}"
    _stat_tile(pdf, pdf.l_margin, y, w, "Inventory value", _money(mt["inventory_value_before"], cur))
    _stat_tile(pdf, pdf.l_margin + (w + 3), y, w, "DIO (before -> target)",
               f"{fmt(mt['dio_before'],'d')} -> {fmt(mt['dio_target'],'d')}")
    _stat_tile(pdf, pdf.l_margin + 2 * (w + 3), y, w, "Inventory turns",
               f"{fmt(mt['turns_before'],'',2)} -> {fmt(mt['turns_target'],'',2)}")
    _stat_tile(pdf, pdf.l_margin + 3 * (w + 3), y, w, "Stock released",
               _money(mt["inventory_value_released"], cur))
    pdf.set_y(y + 20)

    # --- Top opportunities / risks -----------------------------------------
    def rows_from(df: pl.DataFrame, value_col: str, unit_col: str):
        out = []
        for r in df.iter_rows(named=True):
            out.append([
                r["sku"],
                (r.get("description") or "")[:28],
                r.get("abc") or "",
                f"{r.get(unit_col) or 0:,.0f}",
                _money(r.get(value_col), cur),
            ])
        return out

    widths = [22, 62, 12, 26, 30]
    heads = ["SKU", "Description", "ABC", "Units", "Value"]

    _section(pdf, "Top 10 cash-release opportunities")
    _table(pdf, heads, rows_from(result.top_cash_release(10), "excess_value", "excess_units"), widths)

    _section(pdf, "Top 10 supply risks")
    _table(pdf, heads, rows_from(result.top_supply_risks(10), "shortfall_value", "shortfall_units"), widths)

    # --- Methodology / data quality ----------------------------------------
    _section(pdf, f"Methodology, assumptions and data quality  -  score {result.quality_score}/100")
    pdf.set_font("Helvetica", size=7)
    pdf.set_text_color(*MUTED)
    meta = result.meta
    lines = [
        f"Target = order-up-to level: demand over (lead time + review period) + safety stock at the "
        f"SKU's ABC service level ({cfg.service_level_a:.0%}/{cfg.service_level_b:.0%}/{cfg.service_level_c:.0%}).",
        f"Safety stock uses each SKU's own backtested forecast error. Review period "
        f"{cfg.review_period_months} months; lead time read in {cfg.lead_time_unit}; "
        f"default {meta.get('default_lead_time_used', cfg.default_lead_time)} where missing.",
        f"Demand forecast: Nixtla statsforecast, {cfg.forecast_horizon_months} months from "
        f"{meta.get('forecast_start')}, models selected per SKU by backtest. "
        f"History {meta.get('first_period')} to {meta.get('last_training_period')} "
        f"({meta.get('n_periods')} complete months; partial month excluded from training).",
    ]
    if not ro["has_purchase_orders"]:
        lines.append("No purchase-order file supplied: avoidable purchases could not be assessed (N/A).")
    for line in lines:
        pdf.multi_cell(0, 3.6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    top_checks = result.checks.sort("share_of_skus", descending=True).head(4)
    for r in top_checks.iter_rows(named=True):
        if r["skus"]:
            pdf.multi_cell(
                0, 3.6,
                f"- {r['label']}: {r['skus']:,} SKUs ({r['share_of_skus']:.0%}).",
                new_x="LMARGIN", new_y="NEXT",
            )

    pdf.ln(2)
    pdf.set_font("Helvetica", size=6.5)
    pdf.cell(0, 4, f"Generated {date.today().isoformat()}  -  figures are estimates for management review.")

    pdf.output(str(path))
    return path
