import argparse
import logging
import os
import re
import sys
import time
import zipfile
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import s3fs

from src import config

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def get_target_url(tipo):
    if tipo == "minorista":
        return config.URL_MINORISTA
    elif tipo == "mayorista":
        return config.URL_MAYORISTA
    else:
        raise ValueError(f"Unknown type: {tipo}")

def find_resource_url(html_content, target_date_str, tipo):
    """
    Parses HTML to find the ZIP download URL for the given date using heuristic search.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    logger.info(f"Searching for resource matching date: {target_date_str}")
    
    # Escape date for regex to handle potential special chars safely
    date_regex = re.compile(re.escape(target_date_str))
    elements_with_date = soup.find_all(string=date_regex)
    
    if not elements_with_date:
        logger.warning(f"No elements found containing date {target_date_str}")
        return None

    logger.info(f"Found {len(elements_with_date)} text nodes with the date.")
    
    for element in elements_with_date:
        # Search strategy: Check nearby <a> tags for "DESCARGAR"
        # 1. Check previous siblings/elements
        prev_links = element.find_all_previous("a", limit=3)
        for link in prev_links:
            if "DESCARGAR" in link.get_text().upper():
                return link.get("href")
            
        # 2. Check next siblings/elements
        next_links = element.find_all_next("a", limit=3)
        for link in next_links:
            if "DESCARGAR" in link.get_text().upper():
                return link.get("href")
            
    return None

def download_file(url, dest_path, retries=3, backoff_factor=2):
    """
    Downloads a file with exponential backoff retries.
    """
    logger.info(f"Downloading {url}...")
    
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(dest_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            logger.info(f"Download complete: {dest_path}")
            return
        except requests.exceptions.RequestException as e:
            wait_time = backoff_factor ** attempt
            logger.warning(f"Download failed (attempt {attempt + 1}/{retries}): {e}. Retrying in {wait_time}s...")
            time.sleep(wait_time)
            
    raise requests.exceptions.RequestException(f"Max retries exceeded for {url}")

def extract_all_zips(start_dir):
    """
    Iteratively finds and extracts all zip files in a directory tree until no zips remain.
    This avoids recursion depth issues and race conditions.
    """
    while True:
        # Snapshot the list of zips to avoid modifying the iterator while looping
        zips = list(Path(start_dir).rglob("*.zip"))
        
        if not zips:
            break
            
        logger.info(f"Found {len(zips)} nested zip(s). Extracting...")
        
        for zip_file in zips:
            try:
                if not zip_file.exists():
                    continue

                logger.info(f"Extracting {zip_file}")
                with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                    # Extract to the same directory where the zip is located
                    zip_ref.extractall(zip_file.parent)
                
                # Delete the zip file after successful extraction
                zip_file.unlink()
                
            except zipfile.BadZipFile:
                logger.error(f"Invalid zip file: {zip_file}")
                # Delete bad zip to avoid infinite loop
                try:
                    zip_file.unlink()
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Error processing {zip_file}: {e}")

# --- Main Logic ---

def fetch_prices(date_str, tipo="minorista"):
    logger.info(f"--- Starting Fetch for {date_str} ({tipo}) ---")
    
    url = get_target_url(tipo)
    
    # 1. Get HTML Page
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch dataset page: {e}")
        raise
        
    # 2. Find Download Link
    download_url = find_resource_url(response.text, date_str, tipo)
    
    if not download_url:
        msg = f"No download link found for date {date_str}."
        logger.warning(msg)
        raise ValueError(msg) # Raise error to fail the Airflow task
        
    # 3. Setup Temp Directory & S3
    s3 = s3fs.S3FileSystem(**config.STORAGE_OPTIONS)
    s3_bronze_zip = f"{config.BRONZE_PATH}/zip/{tipo}/{date_str}"
    s3_bronze_csv = f"{config.BRONZE_PATH}/csv/{tipo}/{date_str}"
    
    # Use TemporaryDirectory for automatic cleanup
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        zip_filename = f"sepa_{tipo}_{date_str}.zip"
        local_zip_path = temp_path / zip_filename
        
        # 4. Download
        download_file(download_url, local_zip_path)
        
        # 5. Upload Raw ZIP to S3 (Backup)
        try:
            logger.info(f"Backing up ZIP to {s3_bronze_zip}")
            s3.put(str(local_zip_path), f"{s3_bronze_zip}/{zip_filename}")
        except Exception as e:
            logger.warning(f"Failed to upload ZIP backup (continuing): {e}")

        # 6. Extract
        extract_dir = temp_path / "extracted"
        extract_dir.mkdir()
        
        # Extract main zip manually first
        logger.info(f"Extracting main zip: {local_zip_path}")
        with zipfile.ZipFile(local_zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
            
        # Now iteratively extract any zips found inside
        extract_all_zips(extract_dir)
        
        # 7. Upload CSVs to S3
        # Identify all CSVs
        csv_files = list(extract_dir.rglob("*.csv"))
        if not csv_files:
            raise FileNotFoundError("No CSV files found after extraction.")
            
        logger.info(f"Found {len(csv_files)} CSV files. Uploading to {s3_bronze_csv}...")
        
        # Optimized upload: Iterate and put
        for csv_file in csv_files:
            # We preserve the filename but flatten the directory structure for Bronze
            # Or we could keep structure. Let's flatten to keep it simple.
            target_key = f"{s3_bronze_csv}/{csv_file.name}"
            s3.put(str(csv_file), target_key)
            
    logger.info(f"✅ Fetch complete. Data available at {s3_bronze_csv}")
    return s3_bronze_csv

# --- CLI Entrypoint ---

def run_cli():
    parser = argparse.ArgumentParser(description="Fetch SEPA prices.")
    parser.add_argument("--date", type=str, help="YYYY-MM-DD")
    parser.add_argument("--type", type=str, choices=["minorista", "mayorista"], default="minorista")
    
    args = parser.parse_args()
    
    target_date = args.date if args.date else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        fetch_prices(target_date, args.type)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_cli()