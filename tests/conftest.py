"""Shared fixtures: synthetic SEPA archives that mirror the real feed's quirks."""

import io
import zipfile

import pytest

# Byte-for-byte shaped like the live minorista feed: UTF-8 BOM, pipe separator,
# a blank line and an "Ultima actualizacion" footer.
PRODUCTOS_MINORISTA = (
    "﻿id_comercio|id_bandera|id_sucursal|id_producto|productos_ean|"
    "productos_descripcion|productos_cantidad_presentacion|"
    "productos_unidad_medida_presentacion|productos_marca|productos_precio_lista|"
    "productos_precio_referencia|productos_cantidad_referencia|"
    "productos_unidad_medida_referencia|productos_precio_unitario_promo1|"
    "productos_leyenda_promo1|productos_precio_unitario_promo2|productos_leyenda_promo2\n"
    "4|1|289|7791813434412|1|7UP FREE PET X 1.5L|1.00|unidad| |4500.00|4500.00|1|unidad||||\n"
    "4|1|289|7790580109882|1|AGUILA EXTRAFINO 60% CACAO X 150 GR|1.00|unidad|AGUILA|"
    "14400.00|14400.00|1|unidad|13000.00|2x1||\n"
    "4|1|289|7790040133594|1|LECHE ENTERA SACHET 1L|1.00|unidad|LA SERENISIMA|"
    "1800.50|1800.50|1|unidad||||\n"
    # Unparseable price -> must be dropped, not crash the run.
    "4|1|289|7790040298705|1|PRODUCTO SIN PRECIO|1.00|unidad| |N/D|N/D|1|unidad||||\n"
    # Negative price -> outside the valid band.
    "4|1|289|7790040298706|1|PRODUCTO PRECIO NEGATIVO|1.00|unidad| |-99.00|-99.00|1|unidad||||\n"
    " \n"
    "Ultima actualizacion: 2026-07-20T16:00:01-03:00\n"
)

SUCURSALES_MINORISTA = (
    "﻿id_comercio|id_bandera|id_sucursal|sucursales_nombre|sucursales_tipo|"
    "sucursales_calle|sucursales_numero|sucursales_latitud|sucursales_longitud|"
    "sucursales_observaciones|sucursales_barrio|sucursales_codigo_postal|"
    "sucursales_localidad|sucursales_provincia|sucursales_lunes_horario_atencion|"
    "sucursales_martes_horario_atencion|sucursales_miercoles_horario_atencion|"
    "sucursales_jueves_horario_atencion|sucursales_viernes_horario_atencion|"
    "sucursales_sabado_horario_atencion|sucursales_domingo_horario_atencion\n"
    "4|1|289|LIMA|Autoservicio|LIMA|899|-34.617902|-58.38147||Monserrat|C1073AAQ|"
    "CAPITAL FEDERAL|AR-C|00:00 a 24:00|00:00 a 24:00|00:00 a 24:00|00:00 a 24:00|"
    "00:00 a 24:00|00:00 a 24:00|00:00 a 24:00\n"
    " \n"
    "Ultima actualizacion: 2026-07-20T16:00:01-03:00\n"
)

COMERCIO_MINORISTA = (
    "﻿id_comercio|id_bandera|comercio_cuit|comercio_razon_social|"
    "comercio_bandera_nombre|comercio_bandera_url|comercio_ultima_actualizacion|"
    "comercio_version_sepa\n"
    "4|1|30537679855|ESTACION LIMA S.A.|ESTACION LIMA|||1.0\n"
    " \n"
    "Ultima actualizacion: 2026-07-20T16:00:01-03:00\n"
)

