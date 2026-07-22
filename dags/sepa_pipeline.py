"""Airflow DAG: SEPA Bronze -> Silver -> Gold, one branch per dataset type.

Scheduling
----------
The portal publishes day ``D``'s archive during the afternoon of day ``D``
itself. With a daily cron the run for data interval ``D`` fires at ``D+1 06:00``
UTC, which leaves a comfortable margin. ``data_interval_start`` is the date
being processed.

Self-healing
------------
The portal keeps a rolling 7-day window and nothing older, so a failed run is on
a deadline: miss a date for a week and it is gone for good. Rather than trust a
single daily task to always succeed, every run ends by comparing what the portal
currently publishes against the Silver partitions on hand and ingesting the
difference (``catch_up``).

That makes the schedule robust to the things that actually happen: the archive
not being published yet, a network blip, the laptop being closed overnight. It
also means a fresh lake fills itself with the whole available window on the
first run instead of only yesterday.

``catch_up`` and ``generate_gold`` run under ``all_done`` so a failed fetch does
not skip the recovery path -- which is the one case where recovery matters most.
"""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.task_group import TaskGroup
from airflow.utils.trigger_rule import TriggerRule

from src.fetch_sepa_prices import fetch_prices
from src.fetch_sepa_range import ensure_recent
from src.generate_gold import generate_gold_layer
from src.transform_sepa import transform_prices

DATASET_TYPES = ["minorista", "mayorista"]

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
}


def _target_date(context):
    """The date this run is responsible for."""
    interval_start = context.get("data_interval_start") or context["logical_date"]
    return interval_start.strftime("%Y-%m-%d")


def fetch_callable(tipo, **context):
    return fetch_prices(_target_date(context), tipo=tipo)


def transform_callable(tipo, **context):
    return transform_prices(_target_date(context), tipo=tipo)


def catch_up_callable(tipo, **context):
    """Ingest anything the portal still offers that Silver is missing."""
    report = ensure_recent(tipo=tipo)
    if report.get("failed"):
        # Surface it as a failure so it is visible in the UI, but only after the
        # dates that *did* work have been ingested.
        raise RuntimeError(
            f"{len(report['failed'])} date(s) could not be caught up: {report['failed']}"
        )
    return report


def gold_callable(tipo, **context):
    return generate_gold_layer(tipo=tipo)


with DAG(
    dag_id="sepa_pipeline",
    description="Ingesta diaria de precios SEPA (Argentina): bronze -> silver -> gold",
    default_args=default_args,
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    # Each run downloads and rewrites the same partitions, so overlapping runs
    # would race on the same S3 prefixes.
    max_active_runs=1,
    tags=["sepa", "etl", "medallion", "diario"],
    doc_md=__doc__,
) as dag:
    for tipo in DATASET_TYPES:
        with TaskGroup(group_id=tipo) as branch:
            fetch_task = PythonOperator(
                task_id="fetch_bronze",
                python_callable=fetch_callable,
                op_kwargs={"tipo": tipo},
                execution_timeout=timedelta(hours=1),
            )

            transform_task = PythonOperator(
                task_id="transform_silver",
                python_callable=transform_callable,
                op_kwargs={"tipo": tipo},
                execution_timeout=timedelta(hours=2),
            )

            catch_up_task = PythonOperator(
                task_id="catch_up",
                python_callable=catch_up_callable,
                op_kwargs={"tipo": tipo},
                # Runs even when the day's fetch failed: that is exactly when
                # there is a gap to close.
                trigger_rule=TriggerRule.ALL_DONE,
                execution_timeout=timedelta(hours=6),
            )

            gold_task = PythonOperator(
                task_id="generate_gold",
                python_callable=gold_callable,
                op_kwargs={"tipo": tipo},
                trigger_rule=TriggerRule.ALL_DONE,
                execution_timeout=timedelta(hours=1),
            )

            fetch_task >> transform_task >> catch_up_task >> gold_task
