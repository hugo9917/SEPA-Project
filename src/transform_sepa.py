"""Silver layer: clean, enrich and validate the Bronze archive.

Reads the single Bronze ZIP for a date, walks the nested per-retailer archives
in memory, joins ``productos`` with ``sucursales``/``comercio``, and streams the
result to one Parquet file per retailer inside a Hive-style ``fecha=`` partition.

Bugs fixed relative to the original implementation
--------------------------------------------------
* ``config.CHUNK_SIZE`` did not exist -- the module raised ``AttributeError`` on
  the first file it touched, so this stage never ran at all.
* The CSVs are UTF-8 **with BOM**; reading them as ``utf-8`` prefixed the first
  header with ``\\ufeff``, so ``id_comercio`` never mapped and every join key
  was silently missing.
* ``filter_food_beverages`` matched product *descriptions* against category
  keywords ("alimentos", "bebidas", ...). This feed has no ``rubro`` column and
  descriptions are brand strings like "7UP FREE PET X 1.5L", so the filter
  discarded ~100% of rows. Category filtering is now opt-in.
* ``normalize_columns`` emitted duplicate column labels whenever two raw columns
  mapped to the same canonical name, which breaks every downstream ``[]``/merge.
* Schema violations were logged and the data written out anyway; validation is
  now an actual gate.
"""

import argparse
import io
import json
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd
import pandera.errors as pandera_errors
import pyarrow as pa
import pyarrow.parquet as pq

from src import config, storage
from src.fetch_sepa_prices import bronze_archive_path
from src.logging_utils import get_logger

logger = get_logger(__name__)

# pandera >= 0.24 moved the pandas backend into its own namespace and deprecated
# the top-level re-exports.
try:  # pragma: no cover - import shim
    from pandera.pandas import Check, Column, DataFrameSchema
except ImportError:  # pragma: no cover - pandera < 0.24
    from pandera import Check, Column, DataFrameSchema

# ---------------------------------------------------------------------------
# Silver contract
# ---------------------------------------------------------------------------

STRING_COLUMNS = [
    "id_producto",
    "descripcion_producto",
    "marca",
    "unidad_medida",
    "id_comercio",
    "id_bandera",
    "id_sucursal",
    "nombre_comercio",
    "razon_social",
    "nombre_sucursal",
    "tipo_sucursal",
    "calle",
    "numero",
    "localidad",
    "provincia",
]

FLOAT_COLUMNS = [
    "cantidad_presentacion",
    "latitud",
    "longitud",
    "precio_lista",
    "precio_referencia",
    "precio_promocion",
]

# ``fecha`` is the partition key and is therefore not stored inside the files.
SILVER_COLUMNS = STRING_COLUMNS + FLOAT_COLUMNS

SILVER_SCHEMA = pa.schema(
    [pa.field(c, pa.string()) for c in STRING_COLUMNS]
    + [pa.field(c, pa.float64()) for c in FLOAT_COLUMNS]
)

QUALITY_SCHEMA = DataFrameSchema(
    {
        "id_producto": Column(str, nullable=False),
        "id_comercio": Column(str, nullable=False),
        "id_sucursal": Column(str, nullable=False),
        "precio_lista": Column(
            float,
            checks=[
                Check.greater_than_or_equal_to(config.MIN_VALID_PRICE),
                Check.less_than_or_equal_to(config.MAX_VALID_PRICE),
            ],
            nullable=False,
        ),
        "precio_promocion": Column(float, nullable=True, required=False),
    },
    strict=False,
    coerce=False,
)


