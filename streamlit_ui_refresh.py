from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components


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


def maybe_auto_refresh(page_name: str, enabled: bool, interval_seconds: int) -> None:
    if not enabled or page_name not in AUTO_REFRESH_SAFE_PAGES:
        return

    components.html(
        f"""
        <script>
        window.setTimeout(function() {{
          window.parent.location.reload();
        }}, {int(interval_seconds) * 1000});
        </script>
        """,
        height=0,
        width=0,
    )
    st.caption(f"Auto refresh every {interval_seconds}s on {page_name}.")
