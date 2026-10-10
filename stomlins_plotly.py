"""stomlins design system - Plotly template, v0.1.0 (matches core stylesheet v0.2.0)

Source: github.com/satomlins/stomlins-design (charts/stomlins_plotly.py)
Vendored by copy, like stomlins.css. Do not edit in a consuming project:
change it there, then re-copy.

Plotly cannot read CSS variables, so the token values are repeated here.
Keep them in step with site/stomlins.css.

    import stomlins_plotly

    stomlins_plotly.register()                # once at import: becomes the default
    fig.update_layout(template="stomlins")    # or per figure
"""

import plotly.graph_objects as go
import plotly.io as pio

NAME = "stomlins"

# Tokens from site/stomlins.css (dark set)
SURFACE = "#161616"
BORDER = "#262626"
BORDER_STRONG = "#333333"
TEXT = "#e8e8e8"
TEXT_MUTED = "#8a8a8a"
TRANSPARENT = "rgba(0,0,0,0)"

FONT = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", Ubuntu, Cantarell, '
    'Roboto, Helvetica, Arial, "Noto Sans", sans-serif'
)

# Default series colours for a chart that has no palette of its own: the
# Sweepstake owner pastels. A site with meaningful series sets its own.
COLORWAY = [
    "#ffadad", "#ffd6a5", "#fdffb6", "#caffbf", "#9bf6ff", "#a0c4ff",
    "#bdb2ff", "#ffc6ff", "#c7ceea", "#ffdac1", "#b5ead7", "#f8c8d4",
]


def _axis() -> dict:
    return {
        "gridcolor": BORDER,
        "zerolinecolor": BORDER,
        "linecolor": BORDER,
        "tickcolor": BORDER,
        "tickfont": {"size": 12, "color": TEXT_MUTED},
        "title": {"font": {"size": 12, "color": TEXT_MUTED}, "standoff": 8},
        "automargin": True,
    }


def template() -> go.layout.Template:
    """The template itself, for passing straight to update_layout."""
    return go.layout.Template(
        layout={
            "paper_bgcolor": TRANSPARENT,
            "plot_bgcolor": TRANSPARENT,
            "font": {"family": FONT, "size": 13, "color": TEXT_MUTED},
            "title": {"font": {"size": 15, "color": TEXT}},
            "colorway": COLORWAY,
            "xaxis": _axis(),
            "yaxis": _axis(),
            "legend": {
                "bgcolor": TRANSPARENT,
                "borderwidth": 0,
                "font": {"size": 11, "color": TEXT_MUTED},
            },
            "hoverlabel": {
                "bgcolor": SURFACE,
                "bordercolor": BORDER_STRONG,
                "font": {"family": FONT, "size": 12, "color": TEXT},
            },
            "annotationdefaults": {"font": {"color": TEXT_MUTED}},
            "modebar": {
                "bgcolor": TRANSPARENT,
                "color": TEXT_MUTED,
                "activecolor": TEXT,
            },
        }
    )


def register(default: bool = True) -> str:
    """Register the template as "stomlins" and, by default, make it the default."""
    pio.templates[NAME] = template()
    if default:
        pio.templates.default = NAME
    return NAME
