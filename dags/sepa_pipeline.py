from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
import logging

# Import our refactored functions
# Note: We assume PYTHONPATH includes the parent directory of 'src'
from src.fetch_sepa_prices import fetch_prices
from src.transform_sepa import transform_prices
from src.generate_gold import generate_gold_layer

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 3,
    'retry_delay': timedelta(minutes=5),
}

def _fetch_data_callable(**context):
    tipo = context['tipo']
    logical_date = context['logical_date']
    date_str = logical_date.strftime("%Y-%m-%d")
    
    logging.info(f"Fetching data for {date_str} ({tipo})")
    path = fetch_prices(date_str, tipo=tipo)
    logging.info(f"Data fetched to {path}")
    return path

def _transform_data_callable(**context):
    tipo = context['tipo']
    logical_date = context['logical_date']
    date_str = logical_date.strftime("%Y-%m-%d")
    
    logging.info(f"Transforming data for {date_str} ({tipo})")
    path = transform_prices(date_str, tipo=tipo)
    logging.info(f"Data transformed to {path}")
    return path

def _generate_gold_callable(**context):
    tipo = context['tipo']
    logging.info(f"Generating Gold layer ({tipo})")
    path = generate_gold_layer(tipo=tipo)
    logging.info(f"Gold layer generated at {path}")
    return path

with DAG(
    'sepa_pipeline',
    default_args=default_args,
    description='Pipeline for SEPA Food Prices',
    schedule_interval=timedelta(days=1),
    start_date=days_ago(1),
    catchup=False,
    tags=['sepa', 'etl'],
) as dag:

    for tipo in ["minorista", "mayorista"]:
        fetch_task = PythonOperator(
            task_id=f'fetch_data_{tipo}',
            python_callable=_fetch_data_callable,
            op_kwargs={'tipo': tipo},
        )

        transform_task = PythonOperator(
            task_id=f'transform_data_{tipo}',
            python_callable=_transform_data_callable,
            op_kwargs={'tipo': tipo},
        )

        gold_task = PythonOperator(
            task_id=f'generate_gold_{tipo}',
            python_callable=_generate_gold_callable,
            op_kwargs={'tipo': tipo},
        )

        fetch_task >> transform_task >> gold_task
