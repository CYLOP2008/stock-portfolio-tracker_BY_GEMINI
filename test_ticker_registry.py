"""
test_ticker_registry.py
=======================
Unit and integration tests for ticker_registry.py.
"""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ticker_registry import (
    BUNDLED_THAI_FUNDS,
    BUNDLED_THAI_STOCKS,
    BUNDLED_US_STOCKS,
    get_all_symbols,
    get_symbol_display_options,
    get_symbol_info,
    init_ticker_cache,
    search_symbols,
    update_ticker_cache,
)


class TestTickerRegistry(unittest.TestCase):
    """Test ticker registry caching, querying, and search operations."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_registry.db")
        self.json_path = os.path.join(self.temp_dir.name, "test_tickers.json")
        init_ticker_cache(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_and_update_cache(self):
        count = update_ticker_cache(db_path=self.db_path, json_path=self.json_path, force=True)
        self.assertGreater(count, 50)
        self.assertTrue(os.path.exists(self.json_path))

    def test_get_all_symbols(self):
        update_ticker_cache(db_path=self.db_path, json_path=self.json_path)

        all_syms = get_all_symbols(db_path=self.db_path)
        self.assertGreater(len(all_syms), 50)

        # Check US stocks
        us_syms = [s["symbol"] for s in all_syms if s["asset_type"] == "US_STOCK"]
        self.assertIn("AAPL", us_syms)
        self.assertIn("NVDA", us_syms)
        self.assertIn("MSFT", us_syms)

        # Check Thai stocks formatted with .BK
        th_syms = [s["symbol"] for s in all_syms if s["asset_type"] == "TH_STOCK"]
        self.assertIn("PTT.BK", th_syms)
        self.assertIn("AOT.BK", th_syms)
        self.assertIn("CPALL.BK", th_syms)
        for sym in th_syms:
            self.assertTrue(sym.endswith(".BK"))

        # Check Thai mutual funds
        fund_syms = [s["symbol"] for s in all_syms if s["asset_type"] == "TH_MUTUAL_FUND"]
        self.assertIn("ONE-UGG-RA", fund_syms)
        self.assertIn("K-CHANGE-A(A)", fund_syms)
        self.assertIn("SCBDV", fund_syms)

    def test_filtering_by_asset_type(self):
        update_ticker_cache(db_path=self.db_path, json_path=self.json_path)

        us_only = get_all_symbols(db_path=self.db_path, asset_type="US_STOCK")
        self.assertTrue(all(s["asset_type"] == "US_STOCK" for s in us_only))
        self.assertGreater(len(us_only), 10)

        th_only = get_all_symbols(db_path=self.db_path, asset_type="TH_STOCK")
        self.assertTrue(all(s["asset_type"] == "TH_STOCK" for s in th_only))
        self.assertGreater(len(th_only), 10)

        funds_only = get_all_symbols(db_path=self.db_path, asset_type="TH_MUTUAL_FUND")
        self.assertTrue(all(s["asset_type"] == "TH_MUTUAL_FUND" for s in funds_only))
        self.assertGreater(len(funds_only), 10)

    def test_search_symbols(self):
        update_ticker_cache(db_path=self.db_path, json_path=self.json_path)

        # Search by symbol
        res1 = search_symbols("AAPL", db_path=self.db_path)
        self.assertTrue(any(r["symbol"] == "AAPL" for r in res1))

        # Search by company English name
        res2 = search_symbols("Tesla", db_path=self.db_path)
        self.assertTrue(any(r["symbol"] == "TSLA" for r in res2))

        # Search by Thai name / Thai text
        res3 = search_symbols("ปตท", db_path=self.db_path)
        self.assertTrue(any("PTT" in r["symbol"] for r in res3))

        # Search fund name
        res4 = search_symbols("ONE-UGG", db_path=self.db_path)
        self.assertTrue(any("ONE-UGG" in r["symbol"] for r in res4))

    def test_get_symbol_info(self):
        update_ticker_cache(db_path=self.db_path, json_path=self.json_path)

        aapl_info = get_symbol_info("AAPL", db_path=self.db_path)
        self.assertIsNotNone(aapl_info)
        self.assertEqual(aapl_info["symbol"], "AAPL")
        self.assertEqual(aapl_info["currency"], "USD")
        self.assertEqual(aapl_info["asset_type"], "US_STOCK")

        ptt_info = get_symbol_info("PTT.BK", db_path=self.db_path)
        self.assertIsNotNone(ptt_info)
        self.assertEqual(ptt_info["symbol"], "PTT.BK")
        self.assertEqual(ptt_info["currency"], "THB")
        self.assertEqual(ptt_info["asset_type"], "TH_STOCK")

        fund_info = get_symbol_info("ONE-UGG-RA", db_path=self.db_path)
        self.assertIsNotNone(fund_info)
        self.assertEqual(fund_info["symbol"], "ONE-UGG-RA")
        self.assertEqual(fund_info["currency"], "THB")
        self.assertEqual(fund_info["asset_type"], "TH_MUTUAL_FUND")

    def test_get_symbol_display_options(self):
        update_ticker_cache(db_path=self.db_path, json_path=self.json_path)

        options = get_symbol_display_options(asset_type="US_STOCK", db_path=self.db_path)
        self.assertGreater(len(options), 0)
        self.assertTrue(any("AAPL - Apple" in opt for opt in options))


if __name__ == "__main__":
    unittest.main()
