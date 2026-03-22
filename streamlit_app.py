from __future__ import annotations

from typing import Any

import streamlit as st

from clients.bitkub_client import get_ticker
from clients.bitkub_private_client import (
    BitkubMissingCredentialsError,
    BitkubPrivateClient,
    BitkubPrivateClientError,
)
from config import CONFIG_PATH, reload_config
from services.account_service import (
    account_snapshot_errors,
    build_live_holdings_snapshot,
    fetch_account_snapshot,
    summarize_account_capabilities,
)
from services.db_service import (
    DB_PATH,
    fetch_dashboard_summary,
    fetch_db_maintenance_summary,
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_reporting_summary,
    init_db,
    insert_account_snapshot,
    insert_execution_order,
    insert_execution_order_event,
    insert_runtime_event,
    update_execution_order,
)
from services.execution_service import (
    LiveExecutionGuardrailError,
    build_live_execution_guardrails,
    cancel_live_order,
    refresh_live_order_from_exchange,
    submit_manual_live_order,
)
from services.order_service import get_order_foundation_status
from services.reconciliation_service import (
    extract_available_balances,
    summarize_live_reconciliation,
)
from services.state_service import load_runtime_state
from utils.time_utils import now_text, today_key


st.set_page_config(
    page_title="Bitkub Bot Control",
    page_icon="BK",
    layout="wide",
    initial_sidebar_state="expanded",
)


CUSTOM_CSS = """
<style>
:root {
  --bg-0: #f3ecdf;
  --bg-1: #fffaf2;
  --panel: rgba(255, 251, 243, 0.86);
  --panel-strong: rgba(253, 247, 234, 0.96);
  --line: rgba(104, 79, 46, 0.16);
  --ink: #2b2117;
  --muted: #7b6850;
  --accent: #a34a28;
  --accent-2: #1f6a73;
  --good: #2f7d32;
  --warn: #9a6700;
  --bad: #b42318;
}

.stApp {
  background:
    radial-gradient(circle at 12% 18%, rgba(163, 74, 40, 0.14), transparent 24%),
    radial-gradient(circle at 82% 14%, rgba(31, 106, 115, 0.14), transparent 20%),
    linear-gradient(180deg, var(--bg-0), var(--bg-1));
  color: var(--ink);
}

html, body, [class*="css"] {
  font-family: "Palatino Linotype", "Book Antiqua", Georgia, serif;
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(251,245,233,0.98), rgba(245,236,217,0.98));
  border-right: 1px solid var(--line);
}

.hero {
  background: linear-gradient(135deg, rgba(255,249,240,0.94), rgba(247,235,215,0.88));
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 1.2rem 1.4rem 1rem 1.4rem;
  box-shadow: 0 14px 42px rgba(79, 56, 24, 0.08);
}

.hero-kicker {
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--accent);
  font-size: 0.74rem;
  margin-bottom: 0.55rem;
}

.hero-title {
  font-size: 2rem;
  line-height: 1.05;
  font-weight: 700;
  margin: 0 0 0.4rem 0;
  color: var(--ink);
}

.hero-sub {
  color: var(--muted);
  margin: 0;
  font-size: 1rem;
}

.metric-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 22px;
  padding: 1rem 1.05rem 0.9rem 1.05rem;
  min-height: 110px;
  box-shadow: 0 12px 34px rgba(66, 47, 24, 0.06);
}

.metric-label {
  font-size: 0.76rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--muted);
}

.metric-value {
  font-size: 1.7rem;
  line-height: 1.05;
  margin-top: 0.35rem;
  color: var(--ink);
}

.metric-note {
  margin-top: 0.45rem;
  color: var(--muted);
  font-size: 0.92rem;
}

.panel {
  background: var(--panel-strong);
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 1rem 1.15rem 1.05rem 1.15rem;
  box-shadow: 0 14px 38px rgba(83, 62, 31, 0.06);
}

.panel-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--ink);
  margin-bottom: 0.5rem;
}

.note-strip {
  border-left: 4px solid var(--accent);
  background: rgba(163, 74, 40, 0.08);
  border-radius: 0 16px 16px 0;
  padding: 0.8rem 0.9rem;
  color: var(--ink);
}

.badge {
  display: inline-block;
  padding: 0.18rem 0.56rem;
  border-radius: 999px;
  font-size: 0.78rem;
  margin-right: 0.35rem;
  border: 1px solid transparent;
}

.badge.good { background: rgba(47,125,50,0.12); color: var(--good); border-color: rgba(47,125,50,0.22); }
.badge.warn { background: rgba(154,103,0,0.12); color: var(--warn); border-color: rgba(154,103,0,0.22); }
.badge.bad  { background: rgba(180,35,24,0.12); color: var(--bad); border-color: rgba(180,35,24,0.22); }
.badge.info { background: rgba(31,106,115,0.12); color: var(--accent-2); border-color: rgba(31,106,115,0.22); }

div[data-testid="stMetric"] {
  background: transparent;
  border: none;
  box-shadow: none;
}

.compact-code {
  font-family: Consolas, "Courier New", monospace;
  font-size: 0.92rem;
}
</style>
"""


