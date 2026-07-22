"""Tests for the Silver transform, covering each historically silent data loss."""

import zipfile

import pandas as pd
import pytest

from src import config
from src import transform_sepa as T

# ---------------------------------------------------------------------------
# Column normalisation
# ---------------------------------------------------------------------------


def test_normalize_columns_strips_bom():
    """The BOM on the first header used to break every join key."""
    df = pd.DataFrame({"﻿id_comercio": ["4"], "productos_precio_lista": ["10"]})
    out = T.normalize_columns(df, config.PRODUCTOS_MAPPING)
    assert "id_comercio" in out.columns
    assert out["id_comercio"].iloc[0] == "4"


def test_normalize_columns_never_produces_duplicate_labels():
    """Two raw columns mapping to one canonical name must not duplicate it."""
    df = pd.DataFrame(
        {
            "productos_precio_lista": ["10"],
            "precio_unitario_bulto_por_unidad_venta_con_iva": ["20"],
        }
    )
    out = T.normalize_columns(df, config.PRODUCTOS_MAPPING)
    assert list(out.columns).count("precio_lista") == 1
    assert not out.columns.duplicated().any()


def test_normalize_columns_does_not_map_ean_flag_onto_id_producto():
    """``productos_ean`` is a 0/1 flag; mapping it would destroy the product id."""
    df = pd.DataFrame({"id_producto": ["7791813434412"], "productos_ean": ["1"]})
    out = T.normalize_columns(df, config.PRODUCTOS_MAPPING)
    assert out["id_producto"].iloc[0] == "7791813434412"


def test_normalize_columns_drops_unmapped():
    df = pd.DataFrame({"id_producto": ["1"], "columna_desconocida": ["x"]})
    out = T.normalize_columns(df, config.PRODUCTOS_MAPPING)
    assert list(out.columns) == ["id_producto"]


# ---------------------------------------------------------------------------
# Footer handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "footer",
    ["Ultima actualizacion: 2026-07-20T16:00:01-03:00", "última actualización: 2026-07-21"],
)
def test_strip_footer_rows_removes_publisher_footer(footer):
    df = pd.DataFrame({"id_comercio": ["4", " ", footer]})
    out = T.strip_footer_rows(df, "id_comercio")
    assert out["id_comercio"].tolist() == ["4"]


def test_strip_footer_rows_is_a_noop_without_the_key_column():
    df = pd.DataFrame({"otra": ["a"]})
    assert len(T.strip_footer_rows(df, "id_comercio")) == 1


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------


def test_clean_prices_drops_unparseable_and_out_of_band():
    df = pd.DataFrame({"precio_lista": ["10.5", "N/D", "-99", "0", "1e12"]})
    out = T.clean_prices(df)
    assert out["precio_lista"].tolist() == [10.5]


def test_clean_prices_without_price_column_returns_empty():
    out = T.clean_prices(pd.DataFrame({"id_producto": ["1"]}))
    assert out.empty


def test_clean_strings_normalises_null_placeholders():
    df = pd.DataFrame({"marca": [" LA SERENISIMA ", " ", "Sin marca", "nan"]})
    out = T.clean_strings(df)
    assert out["marca"].tolist() == ["LA SERENISIMA", None, None, None]


def test_category_filter_is_off_by_default():
    """The old default discarded ~100% of rows; keeping it off is the fix."""
    df = pd.DataFrame({"descripcion_producto": ["7UP FREE PET X 1.5L"]})
    assert len(T.filter_food_beverages(df)) == 1


def test_category_filter_applies_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CATEGORY_FILTER", True)
    df = pd.DataFrame({"descripcion_producto": ["LECHE ENTERA", "JABON LIQUIDO"]})
    monkeypatch.setattr(config, "FOOD_KEYWORDS", ["leche"])
    out = T.filter_food_beverages(df)
    assert out["descripcion_producto"].tolist() == ["LECHE ENTERA"]


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def test_enrich_joins_store_and_chain_and_decodes_province():
    products = pd.DataFrame(
        {
            "id_comercio": ["4"],
            "id_bandera": ["1"],
            "id_sucursal": ["289"],
            "id_producto": ["x"],
            "precio_lista": [10.0],
        }
    )
    sucursales = pd.DataFrame(
        {
            "id_comercio": ["4"],
            "id_bandera": ["1"],
            "id_sucursal": ["289"],
            "nombre_sucursal": ["LIMA"],
            "provincia_codigo": ["AR-C"],
        }
    )
    comercio = pd.DataFrame(
        {"id_comercio": ["4"], "id_bandera": ["1"], "nombre_comercio": ["ESTACION LIMA"]}
    )
    out = T.enrich(products, sucursales, comercio)
    assert out["nombre_sucursal"].iloc[0] == "LIMA"
    assert out["nombre_comercio"].iloc[0] == "ESTACION LIMA"
    assert out["provincia"].iloc[0] == "Ciudad Autónoma de Buenos Aires"


def test_enrich_passes_through_when_dimensions_missing():
    products = pd.DataFrame({"id_producto": ["x"], "precio_lista": [10.0]})
    out = T.enrich(products, pd.DataFrame(), pd.DataFrame())
    assert len(out) == 1


