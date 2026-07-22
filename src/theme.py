"""Design tokens, Plotly template and small UI primitives for the dashboard.

Palette provenance
------------------
Celeste, blanco y amarillo -- the flag, stepped until it passes. See the note on
:data:`SERIES` for the measured numbers and why the literal flag colours cannot
carry data.

The app is pinned to a single light surface (``.streamlit/config.toml``) so
there is exactly one background to validate against.

Motion is all CSS: Streamlit strips ``<script>`` from injected HTML. Every
animation is written so the *resting* state is the finished one, which means a
paused timeline (background tab, reduced-motion, an old engine) shows the
finished UI rather than a half-drawn one.

Everything visual lives here. ``dashboard.py`` composes; it never spells out a
colour, a radius or a shadow of its own.
"""

import html as _html
import itertools

import plotly.graph_objects as go

# ---------------------------------------------------------------------------

# Tokens

# ---------------------------------------------------------------------------


SURFACE = "#ffffff"  # card / chart surface -- the "blanco"

PLANE = "#eef3f9"  # page plane: a barely-there celeste wash

INK = "#0d1526"

INK_SECONDARY = "#4a5768"

INK_MUTED = "#7d8a9c"

GRID = "#e2e9f1"

AXIS = "#c2ceda"

BORDER = "rgba(13, 21, 38, 0.10)"

BORDER_STRONG = "rgba(13, 21, 38, 0.17)"


# --- Categorical slots -------------------------------------------------------

# Celeste and amarillo, led by the flag but *stepped* to pass the checks: the

# literal flag colours fail outright (sun yellow #f6b40e sits at L 0.81, outside

# the 0.43-0.77 band; flag celeste #74acdf has chroma 0.095 and reads grey).

#

# Validated against this app's surface (#ffffff, light):

#   4 slots, adjacent pairlist   -> ALL CHECKS PASS

#     worst CVD ΔE 12.6 (protan) · worst normal-vision ΔE 15.9

#   first 3 slots, all-pairs     -> ALL CHECKS PASS

#     worst CVD ΔE 12.1 (deutan) · worst normal-vision ΔE 23.9

#   WARN: amarillo #d99a00 sits at 2.45:1 contrast -> relief required, which is

#         why every chart ships direct labels and a table-view twin.

#

# Capped at four on purpose. Green and red cannot sit adjacent (ΔE 4.8 deutan,

# the classic confusion), and a green/magenta pair lands in the 6-8 warn band,

# so the tail folds into "Other" rather than growing more hues.

SERIES = ["#1b7fc4", "#d99a00", "#b5427f", "#7b4fc9"]

CELESTE, AMARILLO, MAGENTA, VIOLETA = SERIES


# Brand chrome only -- decorative washes, never a data series, so the true flag

# colours are free to appear here at full strength.

BRAND_CELESTE = "#74acdf"

BRAND_AMARILLO = "#f6b40e"


# De-emphasis grey for the "one series is the point" (emphasis) form.

MUTED_MARK = "#ccd6e0"


# Diverging pair: warm/cool poles with a neutral grey midpoint. All-pairs PASS.

DIVERGING_NEG = "#1b7fc4"  # price fell

DIVERGING_POS = "#c9432c"  # price rose

DIVERGING_MID = "#eef0f2"


# Status tokens. Fixed by the design system, never themed, never reused as a

# series colour, always shipped with an icon and a label.

STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}


# Consumer semantics: a price going UP is bad news, so it is never green.

DELTA_UP_BAD = "#b3261e"

DELTA_DOWN_GOOD = "#006300"


# Single-hue celeste ramp, light -> dark. Monotone lightness, hue spread 1°.

SEQUENTIAL = [
    [0.0, "#dceaf7"],
    [0.25, "#8ab8e2"],
    [0.5, "#4d95d1"],
    [0.75, "#1b7fc4"],
    [1.0, "#0d4c78"],
]


FONT_STACK = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif'


# Elevation. Two soft, tightly-spread layers rather than one big blur: large

# diffuse shadows read as "floating card" clip-art, which is exactly the look

# this design is avoiding.

SHADOW_SM = "0 1px 2px rgba(19,55,92,.05), 0 1px 3px rgba(19,55,92,.04)"

SHADOW_MD = "0 2px 4px rgba(19,55,92,.06), 0 6px 16px rgba(19,55,92,.07)"

SHADOW_LG = "0 4px 8px rgba(19,55,92,.07), 0 14px 34px rgba(19,55,92,.11)"


RADIUS = "12px"

RADIUS_SM = "8px"


def tint(hex_colour, alpha):
    """``#rrggbb`` -> ``rgba(...)``, for pill and rail washes."""

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))

    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------

# Plotly template

# ---------------------------------------------------------------------------


def _axis(show_grid=True):
    return {
        "showgrid": show_grid,
        "gridcolor": GRID,
        "gridwidth": 1,
        "griddash": "solid",  # dashing reads as "projection", never a grid
        "zeroline": False,
        "linecolor": AXIS,
        "linewidth": 1,
        "ticks": "outside",
        "ticklen": 4,
        "tickcolor": AXIS,
        "tickfont": {"size": 12, "color": INK_MUTED},
        "title": {"font": {"size": 12, "color": INK_SECONDARY}},
        "automargin": True,
    }


