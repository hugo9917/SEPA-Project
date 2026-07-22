"""Gold layer: analytical aggregates built from the Silver partitions.

The original version loaded the whole Silver history into a single pandas frame
before grouping. One day of the real feed is tens of millions of rows, so that
call died on memory long before it could group anything -- and when reading
failed it called ``sys.exit(1)``, which kills the Airflow worker process rather
than failing the task cleanly.

This version scans Silver partition by partition in Arrow record batches, folds
partial aggregates as it goes, and combines them at the end. Memory stays
proportional to one batch, not to the dataset.
"""

import argparse
import sys

import pandas as pd
import pyarrow.dataset as ds

from src import config, storage
from src.logging_utils import get_logger
from src.transform_sepa import silver_dataset_path

logger = get_logger(__name__)

BATCH_SIZE = 500_000

PRODUCT_KEYS = ["fecha", "id_producto", "descripcion_producto", "marca"]
STORE_GROUP_KEYS = ["fecha", "id_comercio", "nombre_comercio", "nombre_sucursal", "provincia"]
# Deliberately excludes the description: keeping the grain narrow stops this
# table from multiplying when one product id carries several descriptions.
# The dashboard resolves labels from ``daily_product_prices``.
PROVINCE_PRODUCT_KEYS = ["fecha", "provincia", "id_producto"]

# Columns pulled from Silver. Anything missing from a partition is filled with
# nulls rather than raising, so a schema drift upstream degrades instead of
# breaking the whole layer.
NEEDED_COLUMNS = [
    "id_producto",
    "descripcion_producto",
    "marca",
    "id_comercio",
    "nombre_comercio",
    "nombre_sucursal",
    "provincia",
    "precio_lista",
]


class NoSilverData(Exception):
    """Raised when there is nothing to aggregate."""


def gold_dir(tipo):
    return f"{config.GOLD_PATH}/{tipo}"


# ---------------------------------------------------------------------------
# Streaming scan
# ---------------------------------------------------------------------------


def _partition_batches(fs, partition_path, date_str):
    """Yield cleaned pandas batches for one ``fecha=`` partition."""
    dataset = ds.dataset(storage.strip_scheme(partition_path), filesystem=fs, format="parquet")
    available = [c for c in NEEDED_COLUMNS if c in dataset.schema.names]
    missing = set(NEEDED_COLUMNS) - set(available)
    if missing:
        logger.warning("Partition %s is missing columns: %s", date_str, sorted(missing))

    if "precio_lista" not in available:
        logger.error("Partition %s has no precio_lista column; skipping.", date_str)
        return

    for batch in dataset.to_batches(columns=available, batch_size=BATCH_SIZE):
        if batch.num_rows == 0:
            continue
        frame = batch.to_pandas()
        for column in missing:
            frame[column] = None
        frame["fecha"] = date_str
        frame = frame[frame["precio_lista"].notna()]
        if not frame.empty:
            yield frame


def _fold(accumulator, key, partial):
    """Stash a partial aggregate under ``key``."""
    if partial.empty:
        return
    accumulator.setdefault(key, []).append(partial)


def _collapse(accumulator, key, keys):
    """Reduce one key's partials to a single frame.

    Holding every per-batch partial until the end defeats the point of streaming:
    the final ``concat`` sees batches x groups rows at once, which is what pushed
    the province table (the widest grain) into an OOM kill on real volumes.
    Because the aggregation is associative, collapsing at each partition
    boundary is free and caps the peak at one partition's worth.
    """
    partials = accumulator.get(key)
    if not partials or len(partials) == 1:
        return
    # _reduce, not _combine: the collapsed frame must stay foldable.
    accumulator[key] = [_reduce(partials, keys)]


def _partial_sums(frame, keys):
    """Sum/min/max/count per group -- all associative, so partials can be folded."""
    present = [k for k in keys if k in frame.columns]
    grouped = frame.groupby(present, dropna=False, observed=True)["precio_lista"]
    return grouped.agg(suma="sum", minimo="min", maximo="max", muestras="count").reset_index()


def _reduce(partials, keys):
    """Fold partials into one partial of the same shape.

    Deliberately keeps ``suma``: dividing it out here would make the result
    un-foldable, and an intermediate collapse has to be able to feed the next
    reduction. Only :func:`_combine` is terminal.
    """
    combined = pd.concat(partials, ignore_index=True)
    present = [k for k in keys if k in combined.columns]
    return (
        combined.groupby(present, dropna=False, observed=True)
        .agg(
            suma=("suma", "sum"),
            minimo=("minimo", "min"),
            maximo=("maximo", "max"),
            muestras=("muestras", "sum"),
        )
        .reset_index()
    )


