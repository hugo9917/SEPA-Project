import argparse
import logging
import subprocess
import sys
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from src import config

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def run_command(cmd):
    """
    Ejecuta un comando en una sub-shell y retorna True si fue exitoso.
    Captura stdout y stderr para loguear.
    """
    logger.info(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        # Loguear la salida estándar (si es relevante)
        if result.stdout:
            logger.info(f"Output: {result.stdout.strip()}")
            
        if result.returncode != 0:
            logger.error(f"Command failed with return code {result.returncode}")
            logger.error(f"Error output: {result.stderr}")
            return False
            
        return True
    except Exception as e:
        logger.error(f"Subprocess execution failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Fetch and transform SEPA prices for a date range.")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD).")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD).")
    parser.add_argument("--type", type=str, choices=["minorista", "mayorista"], default="minorista", help="Dataset type.")
    parser.add_argument("--raw-only", action="store_true", help="Skip transformation step.")
    
    args = parser.parse_args()
    
    start_time = time.time()
    
    try:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError:
        logger.error("Dates must be in YYYY-MM-DD format")
        sys.exit(1)
    
    if start_date > end_date:
        logger.error("Start date cannot be after end date")
        sys.exit(1)

    current_date = start_date
    successful_dates = []
    failed_dates = []
    
    logger.info(f"Starting batch process from {args.start_date} to {args.end_date} for type: {args.type}")

    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        logger.info(f"--- Processing date: {date_str} ---")
        
        # 1. Fetch Step
        # Ejecuta src.fetch_sepa_prices como un módulo (-m)
        fetch_cmd = [sys.executable, "-m", "src.fetch_sepa_prices", "--date", date_str, "--type", args.type]
        
        if run_command(fetch_cmd):
            # 2. Transform Step (si no se pidió solo raw)
            if not args.raw_only:
                transform_cmd = [sys.executable, "-m", "src.transform_sepa", "--date", date_str, "--type", args.type]
                if run_command(transform_cmd):
                    successful_dates.append(date_str)
                else:
                    logger.error(f"Transformation failed for {date_str}")
                    failed_dates.append(date_str)
            else:
                logger.info(f"Skipping transformation for {date_str} (raw-only mode)")
                successful_dates.append(date_str)
        else:
            logger.warning(f"Fetch failed for {date_str} (possibly no data available or connection error).")
            failed_dates.append(date_str)
            
        current_date += timedelta(days=1)
        
        # Pausa para ser amable con el servidor del gobierno (Rate Limiting)
        if current_date <= end_date:
            time.sleep(2)
            
    logger.info("Batch processing complete.")
    logger.info(f"Successful dates count: {len(successful_dates)}")
    logger.info(f"Failed dates count: {len(failed_dates)}")
    
    # Generate Batch Report
    end_time = time.time()
    execution_time = end_time - start_time
    
    report = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "dataset_type": args.type,
        "successful_dates": successful_dates,
        "failed_dates": failed_dates,
        "total_days_processed": (end_date - start_date).days + 1,
        "execution_time_seconds": round(execution_time, 2),
        "timestamp": datetime.now().isoformat()
    }
    
    # Asegurar que el directorio existe
    report_dir = config.DATA_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    
    report_path = report_dir / f"batch_report_{args.start_date}_{args.end_date}.json"
    
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=4)
        
    logger.info(f"Saved batch report to {report_path}")

if __name__ == "__main__":
    main()