PLOTLY_TEMPLATE = go.layout.Template(
    layout={
        "colorway": SERIES,
        "font": {"family": FONT_STACK, "size": 13, "color": INK_SECONDARY},
        "paper_bgcolor": SURFACE,
        "plot_bgcolor": SURFACE,
        "margin": {"l": 8, "r": 8, "t": 28, "b": 8},
        "xaxis": _axis(show_grid=False),
        "yaxis": _axis(show_grid=True),
        "colorscale": {"sequential": SEQUENTIAL},
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "title": {"text": ""},
            "font": {"size": 12, "color": INK_SECONDARY},
            "bgcolor": "rgba(0,0,0,0)",
        },
        "hoverlabel": {
            "bgcolor": SURFACE,
            "bordercolor": AXIS,
            "font": {"family": FONT_STACK, "size": 12, "color": INK},
        },
        "title": {
            "font": {"size": 14, "color": INK, "family": FONT_STACK},
            "x": 0,
            "xanchor": "left",
        },
    }
)


def outside_labels(fig, axis="y", pad=0.20):
    """Stop ``textposition="outside"`` bar labels from being clipped.

    Plotly clips text at the axis by default, so the last characters of a value
    sitting past the longest bar get cropped. Turning off ``cliponaxis`` and
    padding the value axis is the fix; without it the widest bar in every
    horizontal chart loses its label.
    """

    fig.update_traces(cliponaxis=False, selector={"type": "bar"})

    values = []

    for trace in fig.data:
        if trace.type != "bar":
            continue

        series = trace.x if axis == "x" else trace.y

        if series is not None:
            values.extend(v for v in series if v is not None)

    if not values:
        return fig

    low, high = min(values), max(values)

    span = (high - low) or abs(high) or 1

    lower = low - span * pad if low < 0 else 0

    fig.update_layout(**{f"{axis}axis": {"range": [lower, high + span * pad]}})

    return fig


DAY_MS = 86_400_000


def daily_axis(fig, n_days):
    """Force whole-day ticks on a date axis.

    With only a handful of daily points Plotly falls back to an hourly tick
    scale ("00:00 · 06:00 · 12:00"), which is meaningless for a daily feed and
    triples the label count. Below a fortnight every day gets its own tick;
    beyond that Plotly picks the spacing but keeps the day-month format.
    """

    fig.update_xaxes(tickformat="%d %b")

    if n_days and n_days <= 14:
        fig.update_xaxes(dtick=DAY_MS)

    return fig


def style(fig, height=320, y_title="", x_title="", show_legend=None):
    """Apply the house layout to a figure built by plotly.express."""

    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        height=height,
        xaxis_title=x_title,
        yaxis_title=y_title,
    )

    if show_legend is not None:
        fig.update_layout(showlegend=show_legend)

    return fig


# ---------------------------------------------------------------------------

# Formatting

# ---------------------------------------------------------------------------


# es-AR number conventions: a period groups thousands and a comma marks the

# decimal, the reverse of the C locale Python formats with. Done by hand rather

# than through `locale.setlocale`, which needs the locale generated inside the

# image and is process-global (it would silently reformat the ETL's logs too).

MESES = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)

MESES_LARGOS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


# A single translate swaps both separators at once; doing it with two
# sequential replaces needs a sentinel and corrupts on the round trip.
_SEPARADORES = str.maketrans({",": ".", ".": ","})


def _es_number(text):
    """``1,234.56`` (C locale) -> ``1.234,56`` (es-AR)."""
    return text.translate(_SEPARADORES)


def ars(value, decimals=0):
    """Pesos, es-AR: ``$12.149``."""

    if value is None:
        return "—"

    try:
        return "$" + _es_number(f"{value:,.{decimals}f}")

    except (TypeError, ValueError):
        return "—"


def pct(value, decimals=2, signed=True):
    """Percentage, es-AR: ``+2,39 %``."""

    if value is None:
        return "—"

    try:
        raw = f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"

        return _es_number(raw) + "%"

    except (TypeError, ValueError):
        return "—"


def count(value):
    """Whole number, es-AR: ``22.433``."""

    if value is None:
        return "—"

    try:
        return _es_number(f"{int(value):,}")

    except (TypeError, ValueError):
        return "—"


def numero(value, decimals=1):
    """Plain decimal, es-AR: ``100,0``."""
    if value is None:
        return "—"
    try:
        return _es_number(f"{value:,.{decimals}f}")
    except (TypeError, ValueError):
        return "—"


def fecha(value, con_anio=True):
    """``21 jul 2026`` -- Python's %b would emit the C locale's "Jul"."""

    if value is None:
        return "—"

    try:
        mes = MESES[value.month - 1]

        return f"{value.day} {mes} {value.year}" if con_anio else f"{value.day} {mes}"

    except (AttributeError, IndexError):
        return "—"


def fecha_corta(value):
    """``21 jul``."""

    return fecha(value, con_anio=False)


# ---------------------------------------------------------------------------

# Sparkline

# ---------------------------------------------------------------------------


_spark_seq = itertools.count()


def sparkline(values, colour=None, width=240, height=52):
    """Inline SVG trend for a stat tile, drawn full-bleed across its base.

    A stat tile is "value + delta + sparkline". A small square of chart floating
    beside the number reads as an afterthought; running it edge to edge under
    the figure makes the tile one object.

    The polyline declares ``pathLength="1"`` so CSS can draw it on entry with a
    single pair of dash values, no JavaScript and no per-element inline style
    (Streamlit strips ``<script>``, and an inline ``stroke-dashoffset`` outranks
    the keyframes, which silently froze the line fully hidden).
    """

    colour = colour or CELESTE

    clean = [float(v) for v in (values or []) if v is not None and v == v]

    if len(clean) < 2:
        return ""

    low, high = min(clean), max(clean)

    step = width / (len(clean) - 1)

    pad = 6

    if high == low:
        # A flat series has no span to normalise against. Drawing it at the

        # baseline made "100.0" tiles look like they carried a stray underline;

        # a mid-height line reads as "steady", which is what it means.

        points = [(i * step, height / 2) for i in range(len(clean))]

    else:
        span = high - low

        points = [
            (i * step, height - pad - ((v - low) / span) * (height - 2 * pad))
            for i, v in enumerate(clean)
        ]

    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    area = (
        f"M0,{height:.1f} L"
        + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
        + f" L{width:.1f},{height:.1f} Z"
    )

    last_x, last_y = points[-1]

    gid = f"sg{next(_spark_seq)}"

    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{colour}" stop-opacity=".26"/>'
        f'<stop offset="100%" stop-color="{colour}" stop-opacity="0"/>'
        f"</linearGradient></defs>"
        f'<path class="spark-area" d="{area}" fill="url(#{gid})"/>'
        f'<polyline class="spark-line" points="{line}" pathLength="1" '
        f'fill="none" stroke="{colour}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
        f'<circle class="spark-dot" cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" '
        f'fill="{colour}" stroke="{SURFACE}" stroke-width="2" '
        f'vector-effect="non-scaling-stroke"/>'
        "</svg>"
    )


