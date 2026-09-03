"""Diagnostic configuration: every tunable assumption, with defaults.

Exposed at ingestion so the operator can flex them and watch the numbers move.
Persisted per client as config.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DiagnosticConfig:
    """All assumptions behind the diagnostic. Defaults are sensible starting points."""

    # --- Service level, tiered by ABC class (drives safety stock -> the cash number) ---
    service_level_a: float = 0.98
    service_level_b: float = 0.95
    service_level_c: float = 0.90

    # --- ABC classification: share of cumulative revenue per class ---
    abc_threshold_a: float = 0.80
    abc_threshold_b: float = 0.15  # A+B = 0.95; remainder is C

    # --- Inventory policy ---
    # How often the client actually places replenishment orders (0.25 = weekly).
    review_period_months: float = 0.25
    # Lead time is expressed in the SAME unit as the sales grain, i.e. months.
    # (0.25 therefore means about a week.) Kept configurable for other clients.
    lead_time_unit: str = "months"  # days | weeks | months
    default_lead_time: float = 0.25  # in lead_time_unit, used when a SKU has none
    # Prefer the median of the lead times actually present over an arbitrary constant.
    default_lead_time_from_median: bool = True

    # --- Forecasting ---
    forecast_horizon_months: int = 6
    min_history_months: int = 6
    drop_partial_last_month: bool = True
    season_length: int = 12
    # Backtest used both to pick each SKU's model and to measure forecast error.
    cv_horizon: int = 3
    cv_windows: int = 2
    min_train_months: int = 6

    # --- Segmentation ---
    dead_stock_months: int = 12  # no sales in N months -> E&O
    intermittent_zero_share: float = 0.5  # >= this share of zero months -> intermittent

    # --- Valuation ---
    carrying_cost_rate: float = 0.20  # annual, for "carrying cost freed"

    # --- Data handling ---
    missing_stock_as_zero: bool = True  # SKUs sold but absent from stock file -> on-hand 0

    # --- Metadata ---
    client_name: str = "Client"
    currency: str = "EUR"

    @property
    def lead_time_to_months(self) -> float:
        """Factor converting one unit of the client's lead-time column into months."""
        return {"days": 1 / 30.44, "weeks": 7 / 30.44, "months": 1.0}[self.lead_time_unit]

    def service_level_for(self, abc: str) -> float:
        return {
            "A": self.service_level_a,
            "B": self.service_level_b,
            "C": self.service_level_c,
        }.get(abc, self.service_level_c)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.to_dict(), sort_keys=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "DiagnosticConfig":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
