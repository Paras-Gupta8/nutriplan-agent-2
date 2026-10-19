"""Reusable UI building blocks."""
from __future__ import annotations

import html
import inspect

import streamlit as st

from ui.theme import STATUS_COLORS

# Streamlit replaced `use_container_width=True` with `width="stretch"`; pick whichever this version supports.
STRETCH = {"width": "stretch"} if "width" in inspect.signature(st.button).parameters else {"use_container_width": True}

DISCLAIMER_TEXT = (
    "⚕️ **Educational wellness tool - not a medical device.** NutriPlan Agent gives general nutrition "
    "guidance for healthy adults. Food data is approximate. If you have a medical condition, are pregnant, "
    "or have a history of disordered eating, please consult a qualified doctor or registered dietitian."
)


def page_header(title: str, subtitle: str = "", icon: str = "🥗") -> None:
    st.markdown(
        f'<div class="np-header"><h1>{icon} {html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )


def metric_card(label: str, value: str, sub: str = "") -> None:
    st.markdown(
        f'<div class="np-card"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(str(value))}</div>'
        f'<div class="sub">{html.escape(sub)}</div></div>',
        unsafe_allow_html=True,
    )


def explain_card(text: str) -> None:
    st.markdown(f'<div class="np-explain">{html.escape(text)}</div>', unsafe_allow_html=True)


def status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#5B7A72")
    return f'<span class="np-badge" style="background:{color}">{html.escape(status.replace("_", " "))}</span>'


def disclaimer() -> None:
    st.markdown(f'<div class="np-disclaimer">{DISCLAIMER_TEXT}</div>', unsafe_allow_html=True)


def empty_state(title: str, message: str, page: str | None = None, label: str = "Go to page") -> None:
    with st.container(border=True):
        st.markdown(f"#### {title}")
        st.write(message)
        if page:
            st.page_link(page, label=label, icon="➡️")


def lazy_tabs(labels: list[str], key: str) -> str:
    """Tab-like selector that renders only the chosen section.

    `st.tabs` executes every tab's content on every run (including hidden charts); this keeps heavy charts
    and tables off the page until they are actually viewed. Returns the selected label."""
    return st.radio("View", labels, horizontal=True, key=key, label_visibility="collapsed")


STAGE_ICON = {"pending": "⚪", "running": "🟡", "done": "🟢", "warning": "🟠", "failed": "🔴"}
STATUS_EMOJI = {"success": "🟢", "warning": "🟠", "revision_required": "🔁", "failed": "🔴"}


def stage_tracker(stages: dict) -> None:
    """One chip per agent stage, coloured by status."""
    cols = st.columns(len(stages))
    for col, (name, status) in zip(cols, stages.items()):
        col.markdown(f"<div class='np-card' style='text-align:center;padding:.6rem .4rem'>"
                     f"<div style='font-size:1.3rem'>{STAGE_ICON[status]}</div>"
                     f"<div style='font-weight:600;font-size:.85rem'>{html.escape(name)}</div>"
                     f"<div class='sub' style='font-size:.72rem;color:#5B7A72'>{html.escape(status)}</div></div>", unsafe_allow_html=True)
