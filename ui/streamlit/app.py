from __future__ import annotations

import streamlit as st

from config import reload_config
from services.db_service import init_db
from ui.streamlit.data import (
    build_dashboard_context,
    build_overview_context,
    sidebar_private_context,
)
from services.version_service import (
    format_app_version_detail,
    format_app_version_label,
    get_app_version_snapshot,
)
from ui.streamlit.execution_assistant import render_execution_assistant_page
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
    render_deploy_refresh_watcher,
    render_auto_refresh_status,
    render_refreshable_fragment,
)
from ui.streamlit.styles import inject_css, render_hero
from utils.time_utils import today_key


st.set_page_config(
    page_title="Bitkub Bot Control",
    page_icon="BK",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    inject_css()
    init_db()
    version_snapshot = get_app_version_snapshot()
    version_label = format_app_version_label(version_snapshot)
    version_detail = format_app_version_detail(version_snapshot)
    render_deploy_refresh_watcher(version_snapshot)
    render_hero(version_label=version_label, version_detail=version_detail)

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

    page_autorun = st.session_state.pop("ui_page_autorun", None)
    if page_autorun in PAGE_ORDER:
        default_page = str(page_autorun)

    if st.session_state.get("ui_page") not in PAGE_ORDER:
        st.session_state["ui_page"] = default_page
    if page_autorun in PAGE_ORDER:
        st.session_state["ui_page"] = str(page_autorun)
        st.session_state["sidebar_page"] = str(page_autorun)
    current_page = str(st.session_state.get("ui_page", default_page))
    today = today_key()

    selected_page = render_sidebar(
        config=config,
        private_ctx=sidebar_private_context(),
        selected_page=current_page,
        version_label=version_label,
        version_detail=version_detail,
    )
    st.session_state["ui_page"] = selected_page
    if st.query_params.get("page") != selected_page:
        st.query_params["page"] = selected_page

    auto_refresh_enabled, auto_refresh_seconds = render_auto_refresh_controls(selected_page)
    run_every = get_auto_refresh_run_every(selected_page, auto_refresh_enabled, auto_refresh_seconds)

    def render_selected_page() -> None:
        if selected_page == "Overview":
            ctx = build_overview_context(config)
            render_overview_page(
                config=config,
                runtime=ctx["runtime"],
                ticker_rows=ctx["ticker_rows"],
                private_ctx=ctx["private_ctx"],
                today=today,
            )
        elif selected_page == "Account":
            ctx = build_dashboard_context(config)
            render_account_page(
                private_ctx=ctx["private_ctx"],
                latest_prices=ctx["latest_prices"],
            )
        elif selected_page == "Live Ops":
            ctx = build_dashboard_context(config)
            render_live_ops_page(
                config=config,
                runtime=ctx["runtime"],
                private_ctx=ctx["private_ctx"],
                latest_prices=ctx["latest_prices"],
                quote_fetched_at=str(ctx.get("quote_fetched_at") or ""),
                auto_refresh_run_every=run_every,
            )
        elif selected_page == "Execution Assistant":
            ctx = build_dashboard_context(config)
            render_execution_assistant_page(
                config=config,
                private_ctx=ctx["private_ctx"],
                runtime=ctx["runtime"],
                latest_prices=dict(ctx.get("latest_prices") or {}),
                quote_fetched_at=str(ctx.get("quote_fetched_at") or ""),
            )
        elif selected_page == "Strategy":
            ctx = build_dashboard_context(config)
            render_strategy_page(
                config=config,
                private_ctx=ctx["private_ctx"],
                runtime=ctx["runtime"],
                latest_prices=dict(ctx.get("latest_prices") or {}),
                quote_fetched_at=str(ctx.get("quote_fetched_at") or ""),
            )
        elif selected_page == "Reports":
            render_reports_page(today=today, config=config)
        elif selected_page == "Logs":
            render_logs_page(
                config=config,
                today=today,
            )
        elif selected_page == "Diagnostics":
            ctx = build_dashboard_context(config)
            render_diagnostics_page(
                config=config,
                today=today,
                private_ctx=ctx["private_ctx"],
                latest_prices=ctx["latest_prices"],
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
