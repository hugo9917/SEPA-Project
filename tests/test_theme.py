"""Tests for the chart chrome: palette invariants, label clipping, date ticks."""

import pandas as pd
import plotly.graph_objects as go
import pytest

from src import theme

# ---------------------------------------------------------------------------
# Palette invariants
# ---------------------------------------------------------------------------


def test_series_slots_are_unique_and_ordered():
    """Categorical hues are assigned in fixed order and never cycled -- the slot
    ordering is the CVD-safety mechanism, so duplicates would silently break it."""
    assert len(theme.SERIES) == len(set(theme.SERIES))
    assert theme.SERIES[:4] == [theme.CELESTE, theme.AMARILLO, theme.MAGENTA, theme.VIOLETA]


def test_palette_is_capped_at_the_validated_slot_count():
    """Only four slots were validated. A fifth would mean generating a hue,
    which is never the answer to "too many series"."""
    assert len(theme.SERIES) == 4


def test_flag_colours_are_brand_chrome_not_series():
    """The literal flag colours fail the checks (sun yellow is outside the
    lightness band, flag celeste reads grey), so they decorate chrome only."""
    assert theme.BRAND_CELESTE not in theme.SERIES
    assert theme.BRAND_AMARILLO not in theme.SERIES


def test_status_colours_are_not_reused_as_series():
    """A status colour must never impersonate a series."""
    assert not set(theme.STATUS.values()) & set(theme.SERIES)


def test_diverging_poles_are_warm_and_cool_with_a_neutral_midpoint():
    assert theme.DIVERGING_NEG != theme.DIVERGING_POS
    # The midpoint must read as "nothing": a near-neutral grey, not a hue.
    r, g, b = (int(theme.DIVERGING_MID[i : i + 2], 16) for i in (1, 3, 5))
    assert max(r, g, b) - min(r, g, b) < 12


def test_sequential_ramp_is_monotonic_and_single_hue():
    stops = [s for s, _ in theme.SEQUENTIAL]
    assert stops == sorted(stops)
    assert stops[0] == 0.0 and stops[-1] == 1.0
    # Light -> dark: total channel sum must strictly decrease.
    brightness = [sum(int(c[i : i + 2], 16) for i in (1, 3, 5)) for _, c in theme.SEQUENTIAL]
    assert brightness == sorted(brightness, reverse=True)


def test_price_deltas_never_use_green_for_a_rise():
    """A price going up is bad news for a shopper."""
    assert theme.DELTA_UP_BAD != theme.DELTA_DOWN_GOOD
    assert theme.DELTA_DOWN_GOOD.lower() == "#006300"


def test_template_uses_solid_hairline_gridlines():
    """Dashed grids read as 'projection' or 'threshold' when they are just a grid."""
    layout = theme.PLOTLY_TEMPLATE.layout
    assert layout.yaxis.griddash == "solid"
    assert layout.yaxis.gridcolor == theme.GRID
    assert layout.xaxis.showgrid is False  # recessive by default
    assert layout.paper_bgcolor == theme.SURFACE


# ---------------------------------------------------------------------------
# Outside labels
# ---------------------------------------------------------------------------


def _bar(values, horizontal=False):
    if horizontal:
        fig = go.Figure(go.Bar(x=values, y=list("abcdefgh"[: len(values)]), orientation="h"))
    else:
        fig = go.Figure(go.Bar(x=list("abcdefgh"[: len(values)]), y=values))
    return fig


def test_outside_labels_pads_the_value_axis_past_the_longest_bar():
    """Without the pad, the label on the longest bar is cropped at the axis."""
    fig = theme.outside_labels(_bar([10, 50, 100], horizontal=True), axis="x")
    axis_max = fig.layout.xaxis.range[1]
    assert axis_max > 100


def test_outside_labels_disables_clip_on_axis():
    fig = theme.outside_labels(_bar([1, 2, 3]), axis="y")
    assert all(trace.cliponaxis is False for trace in fig.data)


def test_outside_labels_keeps_a_zero_baseline_for_all_positive_data():
    fig = theme.outside_labels(_bar([10, 20, 30]), axis="y")
    assert fig.layout.yaxis.range[0] == 0


def test_outside_labels_pads_both_ends_for_diverging_data():
    fig = theme.outside_labels(_bar([-30, 10, 20]), axis="y")
    low, high = fig.layout.yaxis.range
    assert low < -30 and high > 20


def test_outside_labels_survives_an_all_zero_series():
    """A flat day (every change exactly 0.00%) must not collapse the range."""
    fig = theme.outside_labels(_bar([0, 0, 0]), axis="y")
    low, high = fig.layout.yaxis.range
    assert high > low