# ---------------------------------------------------------------------------

# CSS

# ---------------------------------------------------------------------------


CSS = f"""
<style>
  :root {{
    --surface: {SURFACE};
    --plane: {PLANE};
    --ink: {INK};
    --ink-2: {INK_SECONDARY};
    --ink-muted: {INK_MUTED};
    --border: {BORDER};
    --border-strong: {BORDER_STRONG};
    --accent: {CELESTE};
    --accent-wash: {tint(CELESTE, 0.08)};
    --radius: {RADIUS};
    --radius-sm: {RADIUS_SM};
    --shadow-sm: {SHADOW_SM};
    --shadow-md: {SHADOW_MD};
    --shadow-lg: {SHADOW_LG};
  }}

  html, body, [class*="css"], button, input, select, textarea {{
    font-family: {FONT_STACK};
    -webkit-font-smoothing: antialiased;
  }}

  .stApp {{ background: var(--plane); }}
  .block-container {{ padding-top: 1.4rem; padding-bottom: 4rem; max-width: 1480px; }}
  #MainMenu, footer, [data-testid="stDecoration"] {{ display: none; }}

  /* ---------- Masthead ---------- */
  .masthead {{
    display: flex; align-items: flex-end; justify-content: space-between;
    gap: 1.5rem; flex-wrap: wrap;
    padding: .1rem 0 1.15rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 1.5rem;
  }}
  .masthead .lockup {{ display: flex; align-items: center; gap: .8rem; }}
  .masthead .mark {{
    width: 38px; height: 38px; border-radius: 11px; flex: none;
    display: grid; place-items: center; font-size: 1.15rem;
    background: linear-gradient(150deg, {BRAND_CELESTE} 0%, {BRAND_CELESTE} 55%,
                                {BRAND_AMARILLO} 55%, {BRAND_AMARILLO} 100%);
    border: 1px solid {tint(CELESTE, 0.30)};
    color: #fff; font-weight: 700; letter-spacing: -.02em;
    box-shadow: inset 0 1px 0 rgba(255,255,255,.35);
  }}
  .masthead h1 {{
    font-size: 1.44rem; font-weight: 640; letter-spacing: -0.024em;
    color: var(--ink); margin: 0; line-height: 1.18;
  }}
  .masthead .kicker {{
    font-size: .71rem; font-weight: 700; letter-spacing: .11em;
    text-transform: uppercase; color: {CELESTE}; margin-bottom: .2rem;
  }}
  .masthead .meta {{ display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; }}

  /* ---------- Chips ---------- */
  .chip {{
    position: relative; overflow: hidden;
    display: inline-flex; align-items: center; gap: .38rem;
    padding: .26rem .68rem; border-radius: 999px;
    font-size: .745rem; font-weight: 580; line-height: 1.3;
    border: 1px solid var(--border); color: var(--ink-2);
    background: linear-gradient(180deg, #fff, {tint(CELESTE, 0.05)});
    box-shadow: var(--shadow-sm); white-space: nowrap;
    animation: riseIn .4s .12s cubic-bezier(.22,.68,.32,1) both;
  }}
  /* One slow sheen sweep on entry -- a highlight passing over glass. */
  .chip::after {{
    content: ""; position: absolute; top: 0; bottom: 0; left: -60%; width: 40%;
    background: linear-gradient(90deg, transparent,
                rgba(255,255,255,.75), transparent);
    transform: skewX(-18deg);
    animation: sheen 1.5s .5s cubic-bezier(.4,0,.2,1) 1;
  }}
  .chip.accent {{
    border-color: {tint(CELESTE, 0.28)}; color: {CELESTE}; background: var(--accent-wash);
  }}
  .chip .dot {{
    width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none;
  }}
  .chip .dot.live {{ background: {STATUS["good"]}; }}

  /* ---------- Motion ----------
     Entrance motion is a 320ms rise+fade, staggered across a row so the eye
     lands on the first tile first. Everything here is CSS: Streamlit strips
     <script>, so the sparkline draws itself via stroke-dashoffset. */
  @keyframes riseIn {{
    from {{ opacity: 0; transform: translateY(10px); }}
    to   {{ opacity: 1; transform: none; }}
  }}
  /* from-hidden, not to-visible: the resting state must be the drawn line, so
     a paused timeline (background tab, reduced motion, an old engine) shows the
     sparkline rather than hiding it. */
  @keyframes drawLine {{ from {{ stroke-dashoffset: 1; }} to {{ stroke-dashoffset: 0; }} }}
  @keyframes fadeIn   {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
  @keyframes popDot   {{
    from {{ opacity: 0; transform: scale(.2); }}
    to   {{ opacity: 1; transform: scale(1); }}
  }}
  @keyframes sheen    {{ to {{ transform: translateX(280%) skewX(-18deg); }} }}

  /* ---------- Hero panel ----------
     A real grid with a rule down the middle: the index at display size over its
     own area chart, the supporting figures as a ledger beside it. */
  .hero {{
    position: relative; overflow: hidden;
    display: grid;
    grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
    gap: 0;
    background:
      radial-gradient(120% 140% at 0% 0%, {tint(BRAND_CELESTE, 0.20)} 0%,
                      rgba(255,255,255,0) 58%),
      linear-gradient(180deg, #ffffff 0%, {tint(CELESTE, 0.05)} 100%);
    border: 1px solid var(--border);
    border-radius: 16px;
    box-shadow: var(--shadow-md);
    margin-bottom: 1.5rem;
    animation: riseIn .45s cubic-bezier(.22,.68,.32,1) both;
  }}
  /* Flag stripe: celeste / blanco / amarillo, the one place the literal flag
     colours appear. Decorative only -- nothing is encoded in it. */
  .hero::before {{
    content: ""; position: absolute; inset: 0 0 auto 0; height: 3px;
    background: linear-gradient(90deg,
      {BRAND_CELESTE} 0%, {BRAND_CELESTE} 50%,
      {BRAND_AMARILLO} 50%, {BRAND_AMARILLO} 100%);
  }}
  /* Flex column so the area chart sits flush with the panel's base however
     tall the ledger beside it grows. */
  .hero-main {{
    padding: 1.6rem 1.8rem 0; min-width: 0;
    display: flex; flex-direction: column;
  }}

  .hero-kicker {{
    font-size: .68rem; font-weight: 700; letter-spacing: .13em;
    text-transform: uppercase; color: {CELESTE}; margin-bottom: .75rem;
  }}
  .hero-label {{
    font-size: .8rem; font-weight: 600; color: var(--ink-2);
    letter-spacing: .01em; margin-bottom: .1rem;
  }}
  .hero-figure {{
    display: flex; align-items: baseline; gap: .7rem; flex-wrap: wrap;
    font-size: 3.7rem; font-weight: 680; letter-spacing: -0.045em;
    color: var(--ink); line-height: 1.02;
  }}
  .hero-delta {{
    font-size: .92rem; font-weight: 650; letter-spacing: -0.01em;
    padding: .2rem .6rem; border-radius: 999px;
    animation: fadeIn .5s .25s both;
  }}
  /* letter-spacing inherits as a computed *pixel* value, so the -0.045em the
     figure sets (-2.7px at 59px) lands on this 12px text unchanged and overlaps
     the glyphs. Every small child of a display-size element has to reset it. */
  .hero-dw {{
    font-size: .78rem; color: var(--ink-muted); font-weight: 450;
    letter-spacing: normal;
  }}
  .hero-foot {{
    font-size: .78rem; color: var(--ink-muted); margin-top: .35rem;
  }}
  /* The area chart bleeds out of the panel's padding to the left edge and
     under the divider on the right -- inset by the padding it read as a chart
     dropped into a box rather than part of the panel. */
  .hero .spark {{
    display: block; height: 104px;
    width: calc(100% + 3.6rem);
    /* `auto` top margin pushes it to the base of the flex column; the negative
       side margins escape the panel padding. One shorthand, because a later
       `margin:` would silently reset a separate `margin-top: auto`. */
    margin: auto -1.8rem 0;
  }}

  /* Ledger of supporting figures. */
  .hero-side {{
    border-left: 1px solid var(--border);
    display: flex; flex-direction: column; justify-content: center;
    padding: 1.4rem 0 1.4rem 0; min-width: 0;
  }}
  .s-row {{ padding: .62rem 1.6rem; border-bottom: 1px solid var(--border); }}
  .s-row:last-child {{ border-bottom: none; }}
  .s-label {{
    font-size: .655rem; font-weight: 650; letter-spacing: .1em;
    text-transform: uppercase; color: var(--ink-muted);
  }}
  .s-val {{
    display: flex; align-items: baseline; gap: .5rem; flex-wrap: wrap;
    font-size: 1.32rem; font-weight: 640; letter-spacing: -0.022em;
    color: var(--ink); margin-top: .12rem; white-space: nowrap;
  }}
  .s-delta {{ font-size: .78rem; font-weight: 620; letter-spacing: normal; }}
  .s-foot {{ font-size: .715rem; color: var(--ink-muted); margin-top: .05rem; }}

  .hero-chips {{
    position: absolute; top: 1.35rem; right: 1.6rem;
    display: flex; gap: .35rem; flex-wrap: wrap;
  }}

  @media (max-width: 900px) {{
    .hero {{ grid-template-columns: 1fr; }}
    .hero-side {{ border-left: none; border-top: 1px solid var(--border); }}
    .hero-figure {{ font-size: 2.9rem; }}
    .hero-chips {{ position: static; padding: 0 1.8rem 1rem; }}
  }}

  /* ---------- Stat tiles ---------- */
  .tile {{
    position: relative; overflow: hidden;
    background:
      linear-gradient(168deg, {tint(CELESTE, 0.055)} 0%,
                              rgba(255,255,255,0) 42%),
      var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow-sm);
    padding: 1rem 1.15rem 3.4rem;   /* room for the full-bleed sparkline */
    min-height: 178px;
    display: flex; flex-direction: column;
    animation: riseIn .38s cubic-bezier(.22,.68,.32,1) both;
    transition: box-shadow .28s cubic-bezier(.22,.68,.32,1),
                transform .28s cubic-bezier(.22,.68,.32,1),
                border-color .28s ease;
  }}
  /* Inner top highlight: the detail that stops a white card reading as a box. */
  .tile::after {{
    content: ""; position: absolute; inset: 0 0 auto 0; height: 1px;
    background: linear-gradient(90deg, rgba(255,255,255,0),
                rgba(255,255,255,.9), rgba(255,255,255,0));
    pointer-events: none;
  }}
  .tile:hover {{
    box-shadow: var(--shadow-lg);
    border-color: {tint(CELESTE, 0.34)};
    transform: translateY(-3px);
  }}
  .tile:hover .spark-area {{ opacity: 1; }}
  .tile:hover .rail {{ width: 5px; }}

  /* Stagger the KPI row. */
  [data-testid="stColumn"]:nth-of-type(1) .tile {{ animation-delay: .02s; }}
  [data-testid="stColumn"]:nth-of-type(2) .tile {{ animation-delay: .09s; }}
  [data-testid="stColumn"]:nth-of-type(3) .tile {{ animation-delay: .16s; }}
  [data-testid="stColumn"]:nth-of-type(4) .tile {{ animation-delay: .23s; }}

  /* Accent rail: a gradient edge tying the tile to its series colour. A real
     element rather than ::before + a custom property, because Streamlit's HTML
     sanitiser strips `--custom: value` out of inline style attributes. */
  .tile .rail {{
    position: absolute; inset: 0 auto 0 0; width: 3px;
    transition: width .28s cubic-bezier(.22,.68,.32,1);
    -webkit-mask-image: linear-gradient(180deg, #000 0%, #000 55%, transparent 100%);
    mask-image: linear-gradient(180deg, #000 0%, #000 55%, transparent 100%);
  }}

  .tile .label {{
    font-size: .688rem; font-weight: 650; letter-spacing: .085em;
    text-transform: uppercase; color: var(--ink-muted);
    display: flex; align-items: center; gap: .45rem;
    /* One line, always. A label that wraps makes its tile taller than the rest
       of the KPI row -- which is what the Spanish labels did, being longer than
       the English ones they replaced. */
    white-space: nowrap; overflow: hidden;
  }}
  .tile .label .txt {{ overflow: hidden; text-overflow: ellipsis; }}
  .tile .label .dot {{
    width: 7px; height: 7px; border-radius: 50%; flex: none;
    box-shadow: 0 0 0 3px rgba(255,255,255,.9);
  }}
  .tile .row {{ display: flex; align-items: baseline; margin: .5rem 0 0; }}
  /* Fixed height, always rendered: the delta used to wrap onto its own line
     inside .row on narrow tiles, leaving the KPI row 29px ragged. */
  .tile .metaline {{
    display: flex; align-items: center; gap: .1rem;
    height: 1.78rem; margin-top: .2rem;
  }}
  /* Proportional figures on display numbers: tabular-nums makes a large
     standalone value look loose. Tabular is for aligned columns only. */
  .tile .value {{
    font-size: 2.3rem; font-weight: 660; letter-spacing: -0.035em;
    color: var(--ink); line-height: 1;
    white-space: nowrap;
  }}
  .tile .value.small {{ font-size: 1.72rem; }}
  .tile .delta {{
    display: inline-flex; align-items: center; gap: .28rem;
    padding: .16rem .5rem; border-radius: 999px;
    font-size: .765rem; font-weight: 640; line-height: 1.3;
    animation: fadeIn .5s .2s both;
  }}
  .tile .delta-word {{
    font-size: .735rem; color: var(--ink-muted); margin-left: .1rem;
  }}
  .tile .foot {{
    font-size: .748rem; color: var(--ink-muted); line-height: 1.45;
    margin-top: .55rem; position: relative; z-index: 1;
    /* Reserve two lines so a longer footnote cannot push one tile taller. */
    min-height: 2.15rem;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }}

  /* Full-bleed sparkline pinned to the base of the tile. */
  .tile .spark {{
    position: absolute; left: 0; right: 0; bottom: 0;
    width: 100%; height: 52px; display: block; pointer-events: none;
  }}
  .tile .spark-area {{ opacity: .82; transition: opacity .28s ease; }}
  .tile .spark-line {{
    stroke-dasharray: 1; stroke-dashoffset: 0;
    animation: drawLine 1.15s .18s cubic-bezier(.4,0,.2,1) backwards;
  }}
  .tile .spark-dot  {{ animation: popDot .3s .95s backwards; }}

  /* ---------- Section headings ---------- */
  .sec {{
    margin: 1.7rem 0 .8rem; padding-left: .78rem; position: relative;
    animation: riseIn .38s cubic-bezier(.22,.68,.32,1) both;
  }}
  .sec::before {{
    content: ""; position: absolute; left: 0; top: .18rem; bottom: .18rem;
    width: 3px; border-radius: 2px;
    background: linear-gradient(180deg, {CELESTE}, {tint(CELESTE, 0.15)});
  }}
  .sec .eyebrow {{
    font-size: .672rem; font-weight: 700; letter-spacing: .11em;
    text-transform: uppercase; color: {CELESTE}; margin-bottom: .28rem;
  }}
  .sec h3 {{
    font-size: 1.02rem; font-weight: 630; color: var(--ink);
    margin: 0; letter-spacing: -0.014em;
  }}
  .sec p {{
    font-size: .824rem; color: var(--ink-2); margin: .3rem 0 0;
    max-width: 74ch; line-height: 1.55;
  }}

  /* ---------- Chart cards ----------
     Scoped with :has() to containers that carry our marker. Streamlit reuses
     stVerticalBlockBorderWrapper for plain layout blocks too -- styling it
     blanket-fashion painted a surface and a shadow onto 39 wrappers when only
     10 were real cards. */
  [data-testid="stVerticalBlockBorderWrapper"]:has(
      > div > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] .card-mark
  ) {{
    background:
      linear-gradient(168deg, {tint(CELESTE, 0.04)} 0%, rgba(255,255,255,0) 38%),
      var(--surface);
    border-radius: var(--radius) !important;
    border-color: var(--border) !important;
    box-shadow: var(--shadow-sm);
    animation: riseIn .4s cubic-bezier(.22,.68,.32,1) both;
    transition: box-shadow .28s cubic-bezier(.22,.68,.32,1),
                border-color .28s ease, transform .28s cubic-bezier(.22,.68,.32,1);
  }}
  [data-testid="stVerticalBlockBorderWrapper"]:has(
      > div > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] .card-mark
  ):hover {{
    box-shadow: var(--shadow-md); border-color: {tint(CELESTE, 0.28)};
    transform: translateY(-2px);
  }}
  /* The marker itself must take up no room. */
  .card-mark {{ display: none; }}
  [data-testid="stElementContainer"]:has(.card-mark),
  [data-testid="stMarkdown"]:has(.card-mark) {{ display: none !important; }}

  /* ---------- Card header ----------
     The chart names itself instead of leaning on a heading that floats above an
     anonymous box. */
  .card-head {{
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 1rem; flex-wrap: wrap;
    padding: 0 .15rem .68rem; margin-bottom: .25rem;
    border-bottom: 1px solid var(--border);
  }}
  .ch-title {{
    display: flex; align-items: center; gap: .45rem;
    font-size: .875rem; font-weight: 640; color: var(--ink);
    letter-spacing: -0.01em;
  }}
  .ch-dot {{ width: 8px; height: 8px; border-radius: 50%; flex: none; }}
  .ch-meta {{ font-size: .735rem; color: var(--ink-muted); letter-spacing: normal; }}

  /* ---------- Tabs: segmented control ---------- */
  .stTabs [data-baseweb="tab-list"] {{
    gap: .18rem; border-bottom: none; background: {tint(INK, 0.045)};
    padding: .28rem; border-radius: 11px; width: fit-content; max-width: 100%;
    flex-wrap: wrap;
  }}
  .stTabs [data-baseweb="tab"] {{
    height: 2.1rem; padding: 0 .85rem; border-radius: 8px;
    font-size: .845rem; font-weight: 560; color: var(--ink-2);
    background: transparent; transition: background .16s ease, color .16s ease;
  }}
  .stTabs [data-baseweb="tab"]:hover {{ color: var(--ink); background: {tint(INK, 0.04)}; }}
  .stTabs [aria-selected="true"] {{
    color: var(--ink) !important; background: var(--surface) !important;
    box-shadow: var(--shadow-md);
  }}
  .stTabs [data-baseweb="tab-panel"] > div {{
    animation: fadeIn .32s cubic-bezier(.22,.68,.32,1) both;
  }}
  .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none; }}
  .stTabs [data-baseweb="tab-panel"] {{ padding-top: .55rem; }}

  /* ---------- Sidebar ---------- */
  section[data-testid="stSidebar"] {{
    background: var(--surface); border-right: 1px solid var(--border);
  }}
  section[data-testid="stSidebar"] .block-container {{ padding-top: 1.35rem; }}
  .side-brand {{
    display: flex; align-items: center; gap: .6rem;
    padding-bottom: .85rem; margin-bottom: .95rem;
    border-bottom: 1px solid var(--border);
  }}
  .side-brand .mark {{
    width: 30px; height: 30px; border-radius: 9px; flex: none;
    display: grid; place-items: center; font-size: .95rem;
    background: linear-gradient(150deg, {BRAND_CELESTE} 0%, {BRAND_CELESTE} 55%,
                                {BRAND_AMARILLO} 55%, {BRAND_AMARILLO} 100%);
    border: 1px solid {tint(CELESTE, 0.30)};
    color: #fff; font-weight: 700; font-size: .8rem;
  }}
  .side-brand .name {{ font-size: .875rem; font-weight: 640; color: var(--ink); line-height: 1.2; }}
  .side-brand .role {{ font-size: .715rem; color: var(--ink-muted); }}
  .side-label {{
    font-size: .662rem; font-weight: 650; letter-spacing: .1em;
    text-transform: uppercase; color: var(--ink-muted);
    margin: 1.05rem 0 .3rem;
  }}

  /* ---------- Controls ---------- */
  [data-baseweb="select"] > div, .stTextInput input, .stNumberInput input {{
    border-radius: var(--radius-sm) !important;
    border-color: var(--border-strong) !important;
    background: var(--surface) !important;
    font-size: .862rem !important;
  }}
  [data-baseweb="select"] > div:hover, .stTextInput input:hover {{
    border-color: {tint(CELESTE, 0.45)} !important;
  }}
  .stTextInput input:focus {{
    border-color: {CELESTE} !important; box-shadow: 0 0 0 3px var(--accent-wash) !important;
  }}
  .stButton button, [data-testid="stDownloadButton"] button, [data-testid="stBaseButton-secondary"] {{
    border-radius: var(--radius-sm); border: 1px solid var(--border-strong);
    font-size: .838rem; font-weight: 560; color: var(--ink-2);
    background: var(--surface); transition: all .16s ease;
  }}
  .stButton button:hover, [data-testid="stDownloadButton"] button:hover {{
    border-color: {tint(CELESTE, 0.5)}; color: {CELESTE}; background: var(--accent-wash);
    transform: translateY(-1px); box-shadow: var(--shadow-md);
  }}
  .stButton button:active {{ transform: translateY(0); }}
  .stButton button[kind="primary"] {{
    background: {CELESTE}; border-color: {CELESTE}; color: #fff;
  }}
  .stButton button[kind="primary"]:hover {{ background: #1f65bb; color: #fff; }}
  label p {{ font-size: .8rem !important; font-weight: 540 !important; color: var(--ink-2) !important; }}

  /* ---------- Expanders & tables ---------- */
  [data-testid="stExpander"] details {{
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    background: var(--surface); box-shadow: none;
  }}
  [data-testid="stExpander"] summary {{ font-size: .818rem; color: var(--ink-2); }}
  [data-testid="stExpander"] summary:hover {{ color: {CELESTE}; }}
  .stDataFrame {{ border-radius: var(--radius-sm); overflow: hidden; }}

  [data-testid="stAlert"] {{ border-radius: var(--radius-sm); font-size: .855rem; }}
  .footer {{
    display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
    margin-top: 2.6rem; padding-top: .9rem;
    border-top: 1px solid var(--border);
    font-size: .742rem; color: var(--ink-muted);
  }}
  .footer .ft-right {{ text-align: right; }}

  hr {{ border-color: var(--border); }}

  .masthead {{ animation: riseIn .4s cubic-bezier(.22,.68,.32,1) both; }}
  .masthead .mark {{ box-shadow: 0 4px 12px {tint(CELESTE, 0.32)}; }}
  section[data-testid="stSidebar"] {{
    background: linear-gradient(180deg, #fff 0%, {tint(CELESTE, 0.045)} 100%);
  }}

  /* Motion is decoration; honour the setting that asks for none. */
  @media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{
      animation-duration: .001ms !important; animation-delay: 0ms !important;
      transition-duration: .001ms !important;
    }}
    .tile .spark-line {{ stroke-dashoffset: 0 !important; }}
  }}

  /* Streamlit wraps our masthead <h1> in its heading machinery and appends an
     anchor-link button. Hide it so the lockup stays tight. */
  .masthead [data-testid="stHeaderActionElements"] {{ display: none; }}
  .masthead [data-testid="stHeadingWithActionElements"] {{ padding: 0; }}

  /* The date slider prints its bounds twice: once on the thumbs and once as a
     min/max row underneath. Drop the duplicate row. */
  section[data-testid="stSidebar"] [data-testid="stSliderTickBarMin"],
  section[data-testid="stSidebar"] [data-testid="stSliderTickBarMax"] {{ display: none; }}

  /* Inline code in the sidebar caption was wrapping mid-URL. */
  section[data-testid="stSidebar"] code {{
    font-size: .72rem; background: {tint(CELESTE, 0.09)}; color: {CELESTE};
    padding: .05rem .3rem; border-radius: 5px; word-break: break-all;
  }}
  .stSlider label p {{ font-weight: 540 !important; }}

  /* Charts keep their own surface flush with the card. */
  .js-plotly-plot .plotly .modebar {{ opacity: 0; transition: opacity .2s ease; }}
  [data-testid="stVerticalBlockBorderWrapper"]:hover .modebar {{ opacity: .55; }}
</style>
"""


