"""Read side of the Gold layer, with the analytics the dashboard needs.

Kept apart from ``dashboard.py`` so the query logic is importable and testable
without spinning up Streamlit.
"""

import json

import pandas as pd

from src import config, storage

GOLD_TABLES = {
    "inflation": "daily_inflation_index",
    "products": "daily_product_prices",
    "stores": "store_stats",
    "provinces": "province_product_prices",
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_gold(tipo, fs=None):
    """Load every Gold table for a dataset type. Missing tables come back empty."""
    fs = fs or storage.get_fs()
    prefix = f"{config.GOLD_PATH}/{tipo}"
    frames = {}

    for key, table in GOLD_TABLES.items():
        path = f"{prefix}/{table}.parquet"
        if not storage.exists(path, fs=fs):
            frames[key] = pd.DataFrame()
            continue
        frame = pd.read_parquet(path, storage_options=config.STORAGE_OPTIONS)
        if "fecha" in frame.columns:
            frame["fecha"] = pd.to_datetime(frame["fecha"])
        frames[key] = frame

    return frames


def load_quality(tipo, fs=None):
    """Aggregate the per-date quality reports the transform stage writes."""
    fs = fs or storage.get_fs()
    prefix = storage.strip_scheme(f"{config.SILVER_PATH}/{tipo}/_quality")
    reports = []
    try:
        if fs.exists(prefix):
            for path in fs.glob(f"{prefix}/**/report.json"):
                with fs.open(path, "r") as handle:
                    reports.append(json.load(handle))
    except (OSError, ValueError):
        return pd.DataFrame()

    if not reports:
        return pd.DataFrame()

    frame = pd.DataFrame(reports)
    if "date" in frame.columns:
        frame["fecha"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values("fecha")
    return frame


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def product_labels(products):
    """id_producto -> a single display description.

    The product grain includes the description, so one id can carry several
    spellings across retailers; the most widely reported one wins.
    """
    if products.empty:
        return pd.Series(dtype=str)
    ranked = products.sort_values("cantidad_muestras", ascending=False)
    return ranked.groupby("id_producto", observed=True)["descripcion_producto"].first()


def collapse_to_product_day(products):
    """One row per (fecha, id_producto), weighted by sample count."""
    if products.empty:
        return products
    frame = products.copy()
    frame["_total"] = frame["precio_promedio"] * frame["cantidad_muestras"]
    out = (
        frame.groupby(["fecha", "id_producto"], observed=True)
        .agg(
            _total=("_total", "sum"),
            cantidad_muestras=("cantidad_muestras", "sum"),
            precio_minimo=("precio_minimo", "min"),
            precio_maximo=("precio_maximo", "max"),
        )
        .reset_index()
    )
    out["precio_promedio"] = out["_total"] / out["cantidad_muestras"]
    return out.drop(columns=["_total"])


def date_bounds(frames):
    """Earliest and latest date present across the loaded tables."""
    dates = [f["fecha"] for f in frames if not f.empty and "fecha" in f.columns]
    if not dates:
        return None, None
    combined = pd.concat(dates)
    return combined.min(), combined.max()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def price_movers(products, previous_date, latest_date, min_samples=2):
    """Per-product price change between two days.

    Returns one row per product id -- collapsing first is what stops the join
    from fanning out when a product has several descriptions.
    """
    if products.empty or previous_date is None:
        return pd.DataFrame()

    daily = collapse_to_product_day(products)
    labels = product_labels(products)

    before = daily[daily["fecha"] == previous_date].set_index("id_producto")
    after = daily[daily["fecha"] == latest_date].set_index("id_producto")

    joined = pd.DataFrame(
        {
            "precio_anterior": before["precio_promedio"],
            "precio_actual": after["precio_promedio"],
            "muestras": after["cantidad_muestras"],
        }
    ).dropna()

    joined = joined[(joined["precio_anterior"] > 0) & (joined["muestras"] >= min_samples)]
    if joined.empty:
        return pd.DataFrame()

    joined["variacion_pct"] = (
        (joined["precio_actual"] - joined["precio_anterior"]) / joined["precio_anterior"] * 100
    )
    joined["descripcion_producto"] = joined.index.map(labels)
    return joined.reset_index().sort_values("variacion_pct", ascending=False)


# Above this max/min ratio a "spread" is almost certainly the same barcode
# reported in different units (per unit vs per dozen vs per kilo) rather than
# two shops charging different prices. Without the cap the ranking fills up
# with bakery items showing "99% savings", which is not a real opportunity.
MAX_PLAUSIBLE_PRICE_RATIO = 3.0


def savings_opportunities(products, day, min_samples=5, top=15, max_ratio=None):
    """Products with the widest store-to-store spread on a given day.

    The practical question this answers: how much does shopping around save on
    this exact item? ``precio_minimo``/``precio_maximo`` already span every
    store reporting it, so no store-grain scan is needed.
    """
    if products.empty:
        return pd.DataFrame()

    max_ratio = MAX_PLAUSIBLE_PRICE_RATIO if max_ratio is None else max_ratio

    day_rows = products[products["fecha"] == day]
    day_rows = day_rows[day_rows["cantidad_muestras"] >= min_samples]
    if day_rows.empty:
        return pd.DataFrame()

    frame = day_rows[day_rows["precio_minimo"] > 0].copy()
    frame["ratio"] = frame["precio_maximo"] / frame["precio_minimo"]
    frame = frame[frame["ratio"] <= max_ratio]
    if frame.empty:
        return pd.DataFrame()

    frame["ahorro_pct"] = (
        (frame["precio_maximo"] - frame["precio_minimo"]) / frame["precio_maximo"] * 100
    )
    frame["ahorro_abs"] = frame["precio_maximo"] - frame["precio_minimo"]
    return frame.nlargest(top, "ahorro_pct")


def search_products(products, term, brand=None):
    """Case-insensitive substring search over descriptions."""
    if products.empty or not term:
        return pd.DataFrame()
    mask = products["descripcion_producto"].str.contains(term, case=False, na=False, regex=False)
    found = products[mask]
    if brand:
        found = found[found["marca"] == brand]
    return found


def basket_series(products, ids, quantities=None):
    """Total cost per day of a basket of product ids.

    Only days on which *every* item is present are returned -- a basket whose
    membership changes day to day would show phantom inflation, which is the
    same trap the matched index avoids.
    """
    if products.empty or not ids:
        return pd.DataFrame()

    quantities = quantities or {}
    daily = collapse_to_product_day(products)
    subset = daily[daily["id_producto"].isin(ids)].copy()
    if subset.empty:
        return pd.DataFrame()

    subset["qty"] = subset["id_producto"].map(lambda i: quantities.get(i, 1))
    subset["costo"] = subset["precio_promedio"] * subset["qty"]

    per_day = (
        subset.groupby("fecha", observed=True)
        .agg(costo_total=("costo", "sum"), items=("id_producto", "nunique"))
        .reset_index()
    )
    complete = per_day[per_day["items"] == len(set(ids))]
    return complete.sort_values("fecha") if not complete.empty else pd.DataFrame()


def basket_by_province(province_prices, ids, day, quantities=None, min_coverage=1.0):
    """What the same basket costs in each province on one day.

    Provinces missing part of the basket are dropped by default: comparing a
    full basket against a partial one would rank the province with the least
    data as the cheapest.
    """
    if province_prices.empty or not ids:
        return pd.DataFrame()

    quantities = quantities or {}
    rows = province_prices[
        (province_prices["fecha"] == day) & (province_prices["id_producto"].isin(ids))
    ].copy()
    if rows.empty:
        return pd.DataFrame()

    rows["qty"] = rows["id_producto"].map(lambda i: quantities.get(i, 1))
    rows["costo"] = rows["precio_promedio"] * rows["qty"]

    out = (
        rows.groupby("provincia", observed=True)
        .agg(costo_total=("costo", "sum"), items=("id_producto", "nunique"))
        .reset_index()
    )
    wanted = len(set(ids))
    out["cobertura"] = out["items"] / wanted
    out = out[out["cobertura"] >= min_coverage]
    return out.sort_values("costo_total")


def resolve_preset(products, terms, day):
    """Turn a list of search terms into concrete product ids.

    Descriptions that *start with* the term are preferred over ones that merely
    contain it: a plain "contains" search resolves "leche" to
    "DULCE LECHE REPOSTERIA" and "arroz" to "BARR ARROZ TRAD" — related
    products, but not the staple the basket is meant to track. Within each
    tier the most widely reported product wins, so the basket is anchored to
    items with broad price coverage.
    """
    if products.empty:
        return []

    day_rows = products[products["fecha"] == day]
    if day_rows.empty:
        return []

    descriptions = day_rows["descripcion_producto"].fillna("")
    chosen = []

    for term in terms:
        needle = term.strip()
        contains = day_rows[descriptions.str.contains(needle, case=False, na=False, regex=False)]
        if contains.empty:
            continue

        starts = contains[
            contains["descripcion_producto"].fillna("").str.upper().str.startswith(needle.upper())
        ]
        pool = starts if not starts.empty else contains

        pid = pool.nlargest(1, "cantidad_muestras")["id_producto"].iloc[0]
        if pid not in chosen:
            chosen.append(pid)

    return chosen


def coverage_calendar(frames_dates, start, end):
    """Which days in a range have data, for the gap panel."""
    if start is None or end is None:
        return pd.DataFrame()
    all_days = pd.date_range(start.normalize(), end.normalize(), freq="D")
    present = {pd.Timestamp(d).normalize() for d in frames_dates}
    return pd.DataFrame({"fecha": all_days, "presente": [d in present for d in all_days]})


# Canastas predefinidas, expresadas como términos de búsqueda y resueltas contra
# lo que el feed realmente publica el día seleccionado.
PRESET_BASKETS = {
    "Canasta básica (10 productos)": [
        "LECHE",
        "PAN ",
        "ACEITE",
        "ARROZ",
        "FIDEOS",
        "AZUCAR",
        "YERBA",
        "HARINA",
        "HUEVO",
        "CAFE",
    ],
    "Desayuno": ["LECHE", "CAFE", "AZUCAR", "GALLETITAS", "MERMELADA"],
    "Limpieza": ["LAVANDINA", "DETERGENTE", "JABON", "PAPEL HIGIENICO"],
    "Bebidas": ["GASEOSA", "AGUA", "CERVEZA", "VINO", "JUGO"],
}