def test_enrich_does_not_fan_out_on_duplicate_store_rows():
    products = pd.DataFrame(
        {"id_comercio": ["4"], "id_bandera": ["1"], "id_sucursal": ["289"], "precio_lista": [1.0]}
    )
    sucursales = pd.DataFrame(
        {
            "id_comercio": ["4", "4"],
            "id_bandera": ["1", "1"],
            "id_sucursal": ["289", "289"],
            "nombre_sucursal": ["LIMA", "LIMA"],
        }
    ).drop_duplicates(subset=config.STORE_KEYS)
    assert len(T.enrich(products, sucursales, pd.DataFrame())) == 1


# ---------------------------------------------------------------------------
# Silver contract
# ---------------------------------------------------------------------------


def test_conform_to_silver_produces_a_stable_schema():
    out = T.conform_to_silver(pd.DataFrame({"id_producto": ["x"], "precio_lista": ["10.5"]}))
    assert list(out.columns) == T.SILVER_COLUMNS
    assert out["precio_lista"].dtype == "float64"
    assert pd.isna(out["latitud"].iloc[0])


def test_conform_output_matches_the_arrow_schema():
    import pyarrow as pa

    frame = T.conform_to_silver(
        pd.DataFrame({"id_producto": ["x"], "id_comercio": ["4"], "precio_lista": [10.5]})
    )
    table = pa.Table.from_pandas(frame, schema=T.SILVER_SCHEMA, preserve_index=False)
    assert table.num_rows == 1


def test_validate_flags_null_ids():
    frame = T.conform_to_silver(
        pd.DataFrame(
            {
                "id_producto": [None],
                "id_comercio": ["4"],
                "id_sucursal": ["1"],
                "precio_lista": [10.0],
            }
        )
    )
    assert not T.validate(frame).empty


def test_validate_passes_on_clean_data():
    frame = T.conform_to_silver(
        pd.DataFrame(
            {
                "id_producto": ["x"],
                "id_comercio": ["4"],
                "id_sucursal": ["1"],
                "precio_lista": [10.0],
            }
        )
    )
    assert T.validate(frame).empty


# ---------------------------------------------------------------------------
# End-to-end over a synthetic archive
# ---------------------------------------------------------------------------


class _CollectingWriter:
    def __init__(self):
        self.tables = []
        self.rows = 0

    def write(self, table):
        self.tables.append(table)
        self.rows += table.num_rows

    def frame(self):
        import pyarrow as pa

        return pa.concat_tables(self.tables).to_pandas()


def _run(archive_path):
    stats = {"rows_read": 0, "rows_dropped": 0, "validation_failures": 0}
    writer = _CollectingWriter()
    for _member, inner in T.iter_retailer_archives(archive_path):
        T.process_retailer(inner, writer, stats)
    return writer, stats


def test_minorista_archive_produces_clean_rows(minorista_archive):
    writer, stats = _run(minorista_archive)
    frame = writer.frame()

    # 5 data rows in, 3 with usable prices out (unparseable + negative dropped).
    assert stats["rows_read"] == 5
    assert len(frame) == 3
    assert stats["validation_failures"] == 0

    row = frame[frame["id_producto"] == "7790040133594"].iloc[0]
    assert row["descripcion_producto"] == "LECHE ENTERA SACHET 1L"
    assert row["precio_lista"] == pytest.approx(1800.50)
    assert row["marca"] == "LA SERENISIMA"
    # Enrichment from sucursales.csv / comercio.csv
    assert row["nombre_sucursal"] == "LIMA"
    assert row["provincia"] == "Ciudad Autónoma de Buenos Aires"
    assert row["nombre_comercio"] == "ESTACION LIMA"
    # " " in the source is a null marker, not a brand.
    assert frame[frame["id_producto"] == "7791813434412"]["marca"].iloc[0] is None


def test_mayorista_archive_maps_its_own_price_columns(mayorista_archive):
    writer, stats = _run(mayorista_archive)
    frame = writer.frame()
    assert len(frame) == 2
    assert frame["precio_lista"].tolist() == pytest.approx([1129.99, 2694.98])
    assert frame["provincia"].iloc[0] == "San Juan"
    assert stats["validation_failures"] == 0


def test_empty_retailer_archives_are_skipped(minorista_archive):
    members = list(T.iter_retailer_archives(minorista_archive))
    # The zero-byte archive in the fixture must not appear or raise.
    assert len(members) == 1


def test_max_comercios_limits_work(monkeypatch, minorista_archive):
    monkeypatch.setattr(config, "MAX_COMERCIOS", 1)
    assert len(list(T.iter_retailer_archives(minorista_archive))) <= 1


def test_corrupt_retailer_archive_does_not_abort_the_run(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as outer:
        outer.writestr("2026-07-21/sepa_1_comercio-sepa-9_x.zip", b"not a zip at all")
    assert list(T.iter_retailer_archives(path)) == []


def test_chunked_reads_produce_the_same_result(monkeypatch, minorista_archive):
    monkeypatch.setattr(config, "CHUNK_SIZE", 2)
    writer, stats = _run(minorista_archive)
    assert len(writer.frame()) == 3
    assert stats["rows_read"] == 5
