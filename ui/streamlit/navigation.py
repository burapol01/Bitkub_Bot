from __future__ import annotations

import streamlit as st


def queue_page_autorun(*, page: str) -> None:
    st.session_state["ui_page_autorun"] = str(page)


def queue_live_ops_navigation(*, symbol: str) -> None:
    queue_page_autorun(page="Live Ops")
    st.session_state["live_ops_focus_symbol"] = str(symbol)
    st.session_state["live_ops_manual_symbol"] = str(symbol)


def queue_strategy_workspace_navigation(*, workspace: str, symbol: str | None = None) -> None:
    normalized_workspace = str(workspace)
    queue_page_autorun(page="Strategy")
    st.session_state["strategy_workspace_autorun"] = normalized_workspace
    if symbol is None:
        return

    normalized_symbol = str(symbol)
    st.session_state["strategy_workspace_focus_symbol"] = normalized_symbol
    if normalized_workspace == "Compare":
        st.session_state["strategy_compare_symbol_autorun"] = normalized_symbol
        st.session_state.pop("strategy_tuning_focus_symbol_autorun", None)
    elif normalized_workspace == "Live Tuning":
        st.session_state["strategy_tuning_focus_symbol_autorun"] = normalized_symbol
        st.session_state.pop("strategy_compare_symbol_autorun", None)
