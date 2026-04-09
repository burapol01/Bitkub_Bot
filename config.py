import json
from pathlib import Path
from typing import Any

from services.env_service import get_env_path

CONFIG_PATH = get_env_path(
    "BITKUB_CONFIG_PATH",
    Path(__file__).resolve().parent / "config.json",
)
CONFIG_BASE_PATH = get_env_path(
    "BITKUB_CONFIG_BASE_PATH",
    Path(__file__).resolve().parent / "config.base.json",
)

ROOT_REQUIRED_FIELDS = {
    "mode",
    "base_url",
    "fee_rate",
    "interval_seconds",
    "cooldown_seconds",
    "live_execution_enabled",
    "live_auto_entry_enabled",
    "live_auto_exit_enabled",
    "live_auto_entry_require_ranking",
    "live_auto_entry_rank_resolution",
    "live_auto_entry_rank_lookback_days",
    "live_auto_entry_min_score",
    "live_auto_entry_allowed_biases",
    "live_max_order_thb",
    "live_min_thb_balance",
    "live_slippage_tolerance_percent",
    "live_daily_loss_limit_thb",
    "live_manual_order",
    "watchlist_symbols",
    "telegram_enabled",
    "telegram_control_enabled",
    "telegram_notify_events",
    "market_snapshot_retention_days",
    "signal_log_retention_days",
    "runtime_event_retention_days",
    "account_snapshot_retention_days",
    "reconciliation_retention_days",
    "signal_log_file",
    "trade_log_file",
    "rules",
}

SUPPORTED_MODES = {"paper", "read-only", "live-disabled", "live"}

RULE_REQUIRED_FIELDS = {
    "buy_below",
    "sell_above",
    "budget_thb",
    "stop_loss_percent",
    "take_profit_percent",
    "max_trades_per_day",
}

LIVE_MANUAL_ORDER_REQUIRED_FIELDS = {
    "enabled",
    "symbol",
    "side",
    "order_type",
    "amount_thb",
    "amount_coin",
    "rate",
}

SUPPORTED_TELEGRAM_NOTIFY_EVENTS = {
    "config_reload",
    "manual_live_order",
    "auto_live_entry",
    "auto_live_exit",
    "safety_pause",
    "runtime_error",
}

_ACTIVE_CONFIG: dict[str, Any] | None = None


def _is_layered_config_enabled() -> bool:
    try:
        return CONFIG_BASE_PATH.resolve() != CONFIG_PATH.resolve() and CONFIG_BASE_PATH.exists()
    except OSError:
        return CONFIG_BASE_PATH != CONFIG_PATH and CONFIG_BASE_PATH.exists()


