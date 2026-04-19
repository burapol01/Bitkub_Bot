from __future__ import annotations

import streamlit as st


def queue_page_autorun(*, page: str) -> None:
    st.session_state["ui_page_autorun"] = str(page)


def queue_live_ops_navigation(*, symbol: str) -> None:
    queue_page_autorun(page="Live Ops")
    st.session_state["live_ops_focus_symbol"] = str(symbol)
    st.session_state["live_ops_manual_symbol"] = str(symbol)


def queue_strategy_workspace_navigation(*, workspace: str, symbol: str | None = None) -> None:
    queue_page_autorun(page="Strategy")
    st.session_state["strategy_workspace_autorun"] = str(workspace)
    if symbol is not None:
        st.session_state["strategy_workspace_focus_symbol"] = str(symbol)
