from __future__ import annotations

from typing import Any

from clients.bitkub_private_client import BitkubPrivateClient
from services.audit_service import audit_event, new_correlation_id
from services.db_service import (
    insert_execution_order,
    insert_execution_order_event,
    insert_runtime_event,
    update_execution_order,
)
from services.execution_service import (
    build_live_execution_guardrails,
    submit_manual_live_order,
)
from services.reconciliation_service import extract_available_balances
from ui.streamlit.data import calc_daily_totals
from utils.time_utils import now_text


def persist_execution_order_update(
    execution_order_id: int,
    order_record: dict[str, Any],
    order_events: list[dict[str, Any]],
) -> None:
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


def persist_execution_order_insert(
    order_record: dict[str, Any],
    order_events: list[dict[str, Any]],
) -> int:
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


def submit_manual_order_from_ui(
    *,
    client: BitkubPrivateClient,
    config: dict[str, Any],
    runtime: dict[str, Any],
    private_capabilities: list[str],
    account_snapshot: dict[str, Any] | None,
    form_values: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    correlation_id = new_correlation_id("ui_manual_order")
    modified_config = dict(config)
    modified_config["live_manual_order"] = form_values
    audit_event(
        action_type="manual_order",
        actor_type="ui",
        source="streamlit_ui",
        target_type="order_request",
        target_id=str(form_values.get("symbol") or ""),
        symbol=str(form_values.get("symbol") or ""),
        new_value=form_values,
        status="started",
        message="Manual live order requested from Streamlit UI",
        correlation_id=correlation_id,
    )
    _, _, _, total_pnl = calc_daily_totals(runtime["daily_stats"])
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
    try:
        order_record, order_events = submit_manual_live_order(
            client=client,
            config=modified_config,
            rules=config["rules"],
            guardrails=guardrails,
            available_balances=extract_available_balances(account_snapshot),
            created_at=now_text(),
        )
    except Exception as exc:
        audit_event(
            action_type="manual_order",
            actor_type="ui",
            source="streamlit_ui",
            target_type="order_request",
            target_id=str(form_values.get("symbol") or ""),
            symbol=str(form_values.get("symbol") or ""),
            new_value=form_values,
            status="failed",
            message="Manual live order failed from Streamlit UI",
            reason=str(exc),
            correlation_id=correlation_id,
            metadata={"guardrails": guardrails},
        )
        raise
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
    audit_event(
        action_type="manual_order",
        actor_type="ui",
        source="streamlit_ui",
        target_type="execution_order",
        target_id=str(execution_order_id),
        symbol=order_record["symbol"],
        new_value={
            "request": form_values,
            "execution_order_id": execution_order_id,
            "state": order_record["state"],
            "exchange_order_id": order_record.get("exchange_order_id"),
        },
        status="succeeded",
        message="Manual live order submitted from Streamlit UI",
        correlation_id=correlation_id,
        metadata={"guardrails": order_record.get("guardrails")},
    )
    return execution_order_id, order_record
