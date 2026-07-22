"""Backfill CLI: run fetch + transform (+ gold) over a range of dates.

The original version shelled out to ``python -m src.fetch_sepa_prices`` per date
and read success purely from the exit code, which discarded every exception and
made failures impossible to diagnose. It now calls the functions directly, so
errors surface with a real traceback and the whole thing is importable/testable.

Note on range width: the portal keeps only a rolling 7-day window (see
:mod:`src.sepa_source`), so requesting an older range fails fast with an
explanatory message rather than silently producing nothing.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta

from src import config, sepa_source, storage
from src.fetch_sepa_prices import fetch_prices
from src.generate_gold import generate_gold_layer
from src.logging_utils import get_logger
from src.transform_sepa import silver_dataset_path, transform_prices

logger = get_logger(__name__)

# Seconds between dates, to stay polite to the government portal.
POLITENESS_DELAY = 2


def date_range(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def process_date(date_str, tipo, raw_only=False, strict=True, overwrite=False):
    """Fetch and (optionally) transform a single date. Raises on failure."""
    bronze = fetch_prices(date_str, tipo=tipo, strict=strict, overwrite=overwrite)
    if raw_only:
        return {"date": date_str, "bronze": bronze, "silver": None}
    silver = transform_prices(date_str, tipo=tipo)
    return {"date": date_str, "bronze": bronze, "silver": silver}


def run_backfill(
    start_date,
    end_date,
    tipo="minorista",
    raw_only=False,
    strict=True,
    overwrite=False,
    skip_gold=False,
    delay=POLITENESS_DELAY,
):
    started = time.time()
    successful, failed = [], []

    logger.info("Backfill %s -> %s (%s)", start_date.date(), end_date.date(), tipo)

    dates = list(date_range(start_date, end_date))
    for index, current in enumerate(dates):
        date_str = current.strftime("%Y-%m-%d")
        logger.info("--- %s (%s/%s) ---", date_str, index + 1, len(dates))
        try:
            process_date(date_str, tipo, raw_only=raw_only, strict=strict, overwrite=overwrite)
            successful.append(date_str)
        except Exception as exc:
            # One unavailable date must not abort the whole backfill.
            logger.error("%s failed: %s", date_str, exc)
            failed.append({"date": date_str, "error": str(exc)})

        if delay and index < len(dates) - 1:
            time.sleep(delay)

    if successful and not raw_only and not skip_gold:
        try:
            generate_gold_layer(tipo)
        except Exception as exc:
            logger.error("Gold generation failed: %s", exc)

    report = {
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "dataset_type": tipo,
        "successful_dates": successful,
        "failed_dates": failed,
        "total_days_requested": len(dates),
        "execution_time_seconds": round(time.time() - started, 2),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    _write_report(report)
    logger.info("Backfill done: %s succeeded, %s failed.", len(successful), len(failed))
    return report


def _write_report(report):
    directory = config.DATA_DIR
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = (
            directory
            / f"batch_report_{report['start_date']}_{report['end_date']}_{report['dataset_type']}.json"
        )
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("Saved batch report to %s", path)
    except OSError as exc:
        # A read-only volume must not turn a successful backfill into a failure.
        logger.warning("Could not write batch report: %s", exc)


def ensure_recent(tipo="minorista", max_days=None, delay=POLITENESS_DELAY):
    """Ingest every date the portal still publishes that the lake is missing.

    This is what makes the daily schedule self-healing. The portal keeps a
    rolling 7-day window, so a run that fails -- the archive not published yet,
    a network blip, the stack being off overnight -- has a few days of grace
    before that date is gone for good. Comparing what the portal offers against
    the Silver partitions on hand closes those gaps on the next run, and on a
    fresh lake it pulls the whole available window in one go.

    Dates come from the portal's own ``last_modified``, so the strict date check
    in the fetch stage is satisfied by construction.
    """
    max_days = config.BACKFILL_MAX_DAYS if max_days is None else max_days

    try:
        available = sepa_source.available_dates(tipo)
    except Exception as exc:
        logger.error("Could not list what the portal is publishing: %s", exc)
        return {"missing": [], "ingested": [], "failed": [], "error": str(exc)}

    if max_days:
        available = available[:max_days]

    have = set(storage.list_partitions(silver_dataset_path(tipo)))
    missing = [d.strftime("%Y-%m-%d") for d in available if d.strftime("%Y-%m-%d") not in have]

    if not missing:
        logger.info(
            "%s: nothing to catch up on -- all %s published date(s) are in Silver.",
            tipo,
            len(available),
        )
        return {"missing": [], "ingested": [], "failed": []}

    logger.warning("%s: %s date(s) missing from Silver: %s", tipo, len(missing), missing)

    ingested, failed = [], []
    for index, date_str in enumerate(sorted(missing)):
        try:
            process_date(date_str, tipo)
            ingested.append(date_str)
        except Exception as exc:
            logger.error("Catch-up failed for %s: %s", date_str, exc)
            failed.append({"date": date_str, "error": str(exc)})
        if delay and index < len(missing) - 1:
            time.sleep(delay)

    logger.info("%s: caught up %s date(s), %s still failing.", tipo, len(ingested), len(failed))
    return {"missing": missing, "ingested": ingested, "failed": failed}


def build_parser():
    parser = argparse.ArgumentParser(
        description="Fetch and transform SEPA prices for a date range."
    )
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--type", dest="tipo", choices=list(config.DATASET_TYPES), default="minorista"
    )
    parser.add_argument("--raw-only", action="store_true", help="Land Bronze only, skip Silver.")
    parser.add_argument("--skip-gold", action="store_true", help="Do not rebuild Gold afterwards.")
    parser.add_argument(
        "--overwrite", action="store_true", help="Re-download dates already in Bronze."
    )
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="Accept a weekday archive whose date does not match the request.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=POLITENESS_DELAY,
        help="Seconds to wait between dates (default: %(default)s).",
    )
    parser.set_defaults(strict=True)
    return parser


def run_cli(argv=None):
    args = build_parser().parse_args(argv)

    try:
        start = datetime.strptime(args.start_date, "%Y-%m-%d")
        end = datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError:
        logger.error("Dates must be in YYYY-MM-DD format.")
        return 2

    if start > end:
        logger.error("--start-date cannot be after --end-date.")
        return 2

    storage.ensure_bucket()

    report = run_backfill(
        start,
        end,
        tipo=args.tipo,
        raw_only=args.raw_only,
        strict=args.strict,
        overwrite=args.overwrite,
        skip_gold=args.skip_gold,
        delay=args.delay,
    )
    return 0 if report["successful_dates"] else 1


def main():  # kept for backwards compatibility
    return run_cli()


if __name__ == "__main__":
    sys.exit(run_cli())
