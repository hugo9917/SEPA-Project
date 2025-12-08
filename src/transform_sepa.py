import argparse
import logging
import sys
import pandas as pd
import pandera as pa
from pandera import Column, Check, DataFrameSchema
import s3fs
from src import config

# --- Configuration & Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def normalize_columns(df, mapping):
    """
    Renames columns based on mapping and selects only relevant ones.
    Handles case-insensitivity mapping inputs to canonical names.
    """
    # Normalize input columns to lowercase/stripped
    df.columns = [c.lower().strip() for c in df.columns]
    
    # Identify which mapped columns exist in this dataframe
    # mapping structure assumed: { "raw_col_name": "canonical_name" }
    available_map = {
        col: target 
        for col, target in mapping.items() 
        if col in df.columns
    }
    
    # Rename known columns
    df = df.rename(columns=available_map)
    
    # Filter to keep only the canonical columns we managed to map
    # We use a set to avoid duplicates if multiple inputs mapped to same output
    kept_cols = list(set(available_map.values()))
    
    # Return df with only relevant columns (if they exist)
    return df[[c for c in kept_cols if c in df.columns]]

def filter_food_beverages(df):
    """
    Filters rows where 'rubro' or 'descripcion' contains food-related keywords.
    """
    pattern = "|".join(config.FOOD_KEYWORDS)
    
    if "rubro" in df.columns:
        mask = df["rubro"].astype(str).str.lower().str.contains(pattern, regex=True, na=False)
        return df[mask]
    elif "descripcion_producto" in df.columns:
        # Fallback to description if rubro is missing
        mask = df["descripcion_producto"].astype(str).str.lower().str.contains(pattern, regex=True, na=False)
        return df[mask]
    
    logger.warning("Neither 'rubro' nor 'descripcion_producto' found. Keeping all rows.")
    return df

def process_chunk(chunk, date_str):
    """
    Applies transformation logic to a single chunk of data.
    """
    # 1. Normalize columns
    chunk = normalize_columns(chunk, config.COLUMN_MAPPING)
    
    # 2. Add metadata
    if "fecha" not in chunk.columns:
        chunk["fecha"] = date_str
    
    # 3. Filter rows (Business Logic)
    chunk = filter_food_beverages(chunk)
    
    # 4. Type Casting & Cleaning
    if "precio_lista" in chunk.columns:
        chunk["precio_lista"] = pd.to_numeric(chunk["precio_lista"], errors='coerce')
        chunk = chunk.dropna(subset=["precio_lista"])
        chunk = chunk[chunk["precio_lista"] > 0] # Sanity check
        
    if "precio_promocion" in chunk.columns:
        chunk["precio_promocion"] = pd.to_numeric(chunk["precio_promocion"], errors='coerce')

    # String cleanup
    str_cols = ["descripcion_producto", "marca", "rubro", "nombre_comercio", "nombre_sucursal"]
    for col in str_cols:
        if col in chunk.columns:
            chunk[col] = chunk[col].astype(str).str.strip()
            
    return chunk

def get_sucursales_df(csv_files):
    """
    Locates and loads the 'sucursales' or 'comercio' file from the file list.
    """
    sucursales_files = [f for f in csv_files if "sucursales" in f.lower() or ("comercio" in f.lower() and "prod" not in f.lower())]
    
    if not sucursales_files:
        logger.warning("No sucursales/stores file found in this batch.")
        return pd.DataFrame()
        
    s3_path = f"s3://{sucursales_files[0]}"
    logger.info(f"Loading reference stores from: {s3_path}")
    
    try:
        df = pd.read_csv(
            s3_path,
            sep=config.CSV_DELIMITER,
            encoding='utf-8', # Assuming utf-8, fallback handled in main loop if needed
            on_bad_lines='skip',
            dtype=str,
            storage_options=config.STORAGE_OPTIONS
        )
        # Normalize to ensure we have id_comercio, id_sucursal, etc.
        df = normalize_columns(df, config.SUCURSALES_MAPPING)
        return df
    except Exception as e:
        logger.error(f"Failed to load sucursales: {e}")
        return pd.DataFrame()

# --- Main Pipeline Logic ---