def test_outside_labels_ignores_non_bar_figures():
    fig = go.Figure(go.Scatter(x=[1, 2], y=[1, 2]))
    assert theme.outside_labels(fig) is fig


# ---------------------------------------------------------------------------
# Date axis
# ---------------------------------------------------------------------------


def test_daily_axis_forces_whole_day_ticks_on_short_ranges():
    """Plotly otherwise falls back to hourly ticks ('00:00 · 06:00') on a
    three-point daily series."""
    fig = theme.daily_axis(go.Figure(), n_days=3)
    assert fig.layout.xaxis.dtick == theme.DAY_MS
    assert fig.layout.xaxis.tickformat == "%d %b"


def test_daily_axis_lets_plotly_space_long_ranges():
    fig = theme.daily_axis(go.Figure(), n_days=90)
    assert fig.layout.xaxis.dtick is None
    assert fig.layout.xaxis.tickformat == "%d %b"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1234.5, "$1.234"), (12149.4, "$12.149"), (0, "$0"), (None, "—"), ("nope", "—")],
)
def test_ars_uses_es_ar_thousands_separator(value, expected):
    """es-AR groups thousands with a period, the reverse of the C locale."""
    assert theme.ars(value) == expected


def test_pct_uses_a_decimal_comma_and_is_signed_by_default():
    assert theme.pct(2.39) == "+2,39%"
    assert theme.pct(-2.5) == "-2,50%"
    assert theme.pct(2.5, signed=False) == "2,50%"
    assert theme.pct(7.0, 1, signed=False) == "7,0%"


def test_count_groups_thousands_with_a_period():
    assert theme.count(22433) == "22.433"
    assert theme.count(1234567) == "1.234.567"
    assert theme.count(None) == "—"


def test_numero_formats_a_plain_decimal():
    assert theme.numero(100.0, 1) == "100,0"
    assert theme.numero(None) == "—"


def test_separator_swap_is_atomic():
    """Two sequential replaces need a sentinel and corrupt on the round trip."""
    assert theme._es_number("1,234,567.89") == "1.234.567,89"


def test_fecha_uses_spanish_month_abbreviations():
    """Python's %b would emit the C locale's "Jul"."""
    day = pd.Timestamp("2026-07-21")
    assert theme.fecha(day) == "21 jul 2026"
    assert theme.fecha_corta(day) == "21 jul"
    assert theme.fecha(None) == "—"


def test_month_tables_are_complete():
    assert len(theme.MESES) == 12
    assert len(theme.MESES_LARGOS) == 12
    assert theme.MESES[0] == "ene" and theme.MESES[11] == "dic"


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


class _FakeStreamlit:
    """Captures markdown so components can be asserted without Streamlit."""

    def __init__(self):
        self.html = []
        self.sidebar = self

    def markdown(self, body, unsafe_allow_html=False):
        self.html.append(body)


def test_tile_renders_a_rail_and_a_delta_pill():
    st = _FakeStreamlit()
    theme.tile(st, "Índice", "100,0", delta=2.5, rail=theme.CELESTE, spark=[1, 2, 3])
    out = st.html[0]
    assert f'class="rail" style="background:{theme.CELESTE}"' in out
    assert "↑ +2,50%" in out
    assert theme.DELTA_UP_BAD in out  # a rise is never green
    assert "<svg" in out


def test_tile_uses_a_down_arrow_and_green_for_a_price_drop():
    st = _FakeStreamlit()
    theme.tile(st, "Índice", "98,0", delta=-2.5)
    assert "↓ -2,50%" in st.html[0]
    assert theme.DELTA_DOWN_GOOD in st.html[0]


def test_tile_marks_a_flat_delta_as_neutral():
    st = _FakeStreamlit()
    theme.tile(st, "Índice", "100,0", delta=0.0)
    assert "→" in st.html[0]


def test_tile_omits_the_delta_when_absent_or_nan():
    st = _FakeStreamlit()
    theme.tile(st, "Índice", "100,0")
    theme.tile(st, "Índice", "100,0", delta=float("nan"))
    assert all('class="delta"' not in body for body in st.html)


def test_components_escape_untrusted_text():
    """Province and product names come from the feed, not from us."""
    st = _FakeStreamlit()
    theme.tile(st, "<img src=x onerror=alert(1)>", "1", foot="<script>bad</script>")
    body = st.html[0]
    assert "<img" not in body and "<script>" not in body
    assert "&lt;img" in body

    assert "<b>" not in theme.chip("<b>x</b>")


def test_chip_variants():
    assert "chip accent" in theme.chip("Córdoba", accent=True)
    assert "dot live" in theme.chip("hoy", live=True)
    assert "Córdoba" in theme.chip("Córdoba")


