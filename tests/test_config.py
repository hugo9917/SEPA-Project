"""Guards on configuration invariants that the rest of the pipeline assumes."""

import importlib

from src import config


def test_chunk_size_exists():
    """Its absence made the whole transform stage raise AttributeError."""
    assert isinstance(config.CHUNK_SIZE, int)
    assert config.CHUNK_SIZE > 0


def test_weekday_slugs_align_with_python_weekday():
    assert len(config.WEEKDAY_SLUGS) == 7
    assert config.WEEKDAY_SLUGS[0] == "lunes"
    assert config.WEEKDAY_SLUGS[6] == "domingo"


def test_dataset_types_have_ckan_ids_and_pages():
    for tipo in config.DATASET_TYPES:
        assert config.CKAN_DATASET_IDS[tipo]
        assert config.DATASET_PAGES[tipo].startswith("http")


def test_csv_encoding_handles_the_bom():
    assert config.CSV_ENCODING == "utf-8-sig"


def test_price_band_is_sane():
    assert 0 < config.MIN_VALID_PRICE < config.MAX_VALID_PRICE


def test_province_codes_cover_all_24_jurisdictions():
    assert len(config.PROVINCIA_CODES) == 24
    assert config.PROVINCIA_CODES["AR-C"].startswith("Ciudad")


def test_env_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("SEPA_CHUNK_SIZE", "1234")
    monkeypatch.setenv("S3_BUCKET", "my-bucket")
    monkeypatch.setenv("SEPA_ENABLE_CATEGORY_FILTER", "true")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.CHUNK_SIZE == 1234
        assert reloaded.S3_BUCKET == "my-bucket"
        assert reloaded.BRONZE_PATH == "s3://my-bucket/bronze"
        assert reloaded.ENABLE_CATEGORY_FILTER is True
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_bad_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("SEPA_CHUNK_SIZE", "not-a-number")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.CHUNK_SIZE == 250_000
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_storage_options_target_the_configured_endpoint():
    assert config.STORAGE_OPTIONS["client_kwargs"]["endpoint_url"] == config.S3_ENDPOINT_URL