def transform_prices(date_str, tipo="minorista"):
    fs = s3fs.S3FileSystem(**config.STORAGE_OPTIONS)
    
    # S3 Paths
    s3_input_dir = f"{config.BRONZE_PATH}/csv/{tipo}/{date_str}"
    
    # List files
    try:
        csv_files = fs.glob(f"{s3_input_dir}/**/*.csv")
    except Exception as e:
        logger.error(f"Failed to access S3 path {s3_input_dir}: {e}")
        raise

    if not csv_files:
        raise FileNotFoundError(f"No files found in {s3_input_dir}")
        
    logger.info(f"Found {len(csv_files)} files in Bronze layer.")
    
    # Load Dimension Table (Stores)
    stores_df = get_sucursales_df(csv_files)
    
    processed_chunks = []
    total_rows = 0

    # Iteration over files
    for input_csv in csv_files:
        filename = input_csv.split("/")[-1].lower()
        
        # Skip known non-product files
        if "sucursales" in filename or "comercio" in filename:
            continue
            
        logger.info(f"Processing file: {filename}")
        s3_file_path = f"s3://{input_csv}"
        
        try:
            for chunk in pd.read_csv(
                s3_file_path, 
                chunksize=config.CHUNK_SIZE, 
                sep=config.CSV_DELIMITER, 
                on_bad_lines='skip', 
                dtype=str, 
                storage_options=config.STORAGE_OPTIONS
            ):
                # Process Chunk
                cleaned = process_chunk(chunk, date_str)
                
                if cleaned.empty:
                    continue

                # Join with Stores (Enrichment)
                if not stores_df.empty:
                    join_keys = ["id_comercio", "id_bandera", "id_sucursal"]
                    # Ensure keys exist before merge
                    if all(k in cleaned.columns for k in join_keys) and all(k in stores_df.columns for k in join_keys):
                        cleaned = pd.merge(cleaned, stores_df, on=join_keys, how="left")
                
                processed_chunks.append(cleaned)
                total_rows += len(cleaned)

        except Exception as e:
            logger.error(f"Error processing {filename}: {e}")
            continue

    if not processed_chunks:
        logger.warning("No data resulted from transformation.")
        return None

    # Concatenate & Deduplicate
    logger.info("Concatenating chunks...")
    final_df = pd.concat(processed_chunks, ignore_index=True)
    
    # Remove duplicates based on composite PK
    dedup_subset = [c for c in ["fecha", "id_producto", "id_comercio", "id_sucursal"] if c in final_df.columns]
    final_df = final_df.drop_duplicates(subset=dedup_subset)
    
    # --- Quality Check (Pandera) ---
    logger.info("Running Data Quality Checks (Pandera)...")
    
    schema = DataFrameSchema({
        "fecha": Column(str, checks=Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "id_producto": Column(str, nullable=False),
        "precio_lista": Column(float, checks=Check.greater_than(0), nullable=False),
        "precio_promocion": Column(float, nullable=True, required=False),
    }, coerce=True)

    try:
        final_df = schema.validate(final_df, lazy=True)
        logger.info("✅ Data Quality passed.")
    except pa.errors.SchemaErrors as err:
        logger.warning("⚠️ Schema errors found:")
        logger.warning(err.failure_cases.head())
        # Filter out invalid critical rows if necessary, or just log
        
    # --- Save to Silver (Hive Partitioned) ---
    # Path: silver/sepa/minorista/precios_alimentos/fecha=YYYY-MM-DD/data.parquet
    
    partition_dir = f"{config.SILVER_PATH}/sepa/{tipo}/precios_alimentos/fecha={date_str}"
    output_path = f"{partition_dir}/data.parquet"
    
    # Drop partition column before writing (standard Hive practice)
    final_df_to_write = final_df.drop(columns=["fecha"])
    
    logger.info(f"Writing {len(final_df)} rows to {output_path}")
    final_df_to_write.to_parquet(output_path, index=False, storage_options=config.STORAGE_OPTIONS)
    
    return output_path

# --- CLI Entrypoint ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--type", default="minorista")
    args = parser.parse_args()
    
    transform_prices(args.date, args.type)