"""Panel Streamlit sobre la capa Gold.

Interfaz en español rioplatense: el público del monitor es argentino. Los
comentarios y los nombres del código siguen en inglés, como el resto del repo.

Sólo presentación -- las consultas viven en :mod:`src.data_access` y los tokens
de diseño en :mod:`src.theme`.

Charting rules applied throughout (see the palette note in ``theme.py``):
one axis per plot, categorical hues assigned in fixed order and never cycled,
one hue for nominal bar categories (never a value ramp), emphasis instead of a
rainbow when a single series is the point, and a table-view twin with CSV
export under every chart so nothing is encoded by colour alone.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import config, theme
from src import data_access as da

st.set_page_config(
    page_title="Monitor de Precios SEPA",
    page_icon="🇦🇷",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.inject_css(st)


# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner="Leyendo la capa Gold…")
def load_gold(tipo):
    return da.load_gold(tipo)


@st.cache_data(ttl=300)
def load_quality(tipo):
    return da.load_quality(tipo)


# ---------------------------------------------------------------------------
# Sidebar — one filter surface scoping every tab
# ---------------------------------------------------------------------------

theme.sidebar_brand(st, "Monitor SEPA", "Precios de Argentina")

theme.sidebar_label(st, "Fuente")
dataset_type = st.sidebar.selectbox(
    "Fuente de datos",
    list(config.DATASET_TYPES),
    index=0,
    format_func=lambda v: v.capitalize(),
    label_visibility="collapsed",
    help="Minorista: precios de góndola. Mayorista: venta por bulto.",
)

try:
    data = load_gold(dataset_type)
    quality = load_quality(dataset_type)
except Exception as exc:
    st.error(f"No se pudo acceder al data lake en `{config.S3_ENDPOINT_URL}`.")
    st.caption(f"{type(exc).__name__}: {exc}")
    st.caption("Verificá que el servicio `minio` esté levantado y que coincidan las credenciales.")
    st.stop()

df_inf = (
    data["inflation"].sort_values("fecha") if not data["inflation"].empty else data["inflation"]
)
df_prod = data["products"]
df_stores = data["stores"]
df_prov = data["provinces"]

if df_inf.empty:
    theme.masthead(st, "Argentina · SEPA", "Monitor de Precios")
    st.warning(
        f"La capa Gold no tiene datos para **{dataset_type}**. Ejecutá el DAG "
        "`sepa_pipeline` en Airflow, o corré la carga por CLI, y actualizá."
    )
    st.code(
        "docker compose run --rm streamlit python -m src.fetch_sepa_range \\\n"
        "  --start-date 2026-07-20 --end-date 2026-07-21 "
        f"--type {dataset_type}",
        language="bash",
    )
    st.stop()

# --- Date range -------------------------------------------------------------
min_date, max_date = da.date_bounds([df_inf, df_prod, df_stores])
all_dates = sorted(df_inf["fecha"].unique())

theme.sidebar_label(st, "Período")
if len(all_dates) > 1:
    picked = st.sidebar.select_slider(
        "Período",
        options=all_dates,
        value=(all_dates[0], all_dates[-1]),
        format_func=lambda d: theme.fecha_corta(pd.Timestamp(d)),
        label_visibility="collapsed",
    )
    range_start, range_end = pd.Timestamp(picked[0]), pd.Timestamp(picked[1])
else:
    range_start = range_end = pd.Timestamp(all_dates[0])
    st.sidebar.caption(f"Un solo día cargado: {theme.fecha(range_start)}")


def in_range(frame):
    if frame.empty or "fecha" not in frame.columns:
        return frame
    return frame[(frame["fecha"] >= range_start) & (frame["fecha"] <= range_end)]


df_inf = in_range(df_inf)
df_prod = in_range(df_prod)
df_stores = in_range(df_stores)
df_prov = in_range(df_prov)

# --- Province ---------------------------------------------------------------
province_options = []
if not df_prov.empty:
    province_options = sorted(df_prov["provincia"].dropna().unique())
elif not df_stores.empty and "provincia" in df_stores.columns:
    province_options = sorted(df_stores["provincia"].dropna().unique())

theme.sidebar_label(st, "Región")
province = st.sidebar.selectbox(
    "Provincia",
    ["Todas las provincias"] + list(province_options),
    label_visibility="collapsed",
    help="Acota los precios de productos y el ranking de sucursales. El índice "
    "nacional siempre se calcula sobre todo el país.",
)
province_filter = None if province == "Todas las provincias" else province

if province_filter and not df_prov.empty:
    labels = da.product_labels(df_prod)
    scoped = df_prov[df_prov["provincia"] == province_filter].copy()
    scoped["descripcion_producto"] = scoped["id_producto"].map(labels)
    scoped["marca"] = None
    products_scoped = scoped.dropna(subset=["descripcion_producto"])
else:
    products_scoped = df_prod

if not df_stores.empty and province_filter:
    stores_scoped = df_stores[df_stores["provincia"] == province_filter]
else:
    stores_scoped = df_stores

st.sidebar.markdown("---")
if st.sidebar.button("Actualizar datos", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

theme.sidebar_label(st, "Conexión")
st.sidebar.caption(f"MinIO · `{config.S3_BUCKET}`  \nOrquestado con Apache Airflow")

# ---------------------------------------------------------------------------
# Masthead + KPI row
# ---------------------------------------------------------------------------

latest_date = df_inf["fecha"].max()
latest = df_inf[df_inf["fecha"] == latest_date].iloc[0]
previous_rows = df_inf[df_inf["fecha"] < latest_date].tail(1)
previous_date = previous_rows.iloc[0]["fecha"] if not previous_rows.empty else None

chips = [
    theme.chip(theme.fecha(latest_date), live=True),
    theme.chip(f"{len(df_inf)} día{'s' if len(df_inf) != 1 else ''}"),
]
if province_filter:
    chips.append(theme.chip(province, accent=True))

theme.masthead(st, f"Argentina · {dataset_type.capitalize()}", "Monitor de Precios")

# --- Supporting figures for the hero ledger --------------------------------
avg = latest.get("indice_precio_global", float("nan"))
delta_avg = None
if not previous_rows.empty:
    prev_avg = previous_rows.iloc[0]["indice_precio_global"]
    if prev_avg:
        delta_avg = (avg - prev_avg) / prev_avg * 100

changed = latest.get("pct_productos_con_cambio")
comparable = latest.get("productos_comparables", 0)

if not quality.empty and "completeness_pct" in quality.columns:
    score = quality["completeness_pct"].iloc[-1]
    failures = int(quality["validation_failures"].iloc[-1])
    # El estado lo lleva un ícono junto al texto, nunca sólo el color.
    calidad = (
        theme.pct(score, 1, signed=False),
        "✓ sin violaciones de esquema" if failures == 0 else f"! {failures} violaciones de esquema",
    )
else:
    calidad = ("s/d", "Sin reporte de calidad")

theme.hero(
    st,
    kicker="Índice de precios",
    label="Índice comparable · base 100",
    value=theme.numero(latest.get("indice_matched_base100", float("nan")), 1),
    delta=latest.get("variacion_matched_pct"),
    delta_word="vs. día anterior",
    foot="Sólo productos presentes ambos días, así el cambio de mix no lo ensucia.",
    spark=df_inf["indice_matched_base100"].tolist(),
    colour=theme.CELESTE,
    chips=chips,
    stats=[
        ("Precio promedio publicado", theme.ars(avg), delta_avg, "Sensible al mix de productos"),
        (
            "Productos remarcados",
            "—" if pd.isna(changed) else theme.pct(changed, 1, signed=False),
            None,
            f"de {theme.count(comparable)} comparables",
        ),
        ("Calidad de datos", calidad[0], None, calidad[1]),
    ],
)

tab_overview, tab_basket, tab_products, tab_geo, tab_health = st.tabs(
    ["Resumen", "Canasta", "Productos", "Sucursales y provincias", "Estado del pipeline"]
)

# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

with tab_overview:
    theme.section(
        st,
        "Índice de precios",
        "Dos formas de leer el mismo feed. El índice comparable mira sólo los "
        "productos presentes ambos días; el promedio simple también se mueve "
        "cuando cambia qué productos se informan, que suele ser el efecto mayor.",
        eyebrow="Tendencia",
    )

    index_cols = [
        c for c in ("indice_matched_base100", "indice_global_base100") if c in df_inf.columns
    ]
    if index_cols and len(df_inf) >= 1:
        labels = {
            "indice_matched_base100": "Productos comparables",
            "indice_global_base100": "Promedio simple",
        }
        melted = df_inf.melt(
            id_vars="fecha", value_vars=index_cols, var_name="serie", value_name="valor"
        )
        melted["serie"] = melted["serie"].map(labels).fillna(melted["serie"])

        fig = px.line(
            melted,
            x="fecha",
            y="valor",
            color="serie",
            markers=True,
            # Fixed slot order: matched = slot 1, simple average = slot 2.
            color_discrete_map={
                "Productos comparables": theme.CELESTE,
                "Promedio simple": theme.AMARILLO,
            },
        )
        fig.update_traces(line={"width": 2}, marker={"size": 8})
        # Direct labels at the endpoints so identity never rests on hue alone.
        for name, colour in (
            ("Productos comparables", theme.CELESTE),
            ("Promedio simple", theme.AMARILLO),
        ):
            series = melted[melted["serie"] == name]
            if series.empty:
                continue
            end = series.iloc[-1]
            fig.add_annotation(
                x=end["fecha"],
                y=end["valor"],
                text=f"  {theme.numero(end['valor'], 1)}",
                showarrow=False,
                xanchor="left",
                font={"size": 12, "color": colour},
            )
        fig.add_hline(y=100, line_color=theme.AXIS, line_width=1)
        theme.style(fig, height=290, y_title="Índice")
        theme.daily_axis(fig, len(df_inf))
        # plotly.express titles the legend after the column it coloured by,
        # which surfaced a literal "serie" above the swatches.
        fig.update_layout(hovermode="x unified", legend_title_text="")
        theme.chart(
            st,
            fig,
            title="Índice comparable vs. promedio simple",
            meta=f"{theme.fecha_corta(df_inf['fecha'].min())} – {theme.fecha_corta(latest_date)}",
            accent=theme.CELESTE,
        )

        theme.table_view(
            st,
            df_inf[["fecha"] + index_cols + ["variacion_matched_pct", "productos_comparables"]]
            .rename(
                columns={
                    "fecha": "Fecha",
                    "indice_matched_base100": "Índice comparable",
                    "indice_global_base100": "Promedio simple",
                    "variacion_matched_pct": "Var. comparable %",
                    "productos_comparables": "Productos comparables",
                }
            )
            .round(2),
            f"indice_precios_{dataset_type}.csv",
        )

    if len(df_inf) < 2:
        st.info(
            "Hay un solo día cargado, así que todavía no hay con qué comparar. "
            "Cargá algunas fechas más para ver la tendencia."
        )

    col_a, col_b = st.columns(2)

    with col_a:
        theme.section(
            st,
            "Movimiento del índice",
            "Cambio de los productos comparables contra el día anterior.",
            eyebrow="Movimiento",
        )
        movers = df_inf[df_inf["variacion_matched_pct"].notna()].copy()
        if len(movers) >= 2:
            movers = movers.iloc[1:]  # the first day has no predecessor
            # Diverging: warm/cool poles either side of a zero baseline.
            colours = [
                theme.DIVERGING_POS if v > 0 else theme.DIVERGING_NEG
                for v in movers["variacion_matched_pct"]
            ]
            fig = go.Figure(
                go.Bar(
                    x=movers["fecha"],
                    y=movers["variacion_matched_pct"],
                    marker={"color": colours, "line": {"width": 2, "color": theme.SURFACE}},
                    text=[f"{v:+.2f}%" for v in movers["variacion_matched_pct"]],
                    textposition="outside",
                    textfont={"size": 11},
                    hovertemplate="%{x|%d/%m}<br>%{y:+.2f}%<extra></extra>",
                )
            )
            fig.add_hline(y=0, line_color=theme.AXIS, line_width=1)
            theme.style(fig, height=225, y_title="Variación (%)")
            theme.daily_axis(fig, len(movers))
            theme.outside_labels(fig, axis="y")
            theme.chart(st, fig, title="Variación diaria", accent=theme.DIVERGING_POS)
        else:
            st.info("Se necesitan al menos dos días.")

    with col_b:
        theme.section(
            st,
            "Cuántos precios se tocaron",
            "Porcentaje de productos comparables que cambiaron de precio.",
            eyebrow="Actividad",
        )
        act = df_inf[df_inf["productos_comparables"] > 0]
        if not act.empty:
            fig = go.Figure(
                go.Bar(
                    x=act["fecha"],
                    y=act["pct_productos_con_cambio"],
                    # Nominal days, one series: a single hue, never a value ramp.
                    marker={"color": theme.CELESTE, "line": {"width": 2, "color": theme.SURFACE}},
                    text=[f"{v:.1f}%" for v in act["pct_productos_con_cambio"]],
                    textposition="outside",
                    textfont={"size": 11},
                    hovertemplate="%{x|%d/%m}<br>%{y:.1f}% remarcados<extra></extra>",
                )
            )
            theme.style(fig, height=225, y_title="Remarcados (%)")
            theme.daily_axis(fig, len(act))
            theme.outside_labels(fig, axis="y")
            theme.chart(st, fig, title="Actividad de remarcación", accent=theme.CELESTE)
        else:
            st.info("Se necesitan al menos dos días.")

# ---------------------------------------------------------------------------
# Basket
# ---------------------------------------------------------------------------

with tab_basket:
    theme.section(
        st,
        "Armá tu canasta",
        "Elegí los productos que realmente comprás y seguí cuánto cuesta la "
        "canasta completa. Sólo se grafican los días en que están todos los "
        "productos, así la línea refleja precios y no cambios de cobertura.",
        eyebrow="Tu canasta",
    )

    if products_scoped.empty:
        st.info("No hay datos de productos en el recorte elegido.")
    else:
        labels = da.product_labels(products_scoped)
        latest_products = products_scoped[products_scoped["fecha"] == latest_date]

        c1, c2 = st.columns([1, 2])
        with c1:
            preset = st.selectbox(
                "Partí de una canasta armada", ["(vacía)"] + list(da.PRESET_BASKETS)
            )
        with c2:
            st.caption(
                "Cada palabra clave se resuelve al producto con mayor cobertura "
                "que la contenga en el último día. Después editás las cantidades."
            )

        if "basket_ids" not in st.session_state:
            st.session_state.basket_ids = []

        if preset != "(vacía)":
            if st.button(f"Cargar “{preset}”", type="primary"):
                st.session_state.basket_ids = da.resolve_preset(
                    products_scoped, da.PRESET_BASKETS[preset], latest_date
                )
                st.rerun()

        options = latest_products["id_producto"].dropna().unique().tolist()
        chosen = st.multiselect(
            "Productos en la canasta",
            options=options,
            default=[i for i in st.session_state.basket_ids if i in options],
            format_func=lambda i: str(labels.get(i, i))[:70],
            help="Escribí para buscar. Agregá todos los productos que quieras.",
        )
        st.session_state.basket_ids = chosen

        if not chosen:
            st.info("Agregá al menos un producto, o cargá una canasta de arriba.")
        else:
            collapsed = da.collapse_to_product_day(products_scoped)
            latest_prices = collapsed[collapsed["fecha"] == latest_date].set_index("id_producto")[
                "precio_promedio"
            ]
            editor = pd.DataFrame(
                {
                    "Producto": [str(labels.get(i, i)) for i in chosen],
                    "Cantidad": [1] * len(chosen),
                    "Precio unitario": [float(latest_prices.get(i, float("nan"))) for i in chosen],
                }
            )
            edited = st.data_editor(
                editor,
                use_container_width=True,
                hide_index=True,
                disabled=["Producto", "Precio unitario"],
                column_config={
                    "Cantidad": st.column_config.NumberColumn(min_value=0, max_value=99, step=1),
                    "Precio unitario": st.column_config.NumberColumn(format="$%.2f"),
                },
                key="basket_editor",
            )
            quantities = dict(zip(chosen, edited["Cantidad"], strict=False))

            series = da.basket_series(products_scoped, chosen, quantities)

            if series.empty:
                st.warning(
                    "Ningún día tiene todos los productos elegidos, así que el "
                    "total no sería comparable. Probá sacar el menos frecuente."
                )
            else:
                total_now = series.iloc[-1]["costo_total"]
                total_then = series.iloc[0]["costo_total"]
                change = (total_now - total_then) / total_then * 100 if total_then else None

                b1, b2, b3 = st.columns(3)
                with b1:
                    theme.tile(
                        st,
                        "Costo de la canasta hoy",
                        theme.ars(total_now),
                        foot=f"{len(chosen)} producto(s), {int(edited['Cantidad'].sum())} unidad(es)",
                        small=True,
                    )
                with b2:
                    theme.tile(
                        st,
                        "Variación del período",
                        theme.pct(change) if change is not None else "—",
                        delta=change,
                        delta_word=f"desde el {theme.fecha_corta(series.iloc[0]['fecha'])}",
                        small=True,
                    )
                with b3:
                    theme.tile(
                        st,
                        "Días con cobertura",
                        str(len(series)),
                        foot="días en que se informaron todos los productos",
                        small=True,
                    )

                fig = px.line(series, x="fecha", y="costo_total", markers=True)
                fig.update_traces(
                    line={"width": 2, "color": theme.CELESTE},
                    marker={"size": 8, "color": theme.CELESTE},
                    hovertemplate="%{x|%d/%m}<br>$%{y:,.0f}<extra></extra>",
                )
                # Single series: the title names it, so no legend box.
                theme.style(fig, height=265, y_title="Costo ($)", show_legend=False)
                theme.daily_axis(fig, len(series))
                theme.chart(st, fig, title="Costo de la canasta", accent=theme.CELESTE)
                theme.table_view(st, series.round(2), f"canasta_{dataset_type}.csv", "Ver tabla")

                # --- Same basket, every province -------------------------
                if not df_prov.empty:
                    theme.section(
                        st,
                        "La misma canasta por provincia",
                        "Sólo aparecen las provincias que informan todos los "
                        "productos: comparar una canasta parcial contra una "
                        "completa haría que la provincia con menos datos parezca "
                        "la más barata.",
                        eyebrow="Geografía",
                    )
                    by_prov = da.basket_by_province(df_prov, chosen, latest_date, quantities)
                    if by_prov.empty:
                        st.info("Ninguna provincia informa la canasta completa el último día.")
                    else:
                        cheapest = by_prov.iloc[0]["provincia"]
                        # Emphasis: the cheapest province is the point, the rest
                        # are context — one accent hue plus de-emphasis grey.
                        colours = [
                            theme.CELESTE if p == cheapest else theme.MUTED_MARK
                            for p in by_prov["provincia"]
                        ]
                        fig = go.Figure(
                            go.Bar(
                                x=by_prov["costo_total"],
                                y=by_prov["provincia"],
                                orientation="h",
                                marker={
                                    "color": colours,
                                    "line": {"width": 2, "color": theme.SURFACE},
                                },
                                text=[theme.ars(v) for v in by_prov["costo_total"]],
                                textposition="outside",
                                textfont={"size": 11},
                                hovertemplate="%{y}<br>$%{x:,.0f}<extra></extra>",
                            )
                        )
                        theme.style(
                            fig,
                            height=max(240, 30 * len(by_prov) + 80),
                            x_title="Costo de la canasta ($)",
                            show_legend=False,
                        )
                        fig.update_layout(
                            yaxis={"categoryorder": "total descending"},
                            xaxis={"showgrid": True},
                        )
                        theme.outside_labels(fig, axis="x")
                        theme.chart(st, fig, title="Costo por provincia", accent=theme.CELESTE)

                        spread = by_prov.iloc[-1]["costo_total"] - by_prov.iloc[0]["costo_total"]
                        st.caption(
                            f"Más barata: **{cheapest}** · más cara: "
                            f"**{by_prov.iloc[-1]['provincia']}** · diferencia "
                            f"**{theme.ars(spread)}** "
                            f"({theme.pct(spread / by_prov.iloc[-1]['costo_total'] * 100, 1, signed=False)})"
                        )
                        theme.table_view(
                            st,
                            by_prov.round(2),
                            f"canasta_por_provincia_{dataset_type}.csv",
                        )

# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

with tab_products:
    if products_scoped.empty:
        st.info("No hay datos de productos en el recorte elegido.")
    else:
        theme.section(st, "Buscador", "Encontrá un producto y seguí su precio.", eyebrow="Explorar")

        s1, s2 = st.columns([2, 1])
        with s1:
            term = st.text_input("El nombre contiene", "", placeholder="leche, arroz, yerba…")
        with s2:
            brands = (
                sorted(products_scoped["marca"].dropna().unique())
                if "marca" in products_scoped.columns
                else []
            )
            brand = st.selectbox("Marca", ["Todas las marcas"] + list(brands))
            brand = None if brand == "Todas las marcas" else brand

        if term:
            matches = da.search_products(products_scoped, term, brand)
            if matches.empty:
                st.info("No hubo resultados para esa búsqueda.")
            else:
                st.caption(
                    f"**{theme.count(matches['id_producto'].nunique())}** productos distintos."
                )
                # Four is the validated slot count. Past it the palette would
                # have to grow hues, which is never the answer to "too many
                # series"; the table view below carries the rest.
                top_ids = (
                    matches.groupby("id_producto", observed=True)["cantidad_muestras"]
                    .sum()
                    .nlargest(4)
                    .index
                )
                chart_rows = matches[matches["id_producto"].isin(top_ids)]
                fig = px.line(
                    chart_rows,
                    x="fecha",
                    y="precio_promedio",
                    color="descripcion_producto",
                    markers=True,
                    color_discrete_sequence=theme.SERIES,
                )
                fig.update_traces(line={"width": 2}, marker={"size": 7})
                theme.style(fig, height=295, y_title="Precio promedio ($)")
                theme.daily_axis(fig, chart_rows["fecha"].nunique())
                fig.update_layout(hovermode="x unified", legend_title_text="")
                theme.chart(st, fig, title="Evolución de precios", accent=theme.CELESTE)
                theme.table_view(
                    st,
                    matches.sort_values(["fecha", "precio_promedio"], ascending=[False, True])
                    .head(300)
                    .round(2),
                    f"busqueda_{term}_{dataset_type}.csv",
                    "Ver tabla — todos los resultados",
                )
        else:
            st.info("Escribí el nombre de un producto arriba para ver su historial.")

        # --- Savings finder ---------------------------------------------
        theme.section(
            st,
            "Dónde más conviene comparar",
            "Mismo código de barras, sucursal más barata contra la más cara el "
            f"último día. Se excluyen brechas mayores a {da.MAX_PLAUSIBLE_PRICE_RATIO:.0f}× "
            "porque a esa altura es el mismo producto informado en otra unidad "
            "(por unidad vs. por docena), no un ahorro real.",
            eyebrow="Ahorro",
        )
        savings = da.savings_opportunities(products_scoped, latest_date)
        if savings.empty:
            st.info("Todavía no hay cobertura suficiente entre sucursales para calcular brechas.")
        else:
            plot = savings.sort_values("ahorro_pct").copy()
            plot["label"] = plot["descripcion_producto"].str.slice(0, 42)
            fig = go.Figure()
            for _, row in plot.iterrows():
                fig.add_trace(
                    go.Scatter(
                        x=[row["precio_minimo"], row["precio_maximo"]],
                        y=[row["label"], row["label"]],
                        mode="lines",
                        line={"width": 3, "color": theme.MUTED_MARK},
                        hoverinfo="skip",
                        showlegend=False,
                    )
                )
            fig.add_trace(
                go.Scatter(
                    x=plot["precio_minimo"],
                    y=plot["label"],
                    mode="markers",
                    name="Más barata",
                    marker={
                        "size": 10,
                        "color": theme.CELESTE,
                        "line": {"width": 2, "color": theme.SURFACE},
                    },
                    hovertemplate="%{y}<br>Más barata $%{x:,.0f}<extra></extra>",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=plot["precio_maximo"],
                    y=plot["label"],
                    mode="markers",
                    name="Más cara",
                    marker={
                        "size": 10,
                        "color": theme.AMARILLO,
                        "line": {"width": 2, "color": theme.SURFACE},
                    },
                    hovertemplate="%{y}<br>Más cara $%{x:,.0f}<extra></extra>",
                )
            )
            theme.style(fig, height=max(300, 26 * len(plot) + 90), x_title="Precio ($)")
            fig.update_layout(xaxis={"showgrid": True}, yaxis={"showgrid": False})
            theme.chart(st, fig, title="Brecha entre sucursales", accent=theme.AMARILLO)

            best = savings.iloc[0]
            st.caption(
                f"Mayor brecha: **{best['descripcion_producto']}** — "
                f"{theme.ars(best['precio_minimo'])} contra {theme.ars(best['precio_maximo'])}, "
                f"un ahorro de **{theme.pct(best['ahorro_pct'], 0, signed=False)}** "
                f"({theme.ars(best['ahorro_abs'])}) por comprarlo en la sucursal indicada."
            )
            theme.table_view(
                st,
                savings[
                    [
                        "descripcion_producto",
                        "precio_minimo",
                        "precio_promedio",
                        "precio_maximo",
                        "ahorro_abs",
                        "ahorro_pct",
                        "ratio",
                        "cantidad_muestras",
                    ]
                ].round(2),
                f"ahorro_{dataset_type}.csv",
            )

        # --- Movers ------------------------------------------------------
        theme.section(
            st,
            "Mayores variaciones",
            f"Cambio de precio entre el {theme.fecha_corta(previous_date)} y el "
            f"{theme.fecha_corta(latest_date)}."
            if previous_date is not None
            else "Se necesitan al menos dos días de datos.",
            eyebrow="Movimiento",
        )
        movers = da.price_movers(products_scoped, previous_date, latest_date)
        if movers.empty:
            st.info("Todavía no hay productos presentes en ambos días.")
        else:
            up = movers.head(10)
            down = movers.tail(10).sort_values("variacion_pct")

            m1, m2 = st.columns(2)
            for column, frame, title, colour in (
                (m1, up, "↑ Aumentos", theme.DIVERGING_POS),
                (m2, down, "↓ Bajas", theme.DIVERGING_NEG),
            ):
                with column:
                    st.markdown(f"**{title}**")
                    fig = go.Figure(
                        go.Bar(
                            x=frame["variacion_pct"],
                            y=frame["descripcion_producto"].str.slice(0, 38),
                            orientation="h",
                            marker={"color": colour, "line": {"width": 2, "color": theme.SURFACE}},
                            text=[f"{v:+.1f}%" for v in frame["variacion_pct"]],
                            textposition="outside",
                            textfont={"size": 11},
                            hovertemplate="%{y}<br>%{x:+.2f}%<extra></extra>",
                        )
                    )
                    theme.style(fig, height=340, x_title="Variación (%)", show_legend=False)
                    fig.update_layout(
                        yaxis={
                            "categoryorder": "total ascending"
                            if colour == theme.DIVERGING_POS
                            else "total descending"
                        },
                        xaxis={"showgrid": True},
                    )
                    theme.outside_labels(fig, axis="x")
                    theme.chart(st, fig)

            theme.table_view(
                st,
                movers[
                    [
                        "descripcion_producto",
                        "precio_anterior",
                        "precio_actual",
                        "variacion_pct",
                        "muestras",
                    ]
                ].round(2),
                f"variaciones_{dataset_type}.csv",
                "Ver tabla — todas las variaciones",
            )

# ---------------------------------------------------------------------------
# Stores & provinces
# ---------------------------------------------------------------------------

with tab_geo:
    if stores_scoped.empty:
        st.info("No hay estadísticas de sucursales en el recorte elegido.")
    else:
        latest_stores = stores_scoped[stores_scoped["fecha"] == latest_date]
        ranked = latest_stores[latest_stores["productos_reportados"] >= config.MIN_STORE_SAMPLES]

        theme.section(
            st,
            "Sucursales más baratas",
            "Precio promedio publicado sobre todo lo que informó cada sucursal, "
            f"limitado a las que tienen al menos {config.MIN_STORE_SAMPLES} productos: "
            "una sucursal que publica tres artículos no es barata, informa poco.",
            eyebrow="Ranking",
        )

        if ranked.empty:
            st.info(
                f"Ninguna sucursal informó {config.MIN_STORE_SAMPLES}+ productos "
                f"el {theme.fecha_corta(latest_date)}."
            )
        else:
            cheapest = ranked.nsmallest(12, "precio_promedio_general").copy()
            cheapest["label"] = (
                cheapest["nombre_sucursal"].fillna("—").str.slice(0, 34)
                + "  ·  "
                + cheapest["provincia"].fillna("—")
            )
            fig = go.Figure(
                go.Bar(
                    x=cheapest["precio_promedio_general"],
                    y=cheapest["label"],
                    orientation="h",
                    # Nominal categories, one series: a single hue.
                    marker={"color": theme.CELESTE, "line": {"width": 2, "color": theme.SURFACE}},
                    text=[theme.ars(v) for v in cheapest["precio_promedio_general"]],
                    textposition="outside",
                    textfont={"size": 11},
                    customdata=cheapest[["nombre_comercio", "productos_reportados"]],
                    hovertemplate=(
                        "%{y}<br>%{customdata[0]}<br>"
                        "Prom. $%{x:,.0f} · %{customdata[1]:,} productos<extra></extra>"
                    ),
                )
            )
            theme.style(fig, height=390, x_title="Precio promedio publicado ($)", show_legend=False)
            fig.update_layout(yaxis={"categoryorder": "total descending"}, xaxis={"showgrid": True})
            theme.outside_labels(fig, axis="x")
            theme.chart(st, fig, title="Sucursales más baratas", accent=theme.CELESTE)
            theme.table_view(
                st,
                ranked.sort_values("precio_promedio_general")[
                    [
                        "nombre_comercio",
                        "nombre_sucursal",
                        "provincia",
                        "productos_reportados",
                        "precio_promedio_general",
                    ]
                ].round(2),
                f"sucursales_{dataset_type}.csv",
            )

        # --- Retail chains ----------------------------------------------
        theme.section(
            st,
            "Por cadena",
            "Precio promedio publicado por cadena, con la cantidad de sucursales que informan.",
            eyebrow="Cadenas",
        )
        chains = (
            latest_stores.groupby("nombre_comercio", observed=True)
            .agg(
                sucursales=("nombre_sucursal", "nunique"),
                productos=("productos_reportados", "sum"),
                precio_promedio=("precio_promedio_general", "mean"),
            )
            .reset_index()
            .dropna(subset=["nombre_comercio"])
            .sort_values("precio_promedio")
        )
        if chains.empty:
            st.info("No hay información de cadenas disponible.")
        else:
            fig = go.Figure(
                go.Bar(
                    x=chains["precio_promedio"],
                    y=chains["nombre_comercio"].str.slice(0, 40),
                    orientation="h",
                    marker={"color": theme.CELESTE, "line": {"width": 2, "color": theme.SURFACE}},
                    text=[theme.ars(v) for v in chains["precio_promedio"]],
                    textposition="outside",
                    textfont={"size": 11},
                    customdata=chains[["sucursales", "productos"]],
                    hovertemplate=(
                        "%{y}<br>Avg $%{x:,.0f}<br>"
                        "%{customdata[0]} sucursal(es) · %{customdata[1]:,} productos<extra></extra>"
                    ),
                )
            )
            theme.style(
                fig,
                height=max(240, 34 * len(chains) + 90),
                x_title="Precio promedio publicado ($)",
                show_legend=False,
            )
            fig.update_layout(yaxis={"categoryorder": "total descending"}, xaxis={"showgrid": True})
            theme.outside_labels(fig, axis="x")
            theme.chart(st, fig, title="Precio promedio por cadena", accent=theme.CELESTE)
            theme.table_view(st, chains.round(2), f"cadenas_{dataset_type}.csv")

        # --- Provinces ---------------------------------------------------
        if "provincia" in df_stores.columns:
            theme.section(
                st,
                "Por provincia",
                "Promedio de las sucursales que informan en cada provincia.",
                eyebrow="Geografía",
            )
            provs = (
                df_stores[df_stores["fecha"] == latest_date]
                .groupby("provincia", observed=True)
                .agg(
                    sucursales=("nombre_sucursal", "nunique"),
                    precio_promedio=("precio_promedio_general", "mean"),
                )
                .reset_index()
                .sort_values("precio_promedio", ascending=False)
            )
            if not provs.empty:
                colours = [
                    theme.AMARILLO if p == province_filter else theme.CELESTE
                    for p in provs["provincia"]
                ]
                fig = go.Figure(
                    go.Bar(
                        x=provs["provincia"],
                        y=provs["precio_promedio"],
                        marker={"color": colours, "line": {"width": 2, "color": theme.SURFACE}},
                        customdata=provs[["sucursales"]],
                        hovertemplate=(
                            "%{x}<br>Prom. $%{y:,.0f}<br>%{customdata[0]} sucursal(es)<extra></extra>"
                        ),
                    )
                )
                theme.style(
                    fig, height=320, y_title="Precio promedio publicado ($)", show_legend=False
                )
                theme.chart(st, fig)
                theme.table_view(st, provs.round(2), f"provincias_{dataset_type}.csv")

# ---------------------------------------------------------------------------
# Pipeline health
# ---------------------------------------------------------------------------

with tab_health:
    theme.section(
        st,
        "Cobertura",
        "Qué días llegaron a la capa Gold y dónde están los huecos.",
        eyebrow="Actualidad",
    )

    calendar = da.coverage_calendar(all_dates, min_date, max_date)
    if calendar.empty:
        st.info("Sin información de cobertura.")
    else:
        missing = calendar[~calendar["presente"]]
        c1, c2, c3 = st.columns(3)
        with c1:
            theme.tile(st, "Días en el lake", str(int(calendar["presente"].sum())), small=True)
        with c2:
            theme.tile(
                st,
                "Días faltantes",
                str(len(missing)),
                foot="dentro de la ventana cargada",
                small=True,
            )
        with c3:
            theme.tile(
                st,
                "Ventana",
                f"{theme.fecha_corta(min_date)} – {theme.fecha_corta(max_date)}",
                small=True,
            )
        if not missing.empty:
            st.caption(
                "Huecos: "
                + ", ".join(theme.fecha_corta(d) for d in missing["fecha"])
                + f" · el portal guarda sólo {config.SOURCE_RETENTION_DAYS} días, "
                "así que los huecos más viejos no se pueden recuperar."
            )

    theme.section(
        st,
        "Filas procesadas",
        "Cuántas filas crudas del CSV sobrevivieron a la limpieza y la validación.",
        eyebrow="Volumen",
    )
    if quality.empty:
        st.info("No se encontraron reportes de calidad bajo el prefijo `_quality` de Silver.")
    else:
        q = quality.copy()
        fig = go.Figure()
        # Part-to-whole: stacked, categorical slots in fixed order, with a 2px
        # surface gap between segments instead of a border.
        fig.add_trace(
            go.Bar(
                x=q["fecha"],
                y=q["rows_written"],
                name="Conservadas",
                marker={"color": theme.CELESTE, "line": {"width": 2, "color": theme.SURFACE}},
                hovertemplate="%{x|%d/%m}<br>Conservadas %{y:,}<extra></extra>",
            )
        )
        fig.add_trace(
            go.Bar(
                x=q["fecha"],
                y=q["rows_dropped"],
                name="Descartadas",
                marker={"color": theme.AMARILLO, "line": {"width": 2, "color": theme.SURFACE}},
                hovertemplate="%{x|%d/%m}<br>Descartadas %{y:,}<extra></extra>",
            )
        )
        theme.style(fig, height=300, y_title="Filas")
        theme.daily_axis(fig, len(q))
        fig.update_layout(barmode="stack", hovermode="x unified")
        theme.chart(st, fig)

        cols = [
            c
            for c in [
                "date",
                "rows_read",
                "rows_written",
                "rows_dropped",
                "completeness_pct",
                "validation_failures",
                "retailers",
            ]
            if c in q.columns
        ]
        theme.table_view(st, q[cols], f"calidad_{dataset_type}.csv", "Ver tabla")

    theme.section(
        st, "Configuración", "Parámetros efectivos de este despliegue.", eyebrow="Ajustes"
    )
    st.json(
        {
            "dataset_origen": config.CKAN_DATASET_IDS[dataset_type],
            "dias_de_retencion": config.SOURCE_RETENTION_DAYS,
            "bucket": config.S3_BUCKET,
            "endpoint": config.S3_ENDPOINT_URL,
            "capas": "bronze (zip crudo) → silver (parquet) → gold (tablas de KPI)",
            "filas_por_chunk": config.CHUNK_SIZE,
            "filtro_de_categoria": config.ENABLE_CATEGORY_FILTER,
            "muestras_minimas_por_sucursal": config.MIN_STORE_SAMPLES,
            "banda_de_precios": [config.MIN_VALID_PRICE, config.MAX_VALID_PRICE],
        }
    )

# ---------------------------------------------------------------------------
# Pie de página
# ---------------------------------------------------------------------------

theme.footer(
    st,
    left=(
        f"Fuente: Precios Claros / SEPA · {config.CKAN_DATASET_IDS[dataset_type]} · "
        f"datos.produccion.gob.ar"
    ),
    right=f"Último dato: {theme.fecha(latest_date)} · Bronze → Silver → Gold sobre MinIO",
)
