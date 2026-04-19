import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import requests

from config import load_config
from services.api_retry_service import (
    classify_retry_error,
    get_retry_policy,
    log_api_retry_event,
    retry_delay_seconds,
    should_retry,
)
from services.env_service import get_bitkub_api_credentials


class BitkubPrivateClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        category: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        ambiguous: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
        self.ambiguous = ambiguous


class BitkubMissingCredentialsError(BitkubPrivateClientError):
    pass


class BitkubAPIResponseError(BitkubPrivateClientError):
    pass


def is_unsupported_symbol_error_message(error: str | None) -> bool:
    normalized = str(error or "").strip().lower()
    if not normalized:
        return False

    return (
        "bitkub api error=61" in normalized
        or "endpoint not found for path /api/market/my-open-orders" in normalized
        or "endpoint not found for path /api/v3/market/my-open-orders" in normalized
    )


def is_symbol_required_error_message(error: str | None) -> bool:
    normalized = str(error or "").strip().lower()
    if not normalized:
        return False

    return (
        "open-orders endpoint requires sym" in normalized
        or "order-history endpoint requires sym" in normalized
        or (
            "bitkub api error=10" in normalized
            and "sym" in normalized
            and "required" in normalized
        )
    )


def _json_body(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _split_symbol(symbol: str) -> tuple[str, str]:
    parts = str(symbol).split("_", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid trading symbol format: {symbol}")
    return parts[0], parts[1]


def _quote_base_lower_symbol(symbol: str) -> str:
    quote_asset, base_asset = _split_symbol(symbol)
    return f"{base_asset.lower()}_{quote_asset.lower()}"


def _base_quote_upper_symbol(symbol: str) -> str:
    quote_asset, base_asset = _split_symbol(symbol)
    return f"{base_asset.upper()}_{quote_asset.upper()}"


def _quote_base_upper_symbol(symbol: str) -> str:
    quote_asset, base_asset = _split_symbol(symbol)
    return f"{quote_asset.upper()}_{base_asset.upper()}"


def _cancel_symbol_variants(symbol: str) -> tuple[str, ...]:
    return (
        _quote_base_upper_symbol(symbol).lower(),
        _quote_base_lower_symbol(symbol),
        _base_quote_upper_symbol(symbol).lower(),
    )


def describe_open_orders_symbol_variants(symbol: str) -> dict[str, str | None]:
    return {
        "quote_base_lower": _quote_base_lower_symbol(symbol),
        "base_quote_upper": _base_quote_upper_symbol(symbol),
        "without_symbol": None,
    }


class BitkubPrivateClient:
    SERVER_TIME_PATHS = ("/api/v3/servertime", "/api/servertime")
    WALLET_PATHS = ("/api/v3/market/wallet", "/api/market/wallet")
    BALANCES_PATHS = ("/api/v3/market/balances", "/api/market/balances")
    PLACE_BID_PATHS = ("/api/v3/market/place-bid", "/api/market/place-bid")
    PLACE_ASK_PATHS = ("/api/v3/market/place-ask", "/api/market/place-ask")
    CANCEL_ORDER_PATHS = ("/api/v3/market/cancel-order", "/api/market/cancel-order")
    OPEN_ORDERS_PATHS = ("/api/v3/market/my-open-orders", "/api/market/my-open-orders")
    ORDER_INFO_PATHS = ("/api/v3/market/order-info", "/api/market/order-info")
    ORDER_HISTORY_PATHS = (
        "/api/v3/market/my-order-history",
        "/api/market/my-order-history",
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout: int = 10,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    @classmethod
    def from_env(cls) -> "BitkubPrivateClient":
        api_key, api_secret = get_bitkub_api_credentials()
        return cls(api_key=api_key, api_secret=api_secret)

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def base_url(self) -> str:
        config = load_config()
        return str(config["base_url"]).rstrip("/")

    def _require_credentials(self):
        if not self.is_configured():
            raise BitkubMissingCredentialsError(
                "BITKUB_API_KEY and BITKUB_API_SECRET must be set for private API access."
            )

    def _timestamp_ms(self) -> str:
        return str(int(time.time() * 1000))

    def _signature(
        self,
        *,
        timestamp: str,
        method: str,
        request_path: str,
        body: str,
    ) -> str:
        self._require_credentials()
        payload = f"{timestamp}{method.upper()}{request_path}{body}"
        return hmac.new(
            str(self.api_secret).encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _signed_headers(self, *, timestamp: str, signature: str) -> dict[str, str]:
        return {
            "X-BTK-APIKEY": str(self.api_key),
            "X-BTK-TIMESTAMP": timestamp,
            "X-BTK-SIGN": signature,
        }

    def _request(
        self,
        method: str,
        path_candidates: tuple[str, ...],
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        auth_required: bool = True,
        retry_policy: str = "open_order_status_read",
        action: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        body = _json_body(payload)
        query_string = ""
        if params:
            query_string = urlencode(
                [(key, value) for key, value in params.items() if value is not None]
            )

        last_error: Exception | None = None
        policy = get_retry_policy(retry_policy)

        for path in path_candidates:
            request_path = path
            if query_string:
                request_path = f"{path}?{query_string}"

            for attempt in range(1, int(policy.max_attempts) + 1):
                timestamp = self._timestamp_ms()
                headers = {}
                if auth_required:
                    signature = self._signature(
                        timestamp=timestamp,
                        method=method,
                        request_path=request_path,
                        body=body,
                    )
                    headers = self._signed_headers(timestamp=timestamp, signature=signature)

                try:
                    response = self.session.request(
                        method=method.upper(),
                        url=f"{self.base_url}{path}",
                        params=params,
                        data=body if method.upper() != "GET" else None,
                        headers=headers,
                        timeout=self.timeout,
                    )
                except requests.RequestException as e:
                    classification = classify_retry_error(error=e)
                    last_error = BitkubPrivateClientError(
                        str(e),
                        category=classification.get("category"),
                        retryable=classification.get("retryable"),
                        ambiguous=classification.get("ambiguous"),
                    )
                    if should_retry(
                        policy_name=retry_policy,
                        classification=classification,
                        attempt=attempt,
                    ):
                        delay_seconds = retry_delay_seconds(
                            policy_name=retry_policy,
                            attempt=attempt,
                        )
                        log_api_retry_event(
                            endpoint=path,
                            action=action or method.lower(),
                            attempt=attempt,
                            policy_name=retry_policy,
                            classification=classification,
                            outcome="retrying",
                            correlation_id=correlation_id,
                            delay_seconds=delay_seconds,
                        )
                        time.sleep(delay_seconds)
                        continue
                    log_api_retry_event(
                        endpoint=path,
                        action=action or method.lower(),
                        attempt=attempt,
                        policy_name=retry_policy,
                        classification=classification,
                        outcome="give_up",
                        correlation_id=correlation_id,
                    )
                    break

                if response.status_code == 404:
                    classification = classify_retry_error(
                        status_code=response.status_code,
                        response_text=response.text,
                    )
                    log_api_retry_event(
                        endpoint=path,
                        action=action or method.lower(),
                        attempt=attempt,
                        policy_name=retry_policy,
                        classification=classification,
                        outcome="path_fallback",
                        correlation_id=correlation_id,
                        status_code=response.status_code,
                    )
                    last_error = BitkubPrivateClientError(
                        f"Endpoint not found for path {path}",
                        category=classification.get("category"),
                        status_code=response.status_code,
                        retryable=classification.get("retryable"),
                        ambiguous=classification.get("ambiguous"),
                    )
                    break

                try:
                    response.raise_for_status()
                except requests.HTTPError as e:
                    classification = classify_retry_error(
                        error=e,
                        status_code=response.status_code,
                        response_text=response.text,
                    )
                    last_error = BitkubPrivateClientError(
                        f"Bitkub API HTTP error {response.status_code}: {response.text}",
                        category=classification.get("category"),
                        status_code=response.status_code,
                        retryable=classification.get("retryable"),
                        ambiguous=classification.get("ambiguous"),
                    )
                    if should_retry(
                        policy_name=retry_policy,
                        classification=classification,
                        attempt=attempt,
                    ):
                        delay_seconds = retry_delay_seconds(
                            policy_name=retry_policy,
                            attempt=attempt,
                        )
                        log_api_retry_event(
                            endpoint=path,
                            action=action or method.lower(),
                            attempt=attempt,
                            policy_name=retry_policy,
                            classification=classification,
                            outcome="retrying",
                            correlation_id=correlation_id,
                            delay_seconds=delay_seconds,
                            status_code=response.status_code,
                        )
                        time.sleep(delay_seconds)
                        continue
                    log_api_retry_event(
                        endpoint=path,
                        action=action or method.lower(),
                        attempt=attempt,
                        policy_name=retry_policy,
                        classification=classification,
                        outcome="give_up",
                        correlation_id=correlation_id,
                        status_code=response.status_code,
                    )
                    break

                try:
                    data = response.json()
                except ValueError as e:
                    classification = classify_retry_error(
                        error=e,
                        status_code=response.status_code,
                        error_message="Bitkub API returned invalid JSON.",
                    )
                    last_error = BitkubPrivateClientError(
                        "Bitkub API returned invalid JSON.",
                        category=classification.get("category"),
                        status_code=response.status_code,
                        retryable=classification.get("retryable"),
                        ambiguous=classification.get("ambiguous"),
                    )
                    if should_retry(
                        policy_name=retry_policy,
                        classification=classification,
                        attempt=attempt,
                    ):
                        delay_seconds = retry_delay_seconds(
                            policy_name=retry_policy,
                            attempt=attempt,
                        )
                        log_api_retry_event(
                            endpoint=path,
                            action=action or method.lower(),
                            attempt=attempt,
                            policy_name=retry_policy,
                            classification=classification,
                            outcome="retrying",
                            correlation_id=correlation_id,
                            delay_seconds=delay_seconds,
                            status_code=response.status_code,
                        )
                        time.sleep(delay_seconds)
                        continue
                    log_api_retry_event(
                        endpoint=path,
                        action=action or method.lower(),
                        attempt=attempt,
                        policy_name=retry_policy,
                        classification=classification,
                        outcome="give_up",
                        correlation_id=correlation_id,
                        status_code=response.status_code,
                    )
                    break

                if isinstance(data, dict) and data.get("error") not in (None, 0):
                    api_message = (
                        f"Bitkub API error={data.get('error')} message={data.get('message') or data.get('result')}"
                    )
                    classification = classify_retry_error(error_message=api_message)
                    log_api_retry_event(
                        endpoint=path,
                        action=action or method.lower(),
                        attempt=attempt,
                        policy_name=retry_policy,
                        classification=classification,
                        outcome="give_up",
                        correlation_id=correlation_id,
                        status_code=response.status_code,
                    )
                    raise BitkubAPIResponseError(
                        api_message,
                        category=classification.get("category"),
                        status_code=response.status_code,
                        retryable=classification.get("retryable"),
                        ambiguous=classification.get("ambiguous"),
                    )

                if attempt > 1:
                    log_api_retry_event(
                        endpoint=path,
                        action=action or method.lower(),
                        attempt=attempt,
                        policy_name=retry_policy,
                        classification={
                            "category": "success_after_retry",
                            "retryable": False,
                            "ambiguous": False,
                            "reason": "request succeeded after retry",
                        },
                        outcome="succeeded_after_retry",
                        correlation_id=correlation_id,
                        status_code=response.status_code,
                    )

                return data

        if last_error is not None:
            raise last_error

        raise BitkubPrivateClientError("Bitkub API request failed.")

    def _request_methods(
        self,
        methods: tuple[str, ...],
        path_candidates: tuple[str, ...],
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        auth_required: bool = True,
        retry_policy: str = "open_order_status_read",
        action: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        last_error: Exception | None = None

        for method in methods:
            try:
                return self._request(
                    method,
                    path_candidates,
                    params=params,
                    payload=payload,
                    auth_required=auth_required,
                    retry_policy=retry_policy,
                    action=action,
                    correlation_id=correlation_id,
                )
            except BitkubPrivateClientError as e:
                last_error = e

        if last_error is not None:
            raise last_error

        raise BitkubPrivateClientError("Bitkub API request failed.")

    def get_server_time(self) -> Any:
        return self._request(
            "GET",
            self.SERVER_TIME_PATHS,
            auth_required=False,
            retry_policy="market_public_read",
            action="get_server_time",
        )

    def get_wallet(self) -> Any:
        return self._request_methods(
            ("POST", "GET"),
            self.WALLET_PATHS,
            retry_policy="balance_account_read",
            action="get_wallet",
        )

    def get_balances(self) -> Any:
        return self._request_methods(
            ("POST", "GET"),
            self.BALANCES_PATHS,
            retry_policy="balance_account_read",
            action="get_balances",
        )

    def prepare_place_bid_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_payload = dict(payload)
        if normalized_payload.get("sym") is not None:
            normalized_payload["sym"] = _quote_base_lower_symbol(
                str(normalized_payload["sym"])
            )
        return normalized_payload

    def place_bid(self, payload: dict[str, Any], *, correlation_id: str | None = None) -> Any:
        return self._request(
            "POST",
            self.PLACE_BID_PATHS,
            payload=self.prepare_place_bid_payload(payload),
            retry_policy="create_order",
            action="place_bid",
            correlation_id=correlation_id,
        )

    def prepare_place_ask_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_payload = dict(payload)
        if normalized_payload.get("sym") is not None:
            normalized_payload["sym"] = _quote_base_lower_symbol(
                str(normalized_payload["sym"])
            )
        return normalized_payload

    def place_ask(self, payload: dict[str, Any], *, correlation_id: str | None = None) -> Any:
        return self._request(
            "POST",
            self.PLACE_ASK_PATHS,
            payload=self.prepare_place_ask_payload(payload),
            retry_policy="create_order",
            action="place_ask",
            correlation_id=correlation_id,
        )

    def prepare_cancel_order_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_payload = dict(payload)
        if normalized_payload.get("sym") is not None:
            normalized_payload["sym"] = str(normalized_payload["sym"]).lower()
        return normalized_payload

    def cancel_order(self, payload: dict[str, Any], *, correlation_id: str | None = None) -> Any:
        normalized_payload = dict(payload)
        symbol = normalized_payload.get("sym")
        if symbol is None:
            return self._request(
                "POST",
                self.CANCEL_ORDER_PATHS,
                payload=self.prepare_cancel_order_payload(normalized_payload),
                retry_policy="cancel_order",
                action="cancel_order",
                correlation_id=correlation_id,
            )

        last_error: Exception | None = None
        for sym_value in _cancel_symbol_variants(str(symbol)):
            try:
                attempt_payload = dict(normalized_payload)
                attempt_payload["sym"] = sym_value
                return self._request(
                    "POST",
                    self.CANCEL_ORDER_PATHS,
                    payload=attempt_payload,
                    retry_policy="cancel_order",
                    action="cancel_order",
                    correlation_id=correlation_id,
                )
            except BitkubPrivateClientError as e:
                last_error = e

        if last_error is not None:
            raise last_error
        raise BitkubPrivateClientError("Unable to cancel order.")

    def get_open_orders(self, symbol: str | None = None) -> Any:
        if symbol is None:
            try:
                return self._request(
                    "GET",
                    self.OPEN_ORDERS_PATHS,
                    retry_policy="open_order_status_read",
                    action="get_open_orders",
                )
            except BitkubPrivateClientError as e:
                if is_symbol_required_error_message(str(e)):
                    raise BitkubPrivateClientError(
                        "Open-orders endpoint requires sym; global open-orders query is not supported by this API path."
                    ) from e
                raise

        symbol_variants = (
            _quote_base_lower_symbol(symbol),
            _base_quote_upper_symbol(symbol),
        )
        last_error: Exception | None = None

        for sym_value in symbol_variants:
            try:
                return self._request(
                    "GET",
                    self.OPEN_ORDERS_PATHS,
                    params={"sym": sym_value},
                    retry_policy="open_order_status_read",
                    action="get_open_orders",
                )
            except BitkubPrivateClientError as e:
                last_error = e

        if last_error is not None:
            raise last_error
        raise BitkubPrivateClientError("Unable to fetch open orders.")

    def get_order_info(
        self,
        *,
        order_id: int | str,
        symbol: str | None = None,
        side: str | None = None,
    ) -> Any:
        base_params = {"id": order_id, "sd": side}

        if symbol is None:
            return self._request(
                "GET",
                self.ORDER_INFO_PATHS,
                params=base_params,
                retry_policy="open_order_status_read",
                action="get_order_info",
            )

        symbol_variants = (
            _quote_base_lower_symbol(symbol),
            _base_quote_upper_symbol(symbol),
        )
        last_error: Exception | None = None

        for sym_value in symbol_variants:
            try:
                return self._request(
                    "GET",
                    self.ORDER_INFO_PATHS,
                    params={**base_params, "sym": sym_value},
                    retry_policy="open_order_status_read",
                    action="get_order_info",
                )
            except BitkubPrivateClientError as e:
                last_error = e

        if last_error is not None:
            raise last_error
        raise BitkubPrivateClientError("Unable to fetch order info.")

    def get_order_history(
        self,
        *,
        symbol: str | None = None,
        page: int | None = None,
        limit: int | None = None,
    ) -> Any:
        base_params = {"p": page, "lmt": limit}

        if symbol is None:
            try:
                return self._request(
                    "GET",
                    self.ORDER_HISTORY_PATHS,
                    params=base_params,
                    retry_policy="open_order_status_read",
                    action="get_order_history",
                )
            except BitkubPrivateClientError as e:
                if is_symbol_required_error_message(str(e)):
                    raise BitkubPrivateClientError(
                        "Order-history endpoint requires sym; global order-history query is not supported by this API path."
                    ) from e
                raise

        symbol_variants = (
            _base_quote_upper_symbol(symbol),
            _quote_base_lower_symbol(symbol),
        )
        last_error: Exception | None = None

        for sym_value in symbol_variants:
            try:
                return self._request(
                    "GET",
                    self.ORDER_HISTORY_PATHS,
                    params={**base_params, "sym": sym_value},
                    retry_policy="open_order_status_read",
                    action="get_order_history",
                )
            except BitkubPrivateClientError as e:
                last_error = e

        if last_error is not None:
            raise last_error
        raise BitkubPrivateClientError("Unable to fetch order history.")

    def probe_open_orders_variants(self, symbol: str) -> dict[str, Any]:
        variants: dict[str, Any] = {}
        attempts = {
            "quote_base_lower": lambda: self._request(
                "GET",
                self.OPEN_ORDERS_PATHS,
                params={"sym": _quote_base_lower_symbol(symbol)},
            ),
            "base_quote_upper": lambda: self._request(
                "GET",
                self.OPEN_ORDERS_PATHS,
                params={"sym": _base_quote_upper_symbol(symbol)},
            ),
            "without_symbol": lambda: self.get_open_orders(),
        }

        for name, fetcher in attempts.items():
            try:
                variants[name] = {"ok": True, "data": fetcher(), "error": None}
            except BitkubPrivateClientError as e:
                variants[name] = {"ok": False, "data": None, "error": str(e)}

        return variants

    def probe_order_history_variants(self, symbol: str) -> dict[str, Any]:
        variants: dict[str, Any] = {}
        attempts = {
            "base_quote_upper": lambda: self._request(
                "GET",
                self.ORDER_HISTORY_PATHS,
                params={"sym": _base_quote_upper_symbol(symbol)},
            ),
            "quote_base_lower": lambda: self._request(
                "GET",
                self.ORDER_HISTORY_PATHS,
                params={"sym": _quote_base_lower_symbol(symbol)},
            ),
            "base_quote_upper_with_lmt": lambda: self._request(
                "GET",
                self.ORDER_HISTORY_PATHS,
                params={"sym": _base_quote_upper_symbol(symbol), "lmt": 1},
            ),
            "without_symbol": lambda: self.get_order_history(),
        }

        for name, fetcher in attempts.items():
            try:
                variants[name] = {"ok": True, "data": fetcher(), "error": None}
            except BitkubPrivateClientError as e:
                variants[name] = {"ok": False, "data": None, "error": str(e)}

        return variants
