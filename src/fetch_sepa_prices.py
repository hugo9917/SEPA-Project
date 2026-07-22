"""Bronze layer: download the daily SEPA archive and land it untouched in S3.

Design note
-----------
The previous version extracted every nested ZIP and uploaded each CSV to a flat
prefix (``bronze/csv/<tipo>/<date>/<filename>``). Every retailer archive contains
files with the *same three names* (``productos.csv``, ``sucursales.csv``,
``comercio.csv``), so each upload overwrote the previous one and only a single
retailer survived -- roughly 99% of the day's data was lost before it ever
reached Silver.

Bronze now keeps the original archive byte-for-byte as a single object. That is
what a raw layer is for: it is reproducible, cheap to write, and lets Silver be
rebuilt without re-hitting the government portal.
"""

import argparse
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import requests

from src import config, sepa_source, storage
from src.logging_utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def bronze_prefix(date_str, tipo):
    return f"{config.BRONZE_PATH}/{tipo}/fecha={date_str}"


def bronze_archive_path(date_str, tipo):
    return f"{bronze_prefix(date_str, tipo)}/sepa_{tipo}_{date_str}.zip"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_file(url, dest_path, retries=None, backoff_factor=None, session=None):
    """Stream a URL to disk with exponential backoff.

    Chains the last :class:`requests.RequestException` when every attempt fails,
    instead of discarding the real cause as the original version did.
    """
    retries = config.HTTP_RETRIES if retries is None else retries
    backoff_factor = config.HTTP_BACKOFF_FACTOR if backoff_factor is None else backoff_factor
    session = session or requests.Session()
    dest_path = Path(dest_path)
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            logger.info("Downloading %s (attempt %s/%s)", url, attempt, retries)
            with session.get(
                url,
                stream=True,
                timeout=config.HTTP_TIMEOUT,
                headers={"User-Agent": config.USER_AGENT},
            ) as response:
                response.raise_for_status()
                written = 0
                with open(dest_path, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        if chunk:
                            handle.write(chunk)
                            written += len(chunk)

            if written == 0:
                raise requests.exceptions.RequestException("Empty response body")

            logger.info("Downloaded %.1f MiB to %s", written / 1024 / 1024, dest_path)
            return dest_path

        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt == retries:
                break
            wait = backoff_factor**attempt
            logger.warning("Download failed: %s. Retrying in %ss...", exc, wait)
            time.sleep(wait)

    raise requests.exceptions.RequestException(
        f"Max retries ({retries}) exceeded for {url}"
    ) from last_error


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def archive_dates(zip_path):
    """Dates encoded in the archive's top-level folders (``2026-07-21/...``)."""
    dates = set()
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            head = name.split("/", 1)[0]
            try:
                dates.add(datetime.strptime(head, "%Y-%m-%d").date())
            except ValueError:
                continue
    return dates


def validate_archive(zip_path, date_str, strict=True):
    """Confirm the archive is a readable ZIP holding the expected date.

    The publisher stamps the data date into the top-level directory name, which
    is a much stronger guarantee than the resource's ``last_modified`` field.
    """
    path = Path(zip_path)
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError(f"Downloaded archive is empty: {path}")

    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Downloaded file is not a valid ZIP: {path}") from exc

    if not members:
        raise ValueError(f"Downloaded archive contains no entries: {path}")

    found = archive_dates(path)
    expected = sepa_source.parse_date(date_str)

    if not found:
        logger.warning("Archive carries no date folder; skipping date validation.")
    elif expected not in found:
        message = (
            f"Archive content is dated {sorted(str(d) for d in found)} "
            f"but {date_str} was requested."
        )
        if strict:
            raise ValueError(message)
        logger.warning(message)
    else:
        logger.info("Archive date folder confirms %s", date_str)

    return members


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def fetch_prices(date_str, tipo="minorista", strict=True, overwrite=False, fs=None):
    """Download the archive for ``date_str`` and store it in Bronze.

    Returns the ``s3://`` path of the stored archive.
    """
    logger.info("--- Bronze fetch: %s (%s) ---", date_str, tipo)
    sepa_source.parse_date(date_str)  # fail fast on malformed input

    fs = fs or storage.get_fs()
    storage.ensure_bucket(fs)

    target = bronze_archive_path(date_str, tipo)

    if not overwrite and storage.exists(target, fs=fs):
        logger.info("Bronze object already present, skipping download: %s", target)
        return target

    url = sepa_source.resolve_download_url(date_str, tipo, strict=strict)

    with tempfile.TemporaryDirectory(prefix="sepa-fetch-") as tmp:
        local_zip = Path(tmp) / f"sepa_{tipo}_{date_str}.zip"
        download_file(url, local_zip)
        members = validate_archive(local_zip, date_str, strict=strict)
        logger.info("Archive holds %s member(s)", len(members))
        storage.upload_file(local_zip, target, fs=fs)

    logger.info("Bronze fetch complete: %s", target)
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        description="Fetch the daily SEPA archive into the Bronze layer."
    )
    parser.add_argument("--date", help="YYYY-MM-DD (defaults to yesterday)")
    parser.add_argument(
        "--type", dest="tipo", choices=list(config.DATASET_TYPES), default="minorista"
    )
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="Ingest the weekday archive even when its date does not match --date.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download even if the Bronze object already exists.",
    )
    parser.set_defaults(strict=True)
    return parser


def run_cli(argv=None):
    args = build_parser().parse_args(argv)
    target_date = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        fetch_prices(target_date, tipo=args.tipo, strict=args.strict, overwrite=args.overwrite)
    except Exception as exc:
        logger.error("Fetch failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
