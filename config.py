import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

ROOT_REQUIRED_FIELDS = {
    "base_url",
    "fee_rate",
    "interval_seconds",
    "cooldown_seconds",
    "signal_log_file",
    "trade_log_file",
    "rules",
}

SUPPORTED_MODES = {"paper", "read-only", "live-disabled"}

RULE_REQUIRED_FIELDS = {
    "buy_below",
    "sell_above",
    "budget_thb",
    "stop_loss_percent",
    "take_profit_percent",
    "max_trades_per_day",
}

_ACTIVE_CONFIG: dict[str, Any] | None = None


def _read_config_file() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("config.json must contain a JSON object at the top level")

    if "mode" not in data:
        data["mode"] = "paper"

    return data


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
            "mode must be one of: paper, read-only, live-disabled"
        )

    if not _is_number(config["fee_rate"]) or not (0 <= float(config["fee_rate"]) < 1):
        errors.append("fee_rate must be a number between 0 and 1")

    if not isinstance(config["interval_seconds"], int) or config["interval_seconds"] <= 0:
        errors.append("interval_seconds must be an integer greater than 0")

    if not isinstance(config["cooldown_seconds"], int) or config["cooldown_seconds"] < 0:
        errors.append("cooldown_seconds must be an integer >= 0")

    if not isinstance(config["signal_log_file"], str) or not config["signal_log_file"].strip():
        errors.append("signal_log_file must be a non-empty string")

    if not isinstance(config["trade_log_file"], str) or not config["trade_log_file"].strip():
        errors.append("trade_log_file must be a non-empty string")

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
        return None, [f"unable to read config.json: {e}"]
    except ValueError as e:
        return None, [str(e)]

    errors = validate_config(candidate)
    if errors:
        return None, errors

    activate_config(candidate)
    return candidate, []


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
