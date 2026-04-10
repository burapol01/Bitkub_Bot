from __future__ import annotations

import unittest

from services.account_service import probe_open_orders_support_snapshot


class _FakePrivateClient:
    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self._responses = responses

    def probe_open_orders_variants(self, symbol: str) -> dict[str, object]:
        return dict(self._responses[str(symbol)])


class OpenOrdersSupportProbeTests(unittest.TestCase):
    def test_supported_symbol_reports_working_request_recipe(self) -> None:
        client = _FakePrivateClient(
            {
                "THB_TRX": {
                    "quote_base_lower": {"ok": True, "data": {"result": []}, "error": None},
                    "base_quote_upper": {
                        "ok": False,
                        "data": None,
                        "error": "Endpoint not found for path /api/market/my-open-orders",
                    },
                    "without_symbol": {
                        "ok": False,
                        "data": None,
                        "error": "Open-orders endpoint requires sym; global open-orders query is not supported by this API path.",
                    },
                }
            }
        )

        snapshot = probe_open_orders_support_snapshot(
            client,
            symbols=["THB_TRX"],
            source_by_symbol={"THB_TRX": "exchange"},
        )

        self.assertEqual(snapshot["summary"]["supported"], 1)
        row = snapshot["rows"][0]
        self.assertEqual(row["status"], "SUPPORTED")
        self.assertEqual(row["market_source"], "exchange")
        self.assertEqual(row["working_variant"], "quote_base_lower")
        self.assertEqual(row["request_recipe"], "GET my-open-orders?sym=trx_thb")
        self.assertIn("sym=trx_thb", row["next_step"])

    def test_unsupported_symbol_is_listed_when_both_known_formats_fail(self) -> None:
        client = _FakePrivateClient(
            {
                "THB_PENGU": {
                    "quote_base_lower": {
                        "ok": False,
                        "data": None,
                        "error": "Endpoint not found for path /api/market/my-open-orders",
                    },
                    "base_quote_upper": {
                        "ok": False,
                        "data": None,
                        "error": "Endpoint not found for path /api/market/my-open-orders",
                    },
                    "without_symbol": {
                        "ok": False,
                        "data": None,
                        "error": "Open-orders endpoint requires sym; global open-orders query is not supported by this API path.",
                    },
                }
            }
        )

        snapshot = probe_open_orders_support_snapshot(
            client,
            symbols=["THB_PENGU"],
            source_by_symbol={"THB_PENGU": "broker"},
        )

        self.assertEqual(snapshot["summary"]["unsupported"], 1)
        self.assertEqual(snapshot["unsupported_symbols"], ["THB_PENGU"])
        row = snapshot["rows"][0]
        self.assertEqual(row["status"], "UNSUPPORTED")
        self.assertEqual(row["market_source"], "broker")
        self.assertIn("No known sym format passes", row["next_step"])

    def test_global_only_symbol_is_marked_for_local_filtering(self) -> None:
        client = _FakePrivateClient(
            {
                "THB_BTC": {
                    "quote_base_lower": {
                        "ok": False,
                        "data": None,
                        "error": "Bitkub API HTTP error 400: bad sym",
                    },
                    "base_quote_upper": {
                        "ok": False,
                        "data": None,
                        "error": "Bitkub API HTTP error 400: bad sym",
                    },
                    "without_symbol": {
                        "ok": True,
                        "data": {"result": [{"sym": "btc_thb"}]},
                        "error": None,
                    },
                }
            }
        )

        snapshot = probe_open_orders_support_snapshot(client, symbols=["THB_BTC"])

        self.assertEqual(snapshot["summary"]["global_only"], 1)
        row = snapshot["rows"][0]
        self.assertEqual(row["status"], "GLOBAL_ONLY")
        self.assertEqual(row["request_recipe"], "GET my-open-orders")
        self.assertIn("filter rows locally", row["next_step"])


if __name__ == "__main__":
    unittest.main()