def inject_css():
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def badge(text: str, tone: str = "info") -> str:
    return f'<span class="badge {tone}">{text}</span>'


def render_metric_card(label: str, value: str, note: str = ""):
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-label">{label}</div>
          <div class="metric-value">{value}</div>
          <div class="metric-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def runtime_snapshot() -> dict[str, Any]:
    runtime_last_zones: dict[str, Any] = {}
    runtime_positions: dict[str, Any] = {}
    runtime_daily_stats: dict[str, Any] = {}
    runtime_cooldowns: dict[str, Any] = {}
    manual_pause, messages = load_runtime_state(
        runtime_last_zones,
        runtime_positions,
        runtime_daily_stats,
        runtime_cooldowns,
    )
    return {
        "manual_pause": manual_pause,
        "messages": messages,
        "last_zones": runtime_last_zones,
        "positions": runtime_positions,
        "daily_stats": runtime_daily_stats,
        "cooldowns": runtime_cooldowns,
    }


def calc_daily_totals(daily_stats: dict[str, Any]) -> tuple[int, int, int, float]:
    today_stats = daily_stats.get(today_key(), {})
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0

    for stats in today_stats.values():
        total_trades += int(stats["trades"])
        total_wins += int(stats["wins"])
        total_losses += int(stats["losses"])
        total_pnl += float(stats["realized_pnl_thb"])

    return total_trades, total_wins, total_losses, total_pnl


def private_context() -> dict[str, Any]:
    private_api_status = "not configured"
    private_api_capabilities: list[str] = ["wallet=OFF", "balances=OFF", "open_orders=OFF"]
    client: BitkubPrivateClient | None = None
    account_snapshot: dict[str, Any] | None = None
    errors: list[str] = []

    try:
        candidate = BitkubPrivateClient.from_env()
        if candidate.is_configured():
            client = candidate
            account_snapshot = fetch_account_snapshot(candidate)
            private_api_capabilities = summarize_account_capabilities(account_snapshot)
            snapshot_errors = account_snapshot_errors(account_snapshot)
            if snapshot_errors:
                private_api_status = "wallet/balance ready, some order endpoints unavailable"
                errors = snapshot_errors
            else:
                private_api_status = "wallet/balance/open-orders ready"
    except BitkubMissingCredentialsError:
        private_api_status = "missing credentials"
    except BitkubPrivateClientError as e:
        private_api_status = "private API error"
        errors = [str(e)]

    return {
        "client": client,
        "account_snapshot": account_snapshot,
        "private_api_status": private_api_status,
        "private_api_capabilities": private_api_capabilities,
        "errors": errors,
    }


def persist_execution_order_insert(order_record: dict[str, Any], order_events: list[dict[str, Any]]) -> int:
    execution_order_id = insert_execution_order(
        created_at=order_record["created_at"],
        updated_at=order_record["updated_at"],
        symbol=order_record["symbol"],
        side=order_record["side"],
        order_type=order_record["order_type"],
        state=order_record["state"],
        request_payload=order_record["request_payload"],
        response_payload=order_record.get("response_payload"),
        guardrails=order_record.get("guardrails"),
        exchange_order_id=order_record.get("exchange_order_id"),
        exchange_client_id=order_record.get("exchange_client_id"),
        message=order_record["message"],
    )
    persist_execution_order_update(execution_order_id, order_record, order_events)
    return execution_order_id


