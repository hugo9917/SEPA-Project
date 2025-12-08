import os
from pathlib import Path

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / "sepa"
PROCESSED_DIR = DATA_DIR / "processed" / "sepa"

# URLs
URL_MINORISTA = "https://datos.produccion.gob.ar/dataset/sepa-precios"
URL_MAYORISTA = "https://datos.produccion.gob.ar/dataset/precios-claros-sepa-mayoristas"

# S3 / MinIO Configuration
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_BUCKET = "sepa-datalake"

# S3 Paths
BRONZE_PATH = f"s3://{S3_BUCKET}/bronze"
SILVER_PATH = f"s3://{S3_BUCKET}/silver"
GOLD_PATH = f"s3://{S3_BUCKET}/gold"

# Storage Options for pandas/s3fs
STORAGE_OPTIONS = {
    "key": S3_ACCESS_KEY,
    "secret": S3_SECRET_KEY,
    "client_kwargs": {
        "endpoint_url": S3_ENDPOINT_URL
    }
}

# Delimiter
CSV_DELIMITER = "|"

# Column Mapping (Raw -> Canonical)
COLUMN_MAPPING = {
    "fecha": "fecha",
    "id_producto": "id_producto",
    "productos_ean": "id_producto", # Fallback or alternative
    "productos_descripcion": "descripcion_producto",
    "productos_marca": "marca",
    "rubro": "rubro",
    "categoria": "rubro", 
    "id_comercio": "id_comercio",
    "id_bandera": "id_bandera",
    "id_sucursal": "id_sucursal",
    "nombre_comercio": "nombre_comercio",
    "razon_social": "nombre_comercio",
    "provincia": "provincia",
    "localidad": "localidad",
    "productos_precio_lista": "precio_lista",
    "precio_unitario_bulto_por_unidad_venta_con_iva": "precio_lista", # Mayorista specific
    "productos_precio_unitario_promo1": "precio_promocion",
    "productos_precio_unitario_con_iva_promo1": "precio_promocion", # Mayorista specific
    "productos_unidad_medida_presentacion": "unidad_medida",
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
    "sucursales_provincia": "provincia",
    "sucursales_latitud": "latitud",
    "sucursales_longitud": "longitud"
}

# Expected Output Schema
EXPECTED_COLUMNS = [
    "fecha",
    "id_producto",
    "descripcion_producto",
    "marca",
    "rubro",
    "id_comercio",
    "id_bandera",
    "id_sucursal",
    "nombre_sucursal",
    "tipo_sucursal",
    "calle",
    "numero",
    "localidad",
    "provincia",
    "latitud",
    "longitud",
    "precio_lista",
    "precio_promocion",
    "unidad_medida"
]

MANDATORY_COLUMNS = ["fecha", "id_producto", "precio_lista"]

# Food and Beverage Keywords for Filtering
FOOD_KEYWORDS = [
    "alimentos", "bebidas", "lacteos", "carnes", "frutas", "verduras", 
    "panaderia", "fiambreria", "congelados", "almacen", "limpieza", "perfumeria" # Keeping broad for now, can refine
]

# Exclude non-food if strictly required, but SEPA is mostly supermarket items.
# We will filter by 'rubro' containing these keywords (case insensitive).