def test_section_eyebrow_is_optional():
    st = _FakeStreamlit()
    theme.section(st, "Cobertura", "desc", eyebrow="Actualidad")
    theme.section(st, "Cobertura")
    assert "Actualidad" in st.html[0]
    assert "eyebrow" not in st.html[1]


def test_tint_converts_hex_to_rgba():
    assert theme.tint("#1b7fc4", 0.1) == "rgba(27,127,196,0.1)"


def test_plotly_config_hides_developer_chrome():
    assert theme.PLOTLY_CONFIG["displaylogo"] is False
    assert "lasso2d" in theme.PLOTLY_CONFIG["modeBarButtonsToRemove"]


# ---------------------------------------------------------------------------
# Hero panel
# ---------------------------------------------------------------------------


def test_hero_puts_the_headline_figure_above_the_ledger():
    """The hero exists to break the four-equal-tiles tie: one number leads and
    the rest support it, so the figure must precede the supporting rows."""
    st = _FakeStreamlit()
    theme.hero(
        st,
        kicker="Índice",
        label="Base 100",
        value="100,0",
        delta=2.5,
        spark=[1, 2, 3],
        stats=[("Precio", "$12.149", 2.39, "nota")],
    )
    body = st.html[0]
    assert body.index('class="hero-figure"') < body.index('class="s-row"')
    assert "100,0" in body and "$12.149" in body


def test_hero_delta_never_greens_a_price_rise():
    st = _FakeStreamlit()
    theme.hero(st, kicker="k", label="l", value="1", delta=2.5)
    assert theme.DELTA_UP_BAD in st.html[0]

    st2 = _FakeStreamlit()
    theme.hero(st2, kicker="k", label="l", value="1", delta=-2.5)
    assert theme.DELTA_DOWN_GOOD in st2.html[0]


def test_hero_stat_rows_tolerate_missing_delta_and_foot():
    """`stats` entries are padded, so a two-tuple must not raise."""
    st = _FakeStreamlit()
    theme.hero(st, kicker="k", label="l", value="1", stats=[("Solo", "42")])
    assert "Solo" in st.html[0] and "42" in st.html[0]


def test_hero_escapes_untrusted_text():
    st = _FakeStreamlit()
    theme.hero(
        st,
        kicker="<script>x</script>",
        label="l",
        value="1",
        foot="<img src=x onerror=y>",
        stats=[("<b>bold</b>", "1", None, "<i>i</i>")],
    )
    body = st.html[0]
    assert "<script>" not in body and "<img" not in body and "<b>bold" not in body


def test_hero_without_a_sparkline_still_renders():
    st = _FakeStreamlit()
    theme.hero(st, kicker="k", label="l", value="1")
    assert "hero-figure" in st.html[0] and "<svg" not in st.html[0]


def test_hero_delta_word_resets_inherited_tracking():
    """letter-spacing inherits as a computed *pixel* value, so the figure's
    -0.045em (-2.7px at 59px) lands on 12px text unchanged and overlaps the
    glyphs. Every small child of a display-size element must reset it."""
    assert ".hero-dw" in theme.CSS
    block = theme.CSS.split(".hero-dw")[1].split("}")[0]
    assert "letter-spacing: normal" in block


def test_flag_stripe_has_no_invisible_band():
    """A white band on a white card read as two disconnected bars."""
    stripe = theme.CSS.split(".hero::before")[1].split("}")[0]
    assert "#ffffff" not in stripe
    assert theme.BRAND_CELESTE in stripe and theme.BRAND_AMARILLO in stripe


class _FakeContainer:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ChartStreamlit(_FakeStreamlit):
    def __init__(self):
        super().__init__()
        self.figs = []
        self.captions = []

    def container(self, border=False):
        return _FakeContainer(self)

    def plotly_chart(self, fig, **kw):
        self.figs.append(fig)

    def caption(self, text):
        self.captions.append(text)


def test_chart_renders_its_title_inside_the_card():
    st = _ChartStreamlit()
    theme.chart(
        st, go.Figure(), title="Índice comparable", meta="19 – 21 jul", accent=theme.CELESTE
    )
    head = next(h for h in st.html if "card-head" in h)
    assert "Índice comparable" in head and "19 – 21 jul" in head
    assert theme.CELESTE in head
    assert len(st.figs) == 1


def test_chart_without_a_title_renders_only_the_marker():
    st = _ChartStreamlit()
    theme.chart(st, go.Figure())
    assert all("card-head" not in h for h in st.html)
    assert any("card-mark" in h for h in st.html)


def test_chart_escapes_its_title():
    st = _ChartStreamlit()
    theme.chart(st, go.Figure(), title="<b>x</b>", meta="<i>y</i>")
    head = next(h for h in st.html if "card-head" in h)
    assert "<b>" not in head and "<i>" not in head