class ValidationFailed(Exception):
    """Raised when the Silver output violates its schema contract."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def silver_dataset_path(tipo):
    return f"{config.SILVER_PATH}/{tipo}/precios"


def silver_partition_path(date_str, tipo):
    return f"{silver_dataset_path(tipo)}/fecha={date_str}"


def quality_report_path(date_str, tipo):
    return f"{config.SILVER_PATH}/{tipo}/_quality/fecha={date_str}/report.json"


# ---------------------------------------------------------------------------
# Frame helpers
# ---------------------------------------------------------------------------


def _deaccent(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c)
    )


def normalize_columns(df, mapping):
    """Lower-case headers, apply ``mapping`` and keep only canonical columns.

    Two raw columns can legitimately map to the same canonical name (the
    minorista and mayorista price columns, for instance). Renaming both would
    produce duplicate labels, so the first source listed in the mapping wins and
    the others are dropped.
    """
    df = df.copy()
    # ``.strip()`` does not remove a BOM, so strip it explicitly in case a file
    # was opened without utf-8-sig somewhere upstream.
    df.columns = [str(c).replace("﻿", "").strip().lower() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]

    resolved = {}
    for raw_col, canonical in mapping.items():
        if raw_col in df.columns and canonical not in resolved:
            resolved[canonical] = raw_col

    out = pd.DataFrame(index=df.index)
    for canonical, raw_col in resolved.items():
        out[canonical] = df[raw_col]
    return out


def strip_footer_rows(df, key_column="id_comercio"):
    """Remove the publisher's trailing free-text footer lines.

    Every CSV ends with a blank line and something like
    ``Ultima actualizacion: 2026-07-20T16:00:01-03:00``, which the parser reads
    as a data row whose first field holds the whole sentence.
    """
    if key_column not in df.columns or df.empty:
        return df

    keys = df[key_column].astype(str).map(lambda v: _deaccent(v).strip().lower())
    is_footer = keys.str.startswith(config.FOOTER_MARKERS)
    is_blank = keys.isin({"", "nan", "none"})
    return df[~(is_footer | is_blank)]


def read_csv_member(archive, member, chunksize=None):
    """Read a CSV stored inside a ZIP, tolerating both encodings in the feed."""
    for encoding in (config.CSV_ENCODING, config.CSV_ENCODING_FALLBACK):
        try:
            handle = io.TextIOWrapper(archive.open(member, "r"), encoding=encoding, newline="")
            return pd.read_csv(
                handle,
                sep=config.CSV_DELIMITER,
                dtype=str,
                on_bad_lines="skip",
                chunksize=chunksize,
                engine="c",
            )
        except UnicodeDecodeError:
            logger.warning("%s is not %s; retrying with fallback.", member, encoding)
        except pd.errors.EmptyDataError:
            logger.warning("%s is empty.", member)
            return None
    logger.error("Could not decode %s with any known encoding.", member)
    return None


def clean_prices(df):
    """Coerce price columns and drop rows that cannot carry a usable price."""
    # Own the frame: callers pass boolean-mask slices, and assigning into those
    # raises SettingWithCopyWarning (and may not propagate).
    df = df.copy()
    for column in ("precio_lista", "precio_referencia", "precio_promocion"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    if "precio_lista" not in df.columns:
        return df.iloc[0:0]

    valid = (
        df["precio_lista"].notna()
        & (df["precio_lista"] >= config.MIN_VALID_PRICE)
        & (df["precio_lista"] <= config.MAX_VALID_PRICE)
    )
    return df[valid].copy()


def clean_strings(df):
    """Trim text columns and normalise the feed's null placeholders."""
    df = df.copy()
    placeholders = {"", "nan", "none", "sin marca", "s/d", "-"}
    for column in STRING_COLUMNS:
        if column in df.columns:
            trimmed = df[column].astype(str).str.strip()
            df[column] = trimmed.where(~trimmed.str.lower().isin(placeholders), None)
    return df


def filter_food_beverages(df):
    """Opt-in category filter (see :data:`config.ENABLE_CATEGORY_FILTER`)."""
    if not config.ENABLE_CATEGORY_FILTER:
        return df

    pattern = "|".join(config.FOOD_KEYWORDS)
    for column in ("rubro", "descripcion_producto"):
        if column in df.columns:
            haystack = df[column].fillna("").map(_deaccent).str.lower()
            return df[haystack.str.contains(pattern, regex=True, na=False)]

    logger.warning("Category filter enabled but no filterable column present.")
    return df


def conform_to_silver(df):
    """Reindex to the Silver contract so every Parquet part shares one schema."""
    out = df.reindex(columns=SILVER_COLUMNS)
    for column in STRING_COLUMNS:
        series = out[column].astype("object")
        out[column] = series.where(series.notna(), None)
    for column in FLOAT_COLUMNS:
        out[column] = pd.to_numeric(out[column], errors="coerce").astype("float64")
    return out


def validate(df):
    """Run the Pandera contract and return the failure cases (empty if clean)."""
    try:
        QUALITY_SCHEMA.validate(df, lazy=True)
        return pd.DataFrame()
    except pandera_errors.SchemaErrors as err:
        failures = err.failure_cases
        logger.error("Schema violations (%s):\n%s", len(failures), failures.head(10))
        return failures


# ---------------------------------------------------------------------------
# Dimension tables
# ---------------------------------------------------------------------------


