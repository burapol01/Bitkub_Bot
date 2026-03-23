from __future__ import annotations

import streamlit as st

from config import reload_config
from services.db_service import init_db
from streamlit_ui_data import build_dashboard_context
from streamlit_ui_pages import (
    render_account_page,
    render_config_page,
    render_diagnostics_page,
    render_live_ops_page,
    render_overview_page,
    render_reports_page,
    render_sidebar,
)
from streamlit_ui_refresh import PAGE_ORDER, maybe_auto_refresh, render_auto_refresh_controls
from streamlit_ui_styles import inject_css, render_hero


st.set_page_config(
    page_title="Bitkub Bot Control",
    page_icon="BK",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    inject_css()
    init_db()
    render_hero()

    config, config_errors = reload_config()
    if config is None:
        st.error("config.json is invalid")
        for error in config_errors:
            st.write(f"- {error}")
        st.stop()

    default_page = st.session_state.get("ui_page", PAGE_ORDER[0])
    ctx = build_dashboard_context(config)
    selected_page = render_sidebar(
        config=config,
        private_ctx=ctx["private_ctx"],
        selected_page=default_page if default_page in PAGE_ORDER else PAGE_ORDER[0],
    )
    st.session_state["ui_page"] = selected_page

    auto_refresh_enabled, auto_refresh_seconds = render_auto_refresh_controls(selected_page)

    runtime = ctx["runtime"]
    private_ctx = ctx["private_ctx"]
    latest_prices = ctx["latest_prices"]
    ticker_rows = ctx["ticker_rows"]
    today = ctx["today"]

    if selected_page == "Overview":
        render_overview_page(
            config=config,
            runtime=runtime,
            ticker_rows=ticker_rows,
            private_ctx=private_ctx,
            today=today,
        )
    elif selected_page == "Account":
        render_account_page(
            private_ctx=private_ctx,
            latest_prices=latest_prices,
        )
    elif selected_page == "Live Ops":
        render_live_ops_page(
            config=config,
            runtime=runtime,
            private_ctx=private_ctx,
            latest_prices=latest_prices,
        )
    elif selected_page == "Reports":
        render_reports_page(today=today, config=config)
    elif selected_page == "Diagnostics":
        render_diagnostics_page(
            today=today,
            private_ctx=private_ctx,
            latest_prices=latest_prices,
        )
    else:
        render_config_page(config=config)

    maybe_auto_refresh(selected_page, auto_refresh_enabled, auto_refresh_seconds)


if __name__ == "__main__":
    main()
