"""Locating the SEPA ZIP for a given date on the open-data portal.

How the source actually works
----------------------------
``datos.produccion.gob.ar`` does **not** publish one resource per calendar date.
It publishes exactly seven ZIP resources per dataset, named after the days of
the week (``sepa_lunes.zip`` ... ``sepa_domingo.zip``). Each one is overwritten
in place with the latest snapshot for that weekday, so the portal always holds a
rolling 7-day window.

Consequences that drive the design here:

* To fetch date ``D`` you pick the resource whose weekday matches ``D`` and then
  confirm the resource's ``last_modified`` really is ``D``. Otherwise you would
  silently ingest data from a week ago under today's partition.
* Backfilling further back than :data:`config.SOURCE_RETENTION_DAYS` days is
  impossible from this endpoint, no matter how the request is phrased.

The original implementation scraped the HTML page looking for a date string next
to a "DESCARGAR" link. The page renders weekday names, not dates, so that search
was matching unrelated text. The CKAN API is used as the primary strategy and
the scrape is kept only as a degraded fallback.
"""

import re
from datetime import UTC, datetime, timedelta

import requests

from src import config
from src.logging_utils import get_logger

logger = get_logger(__name__)

_ZIP_SLUG_RE = re.compile(r"sepa_([a-z]+)\.zip$", re.IGNORECASE)


class ResourceNotFound(Exception):
    """No downloadable ZIP matches the requested date."""


def parse_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def _today():
    """Indirection so tests can pin "now" when checking the retention window."""
    return datetime.now().date()


def weekday_slug(date):
    """``date`` -> ``'lunes'`` ... ``'domingo'``."""
    return config.WEEKDAY_SLUGS[date.weekday()]


def dataset_id(tipo):
    try:
        return config.CKAN_DATASET_IDS[tipo]
    except KeyError as exc:
        raise ValueError(
            f"Unknown dataset type {tipo!r}; expected one of {list(config.CKAN_DATASET_IDS)}"
        ) from exc


def get_target_url(tipo):
    """Landing page for a dataset type (kept for the HTML fallback)."""
    try:
        return config.DATASET_PAGES[tipo]
    except KeyError as exc:
        raise ValueError(f"Unknown type: {tipo}") from exc


def _session():
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def _parse_ckan_timestamp(value):
    """CKAN emits naive ISO-8601 timestamps such as ``2026-07-21T16:18:26.185``."""
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def fetch_package(tipo, session=None):
    """Return the raw CKAN ``package_show`` payload for a dataset type."""
    session = session or _session()
    url = f"{config.CKAN_BASE_URL}/api/3/action/package_show"
    response = session.get(url, params={"id": dataset_id(tipo)}, timeout=config.HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise ResourceNotFound(f"CKAN reported failure for dataset {dataset_id(tipo)}")
    return payload["result"]


def list_zip_resources(package):
    """Normalise the ZIP resources of a package into ``{slug, url, last_modified}``."""
    resources = []
    for raw in package.get("resources", []):
        url = raw.get("url") or ""
        match = _ZIP_SLUG_RE.search(url)
        if not match:
            continue
        slug = match.group(1).lower()
        if slug not in config.WEEKDAY_SLUGS:
            continue
        # ``last_modified`` is the reliable field; ``created`` only records when
        # the resource slot was first added back in 2024.
        modified = _parse_ckan_timestamp(raw.get("last_modified") or raw.get("created"))
        resources.append(
            {
                "slug": slug,
                "url": url,
                "last_modified": modified,
                # Resource names come through with broken accents ("Mi�rcoles"),
                # so the URL slug is what we match on.
                "name": raw.get("name"),
            }
        )
    return resources


def available_dates(tipo, session=None):
    """Dates currently retrievable from the portal, newest first."""
    package = fetch_package(tipo, session=session)
    dates = [
        r["last_modified"].date()
        for r in list_zip_resources(package)
        if r["last_modified"] is not None
    ]
    return sorted(set(dates), reverse=True)


def find_resource_for_date(date_str, tipo, session=None, strict=True):
    """Resolve the download URL for ``date_str``.

    Parameters
    ----------
    strict:
        When True (default) the resource's ``last_modified`` date must equal the
        requested date. Set False to accept the weekday slot regardless, which
        is only sensible for exploratory runs.
    """
    target = parse_date(date_str)
    slug = weekday_slug(target)

    package = fetch_package(tipo, session=session)
    resources = list_zip_resources(package)

    if not resources:
        raise ResourceNotFound(f"Dataset {dataset_id(tipo)} exposes no weekday ZIP resources.")

    matches = [r for r in resources if r["slug"] == slug]
    if not matches:
        raise ResourceNotFound(
            f"No '{slug}' resource in dataset {dataset_id(tipo)}. "
            f"Available: {sorted(r['slug'] for r in resources)}"
        )

    resource = matches[0]
    published = resource["last_modified"].date() if resource["last_modified"] else None

    if published == target:
        logger.info(
            "Resolved %s (%s) -> %s [published %s]", date_str, slug, resource["url"], published
        )
        return resource["url"]

    horizon = _today() - timedelta(days=config.SOURCE_RETENTION_DAYS)
    if target < horizon:
        raise ResourceNotFound(
            f"{date_str} is older than the portal's {config.SOURCE_RETENTION_DAYS}-day "
            f"retention window. The '{slug}' slot now holds {published}. "
            "Historical SEPA data has to come from your own Bronze archive."
        )

    if strict:
        raise ResourceNotFound(
            f"The '{slug}' resource currently holds {published}, not {date_str}. "
            "Re-run once the portal publishes, or pass strict=False to ingest anyway."
        )

    logger.warning(
        "Date mismatch tolerated: '%s' slot holds %s but %s was requested.",
        slug,
        published,
        date_str,
    )
    return resource["url"]


# ---------------------------------------------------------------------------
# Fallback: HTML scraping
# ---------------------------------------------------------------------------


def find_resource_url_from_html(html_content, date_str, tipo=None):
    """Best-effort scrape used only when the CKAN API is unreachable.

    Matches ``sepa_<weekday>.zip`` hrefs, which is what the page actually links
    to -- searching for the literal date string never worked.
    """
    from bs4 import BeautifulSoup

    slug = weekday_slug(parse_date(date_str))
    soup = BeautifulSoup(html_content, "html.parser")

    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = _ZIP_SLUG_RE.search(href)
        if match and match.group(1).lower() == slug:
            if href.startswith("/"):
                href = f"{config.CKAN_BASE_URL}{href}"
            logger.info("HTML fallback resolved %s -> %s", date_str, href)
            return href
    return None


# Backwards-compatible alias for the original function name.
find_resource_url = find_resource_url_from_html


def resolve_download_url(date_str, tipo, session=None, strict=True):
    """CKAN first, HTML scrape second."""
    session = session or _session()
    try:
        return find_resource_for_date(date_str, tipo, session=session, strict=strict)
    except ResourceNotFound:
        raise
    except Exception as exc:
        logger.warning("CKAN lookup failed (%s); falling back to HTML scrape.", exc)

    response = session.get(get_target_url(tipo), timeout=config.HTTP_TIMEOUT)
    response.raise_for_status()
    url = find_resource_url_from_html(response.text, date_str, tipo)
    if not url:
        raise ResourceNotFound(f"No download link found for {date_str} ({tipo}).")
    return url
