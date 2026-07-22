"""Tests for the dashboard's query layer (no Streamlit, no S3)."""

import pandas as pd
import pytest

from src import data_access as da


def _products(rows):
    frame = pd.DataFrame(rows)
    frame["fecha"] = pd.to_datetime(frame["fecha"])
    if "marca" not in frame.columns:
        frame["marca"] = None
    if "cantidad_muestras" not in frame.columns:
        frame["cantidad_muestras"] = 1
    # Default the price band to the average so callers only spell out the
    # spread when the test is actually about dispersion.
    for column in ("precio_minimo", "precio_maximo"):
        if column not in frame.columns:
            frame[column] = frame["precio_promedio"]
        else:
            frame[column] = pd.to_numeric(frame[column]).fillna(frame["precio_promedio"])
    return frame


D1, D2 = pd.Timestamp("2026-07-20"), pd.Timestamp("2026-07-21")


BASIC = _products(
    [
        {
            "fecha": "2026-07-20",
            "id_producto": "a",
            "descripcion_producto": "LECHE 1L",
            "precio_promedio": 100.0,
            "cantidad_muestras": 10,
        },
        {
            "fecha": "2026-07-20",
            "id_producto": "b",
            "descripcion_producto": "ARROZ 1KG",
            "precio_promedio": 200.0,
            "cantidad_muestras": 8,
        },
        {
            "fecha": "2026-07-21",
            "id_producto": "a",
            "descripcion_producto": "LECHE 1L",
            "precio_promedio": 110.0,
            "cantidad_muestras": 10,
        },
        {
            "fecha": "2026-07-21",
            "id_producto": "b",
            "descripcion_producto": "ARROZ 1KG",
            "precio_promedio": 180.0,
            "cantidad_muestras": 8,
        },
    ]
)


# ---------------------------------------------------------------------------
# Collapsing / labels
# ---------------------------------------------------------------------------


def test_product_labels_picks_the_most_reported_description():
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "LECHE",
                "precio_promedio": 100.0,
                "cantidad_muestras": 2,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "LECHE ENTERA 1L",
                "precio_promedio": 100.0,
                "cantidad_muestras": 50,
            },
        ]
    )
    assert da.product_labels(frame)["a"] == "LECHE ENTERA 1L"


def test_collapse_weights_by_sample_count():
    """A price seen in 90 stores must not weigh the same as one seen in 10."""
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "X",
                "precio_promedio": 100.0,
                "cantidad_muestras": 90,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "X (bis)",
                "precio_promedio": 200.0,
                "cantidad_muestras": 10,
            },
        ]
    )
    out = da.collapse_to_product_day(frame)
    assert len(out) == 1
    assert out["precio_promedio"].iloc[0] == pytest.approx(110.0)
    assert out["cantidad_muestras"].iloc[0] == 100


# ---------------------------------------------------------------------------
# Movers
# ---------------------------------------------------------------------------


def test_price_movers_ranks_by_change():
    out = da.price_movers(BASIC, D1, D2, min_samples=1)
    assert out.iloc[0]["id_producto"] == "a"
    assert out.iloc[0]["variacion_pct"] == pytest.approx(10.0)
    assert out.iloc[-1]["variacion_pct"] == pytest.approx(-10.0)


def test_price_movers_returns_one_row_per_product():
    """Regression: joining on id without collapsing fans out duplicate rows."""
    frame = _products(
        [
            {
                "fecha": "2026-07-20",
                "id_producto": "a",
                "descripcion_producto": "LECHE",
                "precio_promedio": 100.0,
            },
            {
                "fecha": "2026-07-20",
                "id_producto": "a",
                "descripcion_producto": "LECHE ENTERA",
                "precio_promedio": 100.0,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "LECHE",
                "precio_promedio": 110.0,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "LECHE ENTERA",
                "precio_promedio": 110.0,
            },
        ]
    )
    out = da.price_movers(frame, D1, D2, min_samples=1)
    assert len(out) == 1
    assert out["variacion_pct"].iloc[0] == pytest.approx(10.0)


def test_price_movers_without_a_previous_day():
    assert da.price_movers(BASIC, None, D2).empty


def test_price_movers_respects_min_samples():
    assert da.price_movers(BASIC, D1, D2, min_samples=100).empty


# ---------------------------------------------------------------------------
# Savings
# ---------------------------------------------------------------------------