# The mayorista feed has no BOM, different price columns, and accented footers.
PRODUCTOS_MAYORISTA = (
    "id_comercio|id_bandera|id_sucursal|id_producto|productos_ean|id_dun_14|"
    "productos_descripcion|productos_marca|"
    "precio_unitario_bulto_por_unidad_venta_con_iva|"
    "precio_unitario_bulto_por_unidad_venta_sin_iva|unidad_venta|precio_bulto_con_iva|"
    "precio_bulto_sin_iva|productos_precio_unitario_con_iva_promo1|"
    "productos_precio_unitario_sin_iva_promo1|productos_leyenda_promo1|"
    "productos_precio_unitario_con_iva_promo2|productos_precio_unitario_sin_iva_promo2|"
    "productos_leyenda_promo2\n"
    "62|1|4|7798184585019|1|17798184585019|MULTIMAX JAB LIQ ROPA 200ML|Sin marca|"
    "1129.99|933.88|1|1129.99|933.88||||||\n"
    "62|1|4|7791290796638|1|17791290796638|VIVERE SUAV FLORAL 900ML|Sin marca|"
    "2694.98|2227.26|1|2694.98|2227.26||||||\n"
    "\n"
    "última actualización: 2026-07-21T05:15:03-03:00\n"
)

SUCURSALES_MAYORISTA = (
    "id_comercio|id_bandera|id_sucursal|sucursales_nombre|sucursales_tipo|"
    "sucursales_calle|sucursales_numero|sucursales_latitud|sucursales_longitud|"
    "sucursales_observaciones|sucursales_barrio|sucursales_codigo_postal|"
    "sucursales_localidad|sucursales_provincia\n"
    "62|1|4|SAN JUAN|Mayorista|AV. RAWSON|1668|-31.552167|-68.514250|SAN JUAN|"
    "Trinidad|5400|SAN JUAN|AR-J\n"
)


def _retailer_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8"))
    return buffer.getvalue()


def build_archive(path, date_str="2026-07-21", tipo="minorista", include_empty=True):
    """Write a synthetic daily archive with the real nested-ZIP layout."""
    if tipo == "minorista":
        retailers = {
            f"{date_str}/sepa_1_comercio-sepa-4_{date_str}_09-05-10.zip": _retailer_zip(
                {
                    "productos.csv": PRODUCTOS_MINORISTA,
                    "sucursales.csv": SUCURSALES_MINORISTA,
                    "comercio.csv": COMERCIO_MINORISTA,
                }
            )
        }
    else:
        retailers = {
            f"{date_str}/sepa_1_comercio-sepa-62_{date_str}_09-05-10.zip": _retailer_zip(
                {
                    "productos.csv": PRODUCTOS_MAYORISTA,
                    "sucursales.csv": SUCURSALES_MAYORISTA,
                }
            )
        }

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as outer:
        outer.writestr(f"{date_str}/", "")
        for name, payload in retailers.items():
            outer.writestr(name, payload)
        if include_empty:
            # The real feed ships zero-byte retailer archives; they must be
            # skipped rather than raising BadZipFile.
            outer.writestr(f"{date_str}/sepa_2_comercio-sepa-36_{date_str}_01-05-08.zip", b"")
    return path


@pytest.fixture
def minorista_archive(tmp_path):
    return build_archive(tmp_path / "sepa_minorista.zip", tipo="minorista")


@pytest.fixture
def mayorista_archive(tmp_path):
    return build_archive(tmp_path / "sepa_mayorista.zip", tipo="mayorista")


@pytest.fixture
def ckan_payload():
    """A trimmed package_show response with the real weekday-resource shape."""
    return {
        "success": True,
        "result": {
            "resources": [
                {
                    "name": "Martes",
                    "format": "ZIP",
                    "url": "https://example.test/download/sepa_martes.zip",
                    "last_modified": "2026-07-21T16:18:26.185704",
                },
                {
                    "name": "Mi�rcoles",
                    "format": "ZIP",
                    "url": "https://example.test/download/sepa_miercoles.zip",
                    "last_modified": "2026-07-15T16:19:59.823590",
                },
                {
                    "name": "Metadata",
                    "format": "PDF",
                    "url": "https://example.test/download/anexo.pdf",
                    "last_modified": "2024-08-20T16:54:55.573247",
                },
            ]
        },
    }
