"""Tests for the Gold aggregation logic (pure functions, no S3 needed)."""

import pandas as pd
import pytest

from src import generate_gold as G


def _frame(date_str, rows):
    return pd.DataFrame(rows).assign(fecha=date_str)


def test_partial_sums_are_foldable():
    """Folding two partials must equal aggregating the whole frame at once."""
    rows = [
        {"id_producto": "a", "descripcion_producto": "A", "marca": "M", "precio_lista": 10.0},
        {"id_producto": "a", "descripcion_producto": "A", "marca": "M", "precio_lista": 20.0},
        {"id_producto": "b", "descripcion_producto": "B", "marca": "M", "precio_lista": 5.0},
    ]
    whole = _frame("2026-07-21", rows)

    one_shot = G._combine([G._partial_sums(whole, G.PRODUCT_KEYS)], G.PRODUCT_KEYS)
    folded = G._combine(
        [
            G._partial_sums(_frame("2026-07-21", rows[:2]), G.PRODUCT_KEYS),
            G._partial_sums(_frame("2026-07-21", rows[2:]), G.PRODUCT_KEYS),
        ],
        G.PRODUCT_KEYS,
    )

    key = ["fecha", "id_producto"]
    left = one_shot.sort_values(key).reset_index(drop=True)
    right = folded.sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)

    product_a = left[left["id_producto"] == "a"].iloc[0]
    assert product_a["precio_promedio"] == pytest.approx(15.0)
    assert product_a["minimo"] == 10.0
    assert product_a["maximo"] == 20.0
    assert product_a["muestras"] == 2


def test_build_daily_product_prices_renames_and_types():
    partial = G._partial_sums(
        _frame(
            "2026-07-21",
            [{"id_producto": "a", "descripcion_producto": "A", "marca": "M", "precio_lista": 10.0}],
        ),
        G.PRODUCT_KEYS,
    )
    out = G.build_daily_product_prices([partial])
    assert {"precio_minimo", "precio_maximo", "cantidad_muestras", "precio_promedio"} <= set(
        out.columns
    )
    assert pd.api.types.is_datetime64_any_dtype(out["fecha"])


def test_build_store_stats_shape():
    partial = G._partial_sums(
        _frame(
            "2026-07-21",
            [
                {
                    "id_comercio": "4",
                    "nombre_comercio": "C",
                    "nombre_sucursal": "S",
                    "provincia": "P",
                    "precio_lista": 10.0,
                },
                {
                    "id_comercio": "4",
                    "nombre_comercio": "C",
                    "nombre_sucursal": "S",
                    "provincia": "P",
                    "precio_lista": 30.0,
                },
            ],
        ),
        G.STORE_GROUP_KEYS,
    )
    out = G.build_store_stats([partial])
    assert len(out) == 1
    assert out["precio_promedio_general"].iloc[0] == pytest.approx(20.0)
    assert out["productos_reportados"].iloc[0] == 2


def test_build_province_product_prices_splits_by_province():
    partial = G._partial_sums(
        _frame(
            "2026-07-21",
            [
                {"provincia": "Córdoba", "id_producto": "a", "precio_lista": 100.0},
                {"provincia": "Córdoba", "id_producto": "a", "precio_lista": 120.0},
                {"provincia": "Salta", "id_producto": "a", "precio_lista": 200.0},
            ],
        ),
        G.PROVINCE_PRODUCT_KEYS,
    )
    out = G.build_province_product_prices([partial])
    assert len(out) == 2
    cordoba = out[out["provincia"] == "Córdoba"].iloc[0]
    assert cordoba["precio_promedio"] == pytest.approx(110.0)
    assert cordoba["precio_minimo"] == 100.0
    assert cordoba["precio_maximo"] == 120.0
    assert cordoba["cantidad_muestras"] == 2


def test_province_grain_excludes_the_description():
    """Keeping the description out of the key is what stops this table from
    multiplying when one product id carries several spellings."""
    assert "descripcion_producto" not in G.PROVINCE_PRODUCT_KEYS
    partial = G._partial_sums(
        _frame(
            "2026-07-21",
            [
                {
                    "provincia": "Salta",
                    "id_producto": "a",
                    "descripcion_producto": "LECHE",
                    "precio_lista": 100.0,
                },
                {
                    "provincia": "Salta",
                    "id_producto": "a",
                    "descripcion_producto": "LECHE ENTERA",
                    "precio_lista": 100.0,
                },
            ],
        ),
        G.PROVINCE_PRODUCT_KEYS,
    )
    assert len(G.build_province_product_prices([partial])) == 1


def test_build_province_product_prices_drops_null_provinces():
    partial = G._partial_sums(
        _frame(
            "2026-07-21",
            [
                {"provincia": None, "id_producto": "a", "precio_lista": 100.0},
                {"provincia": "Salta", "id_producto": "a", "precio_lista": 200.0},
            ],
        ),
        G.PROVINCE_PRODUCT_KEYS,
    )
    out = G.build_province_product_prices([partial])
    assert out["provincia"].tolist() == ["Salta"]


