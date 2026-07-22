"""Tests for the Bronze fetch stage: retries, archive validation, path layout."""

import zipfile

import pytest
import requests

from src import config
from src import fetch_sepa_prices as F


def test_bronze_path_layout_is_hive_partitioned():
    path = F.bronze_archive_path("2026-07-21", "minorista")
    assert path == (
        f"{config.BRONZE_PATH}/minorista/fecha=2026-07-21/sepa_minorista_2026-07-21.zip"
    )


def test_archive_dates_reads_the_top_level_folder(minorista_archive):
    assert {str(d) for d in F.archive_dates(minorista_archive)} == {"2026-07-21"}


def test_validate_archive_accepts_a_matching_date(minorista_archive):
    members = F.validate_archive(minorista_archive, "2026-07-21")
    assert members


def test_validate_archive_rejects_a_mismatched_date(minorista_archive):
    """Guards against ingesting last week's snapshot under today's partition."""
    with pytest.raises(ValueError, match="dated"):
        F.validate_archive(minorista_archive, "2026-07-14")


def test_validate_archive_tolerates_mismatch_when_not_strict(minorista_archive):
    assert F.validate_archive(minorista_archive, "2026-07-14", strict=False)


def test_validate_archive_rejects_a_non_zip(tmp_path):
    path = tmp_path / "broken.zip"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(ValueError, match="not a valid ZIP"):
        F.validate_archive(path, "2026-07-21")


def test_validate_archive_rejects_an_empty_file(tmp_path):
    path = tmp_path / "empty.zip"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        F.validate_archive(path, "2026-07-21")


def test_validate_archive_rejects_an_empty_zip(tmp_path):
    path = tmp_path / "hollow.zip"
    with zipfile.ZipFile(path, "w"):
        pass
    with pytest.raises(ValueError, match="no entries"):
        F.validate_archive(path, "2026-07-21")


# ---------------------------------------------------------------------------
# Download retries
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, chunks=(b"payload",), error=None):
        self._chunks = chunks
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


def test_download_file_retries_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda _: None)
    session = _FakeSession(
        [
            _FakeResponse(error=requests.exceptions.ConnectionError("boom")),
            _FakeResponse(chunks=(b"hello",)),
        ]
    )
    dest = tmp_path / "out.bin"
    F.download_file("https://example.test/x.zip", dest, retries=3, session=session)

    assert dest.read_bytes() == b"hello"
    assert session.calls == 2


def test_download_file_raises_after_exhausting_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda _: None)
    session = _FakeSession(
        [_FakeResponse(error=requests.exceptions.ConnectionError("boom")) for _ in range(3)]
    )
    with pytest.raises(requests.exceptions.RequestException, match="Max retries"):
        F.download_file(
            "https://example.test/x.zip", tmp_path / "out.bin", retries=3, session=session
        )


def test_download_file_treats_an_empty_body_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda _: None)
    session = _FakeSession([_FakeResponse(chunks=()) for _ in range(2)])
    with pytest.raises(requests.exceptions.RequestException):
        F.download_file(
            "https://example.test/x.zip", tmp_path / "out.bin", retries=2, session=session
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_rejects_an_unknown_dataset_type():
    with pytest.raises(SystemExit):
        F.build_parser().parse_args(["--type", "nope"])


def test_cli_defaults():
    args = F.build_parser().parse_args([])
    assert args.tipo == "minorista"
    assert args.strict is True
    assert args.overwrite is False


def test_cli_returns_nonzero_on_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("portal down")

    monkeypatch.setattr(F, "fetch_prices", boom)
    assert F.run_cli(["--date", "2026-07-21"]) == 1
