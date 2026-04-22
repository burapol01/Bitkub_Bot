from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from services.version_service import get_runtime_version_snapshot


PAGE_ORDER = (
    "Overview",
    "Account",
    "Live Ops",
    "Execution Assistant",
    "Strategy",
    "Strategy Inbox",
    "Reports",
    "Logs",
    "Diagnostics",
    "Config",
)

AUTO_REFRESH_SAFE_PAGES = {
    "Overview",
    "Account",
    "Live Ops",
    "Execution Assistant",
    "Strategy",
    "Strategy Inbox",
    "Reports",
    "Logs",
    "Diagnostics",
}

DEPLOY_VERSION_POLL_SECONDS = 30
DEPLOY_VERSION_STATE_KEY = "ui_seen_app_version_signature"


def render_auto_refresh_controls(page_name: str) -> tuple[bool, int]:
    safe_page = page_name in AUTO_REFRESH_SAFE_PAGES
    enabled_state_key = f"ui_auto_refresh_enabled::{page_name}"
    interval_state_key = f"ui_auto_refresh_seconds::{page_name}"

    if enabled_state_key not in st.session_state:
        st.session_state[enabled_state_key] = (
            bool(st.session_state.get("ui_auto_refresh_enabled", False)) and safe_page
        )
    if interval_state_key not in st.session_state:
        st.session_state[interval_state_key] = int(
            st.session_state.get("ui_auto_refresh_seconds", 10)
        )

    enabled = st.checkbox(
        "Auto refresh current page",
        key=enabled_state_key,
        disabled=not safe_page,
        help="Config stays manual so form edits are not interrupted.",
    )
    interval_seconds = int(
        st.select_slider(
            "Refresh interval",
            options=[5, 10, 15, 30, 60],
            key=interval_state_key,
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


def _app_version_signature(snapshot: dict[str, object]) -> str:
    branch = str(snapshot.get("branch") or "").strip()
    commit = str(snapshot.get("commit") or "").strip()
    label = str(snapshot.get("label") or "").strip()

    if branch and commit:
        return f"{branch}@{commit}"
    if commit:
        return commit
    if label:
        return label
    return "unknown"


def render_deploy_refresh_watcher(
    current_snapshot: dict[str, object],
    *,
    interval_seconds: int = DEPLOY_VERSION_POLL_SECONDS,
) -> None:
    current_signature = _app_version_signature(current_snapshot)
    known_signature = str(st.session_state.get(DEPLOY_VERSION_STATE_KEY) or "").strip()

    if not known_signature:
        st.session_state[DEPLOY_VERSION_STATE_KEY] = current_signature
    elif known_signature != current_signature:
        # The app is already running on the new version, so just sync the marker.
        st.session_state[DEPLOY_VERSION_STATE_KEY] = current_signature

    poll_seconds = max(5, int(interval_seconds))

    @st.fragment(run_every=f"{poll_seconds}s")
    def _deploy_refresh_fragment() -> None:
        latest_snapshot = get_runtime_version_snapshot()
        latest_signature = _app_version_signature(latest_snapshot)
        known = str(st.session_state.get(DEPLOY_VERSION_STATE_KEY) or "").strip()

        if not latest_signature:
            return
        if not known:
            st.session_state[DEPLOY_VERSION_STATE_KEY] = latest_signature
            return
        if latest_signature == known:
            return

        st.session_state[DEPLOY_VERSION_STATE_KEY] = latest_signature
        st.rerun()

    _deploy_refresh_fragment()
