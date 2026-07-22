"""Single place to configure logging for every entrypoint."""

import logging
import os
import sys

_CONFIGURED = False


def setup_logging(level=None):
    """Configure the root logger once, idempotently.

    Airflow already installs its own handlers, so we only touch the root logger
    when it has none -- otherwise every task log line would be duplicated.
    """
    global _CONFIGURED

    resolved = level or os.getenv("SEPA_LOG_LEVEL", "INFO")

    if not _CONFIGURED and not logging.getLogger().handlers:
        logging.basicConfig(
            level=resolved,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
        _CONFIGURED = True
    else:
        logging.getLogger().setLevel(resolved)

    # botocore/s3fs are extremely chatty at DEBUG.
    for noisy in ("botocore", "aiobotocore", "s3fs", "urllib3", "fsspec"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name):
    setup_logging()
    return logging.getLogger(name)
