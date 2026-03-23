from __future__ import annotations

import streamlit as st

from config import reload_config
from services.db_service import init_db
from ui.streamlit.data import build_dashboard_context
from ui.streamlit.pages import (
    render_account_page,
    render_config_page,
    render_diagnostics_page,
    render_live_ops_page,
    render_logs_page,
    render_overview_page,
    render_reports_page,
    render_sidebar,
    render_strategy_page,
)
from ui.streamlit.refresh import (
    PAGE_ORDER,
    get_auto_refresh_run_every,
    render_auto_refresh_controls,
    render_auto_refresh_status,
    render_refreshable_fragment,
)
from ui.streamlit.styles import inject_css, render_hero


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

    page_param = st.query_params.get("page", PAGE_ORDER[0])
    if isinstance(page_param, list):
        page_param = page_param[0] if page_param else PAGE_ORDER[0]
    default_page = str(page_param)
    if default_page not in PAGE_ORDER:
        default_page = PAGE_ORDER[0]

    sidebar_ctx = build_dashboard_context(config)
    selected_page = render_sidebar(
        config=config,
        private_ctx=sidebar_ctx["private_ctx"],
        selected_page=default_page,
    )
    st.session_state["ui_page"] = selected_page
    st.query_params["page"] = selected_page

    auto_refresh_enabled, auto_refresh_seconds = render_auto_refresh_controls(selected_page)
    run_every = get_auto_refresh_run_every(selected_page, auto_refresh_enabled, auto_refresh_seconds)

    def render_selected_page() -> None:
        ctx = build_dashboard_context(config)
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
                auto_refresh_run_every=run_every,
            )
        elif selected_page == "Strategy":
            render_strategy_page(config=config)
        elif selected_page == "Reports":
            render_reports_page(today=today, config=config)
        elif selected_page == "Logs":
            render_logs_page(private_ctx=private_ctx)
        elif selected_page == "Diagnostics":
            render_diagnostics_page(
                today=today,
                private_ctx=private_ctx,
                latest_prices=latest_prices,
            )
        else:
            render_config_page(config=config)

    if selected_page == "Live Ops" or selected_page == "Config" or run_every is None:
        render_selected_page()
    else:
        render_refreshable_fragment(run_every, render_selected_page)

    render_auto_refresh_status(selected_page, auto_refresh_enabled, auto_refresh_seconds)


if __name__ == "__main__":
    main()