def persist_execution_order_update(
    execution_order_id: int,
    order_record: dict[str, Any],
    order_events: list[dict[str, Any]],
):
    update_execution_order(
        execution_order_id=execution_order_id,
        updated_at=order_record["updated_at"],
        state=order_record["state"],
        response_payload=order_record.get("response_payload"),
        exchange_order_id=order_record.get("exchange_order_id"),
        exchange_client_id=order_record.get("exchange_client_id"),
        message=order_record["message"],
    )
    for event in order_events:
        insert_execution_order_event(
            execution_order_id=execution_order_id,
            created_at=event["created_at"],
            from_state=event["from_state"],
            to_state=event["to_state"],
            event_type=event["event_type"],
            message=event["message"],
            details=event.get("details"),
        )


def market_rows(config: dict[str, Any], ticker: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, rule in sorted(config["rules"].items()):
        ticker_entry = ticker.get(symbol, {})
        last_price = (
            float(ticker_entry["last"])
            if isinstance(ticker_entry, dict) and "last" in ticker_entry
            else None
        )
        current_zone = "n/a"
        if last_price is not None:
            buy_below = float(rule["buy_below"])
            sell_above = float(rule["sell_above"])
            if last_price <= buy_below:
                current_zone = "BUY"
            elif last_price >= sell_above:
                current_zone = "SELL"
            else:
                current_zone = "WAIT"

        rows.append(
            {
                "symbol": symbol,
                "last_price": last_price,
                "buy_below": float(rule["buy_below"]),
                "sell_above": float(rule["sell_above"]),
                "zone": current_zone,
            }
        )
    return rows


def live_reconciliation_bundle(
    account_snapshot: dict[str, Any] | None,
    latest_prices: dict[str, float],
    private_client: BitkubPrivateClient | None,
) -> dict[str, Any]:
    return summarize_live_reconciliation(
        execution_orders=fetch_open_execution_orders(),
        live_holdings_rows=build_live_holdings_snapshot(
            account_snapshot=account_snapshot,
            latest_prices=latest_prices,
            latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
        ),
        account_snapshot=account_snapshot,
        private_client=private_client,
    )


def render_reconciliation_block(live_reconciliation: dict[str, Any]):
    groups = (
        ("warnings", "warn"),
        ("partially_filled_orders", "warn"),
        ("reserved_without_open_order", "bad"),
        ("open_order_without_reserved", "warn"),
        ("triggered_exit_candidates", "info"),
        ("unmanaged_live_holdings", "bad"),
    )
    printed = False
    for key, tone in groups:
        rows = live_reconciliation.get(key, [])
        if not rows:
            continue
        printed = True
        st.markdown(badge(key.replace("_", " "), tone), unsafe_allow_html=True)
        for row in rows:
            st.write(f"- {row}")
    if not printed:
        st.caption("No live reconciliation issues detected.")


def submit_manual_order_from_ui(
    *,
    client: BitkubPrivateClient,
    config: dict[str, Any],
    runtime: dict[str, Any],
    private_capabilities: list[str],
    account_snapshot: dict[str, Any] | None,
    form_values: dict[str, Any],
):
    modified_config = dict(config)
    modified_config["live_manual_order"] = form_values
    total_trades, total_wins, total_losses, total_pnl = calc_daily_totals(runtime["daily_stats"])
    guardrails = build_live_execution_guardrails(
        config=modified_config,
        trading_mode=str(config["mode"]),
        private_client=client,
        private_api_capabilities=private_capabilities,
        manual_pause=runtime["manual_pause"],
        safety_pause=False,
        total_realized_pnl_thb=total_pnl,
        available_balances=extract_available_balances(account_snapshot),
        strategy_execution_wired=False,
    )
    order_record, order_events = submit_manual_live_order(
        client=client,
        config=modified_config,
        rules=config["rules"],
        guardrails=guardrails,
        available_balances=extract_available_balances(account_snapshot),
        created_at=now_text(),
    )
    execution_order_id = persist_execution_order_insert(order_record, order_events)
    insert_runtime_event(
        created_at=now_text(),
        event_type="manual_live_order_ui",
        severity="warning",
        message="Manual live order submitted from Streamlit UI",
        details={
            "execution_order_id": execution_order_id,
            "symbol": order_record["symbol"],
            "side": order_record["side"],
            "state": order_record["state"],
        },
    )
    return execution_order_id, order_record


def render_overview_tab(
    config: dict[str, Any],
    runtime: dict[str, Any],
    ticker_rows: list[dict[str, Any]],
    private_ctx: dict[str, Any],
):
    total_trades, total_wins, total_losses, total_pnl = calc_daily_totals(runtime["daily_stats"])
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_metric_card("Trading Mode", str(config["mode"]).upper(), private_ctx["private_api_status"])
    with col2:
        render_metric_card("Rules", str(len(config["rules"])), f"Open paper positions {len(runtime['positions'])}")
    with col3:
        render_metric_card("Tracked Today", str(len(runtime["daily_stats"].get(today_key(), {}))), f"Cooldowns {len(runtime['cooldowns'])}")
    with col4:
        render_metric_card("Realized Today", f"{total_pnl:,.2f} THB", f"Trades {total_trades} | W {total_wins} / L {total_losses}")

    st.markdown('<div class="panel-title">Market Overview</div>', unsafe_allow_html=True)
    st.dataframe(
        [
            {
                "symbol": row["symbol"],
                "last": f"{row['last_price']:,.8f}" if row["last_price"] is not None else "n/a",
                "buy_below": f"{row['buy_below']:,.8f}",
                "sell_above": f"{row['sell_above']:,.8f}",
                "zone": row["zone"],
            }
            for row in ticker_rows
        ],
        use_container_width=True,
        hide_index=True,
    )

    if runtime["messages"]:
        st.markdown('<div class="note-strip"><strong>Runtime Restore</strong><br>' + "<br>".join(runtime["messages"]) + "</div>", unsafe_allow_html=True)


def render_account_tab(
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
    config: dict[str, Any],
):
    account_snapshot = private_ctx["account_snapshot"]
    if account_snapshot is None:
        st.warning("Private API credentials are not configured or account snapshot is unavailable.")
        return

    st.markdown('<div class="panel-title">Capability Matrix</div>', unsafe_allow_html=True)
    st.markdown(
        " ".join(
            badge(item, "good" if item.endswith("=OK") else "warn" if item.endswith("=PARTIAL") else "bad")
            for item in private_ctx["private_api_capabilities"]
        ),
        unsafe_allow_html=True,
    )
    if private_ctx["errors"]:
        for error in private_ctx["errors"]:
            st.warning(error)

    holdings = build_live_holdings_snapshot(
        account_snapshot=account_snapshot,
        latest_prices=latest_prices,
        latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
    )
    st.markdown('<div class="panel-title">Live Holdings</div>', unsafe_allow_html=True)
    st.dataframe(holdings, use_container_width=True, hide_index=True)

    open_orders = account_snapshot.get("open_orders", {})
    open_rows: list[dict[str, Any]] = []
    if isinstance(open_orders, dict):
        for symbol, entry in sorted(open_orders.items()):
            if not isinstance(entry, dict):
                continue
            if entry.get("ok", False):
                payload = entry.get("data", {})
                rows = payload.get("result", payload) if isinstance(payload, dict) else payload
                count = len(rows) if isinstance(rows, list) else 0
                open_rows.append({"symbol": symbol, "status": "OK", "open_orders": count})
            else:
                open_rows.append({"symbol": symbol, "status": "ERROR", "open_orders": entry.get("error")})
    st.markdown('<div class="panel-title">Exchange Open Orders</div>', unsafe_allow_html=True)
    st.dataframe(open_rows, use_container_width=True, hide_index=True)


def render_live_ops_tab(
    config: dict[str, Any],
    runtime: dict[str, Any],
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
):
    client = private_ctx["client"]
    account_snapshot = private_ctx["account_snapshot"]
    if client is None or account_snapshot is None:
        st.warning("Private API is required for live operations.")
        return

    total_trades, total_wins, total_losses, total_pnl = calc_daily_totals(runtime["daily_stats"])
    guardrails = build_live_execution_guardrails(
        config=config,
        trading_mode=str(config["mode"]),
        private_client=client,
        private_api_capabilities=private_ctx["private_api_capabilities"],
        manual_pause=runtime["manual_pause"],
        safety_pause=False,
        total_realized_pnl_thb=total_pnl,
        available_balances=extract_available_balances(account_snapshot),
        strategy_execution_wired=False,
    )

    left, right = st.columns([1.1, 0.9])
    with left:
        st.markdown('<div class="panel-title">Manual Live Order</div>', unsafe_allow_html=True)
        with st.form("manual_live_order_form"):
            manual_defaults = dict(config.get("live_manual_order", {}))
            symbol = st.selectbox("Symbol", list(config["rules"].keys()), index=max(0, list(config["rules"].keys()).index(manual_defaults.get("symbol", list(config["rules"].keys())[0])) if manual_defaults.get("symbol") in config["rules"] else 0))
            side = st.selectbox("Side", ["buy", "sell"], index=0 if manual_defaults.get("side", "buy") == "buy" else 1)
            order_type = st.selectbox("Order Type", ["limit"], index=0)
            amount_thb = st.number_input("Amount THB", min_value=0.0, value=float(manual_defaults.get("amount_thb", 100.0)), step=10.0)
            amount_coin = st.number_input("Amount Coin", min_value=0.0, value=float(manual_defaults.get("amount_coin", 0.0)), format="%.8f")
            default_rate = float(manual_defaults.get("rate", 1.0))
            if symbol in latest_prices:
                default_rate = float(latest_prices[symbol])
            rate = st.number_input("Rate", min_value=0.0, value=default_rate, format="%.8f")
            confirm = st.checkbox("I understand this can submit a real Bitkub order.")
            submitted = st.form_submit_button("Submit Manual Order", type="primary", use_container_width=True)

        if submitted:
            if not confirm:
                st.error("Confirmation is required before submitting a live order.")
            else:
                try:
                    execution_order_id, order_record = submit_manual_order_from_ui(
                        client=client,
                        config=config,
                        runtime=runtime,
                        private_capabilities=private_ctx["private_api_capabilities"],
                        account_snapshot=account_snapshot,
                        form_values={
                            "enabled": True,
                            "symbol": symbol,
                            "side": side,
                            "order_type": order_type,
                            "amount_thb": float(amount_thb),
                            "amount_coin": float(amount_coin),
                            "rate": float(rate),
                        },
                    )
                except LiveExecutionGuardrailError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(str(e))
                else:
                    st.success(
                        f"Submitted execution order id={execution_order_id} symbol={order_record['symbol']} side={order_record['side']} state={order_record['state']}"
                    )
                    st.rerun()

    with right:
        st.markdown('<div class="panel-title">Live Controls</div>', unsafe_allow_html=True)
        open_orders = fetch_open_execution_orders()
        if st.button("Refresh Open Live Orders", use_container_width=True):
            refreshed = 0
            for order in open_orders:
                refreshed_record, events = refresh_live_order_from_exchange(
                    client=client,
                    order_record=order,
                    occurred_at=now_text(),
                )
                persist_execution_order_update(int(order["id"]), refreshed_record, events)
                refreshed += 1
            insert_runtime_event(
                created_at=now_text(),
                event_type="live_order_refresh_ui",
                severity="info",
                message="Live orders refreshed from Streamlit UI",
                details={"count": refreshed},
            )
            st.success(f"Refreshed {refreshed} open live order(s).")
            st.rerun()

        if open_orders:
            option_map = {
                f"id={order['id']} | {order['symbol']} | {order['side']} | {order['state']}": order
                for order in open_orders
            }
            selected_label = st.selectbox("Cancel Target", list(option_map.keys()))
            confirm_cancel = st.checkbox("Confirm live cancel request")
            if st.button("Cancel Selected Order", use_container_width=True):
                if not confirm_cancel:
                    st.error("Tick confirm before canceling a live order.")
                else:
                    target_order = option_map[selected_label]
                    try:
                        canceled_record, events = cancel_live_order(
                            client=client,
                            order_record=target_order,
                            occurred_at=now_text(),
                        )
                        persist_execution_order_update(int(target_order["id"]), canceled_record, events)
                        insert_runtime_event(
                            created_at=now_text(),
                            event_type="live_order_cancel_ui",
                            severity="warning",
                            message="Live order canceled from Streamlit UI",
                            details={"execution_order_id": int(target_order["id"])},
                        )
                    except Exception as e:
                        st.error(str(e))
                    else:
                        st.success(f"Order id={target_order['id']} is now {canceled_record['state']}.")
                        st.rerun()
        else:
            st.caption("No open live orders are available for cancel.")

        st.markdown('<div class="panel-title">Guardrails</div>', unsafe_allow_html=True)
        st.json(guardrails, expanded=False)


def render_reports_tab(today: str, config: dict[str, Any]):
    symbols = ["ALL"] + sorted(config["rules"].keys())
    selected_symbol = st.selectbox("Report Filter", symbols, index=0)
    report = fetch_reporting_summary(today=today, symbol=None if selected_symbol == "ALL" else selected_symbol)
    st.markdown('<div class="panel-title">Symbol Summary</div>', unsafe_allow_html=True)
    st.dataframe(report["symbol_summary"], use_container_width=True, hide_index=True)
    st.markdown('<div class="panel-title">Recent Execution Orders</div>', unsafe_allow_html=True)
    st.dataframe(report["recent_execution_orders"], use_container_width=True, hide_index=True)
    st.markdown('<div class="panel-title">Recent Auto Exit Events</div>', unsafe_allow_html=True)
    st.dataframe(report["recent_auto_exit_events"], use_container_width=True, hide_index=True)
    st.markdown('<div class="panel-title">Recent Runtime Errors</div>', unsafe_allow_html=True)
    st.dataframe(report["recent_errors"], use_container_width=True, hide_index=True)


def render_diagnostics_tab(
    config: dict[str, Any],
    runtime: dict[str, Any],
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
):
    db_summary = fetch_db_maintenance_summary()
    dashboard_summary = fetch_dashboard_summary(today=today_key())
    live_reconciliation = live_reconciliation_bundle(
        private_ctx["account_snapshot"],
        latest_prices,
        private_ctx["client"],
    )

    col1, col2 = st.columns([0.95, 1.05])
    with col1:
        st.markdown('<div class="panel-title">SQLite Health</div>', unsafe_allow_html=True)
        st.json(db_summary, expanded=False)
        st.markdown('<div class="panel-title">Latest Dashboard Summary</div>', unsafe_allow_html=True)
        st.json(
            {
                "latest_account_snapshot": dashboard_summary.get("latest_account_snapshot"),
                "latest_reconciliation": dashboard_summary.get("latest_reconciliation"),
                "latest_execution_order": dashboard_summary.get("latest_execution_order"),
            },
            expanded=False,
        )
    with col2:
        st.markdown('<div class="panel-title">Live Reconciliation</div>', unsafe_allow_html=True)
        render_reconciliation_block(live_reconciliation)
        st.markdown('<div class="panel-title">Execution Console Summary</div>', unsafe_allow_html=True)
        st.json(fetch_execution_console_summary(), expanded=False)


def main():
    inject_css()
    init_db()

    config, config_errors = reload_config()

    st.markdown(
        """
        <div class="hero">
          <div class="hero-kicker">Bitkub Bot Control Surface</div>
          <div class="hero-title">Live operations, diagnostics, and reports in one place</div>
          <p class="hero-sub">This dashboard sits on top of the current console bot and reuses the same SQLite, private API, and execution services.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if config is None:
        st.error("config.json is invalid")
        for error in config_errors:
            st.write(f"- {error}")
        st.stop()

    runtime = runtime_snapshot()
    private_ctx = private_context()
    ticker = get_ticker()
    latest_prices = {
        symbol: float(payload["last"])
        for symbol, payload in ticker.items()
        if isinstance(payload, dict) and "last" in payload
    }
    ticker_rows = market_rows(config, ticker)

    with st.sidebar:
        st.markdown("### Control")
        st.caption(f"Config: `{CONFIG_PATH}`")
        st.caption(f"SQLite: `{DB_PATH}`")
        if st.button("Refresh Dashboard", use_container_width=True):
            st.rerun()
        st.markdown("### Status")
        mode = str(config["mode"]).upper()
        st.markdown(badge(f"Mode {mode}", "info"), unsafe_allow_html=True)
        for item in private_ctx["private_api_capabilities"]:
            tone = "good" if item.endswith("=OK") else "warn" if item.endswith("=PARTIAL") else "bad"
            st.markdown(badge(item, tone), unsafe_allow_html=True)
        st.caption(private_ctx["private_api_status"])

    tabs = st.tabs(["Overview", "Account", "Live Ops", "Reports", "Diagnostics"])
    with tabs[0]:
        render_overview_tab(config, runtime, ticker_rows, private_ctx)
    with tabs[1]:
        render_account_tab(private_ctx, latest_prices, config)
    with tabs[2]:
        render_live_ops_tab(config, runtime, private_ctx, latest_prices)
    with tabs[3]:
        render_reports_tab(today_key(), config)
    with tabs[4]:
        render_diagnostics_tab(config, runtime, private_ctx, latest_prices)


if __name__ == "__main__":
    main()