def _read_json_object_file(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise OSError(f"{path} does not exist")
        return {}

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object at the top level")

    return data


def _merge_config_layers(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_config_layers(current, value)
        else:
            merged[key] = value
    return merged


def _read_config_file() -> dict[str, Any]:
    if _is_layered_config_enabled():
        base_data = _read_json_object_file(CONFIG_BASE_PATH, required=True)
        override_data = _read_json_object_file(CONFIG_PATH, required=False)
        data = _merge_config_layers(base_data, override_data)
    else:
        data = _read_json_object_file(CONFIG_PATH, required=True)

    if not isinstance(data, dict):
        raise ValueError("config.json must contain a JSON object at the top level")

    if "mode" not in data:
        data["mode"] = "paper"
    if "live_execution_enabled" not in data:
        data["live_execution_enabled"] = False
    if "live_auto_entry_enabled" not in data:
        data["live_auto_entry_enabled"] = False
    if "live_auto_exit_enabled" not in data:
        data["live_auto_exit_enabled"] = False
    if "live_auto_entry_require_ranking" not in data:
        data["live_auto_entry_require_ranking"] = True
    if "live_auto_entry_rank_resolution" not in data:
        data["live_auto_entry_rank_resolution"] = "240"
    if "live_auto_entry_rank_lookback_days" not in data:
        data["live_auto_entry_rank_lookback_days"] = 14
    if "live_auto_entry_min_score" not in data:
        data["live_auto_entry_min_score"] = 50.0
    if "live_auto_entry_allowed_biases" not in data:
        data["live_auto_entry_allowed_biases"] = ["bullish", "mixed"]
    if "live_max_order_thb" not in data:
        data["live_max_order_thb"] = 500
    if "live_min_thb_balance" not in data:
        data["live_min_thb_balance"] = 100
    if "live_slippage_tolerance_percent" not in data:
        data["live_slippage_tolerance_percent"] = 1.0
    if "live_daily_loss_limit_thb" not in data:
        data["live_daily_loss_limit_thb"] = 1000
    if "live_manual_order" not in data:
        data["live_manual_order"] = {
            "enabled": False,
            "symbol": "THB_BTC",
            "side": "buy",
            "order_type": "limit",
            "amount_thb": 100,
            "amount_coin": 0.0001,
            "rate": 1,
        }
    if "watchlist_symbols" not in data:
        rules = data.get("rules") if isinstance(data.get("rules"), dict) else {}
        data["watchlist_symbols"] = sorted(rules.keys())
    if "telegram_enabled" not in data:
        data["telegram_enabled"] = False
    if "telegram_control_enabled" not in data:
        data["telegram_control_enabled"] = False
    if "telegram_notify_events" not in data:
        data["telegram_notify_events"] = [
            "config_reload",
            "safety_pause",
            "manual_live_order",
            "auto_live_entry",
            "auto_live_exit",
            "runtime_error",
        ]
    if "market_snapshot_retention_days" not in data:
        data["market_snapshot_retention_days"] = 30
    if "signal_log_retention_days" not in data:
        data["signal_log_retention_days"] = 30
    if "runtime_event_retention_days" not in data:
        data["runtime_event_retention_days"] = 30
    if "account_snapshot_retention_days" not in data:
        data["account_snapshot_retention_days"] = 30
    if "reconciliation_retention_days" not in data:
        data["reconciliation_retention_days"] = 30

    return data


def _build_config_override(base: Any, current: Any) -> Any:
    if isinstance(base, dict) and isinstance(current, dict):
        diff: dict[str, Any] = {}
        for key in sorted(current):
            current_value = current[key]
            base_value = base.get(key)
            delta = _build_config_override(base_value, current_value)
            if delta is not None:
                diff[key] = delta
        return diff or None

    if base == current:
        return None

    return current


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def ordered_unique_symbols(*groups: Any) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    for group in groups:
        if group is None:
            continue
        for value in group:
            symbol = str(value or "").strip()
            if not symbol or symbol in seen:
                continue
            ordered.append(symbol)
            seen.add(symbol)

    return ordered


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    missing_root_fields = sorted(ROOT_REQUIRED_FIELDS - set(config.keys()))
    for field in missing_root_fields:
        errors.append(f"missing root field: {field}")

    if missing_root_fields:
        return errors

    if not isinstance(config["base_url"], str) or not config["base_url"].strip():
        errors.append("base_url must be a non-empty string")

    mode = config.get("mode")
    if not isinstance(mode, str) or mode not in SUPPORTED_MODES:
        errors.append(
            "mode must be one of: paper, read-only, live-disabled, live"
        )

    if not isinstance(config["live_execution_enabled"], bool):
        errors.append("live_execution_enabled must be true or false")
    if not isinstance(config["live_auto_entry_enabled"], bool):
        errors.append("live_auto_entry_enabled must be true or false")
    if not isinstance(config["live_auto_exit_enabled"], bool):
        errors.append("live_auto_exit_enabled must be true or false")
    if not isinstance(config["live_auto_entry_require_ranking"], bool):
        errors.append("live_auto_entry_require_ranking must be true or false")

    if (
        not isinstance(config["live_auto_entry_rank_resolution"], str)
        or not config["live_auto_entry_rank_resolution"].strip()
    ):
        errors.append("live_auto_entry_rank_resolution must be a non-empty string")
    if (
        not isinstance(config["live_auto_entry_rank_lookback_days"], int)
        or config["live_auto_entry_rank_lookback_days"] <= 0
    ):
        errors.append("live_auto_entry_rank_lookback_days must be an integer greater than 0")
    if (
        not _is_number(config["live_auto_entry_min_score"])
        or float(config["live_auto_entry_min_score"]) < 0
    ):
        errors.append("live_auto_entry_min_score must be a number >= 0")
    allowed_biases = config["live_auto_entry_allowed_biases"]
    if not isinstance(allowed_biases, list) or not allowed_biases:
        errors.append("live_auto_entry_allowed_biases must be a non-empty array")
    else:
        normalized_biases = {
            str(value).strip().lower()
            for value in allowed_biases
            if str(value).strip()
        }
        if not normalized_biases:
            errors.append("live_auto_entry_allowed_biases must contain non-empty strings")
        unsupported_biases = sorted(normalized_biases - {"bullish", "mixed", "weak"})
        for value in unsupported_biases:
            errors.append(
                f"live_auto_entry_allowed_biases contains unsupported value: {value}"
            )

    live_numeric_fields = (
        "live_max_order_thb",
        "live_min_thb_balance",
        "live_slippage_tolerance_percent",
        "live_daily_loss_limit_thb",
    )
    for field in live_numeric_fields:
        if not _is_number(config[field]) or float(config[field]) <= 0:
            errors.append(f"{field} must be a number greater than 0")

    if not _is_number(config["fee_rate"]) or not (0 <= float(config["fee_rate"]) < 1):
        errors.append("fee_rate must be a number between 0 and 1")

    if not isinstance(config["interval_seconds"], int) or config["interval_seconds"] <= 0:
        errors.append("interval_seconds must be an integer greater than 0")

    if not isinstance(config["cooldown_seconds"], int) or config["cooldown_seconds"] < 0:
        errors.append("cooldown_seconds must be an integer >= 0")

    if (
        not isinstance(config["market_snapshot_retention_days"], int)
        or config["market_snapshot_retention_days"] <= 0
    ):
        errors.append("market_snapshot_retention_days must be an integer greater than 0")

    retention_fields = (
        "signal_log_retention_days",
        "runtime_event_retention_days",
        "account_snapshot_retention_days",
        "reconciliation_retention_days",
    )
    for field in retention_fields:
        if not isinstance(config[field], int) or config[field] <= 0:
            errors.append(f"{field} must be an integer greater than 0")

    if not isinstance(config["signal_log_file"], str) or not config["signal_log_file"].strip():
        errors.append("signal_log_file must be a non-empty string")

    if not isinstance(config["trade_log_file"], str) or not config["trade_log_file"].strip():
        errors.append("trade_log_file must be a non-empty string")

    watchlist_symbols = config["watchlist_symbols"]
    if not isinstance(watchlist_symbols, list) or not watchlist_symbols:
        errors.append("watchlist_symbols must be a non-empty array")
    else:
        normalized_watchlist: set[str] = set()
        for raw_symbol in watchlist_symbols:
            if not isinstance(raw_symbol, str) or not raw_symbol.strip():
                errors.append("watchlist_symbols must contain only non-empty strings")
                continue
            normalized_watchlist.add(raw_symbol.strip().upper())
        if len(normalized_watchlist) != len(watchlist_symbols):
            errors.append("watchlist_symbols must not contain duplicates")

    if not isinstance(config["telegram_enabled"], bool):
        errors.append("telegram_enabled must be true or false")
    if not isinstance(config["telegram_control_enabled"], bool):
        errors.append("telegram_control_enabled must be true or false")

    telegram_notify_events = config["telegram_notify_events"]
    if not isinstance(telegram_notify_events, list):
        errors.append("telegram_notify_events must be an array")
    else:
        for event_name in telegram_notify_events:
            if event_name not in SUPPORTED_TELEGRAM_NOTIFY_EVENTS:
                errors.append(
                    "telegram_notify_events contains unsupported value: "
                    f"{event_name}"
                )

    live_manual_order = config["live_manual_order"]
    if not isinstance(live_manual_order, dict):
        errors.append("live_manual_order must be an object")
    else:
        missing_live_manual_order_fields = sorted(
            LIVE_MANUAL_ORDER_REQUIRED_FIELDS - set(live_manual_order.keys())
        )
        for field in missing_live_manual_order_fields:
            errors.append(f"live_manual_order: missing field {field}")

        if not missing_live_manual_order_fields:
            if not isinstance(live_manual_order["enabled"], bool):
                errors.append("live_manual_order.enabled must be true or false")
            if (
                not isinstance(live_manual_order["symbol"], str)
                or not live_manual_order["symbol"].strip()
            ):
                errors.append("live_manual_order.symbol must be a non-empty string")
            if live_manual_order["side"] not in {"buy", "sell"}:
                errors.append("live_manual_order.side must be either buy or sell")
            if live_manual_order["order_type"] != "limit":
                errors.append("live_manual_order.order_type must currently be limit")
            for field in ("amount_thb", "amount_coin", "rate"):
                if not _is_number(live_manual_order[field]) or float(live_manual_order[field]) <= 0:
                    errors.append(f"live_manual_order.{field} must be a number greater than 0")

    rules = config["rules"]
    if not isinstance(rules, dict) or not rules:
        errors.append("rules must be a non-empty object")
        return errors

    for symbol in sorted(rules):
        rule = rules[symbol]
        prefix = f"rules.{symbol}"

        if not isinstance(symbol, str) or not symbol.strip():
            errors.append(f"{prefix}: symbol name must be a non-empty string")
            continue

        if not isinstance(rule, dict):
            errors.append(f"{prefix}: rule must be an object")
            continue

        missing_rule_fields = sorted(RULE_REQUIRED_FIELDS - set(rule.keys()))
        for field in missing_rule_fields:
            errors.append(f"{prefix}: missing field {field}")

        if missing_rule_fields:
            continue

        numeric_fields = (
            "buy_below",
            "sell_above",
            "budget_thb",
            "stop_loss_percent",
            "take_profit_percent",
        )
        for field in numeric_fields:
            if not _is_number(rule[field]):
                errors.append(f"{prefix}.{field} must be numeric")

        if not isinstance(rule["max_trades_per_day"], int) or rule["max_trades_per_day"] <= 0:
            errors.append(f"{prefix}.max_trades_per_day must be an integer greater than 0")

        if any(not _is_number(rule[field]) for field in numeric_fields):
            continue

        buy_below = float(rule["buy_below"])
        sell_above = float(rule["sell_above"])
        budget_thb = float(rule["budget_thb"])
        stop_loss_percent = float(rule["stop_loss_percent"])
        take_profit_percent = float(rule["take_profit_percent"])

        if buy_below <= 0:
            errors.append(f"{prefix}.buy_below must be greater than 0")
        if sell_above <= 0:
            errors.append(f"{prefix}.sell_above must be greater than 0")
        if sell_above <= buy_below:
            errors.append(f"{prefix}.sell_above must be greater than buy_below")
        if budget_thb <= 0:
            errors.append(f"{prefix}.budget_thb must be greater than 0")
        if stop_loss_percent <= 0:
            errors.append(f"{prefix}.stop_loss_percent must be greater than 0")
        if take_profit_percent <= 0:
            errors.append(f"{prefix}.take_profit_percent must be greater than 0")

    return errors


def activate_config(config: dict[str, Any]) -> dict[str, Any]:
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = config
    return _ACTIVE_CONFIG


def load_config() -> dict[str, Any]:
    if _ACTIVE_CONFIG is None:
        reload_config()

    if _ACTIVE_CONFIG is None:
        raise RuntimeError("active config is unavailable")

    return _ACTIVE_CONFIG


def reload_config() -> tuple[dict[str, Any] | None, list[str]]:
    try:
        candidate = _read_config_file()
    except json.JSONDecodeError as e:
        return None, [f"invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}"]
    except OSError as e:
        return None, [f"unable to read config file: {e}"]
    except ValueError as e:
        return None, [str(e)]

    errors = validate_config(candidate)
    if errors:
        return None, errors

    activate_config(candidate)
    return candidate, []


def save_config(config: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    errors = validate_config(config)
    if errors:
        return None, errors

    payload = config
    target_path = CONFIG_PATH
    if _is_layered_config_enabled():
        try:
            base_config = _read_json_object_file(CONFIG_BASE_PATH, required=True)
        except json.JSONDecodeError as e:
            return None, [f"invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}"]
        except (OSError, ValueError) as e:
            return None, [f"unable to read base config: {e}"]
        payload = _build_config_override(base_config, config) or {}

    tmp_path = target_path.with_suffix(".tmp")
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=True)
        tmp_path.replace(target_path)
    except OSError as e:
        return None, [f"unable to write config file: {e}"]

    activate_config(config)
    return config, []


def _format_scalar(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.8f}".rstrip("0").rstrip(".")
    return str(value)


def _format_rule_summary(symbol: str, rule: dict[str, Any]) -> str:
    return (
        f"{symbol}: buy_below={float(rule['buy_below']):,.8f}, "
        f"sell_above={float(rule['sell_above']):,.8f}, "
        f"stop_loss={float(rule['stop_loss_percent']):.2f}%, "
        f"take_profit={float(rule['take_profit_percent']):.2f}%, "
        f"max_trades={int(rule['max_trades_per_day'])}"
    )


def summarize_config_changes(
    old_config: dict[str, Any] | None, new_config: dict[str, Any]
) -> list[str]:
    if old_config is None:
        return ["Initial config loaded."]

    lines: list[str] = []
    scalar_fields = (
        "mode",
        "base_url",
        "fee_rate",
        "interval_seconds",
        "cooldown_seconds",
        "live_execution_enabled",
        "live_auto_entry_enabled",
        "live_auto_exit_enabled",
        "live_auto_entry_require_ranking",
        "live_auto_entry_rank_resolution",
        "live_auto_entry_rank_lookback_days",
        "live_auto_entry_min_score",
        "live_auto_entry_allowed_biases",
        "live_max_order_thb",
        "live_min_thb_balance",
        "live_slippage_tolerance_percent",
        "live_daily_loss_limit_thb",
        "live_manual_order",
        "telegram_enabled",
        "telegram_control_enabled",
        "market_snapshot_retention_days",
        "signal_log_retention_days",
        "runtime_event_retention_days",
        "account_snapshot_retention_days",
        "reconciliation_retention_days",
        "signal_log_file",
        "trade_log_file",
    )

    for field in scalar_fields:
        old_value = old_config.get(field)
        new_value = new_config.get(field)
        if old_value != new_value:
            lines.append(
                f"{field}: {_format_scalar(old_value)} -> {_format_scalar(new_value)}"
            )

    old_watchlist = [str(symbol) for symbol in old_config.get("watchlist_symbols", [])]
    new_watchlist = [str(symbol) for symbol in new_config.get("watchlist_symbols", [])]
    if old_watchlist != new_watchlist:
        lines.append(
            "watchlist_symbols: "
            f"{', '.join(old_watchlist) if old_watchlist else 'none'} -> "
            f"{', '.join(new_watchlist) if new_watchlist else 'none'}"
        )

    old_notify_events = [str(event_name) for event_name in old_config.get("telegram_notify_events", [])]
    new_notify_events = [str(event_name) for event_name in new_config.get("telegram_notify_events", [])]
    if old_notify_events != new_notify_events:
        lines.append(
            "telegram_notify_events: "
            f"{', '.join(old_notify_events) if old_notify_events else 'none'} -> "
            f"{', '.join(new_notify_events) if new_notify_events else 'none'}"
        )

    old_rules = old_config.get("rules", {})
    new_rules = new_config.get("rules", {})
    all_symbols = sorted(set(old_rules) | set(new_rules))
    tracked_rule_fields = (
        "buy_below",
        "sell_above",
        "budget_thb",
        "stop_loss_percent",
        "take_profit_percent",
        "max_trades_per_day",
    )

    for symbol in all_symbols:
        if symbol not in old_rules:
            lines.append(f"added {symbol}")
            lines.append(_format_rule_summary(symbol, new_rules[symbol]))
            continue

        if symbol not in new_rules:
            lines.append(f"removed {symbol}")
            continue

        changed_fields: list[str] = []
        for field in tracked_rule_fields:
            old_value = old_rules[symbol].get(field)
            new_value = new_rules[symbol].get(field)
            if old_value != new_value:
                changed_fields.append(
                    f"{field}: {_format_scalar(old_value)} -> {_format_scalar(new_value)}"
                )

        if changed_fields:
            lines.append(f"updated {symbol}")
            lines.extend(changed_fields)

    if not lines:
        lines.append("No effective config changes detected.")

    return lines