def load_dimension(archive, member, mapping, key_column="id_comercio"):
    frame = read_csv_member(archive, member)
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = normalize_columns(frame, mapping)
    frame = strip_footer_rows(frame, key_column)
    for key in config.STORE_KEYS:
        if key in frame.columns:
            frame[key] = frame[key].astype(str).str.strip()
    # A duplicated join key would fan the fact table out multiplicatively.
    keys = [k for k in config.STORE_KEYS if k in frame.columns]
    frame = frame.drop_duplicates(subset=keys or None)
    return frame


def enrich(products, sucursales, comercio):
    """Left-join the store and chain dimensions onto the product facts."""
    out = products

    if not sucursales.empty:
        keys = [k for k in config.STORE_KEYS if k in out.columns and k in sucursales.columns]
        if keys:
            out = out.merge(sucursales, on=keys, how="left", suffixes=("", "_suc"))

    if not comercio.empty:
        keys = [k for k in config.CHAIN_KEYS if k in out.columns and k in comercio.columns]
        if keys:
            out = out.merge(comercio, on=keys, how="left", suffixes=("", "_com"))

    if "provincia_codigo" in out.columns:
        codes = out["provincia_codigo"].astype(str).str.strip().str.upper()
        out["provincia"] = codes.map(config.PROVINCIA_CODES).fillna(codes)

    return out


def prepare_chunk(chunk):
    """Structural pass: map columns, drop footer lines, tidy the join keys.

    Kept separate from :func:`clean_chunk` so ``rows_read`` counts real data
    rows rather than the publisher's footer text, which would otherwise make
    the completeness metric look worse than reality.
    """
    frame = normalize_columns(chunk, config.PRODUCTOS_MAPPING)
    frame = strip_footer_rows(frame, "id_comercio")
    for key in config.STORE_KEYS:
        if key in frame.columns:
            frame[key] = frame[key].astype(str).str.strip()
    return frame


def clean_chunk(frame, date_str=None):
    """Value pass: coerce prices, normalise text, apply the optional filter."""
    frame = clean_prices(frame)
    frame = clean_strings(frame)
    frame = filter_food_beverages(frame)
    if date_str is not None and "fecha" not in frame.columns:
        frame["fecha"] = date_str
    return frame


def process_chunk(chunk, date_str=None):
    """Full cleaning pipeline for one raw ``productos`` chunk."""
    return clean_chunk(prepare_chunk(chunk), date_str=date_str)


# ---------------------------------------------------------------------------
# Streaming Parquet writer
# ---------------------------------------------------------------------------


class PartWriter:
    """Lazily-opened Parquet writer so empty retailers leave no stray file."""

    def __init__(self, fs, target, schema=SILVER_SCHEMA):
        self._fs = fs
        self._target = storage.strip_scheme(target)
        self._schema = schema
        self._handle = None
        self._writer = None
        self.rows = 0

    def write(self, table):
        if self._writer is None:
            self._handle = self._fs.open(self._target, "wb")
            self._writer = pq.ParquetWriter(self._handle, self._schema, compression="snappy")
        self._writer.write_table(table)
        self.rows += table.num_rows

    def close(self):
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


# ---------------------------------------------------------------------------
# Per-retailer processing
# ---------------------------------------------------------------------------


def iter_retailer_archives(outer_zip_path):
    """Yield ``(member_name, ZipFile)`` for each nested per-retailer archive."""
    with zipfile.ZipFile(outer_zip_path) as outer:
        members = sorted(n for n in outer.namelist() if n.lower().endswith(".zip"))

        if config.MAX_COMERCIOS:
            logger.warning(
                "SEPA_MAX_COMERCIOS=%s -- processing a subset of %s archives.",
                config.MAX_COMERCIOS,
                len(members),
            )
            members = members[: config.MAX_COMERCIOS]

        for member in members:
            if outer.getinfo(member).file_size == 0:
                logger.warning("Skipping empty retailer archive: %s", member)
                continue
            try:
                payload = outer.read(member)
                with zipfile.ZipFile(io.BytesIO(payload)) as inner:
                    yield member, inner
            except zipfile.BadZipFile:
                logger.error("Corrupt retailer archive, skipping: %s", member)


def _find_member(archive, filename):
    for name in archive.namelist():
        if Path(name).name.lower() == filename:
            return name
    return None