def _combine(partials, keys):
    """Terminal step: fold, then turn the running sum into an average."""
    combined = _reduce(partials, keys)
    combined["precio_promedio"] = combined["suma"] / combined["muestras"]
    return combined.drop(columns=["suma"])


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def build_daily_product_prices(product_partials):
    out = _combine(product_partials, PRODUCT_KEYS)
    out = out.rename(
        columns={
            "minimo": "precio_minimo",
            "maximo": "precio_maximo",
            "muestras": "cantidad_muestras",
        }
    )
    out["fecha"] = pd.to_datetime(out["fecha"])
    return out.sort_values(["fecha", "descripcion_producto"])


def build_province_product_prices(province_partials):
    """Product prices split by province.

    Powers the "where is this cheaper?" and "what does my basket cost in each
    province?" views, which the national grain cannot answer.
    """
    out = _combine(province_partials, PROVINCE_PRODUCT_KEYS)
    out = out.rename(
        columns={
            "minimo": "precio_minimo",
            "maximo": "precio_maximo",
            "muestras": "cantidad_muestras",
        }
    )
    out = out[out["provincia"].notna()]
    out["fecha"] = pd.to_datetime(out["fecha"])
    return out.sort_values(["fecha", "provincia"])


def build_store_stats(store_partials):
    out = _combine(store_partials, STORE_GROUP_KEYS)
    out = out.rename(
        columns={
            "muestras": "productos_reportados",
            "precio_promedio": "precio_promedio_general",
        }
    ).drop(columns=["minimo", "maximo"])
    out["fecha"] = pd.to_datetime(out["fecha"])
    return out.sort_values(["fecha", "precio_promedio_general"])


# Share of each tail dropped before averaging the price relatives.
TRIM = 0.05


def _trimmed_mean(ratio, trim=TRIM):
    """Mean of the price relatives with both tails trimmed.

    The median looked like the safe choice and is in fact degenerate here: on
    daily retail data most products hold their price, so as long as increases
    and decreases each stay under 50% the median lands *inside* the unchanged
    block and returns exactly 1.0 -- on the live feed it did so on all seven
    days, including one where 74% of products were repriced. A headline that
    cannot move is worse than a noisy one.

    Trimming instead of taking a plain mean keeps the robustness the median was
    chosen for: a mis-keyed price 100x off cannot drag the index. This is the
    same trimmed-mean construction central banks publish for core inflation.
    """
    clean = ratio.dropna()
    if clean.empty:
        return 1.0
    if len(clean) < 20:
        # Too few points for percentiles to mean anything.
        return float(clean.mean())
    low, high = clean.quantile(trim), clean.quantile(1 - trim)
    kept = clean[(clean >= low) & (clean <= high)]
    return float(kept.mean()) if len(kept) else float(clean.mean())