def test_savings_ranks_by_relative_spread():
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "WIDE",
                "precio_promedio": 150.0,
                "precio_minimo": 100.0,
                "precio_maximo": 200.0,
                "cantidad_muestras": 20,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "b",
                "descripcion_producto": "TIGHT",
                "precio_promedio": 100.0,
                "precio_minimo": 99.0,
                "precio_maximo": 101.0,
                "cantidad_muestras": 20,
            },
        ]
    )
    out = da.savings_opportunities(frame, D2, min_samples=1)
    assert out.iloc[0]["descripcion_producto"] == "WIDE"
    assert out.iloc[0]["ahorro_pct"] == pytest.approx(50.0)
    assert out.iloc[0]["ahorro_abs"] == pytest.approx(100.0)


def test_savings_drops_implausible_spreads():
    """A 20-to-1575 "spread" is the same barcode priced per unit and per dozen,
    not a 99% saving. Real store-to-store variation stays within a few x."""
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "FACTURAS",
                "precio_promedio": 500.0,
                "precio_minimo": 20.0,
                "precio_maximo": 1575.0,
                "cantidad_muestras": 20,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "b",
                "descripcion_producto": "SENTINEL",
                "precio_promedio": 500.0,
                "precio_minimo": 0.01,
                "precio_maximo": 1000.0,
                "cantidad_muestras": 20,
            },
        ]
    )
    assert da.savings_opportunities(frame, D2, min_samples=1).empty


def test_savings_keeps_a_plausible_spread_and_reports_the_ratio():
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "REAL",
                "precio_promedio": 150.0,
                "precio_minimo": 100.0,
                "precio_maximo": 250.0,
                "cantidad_muestras": 20,
            },
        ]
    )
    out = da.savings_opportunities(frame, D2, min_samples=1)
    assert len(out) == 1
    assert out.iloc[0]["ratio"] == pytest.approx(2.5)
    assert out.iloc[0]["ahorro_pct"] == pytest.approx(60.0)


def test_savings_requires_multi_store_coverage():
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "X",
                "precio_promedio": 150.0,
                "precio_minimo": 100.0,
                "precio_maximo": 200.0,
                "cantidad_muestras": 1,
            },
        ]
    )
    assert da.savings_opportunities(frame, D2, min_samples=5).empty


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_is_case_insensitive_and_literal():
    assert len(da.search_products(BASIC, "leche")) == 2
    # Regex metacharacters must be treated literally, not compiled.
    assert da.search_products(BASIC, "LECHE (").empty


def test_search_filters_by_brand():
    frame = BASIC.copy()
    frame["marca"] = ["ACME", "OTRA", "ACME", "OTRA"]
    assert len(da.search_products(frame, "LECHE", brand="ACME")) == 2
    assert da.search_products(frame, "LECHE", brand="OTRA").empty


# ---------------------------------------------------------------------------
# Basket
# ---------------------------------------------------------------------------


def test_basket_totals_respect_quantities():
    out = da.basket_series(BASIC, ["a", "b"], {"a": 2, "b": 1})
    assert len(out) == 2
    assert out.iloc[0]["costo_total"] == pytest.approx(2 * 100 + 200)
    assert out.iloc[1]["costo_total"] == pytest.approx(2 * 110 + 180)


def test_basket_defaults_to_one_of_each():
    out = da.basket_series(BASIC, ["a", "b"])
    assert out.iloc[0]["costo_total"] == pytest.approx(300.0)


def test_basket_drops_days_missing_an_item():
    """A basket whose membership changes day to day shows phantom inflation."""
    frame = _products(
        [
            {
                "fecha": "2026-07-20",
                "id_producto": "a",
                "descripcion_producto": "A",
                "precio_promedio": 100.0,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "A",
                "precio_promedio": 100.0,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "b",
                "descripcion_producto": "B",
                "precio_promedio": 900.0,
            },
        ]
    )
    out = da.basket_series(frame, ["a", "b"])
    assert len(out) == 1
    assert out["fecha"].iloc[0] == D2


def test_basket_empty_inputs():
    assert da.basket_series(BASIC, []).empty
    assert da.basket_series(pd.DataFrame(), ["a"]).empty


# ---------------------------------------------------------------------------
# Basket by province
# ---------------------------------------------------------------------------


PROVINCES = pd.DataFrame(
    {
        "fecha": pd.to_datetime(["2026-07-21"] * 5),
        "provincia": ["Córdoba", "Córdoba", "Santa Fe", "Santa Fe", "Salta"],
        "id_producto": ["a", "b", "a", "b", "a"],
        "precio_promedio": [100.0, 200.0, 120.0, 190.0, 90.0],
        "cantidad_muestras": [5, 5, 5, 5, 5],
    }
)


