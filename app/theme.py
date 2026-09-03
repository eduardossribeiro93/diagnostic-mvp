"""Shared look-and-feel for the operator console.

Calm, dense-but-airy, near-monochrome with a single lime accent - Streamlit's native
theming gets part of the way, the rest is a small CSS layer plus one Plotly template.
"""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# Make the `core` package importable however Streamlit is launched.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INK = "#0D0D0F"
PANEL = "#151518"
BORDER = "#26262B"
TEXT = "#F0F0F2"
MUTED = "#96969E"
ACCENT = "#C6F24E"
DANGER = "#F2785C"

CSS = f"""
<style>
  .block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px; }}
  #MainMenu, footer {{ visibility: hidden; }}

  .wc-eyebrow {{
    font-size: .68rem; letter-spacing: .14em; text-transform: uppercase;
    color: {MUTED}; margin-bottom: .15rem;
  }}
  .wc-title {{ font-size: 1.7rem; font-weight: 700; color: {TEXT}; line-height: 1.15; }}
  .wc-headline {{ font-size: 2.3rem; font-weight: 700; color: {ACCENT}; line-height: 1.2; margin:.2rem 0; }}
  .wc-sub {{ color: {MUTED}; font-size: .82rem; }}

  .wc-tile {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: .8rem .9rem; height: 100%;
  }}
  .wc-tile-label {{
    font-size: .62rem; letter-spacing: .12em; text-transform: uppercase;
    color: {MUTED}; margin-bottom: .35rem;
  }}
  .wc-tile-value {{ font-size: 1.45rem; font-weight: 700; color: {TEXT}; line-height: 1.1; }}
  .wc-tile-value.accent {{ color: {ACCENT}; }}
  .wc-tile-note {{ font-size: .68rem; color: {MUTED}; margin-top: .3rem; }}

  .wc-section {{
    font-size: .95rem; font-weight: 600; color: {TEXT};
    border-bottom: 1px solid {BORDER}; padding-bottom: .4rem; margin: 1.6rem 0 .8rem;
  }}
  .wc-pill {{
    display:inline-block; padding:.12rem .5rem; border-radius:999px;
    border:1px solid {BORDER}; color:{MUTED}; font-size:.68rem; margin-right:.3rem;
  }}
  div[data-testid="stDataFrame"] {{ border:1px solid {BORDER}; border-radius:10px; }}
  section[data-testid="stSidebar"] {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
</style>
"""

TEMPLATE = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=MUTED, size=12),
        colorway=[ACCENT, "#6E7681", "#3FB1B5", "#B57BEE", DANGER],
        xaxis=dict(gridcolor=BORDER, zerolinecolor=BORDER, linecolor=BORDER),
        yaxis=dict(gridcolor=BORDER, zerolinecolor=BORDER, linecolor=BORDER),
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
)
pio.templates["wc"] = TEMPLATE
pio.templates.default = "wc"


def apply(page_title: str) -> None:
    st.set_page_config(page_title=f"{page_title} - Working Capital Optimizer",
                       page_icon="&#9632;", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)


def money(value: float | None, currency: str = "EUR") -> str:
    if value is None:
        return "n/a"
    for unit, div in (("m", 1e6), ("k", 1e3)):
        if abs(value) >= div:
            return f"{currency} {value / div:,.1f}{unit}"
    return f"{currency} {value:,.0f}"


def tile(label: str, value: str, note: str = "", accent: bool = False) -> None:
    cls = "wc-tile-value accent" if accent else "wc-tile-value"
    note_html = f'<div class="wc-tile-note">{note}</div>' if note else ""
    st.markdown(
        f'<div class="wc-tile"><div class="wc-tile-label">{label}</div>'
        f'<div class="{cls}">{value}</div>{note_html}</div>',
        unsafe_allow_html=True,
    )


def section(title: str) -> None:
    st.markdown(f'<div class="wc-section">{title}</div>', unsafe_allow_html=True)


def require_result():
    """Every page except Home needs a completed run in session state."""
    result = st.session_state.get("result")
    if result is None:
        st.info("No diagnostic has been run yet. Start on the **Home** page.")
        st.stop()
    return result
