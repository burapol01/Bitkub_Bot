from __future__ import annotations

import unittest

from services.market_symbol_service import (
    build_market_symbol_directory,
    build_non_exchange_symbol_source_map,
)


class MarketSymbolServiceTests(unittest.TestCase):
    def test_build_market_symbol_directory_maps_source_by_normalized_symbol(self) -> None:
        directory = build_market_symbol_directory(
            [
                {
                    "symbol": "BTC_THB",
                    "source": "exchange",
                    "market_segment": "SPOT",
                    "status": "active",
                    "name": "Bitcoin",
                },
                {
                    "symbol": "PENGU_THB",
                    "source": "broker",
                    "market_segment": "SPOT",
                    "status": "active",
                    "name": "Pudgy Penguins",
                },
            ]
        )

        self.assertEqual(directory["symbols"], ["THB_BTC", "THB_PENGU"])
        self.assertEqual(directory["source_by_symbol"]["THB_BTC"], "exchange")
        self.assertEqual(directory["source_by_symbol"]["THB_PENGU"], "broker")
        self.assertEqual(directory["exchange_symbols"], ["THB_BTC"])
        self.assertEqual(directory["non_exchange_symbols"], ["THB_PENGU"])

    def test_build_non_exchange_symbol_source_map_returns_only_non_exchange_symbols(self) -> None:
        blocked = build_non_exchange_symbol_source_map(
            ["THB_BTC", "THB_PENGU", "THB_POPCAT"],
            source_by_symbol={
                "THB_BTC": "exchange",
                "THB_PENGU": "broker",
                "THB_POPCAT": "broker",
            },
        )

        self.assertEqual(
            blocked,
            {
                "THB_PENGU": "broker",
                "THB_POPCAT": "broker",
            },
        )


if __name__ == "__main__":
    unittest.main()