def process_retailer(inner, writer, stats):
    """Stream one retailer's products through cleaning into ``writer``."""
    products_member = _find_member(inner, "productos.csv")
    if not products_member:
        logger.warning("No productos.csv in retailer archive; skipping.")
        return 0

    sucursales = pd.DataFrame()
    comercio = pd.DataFrame()

    member = _find_member(inner, "sucursales.csv")
    if member:
        sucursales = load_dimension(inner, member, config.SUCURSALES_MAPPING)
    member = _find_member(inner, "comercio.csv")
    if member:
        comercio = load_dimension(inner, member, config.COMERCIO_MAPPING)

    reader = read_csv_member(inner, products_member, chunksize=config.CHUNK_SIZE)
    if reader is None:
        return 0

    written = 0
    for chunk in reader:
        prepared = prepare_chunk(chunk)
        stats["rows_read"] += len(prepared)
        cleaned = clean_chunk(prepared)
        stats["rows_dropped"] += len(prepared) - len(cleaned)
        if cleaned.empty:
            continue

        conformed = conform_to_silver(enrich(cleaned, sucursales, comercio))

        failures = validate(conformed)
        if not failures.empty:
            stats["validation_failures"] += len(failures)

        writer.write(pa.Table.from_pandas(conformed, schema=SILVER_SCHEMA, preserve_index=False))
        written += len(conformed)

    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def transform_prices(date_str, tipo="minorista", fs=None, fail_on_validation=True):
    """Build the Silver partition for one date. Returns its ``s3://`` path."""
    logger.info("--- Silver transform: %s (%s) ---", date_str, tipo)

    fs = fs or storage.get_fs()
    source = bronze_archive_path(date_str, tipo)

    if not storage.exists(source, fs=fs):
        raise FileNotFoundError(
            f"No Bronze archive at {source}. Run the fetch stage for {date_str} first."
        )

    partition = silver_partition_path(date_str, tipo)
    # Rewriting the partition from scratch keeps re-runs idempotent instead of
    # leaving stale part files alongside the new ones.
    storage.remove_prefix(partition, fs=fs)

    stats = {
        "rows_read": 0,
        "rows_written": 0,
        "rows_dropped": 0,
        "validation_failures": 0,
        "retailers": 0,
        "retailers_empty": 0,
    }

    with tempfile.TemporaryDirectory(prefix="sepa-transform-") as tmp:
        local_zip = Path(tmp) / "bronze.zip"
        storage.download_file(source, local_zip, fs=fs)

        for member, inner in iter_retailer_archives(local_zip):
            retailer = Path(member).stem
            with PartWriter(fs, f"{partition}/{retailer}.parquet") as writer:
                rows = process_retailer(inner, writer, stats)

            stats["rows_written"] += rows
            if rows:
                stats["retailers"] += 1
                logger.info("%s -> %s rows", retailer, f"{rows:,}")
            else:
                stats["retailers_empty"] += 1

    if stats["rows_written"] == 0:
        raise ValueError(
            f"Transform produced no rows for {date_str} ({tipo}); "
            f"read {stats['rows_read']:,} raw rows."
        )

    stats["date"] = date_str
    stats["tipo"] = tipo
    stats["completeness_pct"] = round(100.0 * stats["rows_written"] / max(stats["rows_read"], 1), 2)
    write_quality_report(stats, date_str, tipo, fs=fs)

    logger.info(
        "Silver complete: %s rows from %s retailers (%.2f%% of raw rows kept) -> %s",
        f"{stats['rows_written']:,}",
        stats["retailers"],
        stats["completeness_pct"],
        partition,
    )

    if fail_on_validation and stats["validation_failures"]:
        raise ValidationFailed(
            f"{stats['validation_failures']} schema violations in {date_str} ({tipo})."
        )

    return partition


def write_quality_report(stats, date_str, tipo, fs=None):
    """Persist run metrics so the dashboard can show a real quality score."""
    fs = fs or storage.get_fs()
    target = storage.strip_scheme(quality_report_path(date_str, tipo))
    try:
        with fs.open(target, "w") as handle:
            json.dump(stats, handle, indent=2, default=str)
        logger.info("Wrote quality report to s3://%s", target)
    except Exception as exc:  # pragma: no cover - reporting must never fail a run
        logger.warning("Could not write quality report: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        description="Transform the Bronze archive into the Silver layer."
    )
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--type", dest="tipo", choices=list(config.DATASET_TYPES), default="minorista"
    )
    parser.add_argument(
        "--allow-validation-errors",
        dest="fail_on_validation",
        action="store_false",
        help="Write the partition even when the Pandera contract is violated.",
    )
    parser.set_defaults(fail_on_validation=True)
    return parser


def run_cli(argv=None):
    args = build_parser().parse_args(argv)
    try:
        transform_prices(args.date, tipo=args.tipo, fail_on_validation=args.fail_on_validation)
    except Exception as exc:
        logger.error("Transform failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