def build_inflation_index(daily_products):
    """Daily index plus a matched-product chained index.

    The plain daily average (``indice_precio_global``) moves whenever the mix of
    reported products changes, so it overstates or hides real inflation. The
    chained index compares only products present on *both* consecutive days and
    takes the median price relative, which is the standard way to keep basket
    composition from contaminating the signal.
    """
    per_day = (
        daily_products.assign(total=lambda d: d["precio_promedio"] * d["cantidad_muestras"])
        .groupby("fecha", observed=True)
        .agg(
            total=("total", "sum"),
            muestras=("cantidad_muestras", "sum"),
            productos_distintos=("id_producto", "nunique"),
        )
        .reset_index()
    )
    per_day["indice_precio_global"] = per_day["total"] / per_day["muestras"]
    per_day = per_day.drop(columns=["total"]).sort_values("fecha").reset_index(drop=True)

    dates = per_day["fecha"].tolist()

    # The product grain includes description and brand, and the same id can be
    # reported with different descriptions across retailers. Collapsing to one
    # row per (date, id) first is what keeps the joins below from fanning out.
    prices = (
        daily_products.groupby(["fecha", "id_producto"], observed=True)["precio_promedio"]
        .mean()
        .reset_index()
    )

    relatives = [1.0]
    mean_relatives = [1.0]
    median_relatives = [1.0]
    matched_counts = [0]
    changed_shares = [0.0]

    for previous, current in zip(dates, dates[1:], strict=False):
        left = prices[prices["fecha"] == previous].set_index("id_producto")["precio_promedio"]
        right = prices[prices["fecha"] == current].set_index("id_producto")["precio_promedio"]
        matched = pd.concat([left.rename("prev"), right.rename("curr")], axis=1).dropna()
        matched = matched[matched["prev"] > 0]

        if len(matched):
            ratio = matched["curr"] / matched["prev"]
            relatives.append(_trimmed_mean(ratio))
            mean_relatives.append(float(ratio.mean()))
            median_relatives.append(float(ratio.median()))
            matched_counts.append(len(matched))
            # Day-to-day retail prices are sticky: on a typical day the median
            # relative is exactly 1.0, so the median alone reads as "no news".
            # Reporting how many products actually moved makes that legible.
            changed_shares.append(float((~ratio.between(0.999, 1.001)).mean() * 100))
        else:
            relatives.append(1.0)
            mean_relatives.append(1.0)
            median_relatives.append(1.0)
            matched_counts.append(0)
            changed_shares.append(0.0)

    per_day["variacion_matched_pct"] = [(r - 1) * 100 for r in relatives]
    per_day["variacion_mediana_pct"] = [(r - 1) * 100 for r in median_relatives]
    per_day["variacion_media_pct"] = [(r - 1) * 100 for r in mean_relatives]
    per_day["productos_comparables"] = matched_counts
    per_day["pct_productos_con_cambio"] = changed_shares
    chained = []
    level = 100.0
    for relative in relatives:
        level *= relative
        chained.append(level)
    per_day["indice_matched_base100"] = chained

    base = per_day["indice_precio_global"].iloc[0]
    per_day["indice_global_base100"] = per_day["indice_precio_global"] / base * 100 if base else 0.0
    per_day["variacion_diaria_pct"] = per_day["indice_precio_global"].pct_change() * 100

    return per_day


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def generate_gold_layer(tipo="minorista", fs=None):
    """Rebuild every Gold table for ``tipo``. Returns the output prefix."""
    logger.info("--- Gold generation: %s ---", tipo)

    fs = fs or storage.get_fs()
    source = silver_dataset_path(tipo)
    dates = storage.list_partitions(source, fs=fs)

    if not dates:
        raise NoSilverData(f"No Silver partitions under {source}. Run the transform stage first.")

    logger.info("Scanning %s Silver partition(s): %s", len(dates), dates)

    product_partials = {}
    store_partials = {}
    province_partials = {}
    scanned_rows = 0

    for date_str in dates:
        partition = f"{source}/fecha={date_str}"
        batches = 0
        for frame in _partition_batches(fs, partition, date_str):
            scanned_rows += len(frame)
            batches += 1
            _fold(product_partials, date_str, _partial_sums(frame, PRODUCT_KEYS))
            _fold(store_partials, date_str, _partial_sums(frame, STORE_GROUP_KEYS))
            _fold(province_partials, date_str, _partial_sums(frame, PROVINCE_PRODUCT_KEYS))

        # Collapse this date before moving on, so memory tracks one partition
        # rather than the whole scan.
        _collapse(product_partials, date_str, PRODUCT_KEYS)
        _collapse(store_partials, date_str, STORE_GROUP_KEYS)
        _collapse(province_partials, date_str, PROVINCE_PRODUCT_KEYS)
        logger.info("  fecha=%s -> %s batch(es)", date_str, batches)

    flat_products = [p for parts in product_partials.values() for p in parts]
    flat_stores = [p for parts in store_partials.values() for p in parts]
    flat_provinces = [p for parts in province_partials.values() for p in parts]

    if not flat_products:
        raise NoSilverData(f"Silver partitions under {source} contain no usable rows.")

    logger.info("Scanned %s Silver rows.", f"{scanned_rows:,}")

    outputs = {}

    daily_products = build_daily_product_prices(flat_products)
    outputs["daily_product_prices"] = daily_products
    logger.info("daily_product_prices: %s rows", f"{len(daily_products):,}")

    if flat_stores:
        store_stats = build_store_stats(flat_stores)
        outputs["store_stats"] = store_stats
        logger.info("store_stats: %s rows", f"{len(store_stats):,}")

    if flat_provinces:
        province_prices = build_province_product_prices(flat_provinces)
        if not province_prices.empty:
            outputs["province_product_prices"] = province_prices
            logger.info("province_product_prices: %s rows", f"{len(province_prices):,}")

    inflation = build_inflation_index(daily_products)
    outputs["daily_inflation_index"] = inflation
    logger.info("daily_inflation_index: %s rows", len(inflation))

    target_dir = gold_dir(tipo)
    for name, frame in outputs.items():
        path = f"{target_dir}/{name}.parquet"
        frame.to_parquet(path, index=False, storage_options=config.STORAGE_OPTIONS)
        logger.info("Wrote %s", path)

    logger.info("Gold generation complete: %s", target_dir)
    return target_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(description="Generate the Gold analytical layer.")
    parser.add_argument(
        "--type", dest="tipo", choices=list(config.DATASET_TYPES), default="minorista"
    )
    return parser


def run_cli(argv=None):
    args = build_parser().parse_args(argv)
    try:
        generate_gold_layer(args.tipo)
    except Exception as exc:
        logger.error("Gold generation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
