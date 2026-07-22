"""Thin wrapper around the object store so the ETL never touches s3fs directly."""

import s3fs

from src import config
from src.logging_utils import get_logger

logger = get_logger(__name__)


def get_fs(**overrides):
    """Return an S3FileSystem pointed at MinIO (or any S3-compatible endpoint).

    ``use_listings_cache=False`` matters: the pipeline writes objects and reads
    them back within the same process, and a stale listing cache makes freshly
    written partitions invisible.
    """
    options = dict(config.STORAGE_OPTIONS)
    options.update(overrides)
    return s3fs.S3FileSystem(use_listings_cache=False, **options)


def strip_scheme(path):
    """``s3://bucket/key`` -> ``bucket/key`` (s3fs paths are scheme-less)."""
    return path[len("s3://") :] if path.startswith("s3://") else path


def with_scheme(path):
    """``bucket/key`` -> ``s3://bucket/key`` (pandas/pyarrow want the scheme)."""
    return path if path.startswith("s3://") else f"s3://{path}"


def ensure_bucket(fs=None, bucket=None):
    """Create the datalake bucket if it does not exist yet.

    Makes the pipeline runnable without the ``createbuckets`` compose service.
    """
    fs = fs or get_fs()
    bucket = bucket or config.S3_BUCKET
    try:
        if not fs.exists(bucket):
            fs.mkdir(bucket)
            logger.info("Created bucket %s", bucket)
    except FileExistsError:
        pass
    except Exception as exc:  # pragma: no cover - depends on remote state
        # A race with another task, or a policy that forbids CreateBucket while
        # still allowing writes. Not fatal on its own.
        logger.warning("Could not ensure bucket %s: %s", bucket, exc)
    return bucket


def upload_file(local_path, remote_path, fs=None):
    fs = fs or get_fs()
    target = strip_scheme(str(remote_path))
    parent = target.rsplit("/", 1)[0]
    try:
        fs.makedirs(parent, exist_ok=True)
    except Exception:
        # Object stores have no real directories; makedirs is best effort.
        pass
    fs.put(str(local_path), target)
    logger.info("Uploaded %s -> s3://%s", local_path, target)
    return with_scheme(target)


def download_file(remote_path, local_path, fs=None):
    fs = fs or get_fs()
    fs.get(strip_scheme(str(remote_path)), str(local_path))
    logger.info("Downloaded s3://%s -> %s", strip_scheme(str(remote_path)), local_path)
    return local_path


def exists(remote_path, fs=None):
    fs = fs or get_fs()
    return fs.exists(strip_scheme(str(remote_path)))


def remove_prefix(remote_path, fs=None):
    """Delete everything under a prefix; used to make writes idempotent.

    ``fs.rm(recursive=True)`` batches into a single ``DeleteObjects`` call, which
    MinIO rejects with ``MissingContentMD5`` because s3fs does not send that
    header. Falling back to one ``DeleteObject`` per key is slower but works
    against every S3 implementation -- and it has to work, or a re-run leaves
    the previous run's part files sitting beside the new ones.
    """
    fs = fs or get_fs()
    target = strip_scheme(str(remote_path))

    try:
        if not fs.exists(target):
            return True
    except Exception as exc:  # pragma: no cover - transient listing failure
        logger.warning("Could not inspect prefix %s: %s", target, exc)
        return False

    try:
        fs.rm(target, recursive=True)
        logger.info("Removed existing prefix s3://%s", target)
        return True
    except FileNotFoundError:
        return True
    except Exception as exc:
        logger.info("Bulk delete unavailable (%s); removing keys individually.", exc)

    try:
        keys = fs.find(target)
        for key in keys:
            fs.rm_file(key)
        fs.invalidate_cache(target)
        logger.info("Removed %s object(s) under s3://%s", len(keys), target)
        return True
    except Exception as exc:
        # The caller is about to write into this prefix, so a half-cleaned
        # partition is a correctness problem, not a cosmetic one.
        raise RuntimeError(f"Could not clean prefix {target}: {exc}") from exc


def prune_bronze(tipo, keep_days=None, fs=None):
    """Drop Bronze archives older than the retention window.

    Bronze keeps the publisher's ZIP untouched, which is right for
    reproducibility and costly for storage: ~300 MB per day per dataset type.
    Free object-storage tiers are measured in single-digit gigabytes, so an
    un-pruned Bronze fills one in under a fortnight.

    Silver is never pruned -- it is the derived history the dashboard reads, and
    it is an order of magnitude smaller.
    """
    keep_days = config.BRONZE_KEEP_DAYS if keep_days is None else keep_days
    if keep_days <= 0:
        return []

    fs = fs or get_fs()
    prefix = f"{config.BRONZE_PATH}/{tipo}"
    dates = list_partitions(prefix, fs=fs)
    stale = sorted(dates)[:-keep_days] if len(dates) > keep_days else []

    removed = []
    for date_str in stale:
        try:
            remove_prefix(f"{prefix}/fecha={date_str}", fs=fs)
            removed.append(date_str)
        except Exception as exc:  # pragma: no cover - housekeeping only
            logger.warning("Could not prune Bronze %s: %s", date_str, exc)

    if removed:
        logger.info("Pruned %s Bronze partition(s) for %s: %s", len(removed), tipo, removed)
    return removed


def list_partitions(remote_path, fs=None):
    """Return the ``fecha=YYYY-MM-DD`` partition dates found under a prefix."""
    fs = fs or get_fs()
    target = strip_scheme(str(remote_path))
    if not fs.exists(target):
        return []
    dates = []
    for entry in fs.ls(target, detail=False):
        name = entry.rstrip("/").rsplit("/", 1)[-1]
        if name.startswith("fecha="):
            dates.append(name.split("=", 1)[1])
    return sorted(dates)
