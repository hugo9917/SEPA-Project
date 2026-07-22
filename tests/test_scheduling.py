"""Tests for the daily schedule's self-healing catch-up."""

from datetime import date

import pytest

from src import config
from src import fetch_sepa_range as R


@pytest.fixture
def portal(monkeypatch):
    """Pretend the portal publishes a rolling window of dates."""
    published = [date(2026, 7, 21), date(2026, 7, 20), date(2026, 7, 19)]
    monkeypatch.setattr(R.sepa_source, "available_dates", lambda tipo: published)
    return published


@pytest.fixture
def lake(monkeypatch):
    """Track which dates Silver already holds and which get ingested."""
    state = {"have": [], "ingested": [], "fail": set()}
    monkeypatch.setattr(R.storage, "list_partitions", lambda path: state["have"])

    def _process(date_str, tipo, **kwargs):
        if date_str in state["fail"]:
            raise RuntimeError("boom")
        state["ingested"].append(date_str)

    monkeypatch.setattr(R, "process_date", _process)
    return state


def test_catch_up_ingests_only_what_is_missing(portal, lake):
    lake["have"] = ["2026-07-21"]
    report = R.ensure_recent("minorista", delay=0)
    assert report["ingested"] == ["2026-07-19", "2026-07-20"]
    assert "2026-07-21" not in report["ingested"]


def test_catch_up_is_a_noop_when_the_lake_is_current(portal, lake):
    lake["have"] = ["2026-07-19", "2026-07-20", "2026-07-21"]
    report = R.ensure_recent("minorista", delay=0)
    assert report == {"missing": [], "ingested": [], "failed": []}
    assert lake["ingested"] == []


def test_a_fresh_lake_pulls_the_whole_published_window(portal, lake):
    """First boot should not settle for yesterday alone."""
    report = R.ensure_recent("minorista", delay=0)
    assert report["ingested"] == ["2026-07-19", "2026-07-20", "2026-07-21"]


def test_catch_up_ingests_oldest_first(portal, lake):
    """The oldest gap is the one closest to falling out of the 7-day window."""
    R.ensure_recent("minorista", delay=0)
    assert lake["ingested"] == sorted(lake["ingested"])


def test_one_bad_date_does_not_stop_the_others(portal, lake):
    lake["fail"] = {"2026-07-20"}
    report = R.ensure_recent("minorista", delay=0)
    assert report["ingested"] == ["2026-07-19", "2026-07-21"]
    assert [f["date"] for f in report["failed"]] == ["2026-07-20"]


def test_max_days_narrows_the_window(portal, lake):
    """max_days=1 turns the job into a strict "latest day only" ingest."""
    report = R.ensure_recent("minorista", max_days=1, delay=0)
    assert report["ingested"] == ["2026-07-21"]


def test_max_days_defaults_to_the_configured_window(portal, lake, monkeypatch):
    monkeypatch.setattr(config, "BACKFILL_MAX_DAYS", 2)
    report = R.ensure_recent("minorista", delay=0)
    # available_dates comes back newest first, so 2 days == the two newest.
    assert report["ingested"] == ["2026-07-20", "2026-07-21"]


def test_an_unreachable_portal_is_reported_not_raised(monkeypatch, lake):
    """The catch-up task must not mask a portal outage as a pipeline crash."""

    def boom(tipo):
        raise ConnectionError("portal down")

    monkeypatch.setattr(R.sepa_source, "available_dates", boom)
    report = R.ensure_recent("minorista", delay=0)
    assert report["ingested"] == [] and "error" in report


def test_backfill_window_is_bounded_by_source_retention():
    """Looking further back than the portal keeps cannot find anything."""
    assert config.BACKFILL_MAX_DAYS <= config.SOURCE_RETENTION_DAYS


# ---------------------------------------------------------------------------
# Bronze retention
# ---------------------------------------------------------------------------


@pytest.fixture
def bronze(monkeypatch):
    """A fake Bronze prefix with a controllable set of partitions."""
    from src import storage

    state = {"dates": [], "removed": []}
    monkeypatch.setattr(storage, "list_partitions", lambda path, fs=None: state["dates"])
    monkeypatch.setattr(storage, "get_fs", lambda **kw: object())

    def _remove(path, fs=None):
        state["removed"].append(path.rsplit("fecha=", 1)[-1])
        return True

    monkeypatch.setattr(storage, "remove_prefix", _remove)
    return state


def test_prune_keeps_the_newest_days(bronze):
    from src import storage

    bronze["dates"] = ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"]
    removed = storage.prune_bronze("minorista", keep_days=2)
    assert removed == ["2026-07-14", "2026-07-15"]
    assert bronze["removed"] == ["2026-07-14", "2026-07-15"]


def test_prune_is_a_noop_below_the_threshold(bronze):
    from src import storage

    bronze["dates"] = ["2026-07-16", "2026-07-17"]
    assert storage.prune_bronze("minorista", keep_days=7) == []


def test_prune_disabled_keeps_everything(bronze):
    """Bronze is the reproducibility layer; deleting it must be opt-in."""
    from src import storage

    bronze["dates"] = ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert storage.prune_bronze("minorista", keep_days=0) == []
    assert bronze["removed"] == []


# ---------------------------------------------------------------------------
# The scheduler-agnostic daily entrypoint
# ---------------------------------------------------------------------------


def test_daily_entrypoint_imports_no_airflow():
    """The daily job has to run under cron, GitHub Actions or Airflow alike, so
    it must not depend on any of them. Checks the import graph, not the prose."""
    import ast
    import inspect

    from src import daily

    tree = ast.parse(inspect.getsource(daily))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "airflow" not in imported


def test_daily_reports_failure_when_catch_up_fails(monkeypatch):
    from src import daily

    monkeypatch.setattr(daily.storage, "ensure_bucket", lambda: None)
    monkeypatch.setattr(
        daily, "ensure_recent", lambda tipo, max_days=None: {"failed": [{"date": "x"}]}
    )
    monkeypatch.setattr(daily, "generate_gold_layer", lambda tipo: "s3://gold")
    report = daily.run_daily(tipos=["minorista"])
    assert report["ok"] is False and report["failed_tipos"] == ["minorista"]


def test_daily_treats_an_empty_lake_as_normal(monkeypatch):
    """A first run with nothing ingested yet is not a crash."""
    from src import daily

    monkeypatch.setattr(daily.storage, "ensure_bucket", lambda: None)
    monkeypatch.setattr(daily, "ensure_recent", lambda tipo, max_days=None: {"ingested": []})

    def _no_silver(tipo):
        raise daily.NoSilverData("empty")

    monkeypatch.setattr(daily, "generate_gold_layer", _no_silver)
    report = daily.run_daily(tipos=["minorista"])
    assert report["ok"] is True and report["tipos"]["minorista"]["gold"] is None


def test_daily_cli_defaults_to_every_dataset_type():
    from src import daily

    args = daily.build_parser().parse_args([])
    assert args.tipos is None and args.prune is False


def test_an_unwritable_report_path_does_not_fail_a_good_run(monkeypatch, tmp_path):
    """Reporting is observability, not the job."""
    from src import daily

    monkeypatch.setattr(daily.storage, "ensure_bucket", lambda: None)
    monkeypatch.setattr(daily, "ensure_recent", lambda tipo, max_days=None: {"ingested": []})
    monkeypatch.setattr(daily, "generate_gold_layer", lambda tipo: "s3://gold")
    bad = str(tmp_path / "no-such-dir" / "r.json")
    assert daily.run_cli(["--type", "minorista", "--report", bad]) == 0