def inject_css(st):
    st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------

# Components

# ---------------------------------------------------------------------------


def _esc(value):
    return _html.escape(str(value), quote=True)


def masthead(st, kicker, title, chips=(), mark="🇦🇷"):
    """Brand lockup on the left, status chips on the right."""

    chip_html = "".join(chips)

    st.markdown(
        f'<div class="masthead">'
        f'  <div class="lockup">'
        f'    <div class="mark">{mark}</div>'
        f'    <div><div class="kicker">{_esc(kicker)}</div>'
        f"    <h1>{_esc(title)}</h1></div>"
        f"  </div>"
        f'  <div class="meta">{chip_html}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def chip(text, accent=False, live=False):
    dot = '<span class="dot live"></span>' if live else ""

    cls = "chip accent" if accent else "chip"

    return f'<span class="{cls}">{dot}{_esc(text)}</span>'


def sidebar_brand(st, name, role, mark="🇦🇷"):
    st.sidebar.markdown(
        f'<div class="side-brand"><div class="mark">{mark}</div>'
        f'<div><div class="name">{_esc(name)}</div>'
        f'<div class="role">{_esc(role)}</div></div></div>',
        unsafe_allow_html=True,
    )


def sidebar_label(st, text):
    st.sidebar.markdown(f'<div class="side-label">{_esc(text)}</div>', unsafe_allow_html=True)


def section(st, title, description=None, eyebrow=None):
    body = f'<div class="eyebrow">{_esc(eyebrow)}</div>' if eyebrow else ""

    body += f"<h3>{_esc(title)}</h3>"

    if description:
        body += f"<p>{_esc(description)}</p>"

    st.markdown(f'<div class="sec">{body}</div>', unsafe_allow_html=True)


def tile(
    st,
    label,
    value,
    delta=None,
    delta_word=None,
    up_is_bad=True,
    foot=None,
    small=False,
    rail=None,
    spark=None,
    spark_colour=None,
):
    """A stat tile: label, value, optional delta pill, sparkline and footnote.

    ``delta`` is a percentage. The arrow glyph plus ``delta_word`` carry the
    direction, so colour is reinforcement rather than the only signal.
    """

    rail_html = f'<span class="rail" style="background:{rail}"></span>' if rail else ""

    dot = f'<span class="dot" style="background:{rail}"></span>' if rail else ""

    delta_html = ""

    if delta is not None and delta == delta:  # not NaN
        if abs(delta) < 0.005:
            colour, arrow = INK_MUTED, "→"

        elif delta > 0:
            colour, arrow = (DELTA_UP_BAD if up_is_bad else DELTA_DOWN_GOOD), "↑"

        else:
            colour, arrow = (DELTA_DOWN_GOOD if up_is_bad else DELTA_UP_BAD), "↓"

        word = f'<span class="delta-word">{_esc(delta_word)}</span>' if delta_word else ""

        delta_html = (
            f'<span class="delta" style="color:{colour};'
            f'background:{tint(colour, 0.10)}">{arrow} {pct(delta)}</span>{word}'
        )

    spark_html = sparkline(spark, spark_colour or rail or CELESTE) if spark else ""

    foot_html = f'<div class="foot">{_esc(foot)}</div>' if foot else ""

    cls = "value small" if small else "value"

    st.markdown(
        f'<div class="tile">{rail_html}'
        f'<div class="label">{dot}<span class="txt">{_esc(label)}</span></div>'
        # The delta gets its own fixed-height line, rendered whether or not
        # there is a delta, which is what keeps the KPI row level.
        f'<div class="row"><div class="{cls}">{value}</div></div>'
        f'<div class="metaline">{delta_html}</div>'
        f"{foot_html}{spark_html}</div>",
        unsafe_allow_html=True,
    )


# Plotly's toolbar is developer chrome, not product chrome. Keep only the

# actions a reader plausibly wants and drop the logo.

PLOTLY_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": [
        "select2d",
        "lasso2d",
        "autoScale2d",
        "zoomIn2d",
        "zoomOut2d",
    ],
    "displayModeBar": "hover",
}