def test_basket_by_province_ranks_cheapest_first():
    out = da.basket_by_province(PROVINCES, ["a", "b"], D2)
    assert out.iloc[0]["provincia"] == "Córdoba"
    assert out.iloc[0]["costo_total"] == pytest.approx(300.0)
    assert out.iloc[1]["costo_total"] == pytest.approx(310.0)


def test_basket_by_province_excludes_incomplete_provinces():
    """Salta only carries one of the two items; ranking it would be wrong -- its
    partial basket is cheaper than everyone's full one."""
    out = da.basket_by_province(PROVINCES, ["a", "b"], D2)
    assert "Salta" not in out["provincia"].tolist()


def test_basket_by_province_can_relax_coverage():
    out = da.basket_by_province(PROVINCES, ["a", "b"], D2, min_coverage=0.5)
    assert "Salta" in out["provincia"].tolist()


def test_basket_by_province_applies_quantities():
    out = da.basket_by_province(PROVINCES, ["a", "b"], D2, {"a": 3})
    assert out.iloc[0]["costo_total"] == pytest.approx(3 * 100 + 200)


# ---------------------------------------------------------------------------
# Presets & coverage
# ---------------------------------------------------------------------------


def test_resolve_preset_picks_the_best_covered_match():
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "rare",
                "descripcion_producto": "LECHE RARA",
                "precio_promedio": 100.0,
                "cantidad_muestras": 1,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "common",
                "descripcion_producto": "LECHE ENTERA",
                "precio_promedio": 100.0,
                "cantidad_muestras": 500,
            },
        ]
    )
    assert da.resolve_preset(frame, ["LECHE"], D2) == ["common"]


def test_resolve_preset_prefers_a_prefix_match_over_a_popular_substring():
    """ "leche" must resolve to milk, not to a much more widely stocked
    "DULCE DE LECHE" — the staple is the one the description starts with."""
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "dulce",
                "descripcion_producto": "DULCE DE LECHE REPOSTERIA",
                "precio_promedio": 100.0,
                "cantidad_muestras": 900,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "milk",
                "descripcion_producto": "LECHE ENTERA SACHET 1L",
                "precio_promedio": 100.0,
                "cantidad_muestras": 40,
            },
        ]
    )
    assert da.resolve_preset(frame, ["LECHE"], D2) == ["milk"]


def test_resolve_preset_falls_back_to_substring_when_nothing_starts_with_it():
    frame = _products(
        [
            {
                "fecha": "2026-07-21",
                "id_producto": "bar",
                "descripcion_producto": "BARR ARROZ TRAD X3UD",
                "precio_promedio": 100.0,
                "cantidad_muestras": 10,
            },
        ]
    )
    assert da.resolve_preset(frame, ["ARROZ"], D2) == ["bar"]


def test_resolve_preset_skips_terms_with_no_match():
    assert da.resolve_preset(BASIC, ["LECHE", "CAVIAR"], D2) == ["a"]


def test_resolve_preset_does_not_duplicate_ids():
    ids = da.resolve_preset(BASIC, ["LECHE", "LECHE 1L"], D2)
    assert ids == ["a"]


def test_preset_baskets_are_well_formed():
    assert da.PRESET_BASKETS
    for name, terms in da.PRESET_BASKETS.items():
        assert isinstance(name, str) and terms
        assert all(isinstance(t, str) and t.strip() for t in terms)


def test_coverage_calendar_flags_gaps():
    out = da.coverage_calendar([D1, D2], D1, pd.Timestamp("2026-07-23"))
    assert len(out) == 4
    assert out["presente"].tolist() == [True, True, False, False]


def test_coverage_calendar_without_bounds():
    assert da.coverage_calendar([], None, None).empty


def test_date_bounds_across_tables():
    lo, hi = da.date_bounds([BASIC, pd.DataFrame()])
    assert lo == D1 and hi == D2
    assert da.date_bounds([pd.DataFrame()]) == (None, None)


def test_preset_basket_names_are_in_spanish():
    """The monitor's audience is Argentine; preset names are user-facing."""
    assert "Canasta básica (10 productos)" in da.PRESET_BASKETS
    assert set(da.PRESET_BASKETS) == {
        "Canasta básica (10 productos)",
        "Desayuno",
        "Limpieza",
        "Bebidas",
    }
