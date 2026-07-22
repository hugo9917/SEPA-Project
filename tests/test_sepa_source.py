"""Tests for resource discovery -- the layer that was silently returning nothing."""

from datetime import date

import pytest

from src import config, sepa_source


def test_weekday_slug_matches_calendar():
    # 2026-07-21 is a Tuesday.
    assert sepa_source.weekday_slug(date(2026, 7, 21)) == "martes"
    assert sepa_source.weekday_slug(date(2026, 7, 20)) == "lunes"
    assert sepa_source.weekday_slug(date(2026, 7, 19)) == "domingo"


def test_dataset_id_rejects_unknown_type():
    with pytest.raises(ValueError):
        sepa_source.dataset_id("mayoristaX")


def test_list_zip_resources_keeps_only_weekday_zips(ckan_payload):
    resources = sepa_source.list_zip_resources(ckan_payload["result"])
    slugs = {r["slug"] for r in resources}
    assert slugs == {"martes", "miercoles"}
    # PDFs and XLSX helper files must not be treated as data archives.
    assert all(r["url"].endswith(".zip") for r in resources)


def test_find_resource_for_date_matches_on_last_modified(monkeypatch, ckan_payload):
    monkeypatch.setattr(
        sepa_source, "fetch_package", lambda tipo, session=None: ckan_payload["result"]
    )
    url = sepa_source.find_resource_for_date("2026-07-21", "minorista")
    assert url.endswith("sepa_martes.zip")


@pytest.fixture
def pinned_source(monkeypatch, ckan_payload):
    """Freeze both the CKAN response and "today" so these cases are deterministic."""
    monkeypatch.setattr(
        sepa_source, "fetch_package", lambda tipo, session=None: ckan_payload["result"]
    )
    monkeypatch.setattr(sepa_source, "_today", lambda: date(2026, 7, 23))
    return sepa_source


def test_find_resource_for_date_rejects_stale_slot(pinned_source):
    """The 'miercoles' slot holds 07-15, so asking for 07-22 must not silently
    return week-old data under the wrong partition."""
    with pytest.raises(sepa_source.ResourceNotFound, match="currently holds"):
        pinned_source.find_resource_for_date("2026-07-22", "minorista")


def test_find_resource_for_date_explains_retention_window(pinned_source):
    # 2026-04-22 is a Wednesday, so the slot exists but is far out of range.
    with pytest.raises(sepa_source.ResourceNotFound, match="retention window"):
        pinned_source.find_resource_for_date("2026-04-22", "minorista")


def test_non_strict_tolerates_date_mismatch(pinned_source):
    url = pinned_source.find_resource_for_date("2026-07-22", "minorista", strict=False)
    assert url.endswith("sepa_miercoles.zip")


def test_missing_weekday_slot_is_reported(pinned_source):
    # 2026-07-23 is a Thursday and the trimmed fixture has no 'jueves' resource.
    with pytest.raises(sepa_source.ResourceNotFound, match="No 'jueves' resource"):
        pinned_source.find_resource_for_date("2026-07-23", "minorista")


def test_html_fallback_matches_weekday_slug():
    html = """
    <html><body>
      <a href="/download/anexo.pdf">Metadata</a>
      <a href="/dataset/x/resource/y/download/sepa_martes.zip">DESCARGAR</a>
      <a href="/dataset/x/resource/z/download/sepa_lunes.zip">DESCARGAR</a>
    </body></html>
    """
    url = sepa_source.find_resource_url_from_html(html, "2026-07-21")
    assert url == f"{config.CKAN_BASE_URL}/dataset/x/resource/y/download/sepa_martes.zip"


def test_html_fallback_returns_none_when_absent():
    assert sepa_source.find_resource_url_from_html("<html></html>", "2026-07-21") is None


def test_parse_ckan_timestamp_handles_naive_and_aware():
    naive = sepa_source._parse_ckan_timestamp("2026-07-21T16:18:26.185704")
    aware = sepa_source._parse_ckan_timestamp("2026-07-21T16:18:26Z")
    assert naive.date() == date(2026, 7, 21)
    assert aware.date() == date(2026, 7, 21)
    assert sepa_source._parse_ckan_timestamp("") is None
    assert sepa_source._parse_ckan_timestamp("not-a-date") is None
