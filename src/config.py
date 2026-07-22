"""Central configuration for the SEPA pipeline.

Every value can be overridden through environment variables so the same code
runs unchanged locally, inside Airflow and inside the Streamlit container.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("SEPA_DATA_DIR", str(PROJECT_ROOT / "data")))

# ---------------------------------------------------------------------------
# Source (CKAN open-data portal)
# ---------------------------------------------------------------------------

CKAN_BASE_URL = os.getenv("SEPA_CKAN_BASE_URL", "https://datos.produccion.gob.ar")

CKAN_DATASET_IDS = {
    "minorista": os.getenv("SEPA_DATASET_MINORISTA", "sepa-precios"),
    "mayorista": os.getenv("SEPA_DATASET_MAYORISTA", "precios-claros-sepa-mayoristas"),
}

DATASET_TYPES = tuple(CKAN_DATASET_IDS)

# Human-facing landing pages, kept for the HTML fallback discovery strategy.
DATASET_PAGES = {
    "minorista": f"{CKAN_BASE_URL}/dataset/sepa-precios",
    "mayorista": f"{CKAN_BASE_URL}/dataset/precios-claros-sepa-mayoristas",
}

# Backwards-compatible aliases.
URL_MINORISTA = DATASET_PAGES["minorista"]
URL_MAYORISTA = DATASET_PAGES["mayorista"]

# The portal republishes one ZIP per weekday, each holding the most recent
# snapshot for that weekday. Resource URLs end in ``sepa_<weekday>.zip``.
# Index matches ``datetime.weekday()``: Monday == 0 ... Sunday == 6.
WEEKDAY_SLUGS = (
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
)

# Only the last 7 days are retrievable; older snapshots are overwritten upstream.
SOURCE_RETENTION_DAYS = 7

# How far back the scheduled run looks when closing gaps. Bounded by the
# portal's retention: asking for more cannot find anything. Set to 1 to make the
# daily job strictly "yesterday only" and give up on self-healing.
BACKFILL_MAX_DAYS = _env_int("SEPA_BACKFILL_MAX_DAYS", SOURCE_RETENTION_DAYS)

# How many days of raw Bronze archives to keep. Bronze is ~300 MB per day per
# dataset type, so on a free object-storage tier it is the thing that fills the
# quota. 0 disables pruning (keep everything).
BRONZE_KEEP_DAYS = _env_int("SEPA_BRONZE_KEEP_DAYS", SOURCE_RETENTION_DAYS)

HTTP_TIMEOUT = _env_int("SEPA_HTTP_TIMEOUT", 60)
HTTP_RETRIES = _env_int("SEPA_HTTP_RETRIES", 3)
HTTP_BACKOFF_FACTOR = _env_int("SEPA_HTTP_BACKOFF", 2)
USER_AGENT = os.getenv("SEPA_USER_AGENT", "sepa-pipeline/1.0 (+data-engineering)")

# ---------------------------------------------------------------------------
# S3 / MinIO
# ---------------------------------------------------------------------------

S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "sepa-datalake")

BRONZE_PATH = f"s3://{S3_BUCKET}/bronze"
SILVER_PATH = f"s3://{S3_BUCKET}/silver"
GOLD_PATH = f"s3://{S3_BUCKET}/gold"

STORAGE_OPTIONS = {
    "key": S3_ACCESS_KEY,
    "secret": S3_SECRET_KEY,
    "client_kwargs": {
        "endpoint_url": S3_ENDPOINT_URL,
        "region_name": S3_REGION,
    },
}

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

CSV_DELIMITER = "|"

# Minorista CSVs are UTF-8 *with* a byte-order mark. Reading them as plain
# "utf-8" leaves a ﻿ glued to the first header name, which silently breaks
# the whole column mapping -- utf-8-sig is mandatory here.
CSV_ENCODING = "utf-8-sig"
CSV_ENCODING_FALLBACK = "latin-1"

# Rows per chunk when streaming large product files.
CHUNK_SIZE = _env_int("SEPA_CHUNK_SIZE", 250_000)

# The publisher appends a free-text footer to every CSV, e.g.
# "Ultima actualizacion: 2026-07-20T16:00:01-03:00". Compared accent- and
# case-insensitively (see ``transform_sepa.strip_footer_rows``).
FOOTER_MARKERS = ("ultima actualizacion",)

# Cap the number of per-retailer archives processed. 0 means "no limit"; set a
# small value for smoke tests against the real ~300 MB feed.
MAX_COMERCIOS = _env_int("SEPA_MAX_COMERCIOS", 0)

# ---------------------------------------------------------------------------
# Column mappings (raw -> canonical)
# ---------------------------------------------------------------------------

# NOTE: ``productos_ean`` is a 0/1 flag in this feed, not an EAN code, so it is
# deliberately NOT mapped onto ``id_producto`` (``id_producto`` already holds
# the EAN). The original mapping overwrote every product id with 0/1.
PRODUCTOS_MAPPING = {
    "id_comercio": "id_comercio",
    "id_bandera": "id_bandera",
    "id_sucursal": "id_sucursal",
    "id_producto": "id_producto",
    "productos_descripcion": "descripcion_producto",
    "productos_marca": "marca",
    "productos_cantidad_presentacion": "cantidad_presentacion",
    "productos_unidad_medida_presentacion": "unidad_medida",
    "productos_precio_lista": "precio_lista",
    "productos_precio_referencia": "precio_referencia",
    "productos_precio_unitario_promo1": "precio_promocion",
    # --- Mayorista variants (verified against the live feed) ---
    "precio_unitario_bulto_por_unidad_venta_con_iva": "precio_lista",
    "productos_precio_unitario_con_iva_promo1": "precio_promocion",
    "unidad_venta": "unidad_medida",
}

SUCURSALES_MAPPING = {
    "id_comercio": "id_comercio",
    "id_bandera": "id_bandera",
    "id_sucursal": "id_sucursal",
    "sucursales_nombre": "nombre_sucursal",
    "sucursales_tipo": "tipo_sucursal",
    "sucursales_calle": "calle",
    "sucursales_numero": "numero",
    "sucursales_localidad": "localidad",
    "sucursales_provincia": "provincia_codigo",
    "sucursales_latitud": "latitud",
    "sucursales_longitud": "longitud",
}

COMERCIO_MAPPING = {
    "id_comercio": "id_comercio",
    "id_bandera": "id_bandera",
    "comercio_razon_social": "razon_social",
    "comercio_bandera_nombre": "nombre_comercio",
}

# Kept as an alias so older imports keep working.
COLUMN_MAPPING = PRODUCTOS_MAPPING

# Join keys between productos / sucursales / comercio.
STORE_KEYS = ["id_comercio", "id_bandera", "id_sucursal"]
CHAIN_KEYS = ["id_comercio", "id_bandera"]

# ISO 3166-2:AR codes used in ``sucursales_provincia``.
PROVINCIA_CODES = {
    "AR-A": "Salta",
    "AR-B": "Buenos Aires",
    "AR-C": "Ciudad Autónoma de Buenos Aires",
    "AR-D": "San Luis",
    "AR-E": "Entre Ríos",
    "AR-F": "La Rioja",
    "AR-G": "Santiago del Estero",
    "AR-H": "Chaco",
    "AR-J": "San Juan",
    "AR-K": "Catamarca",
    "AR-L": "La Pampa",
    "AR-M": "Mendoza",
    "AR-N": "Misiones",
    "AR-P": "Formosa",
    "AR-Q": "Neuquén",
    "AR-R": "Río Negro",
    "AR-S": "Santa Fe",
    "AR-T": "Tucumán",
    "AR-U": "Chubut",
    "AR-V": "Tierra del Fuego",
    "AR-W": "Corrientes",
    "AR-X": "Córdoba",
    "AR-Y": "Jujuy",
    "AR-Z": "Santa Cruz",
}

# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = [
    "fecha",
    "id_producto",
    "descripcion_producto",
    "marca",
    "cantidad_presentacion",
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
    "latitud",
    "longitud",
    "precio_lista",
    "precio_referencia",
    "precio_promocion",
]

MANDATORY_COLUMNS = ["fecha", "id_producto", "precio_lista"]

PRIMARY_KEY = ["fecha", "id_comercio", "id_bandera", "id_sucursal", "id_producto"]

# Stores reporting only a handful of items would otherwise dominate the
# "cheapest store" ranking, which says nothing useful about a shopping basket.
MIN_STORE_SAMPLES = _env_int("SEPA_MIN_STORE_SAMPLES", 50)

# Prices outside this band are treated as data-entry errors and dropped.
MIN_VALID_PRICE = _env_float("SEPA_MIN_PRICE", 0.01)
MAX_VALID_PRICE = _env_float("SEPA_MAX_PRICE", 50_000_000.0)

# The SEPA feed is supermarket data end to end and carries no ``rubro`` column,
# so category filtering is OFF by default. The original pipeline filtered
# product descriptions against these keywords, which matched nothing and left
# the Silver layer empty.
ENABLE_CATEGORY_FILTER = _env_bool("SEPA_ENABLE_CATEGORY_FILTER", False)

FOOD_KEYWORDS = [
    "alimento",
    "bebida",
    "lacteo",
    "carne",
    "fruta",
    "verdura",
    "panaderia",
    "fiambreria",
    "congelado",
    "almacen",
]
