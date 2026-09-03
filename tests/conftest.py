"""Small synthetic fixtures in the client's 4-file shape.

Deliberately includes the awkward cases: an overstocked SKU, an understocked one,
a dead SKU still holding stock, a short-history SKU, padded SKU codes, a negative
stock line, and a SKU that sells but never appears in the stock file.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TODAY = date(2026, 9, 3)  # so 2026-09 is the partial month
N_MONTHS = 24


def _months(n: int = N_MONTHS) -> list[tuple[int, int]]:
    """The n complete months ending 2026-08, then the partial 2026-09."""
    out = []
    y, m = 2024, 9
    for _ in range(n + 1):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


@pytest.fixture(scope="session")
def dataset(tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("client")
    months = _months()

    sales = ["Year,Month,SKU,Quantity,Price"]
    for i, (y, m) in enumerate(months):
        sales.append(f"{y},{m},0001,{100 + (i % 5) * 10},2.50")   # regular, overstocked
        sales.append(f"{y},{m},0002,{40 + (i % 3) * 5},10.00")    # regular, understocked
        if i % 4 == 0:
            sales.append(f"{y},{m},0003,7,5.00")                  # intermittent
        if i >= len(months) - 4:
            sales.append(f"{y},{m},0004,25,3.00")                 # short history
        if i < 6:
            sales.append(f"{y},{m},0005,50,4.00")                 # stops early -> dead
        sales.append(f"{y},{m},0009,12,1.00")                     # sold, never stocked

    # SKU codes padded to fixed width, as the real stock export does.
    inventory = [
        "SKU,Description,Units",
        "0001                ,OVERSTOCKED ITEM,5000",
        "0002                ,UNDERSTOCKED ITEM,10",
        "0003                ,INTERMITTENT ITEM,60",
        "0004                ,NEW ITEM,200",
        "0005                ,DEAD ITEM,300",
        "0006                ,NEVER SOLD ITEM,150",
        "0007                ,NEGATIVE STOCK,-5",
        "0008                ,ZERO STOCK,0",
    ]
    suppliers = [
        "Supplier,SKU,MOQ,Name,Lead_Time",
        "S001,0001,100,Alpha Foods,0.25",
        "S001,0002,50,Alpha Foods,0.5",
        "S002,0003,10,Beta Supply,",       # missing lead time -> default
        "S002,0004,0,Beta Supply,0.25",
        ",0005,0,,",                        # no supplier code, no lead time
    ]
    pos = [
        "SKU,Ordered Qty,Expected Delivery Date",
        "0001,1000,2026-09-20",   # inbound on an already-overstocked SKU
        "0002,5,2026-09-15",      # partial cover of a real need
        "9999,50,2026-09-30",     # orphan: unknown SKU
    ]

    paths = {}
    for name, rows in [("Sales", sales), ("Stocks", inventory), ("Suppliers", suppliers), ("POs", pos)]:
        p = d / f"{name}.csv"
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
        paths[name.lower()] = p

    # Variant with a real Unit Cost column, to exercise the non-proxy path.
    costed = ["SKU,Description,Units,Unit Cost"] + [
        r + ",1.75" for r in inventory[1:]
    ]
    p = d / "Stocks_with_cost.csv"
    p.write_text("\n".join(costed) + "\n", encoding="utf-8")
    paths["stocks_with_cost"] = p
    return paths


@pytest.fixture(scope="session")
def result(dataset):
    """A full diagnostic on the synthetic data, without purchase orders."""
    from core.config import DiagnosticConfig
    from core.pipeline import run_diagnostic

    cfg = DiagnosticConfig(client_name="Fixture Co", min_history_months=4)
    return run_diagnostic(
        dataset["sales"], dataset["stocks"], dataset["suppliers"], None, cfg, today=TODAY
    )


@pytest.fixture(scope="session")
def result_with_pos(dataset):
    """The same run, but with a purchase-order file supplied."""
    from core.config import DiagnosticConfig
    from core.pipeline import run_diagnostic

    cfg = DiagnosticConfig(client_name="Fixture Co", min_history_months=4)
    return run_diagnostic(
        dataset["sales"], dataset["stocks"], dataset["suppliers"], dataset["pos"], cfg, today=TODAY
    )