def chart(st, fig, caption=None, title=None, meta=None, accent=None):
    """Render a figure inside a bordered card, optionally with its own header.

    Charts floating directly on the page plane read as loose fragments; a card
    gives each one an edge. Pulling the title *inside* the card goes further: a
    heading floating above an anonymous box makes the box look like filler,
    while a card that names itself reads as a finished component.
    """
    with st.container(border=True):
        st.markdown('<span class="card-mark"></span>', unsafe_allow_html=True)
        if title:
            dot = f'<span class="ch-dot" style="background:{accent}"></span>' if accent else ""
            meta_html = f'<div class="ch-meta">{_esc(meta)}</div>' if meta else ""
            st.markdown(
                f'<div class="card-head"><div class="ch-title">{dot}{_esc(title)}</div>'
                f"{meta_html}</div>",
                unsafe_allow_html=True,
            )
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
        if caption:
            st.caption(caption)


def hero(
    st,
    kicker,
    label,
    value,
    delta=None,
    delta_word=None,
    foot=None,
    spark=None,
    colour=None,
    stats=(),
    chips=(),
):
    """The headline panel: one number that dominates, the rest supporting it.

    Four equal tiles give a reader no entry point -- everything weighs the same,
    so nothing reads as the answer. This is a single composed block instead: the
    index at display size over its own area chart on the left, the secondary
    figures stacked as a compact ledger on the right.

    Built as one HTML string rather than Streamlit columns because the layout is
    a real grid with a rule down the middle, which the column API cannot express.
    """
    colour = colour or CELESTE

    delta_html = ""
    if delta is not None and delta == delta:
        if abs(delta) < 0.005:
            dc, arrow = INK_MUTED, "→"
        elif delta > 0:
            dc, arrow = DELTA_UP_BAD, "↑"
        else:
            dc, arrow = DELTA_DOWN_GOOD, "↓"
        word = f'<span class="hero-dw">{_esc(delta_word)}</span>' if delta_word else ""
        delta_html = (
            f'<span class="hero-delta" style="color:{dc};background:{tint(dc, 0.11)}">'
            f"{arrow} {pct(delta)}</span>{word}"
        )

    rows = []
    for stat in stats:
        s_label, s_value, s_delta, s_foot = (list(stat) + [None, None])[:4]
        sd = ""
        if s_delta is not None and s_delta == s_delta:
            if abs(s_delta) < 0.005:
                c2, a2 = INK_MUTED, "→"
            elif s_delta > 0:
                c2, a2 = DELTA_UP_BAD, "↑"
            else:
                c2, a2 = DELTA_DOWN_GOOD, "↓"
            sd = f'<span class="s-delta" style="color:{c2}">{a2} {pct(s_delta)}</span>'
        rows.append(
            f'<div class="s-row"><div class="s-label">{_esc(s_label)}</div>'
            f'<div class="s-val">{s_value}{sd}</div>'
            f'<div class="s-foot">{_esc(s_foot) if s_foot else ""}</div></div>'
        )

    st.markdown(
        f'<div class="hero">'
        f'  <div class="hero-main">'
        f'    <div class="hero-kicker">{_esc(kicker)}</div>'
        f'    <div class="hero-label">{_esc(label)}</div>'
        f'    <div class="hero-figure">{value}{delta_html}</div>'
        f'    <div class="hero-foot">{_esc(foot) if foot else ""}</div>'
        f"    {sparkline(spark, colour, width=520, height=104) if spark else ''}"
        f"  </div>"
        f'  <div class="hero-side">{"".join(rows)}</div>'
        f'  <div class="hero-chips">{"".join(chips)}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def footer(st, left, right=None):
    """A thin provenance rule at the end of the page."""
    right_html = f'<div class="ft-right">{_esc(right)}</div>' if right else ""
    st.markdown(
        f'<div class="footer"><div class="ft-left">{_esc(left)}</div>{right_html}</div>',
        unsafe_allow_html=True,
    )


def table_view(st, frame, filename, label="Ver tabla", caption=None):
    """El gemelo accesible que acompaña a cada gráfico, con exportación a CSV."""

    with st.expander(label):
        if caption:
            st.caption(caption)

        st.dataframe(frame, use_container_width=True, hide_index=True)

        st.download_button(
            "Descargar CSV",
            frame.to_csv(index=False).encode("utf-8"),
            file_name=filename,
            mime="text/csv",
            key=f"dl-{filename}",
        )
