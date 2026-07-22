"""One command for the whole daily job, for any scheduler to call.

Airflow is one way to run this; a GitHub Actions cron, a systemd timer or a
plain crontab are others. Keeping the day's work behind a single entrypoint is
what makes the pipeline portable between them -- nothing here imports Airflow.

    python -m src.daily                       # both dataset types
    python -m src.daily --type minorista      # just one
    python -m src.daily --prune               # and drop Bronze past retention
"""

import argparse
import json
import sys
import time
from datetime import datetime

from src import config, storage
from src.fetch_sepa_range import ensure_recent
from src.generate_gold import NoSilverData, generate_gold_layer
from src.logging_utils import get_logger

logger = get_logger(__name__)


def run_daily(tipos=None, prune=False, max_days=None):
    """Catch up on missing dates and rebuild Gold. Returns a run report."""
    tipos = tipos or list(config.DATASET_TYPES)
    started = time.time()
    report = {"started": datetime.now().isoformat(timespec="seconds"), "tipos": {}}

    storage.ensure_bucket()

    for tipo in tipos:
        logger.info("=== %s ===", tipo)
        entry = {}

        try:
            entry["catch_up"] = ensure_recent(tipo, max_days=max_days)
        except Exception as exc:
            logger.error("Catch-up failed for %s: %s", tipo, exc)
            entry["catch_up"] = {"error": str(exc)}

        try:
            entry["gold"] = generate_gold_layer(tipo)
        except NoSilverData as exc:
            # Nothing ingested yet is a normal first-run state, not a crash.
            logger.warning("No Silver data for %s yet: %s", tipo, exc)
            entry["gold"] = None
        except Exception as exc:
            logger.error("Gold generation failed for %s: %s", tipo, exc)
            entry["gold"] = {"error": str(exc)}

        if prune:
            entry["pruned"] = storage.prune_bronze(tipo)

        report["tipos"][tipo] = entry

    report["seconds"] = round(time.time() - started, 1)

    failures = [
        tipo
        for tipo, entry in report["tipos"].items()
        if entry.get("catch_up", {}).get("error")
        or entry.get("catch_up", {}).get("failed")
        or (isinstance(entry.get("gold"), dict) and entry["gold"].get("error"))
    ]
    report["ok"] = not failures
    report["failed_tipos"] = failures

    logger.info("Daily run finished in %ss -- ok=%s", report["seconds"], report["ok"])
    return report


def build_parser():
    parser = argparse.ArgumentParser(description="Run the daily SEPA ingest end to end.")
    parser.add_argument(
        "--type",
        dest="tipos",
        action="append",
        choices=list(config.DATASET_TYPES),
        help="Repeatable. Defaults to every dataset type.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete Bronze archives older than the retention window.",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=None,
        help=f"How far back to look for gaps (default {config.BACKFILL_MAX_DAYS}).",
    )
    parser.add_argument(
        "--report",
        help="Write the run report as JSON to this path.",
    )
    return parser


def run_cli(argv=None):
    args = build_parser().parse_args(argv)
    report = run_daily(tipos=args.tipos, prune=args.prune, max_days=args.max_days)

    if args.report:
        # Reporting is observability, not the job. An unwritable path must not
        # turn an ingest that already succeeded into a failed run.
        try:
            with open(args.report, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, default=str)
            logger.info("Wrote run report to %s", args.report)
        except OSError as exc:
            logger.warning("Could not write run report to %s: %s", args.report, exc)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run_cli())
