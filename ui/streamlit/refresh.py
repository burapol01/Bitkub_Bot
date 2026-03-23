from __future__ import annotations

from collections.abc import Callable

import streamlit as st


PAGE_ORDER = (
    "Overview",
    "Account",
    "Live Ops",
    "Reports",
    "Diagnostics",
    "Config",
)

AUTO_REFRESH_SAFE_PAGES = {
    "Overview",
    "Account",
    "Live Ops",
    "Reports",
    "Diagnostics",
}


def render_auto_refresh_controls(page_name: str) -> tuple[bool, int]:
    safe_page = page_name in AUTO_REFRESH_SAFE_PAGES
    default_enabled = bool(st.session_state.get("ui_auto_refresh_enabled", False)) and safe_page
    enabled = st.checkbox(
        "Auto refresh current page",
        value=default_enabled,
        disabled=not safe_page,
        help="Config stays manual so form edits are not interrupted.",
    )
    interval_seconds = int(
        st.select_slider(
            "Refresh interval",
            options=[5, 10, 15, 30, 60],
            value=int(st.session_state.get("ui_auto_refresh_seconds", 10)),
            disabled=not safe_page,
        )
    )
    st.session_state["ui_auto_refresh_enabled"] = enabled and safe_page
    st.session_state["ui_auto_refresh_seconds"] = interval_seconds

    if not safe_page:
        st.caption("Auto refresh is disabled on the Config page.")

    return enabled and safe_page, interval_seconds


def get_auto_refresh_run_every(page_name: str, enabled: bool, interval_seconds: int) -> str | None:
    if not enabled or page_name not in AUTO_REFRESH_SAFE_PAGES:
        return None

    return f"{int(interval_seconds)}s"


def render_refreshable_fragment(run_every: str | None, render_fn: Callable[[], None]) -> None:
    if run_every is None:
        render_fn()
        return

    @st.fragment(run_every=run_every)
    def _refresh_fragment() -> None:
        render_fn()

    _refresh_fragment()


def render_auto_refresh_status(page_name: str, enabled: bool, interval_seconds: int) -> None:
    if not enabled or page_name not in AUTO_REFRESH_SAFE_PAGES:
        return

    st.caption(
        f"Auto refresh every {interval_seconds}s on {page_name}. "
        "Live data panels rerun without reloading the whole browser page."
    )
