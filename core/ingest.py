"""File loading + column mapping.

Everything is read as text first so SKU codes keep their leading zeros
("0001" must never become 1) and numerics are parsed defensively.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

# Canonical field -> accepted source column names (lowercased, non-alnum stripped).
ALIASES: dict[str, dict[str, list[str]]] = {
    "sales": {
        "sku": ["sku", "item", "itemid", "materialnumber", "material", "artigo", "codigo", "productid"],
        "year": ["year", "ano", "yr"],
        "month": ["month", "mes", "mth"],
        "quantity": ["quantity", "qty", "quantidade", "units", "unidades"],
        "price": ["price", "unitprice", "preco", "precomedio", "avgprice"],
    },
    "inventory": {
        "sku": ["sku", "item", "itemid", "materialnumber", "material", "artigo", "codigo"],
        "units": ["units", "unit", "stock", "quantity", "qty", "onhand", "existencias"],
        "description": ["description", "descricao", "name", "productname", "designacao"],
        "location": ["location", "warehouse", "site", "armazem", "loc"],
        "unit_cost": ["unitcost", "cost", "custo", "standardcost", "avgcost"],
    },
    "suppliers": {
        "sku": ["sku", "item", "itemid", "materialnumber", "material", "artigo", "codigo"],
        "supplier": ["supplier", "supplierid", "suppliercode", "vendor", "fornecedor"],
        "name": ["name", "suppliername", "description", "nome"],
        "lead_time": ["leadtime", "leadtimedays", "leadtimedias", "prazo", "prazoentrega"],
        "moq": ["moq", "minorderqty", "minimumorderquantity", "qtdminima"],
    },
    "purchase_orders": {
        "sku": ["sku", "item", "itemid", "materialnumber", "material", "artigo", "codigo"],
        "ordered_qty": ["orderedqty", "quantity", "qty", "orderqty", "quantidade"],
        "expected_delivery_date": ["expecteddeliverydate", "deliverydate", "eta", "dataentrega"],
    },
}

REQUIRED: dict[str, list[str]] = {
    "sales": ["sku", "year", "month", "quantity", "price"],
    "inventory": ["sku", "units"],
    "suppliers": ["sku"],
    "purchase_orders": ["sku", "ordered_qty"],
}


def _key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def resolve_columns(columns: list[str], kind: str, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Map canonical field -> actual column name, using aliases plus any explicit overrides."""
    lookup = {_key(c): c for c in columns}
    mapping: dict[str, str] = {}
    for field, names in ALIASES[kind].items():
        if overrides and field in overrides and overrides[field] in columns:
            mapping[field] = overrides[field]
            continue
        for candidate in names:
            if candidate in lookup:
                mapping[field] = lookup[candidate]
                break
    missing = [f for f in REQUIRED[kind] if f not in mapping]
    if missing:
        raise ValueError(
            f"{kind}: could not find required column(s) {missing}. Available: {columns}"
        )
    return mapping


def _read_all_text(path: str | Path) -> pl.DataFrame:
    """Read a CSV with every column as text, so codes keep leading zeros."""
    return pl.read_csv(path, infer_schema_length=0, ignore_errors=True)


def _num(col: str) -> pl.Expr:
    """Parse a text column to float, tolerating thousands separators and comma decimals."""
    s = pl.col(col).cast(pl.String).str.strip_chars().str.replace_all(" ", "")
    both = s.str.contains(",") & s.str.contains(r"\.")
    cleaned = (
        pl.when(both)
        .then(s.str.replace_all(",", ""))  # comma = thousands separator
        .otherwise(s.str.replace_all(",", "."))  # comma = decimal separator
    )
    return cleaned.cast(pl.Float64, strict=False)


def _sku(col: str) -> pl.Expr:
    """SKU stays a string, whitespace-trimmed (the stock export pads to fixed width)."""
    return pl.col(col).cast(pl.String).str.strip_chars().alias("sku")


def load_sales(path: str | Path, overrides: dict[str, str] | None = None) -> pl.DataFrame:
    raw = _read_all_text(path)
    m = resolve_columns(raw.columns, "sales", overrides)
    return raw.select(
        _sku(m["sku"]),
        _num(m["year"]).cast(pl.Int32, strict=False).alias("year"),
        _num(m["month"]).cast(pl.Int32, strict=False).alias("month"),
        _num(m["quantity"]).alias("quantity"),
        _num(m["price"]).alias("price"),
    ).drop_nulls(["sku", "year", "month"])


def load_inventory(path: str | Path, overrides: dict[str, str] | None = None) -> pl.DataFrame:
    raw = _read_all_text(path)
    m = resolve_columns(raw.columns, "inventory", overrides)
    cols = [_sku(m["sku"]), _num(m["units"]).alias("units")]
    cols.append(
        pl.col(m["description"]).cast(pl.String).str.strip_chars().alias("description")
        if "description" in m
        else pl.lit(None, dtype=pl.String).alias("description")
    )
    cols.append(
        pl.col(m["location"]).cast(pl.String).str.strip_chars().alias("location")
        if "location" in m
        else pl.lit(None, dtype=pl.String).alias("location")
    )
    cols.append(
        _num(m["unit_cost"]).alias("unit_cost")
        if "unit_cost" in m
        else pl.lit(None, dtype=pl.Float64).alias("unit_cost")
    )
    return raw.select(cols).drop_nulls(["sku"])


def load_suppliers(path: str | Path, overrides: dict[str, str] | None = None) -> pl.DataFrame:
    raw = _read_all_text(path)
    m = resolve_columns(raw.columns, "suppliers", overrides)
    cols = [_sku(m["sku"])]
    for field, dtype in [("supplier", pl.String), ("name", pl.String)]:
        cols.append(
            pl.col(m[field]).cast(pl.String).str.strip_chars().replace("", None).alias(field)
            if field in m
            else pl.lit(None, dtype=dtype).alias(field)
        )
    for field in ("lead_time", "moq"):
        cols.append(
            _num(m[field]).alias(field)
            if field in m
            else pl.lit(None, dtype=pl.Float64).alias(field)
        )
    return raw.select(cols).drop_nulls(["sku"]).unique(subset=["sku"], keep="first")


def load_purchase_orders(path: str | Path, overrides: dict[str, str] | None = None) -> pl.DataFrame:
    raw = _read_all_text(path)
    m = resolve_columns(raw.columns, "purchase_orders", overrides)
    cols = [_sku(m["sku"]), _num(m["ordered_qty"]).alias("ordered_qty")]
    cols.append(
        pl.col(m["expected_delivery_date"]).cast(pl.String).str.strip_chars().alias("expected_delivery_date")
        if "expected_delivery_date" in m
        else pl.lit(None, dtype=pl.String).alias("expected_delivery_date")
    )
    return raw.select(cols).drop_nulls(["sku"])


def empty_purchase_orders() -> pl.DataFrame:
    """Used when the client has no PO export - position collapses to on-hand."""
    return pl.DataFrame(
        schema={"sku": pl.String, "ordered_qty": pl.Float64, "expected_delivery_date": pl.String}
    )
