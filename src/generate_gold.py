import argparse
import logging
import sys
import pandas as pd
from src import config

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def generate_gold_layer(tipo="minorista"):
    """
    Genera la capa Gold (Vistas Agregadas) a partir de los datos Silver.
    Estrategia: Full Refresh (Recalcula todo el histórico).
    """
    
    # 1. Definir rutas
    input_path = f"{config.SILVER_PATH}/sepa/{tipo}/precios_alimentos"
    output_dir = f"{config.GOLD_PATH}/sepa/{tipo}"
    
    logger.info(f"--- Starting Gold Generation for {tipo} ---")
    logger.info(f"Reading Silver data from: {input_path}")
    
    try:
        # Leemos solo las columnas necesarias para ahorrar RAM
        columns_needed = [
            "fecha", "id_producto", "descripcion_producto", "marca", 
            "precio_lista", "id_comercio", "nombre_sucursal", "provincia"
        ]
        
        # Pandas con PyArrow lee particiones automáticamente (S3 o Local)
        df = pd.read_parquet(
            input_path,
            columns=columns_needed,
            storage_options=config.STORAGE_OPTIONS
        )
        
        if df.empty:
            logger.warning("Silver dataset is empty. Skipping Gold generation.")
            return

        logger.info(f"Loaded {len(df)} rows from Silver.")
        
        # 2. Optimización de Tipos (Crucial para performance en GroupBy)
        # Convertimos columnas de texto repetitivo a 'category'
        cat_cols = ["id_producto", "descripcion_producto", "marca", "id_comercio", "nombre_sucursal", "provincia"]
        for col in cat_cols:
            if col in df.columns:
                df[col] = df[col].astype("category")
                
        # Asegurar que fecha es datetime
        df['fecha'] = pd.to_datetime(df['fecha'])
        
    except Exception as e:
        logger.error(f"Failed to read/process Silver dataset: {e}")
        # Si no hay datos Silver, no podemos generar Gold. Salimos.
        sys.exit(1)

    # --- KPI 1: Evolución de Precios por Producto (Diario) ---
    logger.info("Generating: Daily Product Price Evolution...")
    
    daily_avg = df.groupby(
        ["fecha", "id_producto", "descripcion_producto", "marca"], 
        observed=True
    )["precio_lista"].agg(['mean', 'min', 'max', 'count']).reset_index()
    
    daily_avg.rename(columns={
        "mean": "precio_promedio",
        "min": "precio_minimo",
        "max": "precio_maximo",
        "count": "cantidad_muestras"
    }, inplace=True)
    
    # Guardar KPI 1
    path_kpi1 = f"{output_dir}/daily_product_prices.parquet"
    daily_avg.to_parquet(path_kpi1, index=False, storage_options=config.STORAGE_OPTIONS)
    logger.info(f"Saved KPI 1 to {path_kpi1}")

    # --- KPI 2: Ranking de Sucursales (Baratas vs Caras) ---
    logger.info("Generating: Store Statistics...")
    
    if "nombre_sucursal" in df.columns:
        store_stats = df.groupby(
            ["fecha", "id_comercio", "nombre_sucursal", "provincia"], 
            observed=True
        ).agg(
            productos_reportados=("id_producto", "count"),
            precio_promedio_general=("precio_lista", "mean")
        ).reset_index()
        
        # Guardar KPI 2
        path_kpi2 = f"{output_dir}/store_stats.parquet"
        store_stats.to_parquet(path_kpi2, index=False, storage_options=config.STORAGE_OPTIONS)
        logger.info(f"Saved KPI 2 to {path_kpi2}")

    # --- KPI 3: Índice "Inflación" Simple (Promedio Global Diario) ---
    logger.info("Generating: Global Daily Price Index...")
    
    daily_index = df.groupby("fecha", observed=True)["precio_lista"].mean().reset_index()
    daily_index.rename(columns={"precio_lista": "indice_precio_global"}, inplace=True)
    
    # Guardar KPI 3
    path_kpi3 = f"{output_dir}/daily_inflation_index.parquet"
    daily_index.to_parquet(path_kpi3, index=False, storage_options=config.STORAGE_OPTIONS)
    logger.info(f"Saved KPI 3 to {path_kpi3}")
    
    logger.info("Gold Layer generation completed successfully.")

def main():
    parser = argparse.ArgumentParser(description="Generate Gold Layer (Analytical Aggregates).")
    parser.add_argument("--type", type=str, choices=["minorista", "mayorista"], default="minorista", help="Dataset type.")
    
    args = parser.parse_args()
    
    generate_gold_layer(args.type)

if __name__ == "__main__":
    main()