def _daily_products(records):
    frame = pd.DataFrame(records)
    frame["fecha"] = pd.to_datetime(frame["fecha"])
    frame["cantidad_muestras"] = frame.get("cantidad_muestras", 1)
    return frame


def test_matched_index_ignores_basket_composition_changes():
    """A newly-listed expensive product must not register as inflation."""
    daily = _daily_products(
        [
            {"fecha": "2026-07-20", "id_producto": "a", "precio_promedio": 100.0},
            {"fecha": "2026-07-20", "id_producto": "b", "precio_promedio": 100.0},
            # Day 2: same two products unchanged, plus an expensive newcomer.
            {"fecha": "2026-07-21", "id_producto": "a", "precio_promedio": 100.0},
            {"fecha": "2026-07-21", "id_producto": "b", "precio_promedio": 100.0},
            {"fecha": "2026-07-21", "id_producto": "c", "precio_promedio": 10_000.0},
        ]
    )
    out = G.build_inflation_index(daily)

    # Naive average is wildly distorted by the newcomer...
    assert out["indice_precio_global"].iloc[1] > out["indice_precio_global"].iloc[0] * 30
    # ...while the matched index correctly reports no change.
    assert out["variacion_matched_pct"].iloc[1] == pytest.approx(0.0)
    assert out["indice_matched_base100"].iloc[1] == pytest.approx(100.0)


def test_matched_index_tracks_a_real_price_rise():
    daily = _daily_products(
        [
            {"fecha": "2026-07-20", "id_producto": "a", "precio_promedio": 100.0},
            {"fecha": "2026-07-20", "id_producto": "b", "precio_promedio": 200.0},
            {"fecha": "2026-07-21", "id_producto": "a", "precio_promedio": 110.0},
            {"fecha": "2026-07-21", "id_producto": "b", "precio_promedio": 220.0},
        ]
    )
    out = G.build_inflation_index(daily)
    assert out["variacion_matched_pct"].iloc[1] == pytest.approx(10.0)
    assert out["indice_matched_base100"].iloc[1] == pytest.approx(110.0)


def test_repricing_share_distinguishes_a_sticky_day_from_a_flat_one():
    """Three of four products hold their price, one jumps 50%. The median
    relative is 1.0 (correctly "no typical change"), but the repricing share
    must still show that a quarter of the basket moved."""
    daily = _daily_products(
        [
            {"fecha": "2026-07-20", "id_producto": "a", "precio_promedio": 100.0},
            {"fecha": "2026-07-20", "id_producto": "b", "precio_promedio": 100.0},
            {"fecha": "2026-07-20", "id_producto": "c", "precio_promedio": 100.0},
            {"fecha": "2026-07-20", "id_producto": "d", "precio_promedio": 100.0},
            {"fecha": "2026-07-21", "id_producto": "a", "precio_promedio": 100.0},
            {"fecha": "2026-07-21", "id_producto": "b", "precio_promedio": 100.0},
            {"fecha": "2026-07-21", "id_producto": "c", "precio_promedio": 100.0},
            {"fecha": "2026-07-21", "id_producto": "d", "precio_promedio": 150.0},
        ]
    )
    out = G.build_inflation_index(daily)
    # The median sees nothing here -- that degeneracy is why it is no longer the
    # headline, and it is kept only as a reference column.
    assert out["variacion_mediana_pct"].iloc[1] == pytest.approx(0.0)
    # The trimmed mean registers the move.
    assert out["variacion_matched_pct"].iloc[1] == pytest.approx(12.5)
    assert out["pct_productos_con_cambio"].iloc[1] == pytest.approx(25.0)
    assert out["productos_comparables"].iloc[1] == 4


def test_inflation_index_handles_a_single_day():
    daily = _daily_products([{"fecha": "2026-07-21", "id_producto": "a", "precio_promedio": 100.0}])
    out = G.build_inflation_index(daily)
    assert len(out) == 1
    assert out["indice_matched_base100"].iloc[0] == pytest.approx(100.0)
    assert out["indice_global_base100"].iloc[0] == pytest.approx(100.0)
    assert pd.isna(out["variacion_diaria_pct"].iloc[0])


def test_matched_index_handles_one_id_with_several_descriptions():
    """The product grain includes the description, so a single id can appear on
    multiple rows per day. Joining on id without collapsing first fans the
    comparison out and skews the median."""
    daily = _daily_products(
        [
            {
                "fecha": "2026-07-20",
                "id_producto": "a",
                "descripcion_producto": "LECHE 1L",
                "precio_promedio": 100.0,
            },
            {
                "fecha": "2026-07-20",
                "id_producto": "a",
                "descripcion_producto": "LECHE ENTERA 1L",
                "precio_promedio": 100.0,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "LECHE 1L",
                "precio_promedio": 110.0,
            },
            {
                "fecha": "2026-07-21",
                "id_producto": "a",
                "descripcion_producto": "LECHE ENTERA 1L",
                "precio_promedio": 110.0,
            },
        ]
    )
    out = G.build_inflation_index(daily)
    assert len(out) == 2
    assert out["variacion_matched_pct"].iloc[1] == pytest.approx(10.0)


def test_inflation_index_survives_no_overlapping_products():
    daily = _daily_products(
        [
            {"fecha": "2026-07-20", "id_producto": "a", "precio_promedio": 100.0},
            {"fecha": "2026-07-21", "id_producto": "z", "precio_promedio": 300.0},
        ]
    )
    out = G.build_inflation_index(daily)
    assert out["variacion_matched_pct"].iloc[1] == pytest.approx(0.0)


def test_collapse_reduces_partials_without_changing_the_result():
    """Collapsing at a partition boundary must be a pure memory optimisation:
    the aggregation is associative, so folding early cannot move a number."""
    rows = [
        {"id_producto": "a", "descripcion_producto": "A", "marca": "M", "precio_lista": 10.0},
        {"id_producto": "a", "descripcion_producto": "A", "marca": "M", "precio_lista": 20.0},
        {"id_producto": "b", "descripcion_producto": "B", "marca": "M", "precio_lista": 5.0},
    ]
    partials = [G._partial_sums(_frame("2026-07-21", [r]), G.PRODUCT_KEYS) for r in rows]

    straight = G._combine(list(partials), G.PRODUCT_KEYS)

    acc = {}
    for p in partials:
        G._fold(acc, "2026-07-21", p)
    G._collapse(acc, "2026-07-21", G.PRODUCT_KEYS)

    assert len(acc["2026-07-21"]) == 1  # three partials became one
    collapsed = G._combine(acc["2026-07-21"], G.PRODUCT_KEYS)

    key = ["fecha", "id_producto"]
    pd.testing.assert_frame_equal(
        straight.sort_values(key).reset_index(drop=True),
        collapsed.sort_values(key).reset_index(drop=True),
    )


def test_collapse_is_a_noop_for_a_single_partial():
    acc = {"d": ["only"]}
    G._collapse(acc, "d", G.PRODUCT_KEYS)
    assert acc["d"] == ["only"]


def test_collapse_tolerates_an_absent_key():
    acc = {}
    G._collapse(acc, "missing", G.PRODUCT_KEYS)
    assert acc == {}


# ---------------------------------------------------------------------------
# Trimmed-mean index
# ---------------------------------------------------------------------------


def test_median_is_degenerate_on_sticky_retail_data():
    """Why the index does not use the median: with increases and decreases each
    under 50%, the median lands inside the unchanged block and returns exactly
    1.0 no matter how much moved. On the live feed it read 0.00% on a day when
    74% of products were repriced."""
    import pandas as pd

    # 30% down 10%, 40% unchanged, 30% up 25% -- a real day, not a symmetric one.
    ratio = pd.Series([0.9] * 30 + [1.0] * 40 + [1.25] * 30)
    assert ratio.median() == 1.0  # degenerate: blind to both tails
    assert G._trimmed_mean(ratio) > 1.02  # moves with the data


def test_trimmed_mean_ignores_an_absurd_outlier():
    """The robustness the median was chosen for has to survive the swap."""
    import pandas as pd

    normal = pd.Series([1.01] * 100)
    with_outlier = pd.concat([normal, pd.Series([100.0])], ignore_index=True)
    assert G._trimmed_mean(with_outlier) == pytest.approx(G._trimmed_mean(normal), rel=1e-3)
    # A plain mean would be dragged far away.
    assert with_outlier.mean() > 1.9


def test_trimmed_mean_handles_tiny_and_empty_samples():
    import pandas as pd

    assert G._trimmed_mean(pd.Series([], dtype=float)) == 1.0
    assert G._trimmed_mean(pd.Series([1.5, 2.5])) == pytest.approx(2.0)


def test_index_still_reports_no_change_when_nothing_moves():
    daily = _daily_products(
        [
            {"fecha": "2026-07-20", "id_producto": str(i), "precio_promedio": 100.0}
            for i in range(50)
        ]
        + [
            {"fecha": "2026-07-21", "id_producto": str(i), "precio_promedio": 100.0}
            for i in range(50)
        ]
    )
    out = G.build_inflation_index(daily)
    assert out["variacion_matched_pct"].iloc[1] == pytest.approx(0.0)


def test_index_moves_when_a_real_share_of_prices_moves():
    """The case the median could not see."""
    daily = _daily_products(
        [
            {"fecha": "2026-07-20", "id_producto": str(i), "precio_promedio": 100.0}
            for i in range(100)
        ]
        + [
            {
                "fecha": "2026-07-21",
                "id_producto": str(i),
                "precio_promedio": 110.0 if i < 40 else 100.0,
            }
            for i in range(100)
        ]
    )
    out = G.build_inflation_index(daily)
    assert out["variacion_matched_pct"].iloc[1] > 1.0
    assert out["variacion_mediana_pct"].iloc[1] == pytest.approx(0.0)  # the old metric
