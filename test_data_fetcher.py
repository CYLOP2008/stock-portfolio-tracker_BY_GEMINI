"""
test_data_fetcher.py
====================
Unit and integration test suite for data_fetcher.py
"""

import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

from data_fetcher import (
    clear_price_cache,
    format_ticker_symbol,
    get_stock_price,
    get_historical_stock_data,
    get_usd_thb_rate,
    get_thai_fund_nav,
    get_batch_stock_prices,
    get_portfolio_data,
    _fetch_sec_open_api_nav,
    _fetch_finnomena_fallback_nav,
    validate_symbol,
    validate_symbol_detailed,
)


class TestDataFetcherFormatting(unittest.TestCase):
    def test_format_ticker_symbol(self):
        self.assertEqual(format_ticker_symbol("aapl"), "AAPL")
        self.assertEqual(format_ticker_symbol("  ptt.bk  "), "PTT.BK")
        self.assertEqual(format_ticker_symbol(""), "")
        self.assertEqual(format_ticker_symbol(None), "")


class TestDataFetcherUnit(unittest.TestCase):
    def setUp(self):
        clear_price_cache()
    @patch("data_fetcher.yf.Ticker")
    def test_get_stock_price_success(self, mock_ticker_cls):
        mock_instance = MagicMock()
        mock_instance.fast_info.last_price = 185.5
        mock_instance.fast_info.previous_close = 180.0
        mock_instance.fast_info.open = 181.0
        mock_instance.fast_info.day_high = 186.0
        mock_instance.fast_info.day_low = 180.5
        mock_instance.fast_info.last_volume = 45000000
        mock_instance.fast_info.currency = "USD"
        mock_instance.fast_info.year_high = 200.0
        mock_instance.fast_info.year_low = 140.0
        mock_ticker_cls.return_value = mock_instance

        res = get_stock_price("AAPL")
        self.assertTrue(res["success"])
        self.assertEqual(res["ticker"], "AAPL")
        self.assertEqual(res["current_price"], 185.5)
        self.assertEqual(res["previous_close"], 180.0)
        self.assertEqual(res["change"], 5.5)
        self.assertEqual(res["change_percent"], 3.06)
        self.assertEqual(res["currency"], "USD")
        self.assertIsNone(res["error"])

    @patch("data_fetcher.yf.Ticker")
    def test_get_stock_price_invalid_ticker(self, mock_ticker_cls):
        mock_instance = MagicMock()
        mock_instance.fast_info = None
        mock_instance.history.return_value = pd.DataFrame()
        mock_instance.info = {}
        mock_ticker_cls.return_value = mock_instance

        res = get_stock_price("INVALID999")
        self.assertFalse(res["success"])
        self.assertIn("No market price data found", res["error"])

    @patch("data_fetcher.yf.Ticker")
    def test_get_usd_thb_rate_success(self, mock_ticker_cls):
        mock_instance = MagicMock()
        mock_instance.fast_info.last_price = 36.25
        mock_instance.fast_info.previous_close = 36.00
        mock_ticker_cls.return_value = mock_instance

        res = get_usd_thb_rate()
        self.assertTrue(res["success"])
        self.assertEqual(res["pair"], "USD/THB")
        self.assertEqual(res["rate"], 36.25)
        self.assertEqual(res["change"], 0.25)
        self.assertAlmostEqual(res["change_percent"], 0.69, places=2)

    @patch("data_fetcher.yf.Ticker")
    def test_get_historical_stock_data(self, mock_ticker_cls):
        mock_instance = MagicMock()
        sample_df = pd.DataFrame({
            "Open": [150.0, 152.0],
            "High": [155.0, 156.0],
            "Low": [149.0, 151.0],
            "Close": [153.0, 155.0],
            "Volume": [10000, 12000]
        }, index=pd.date_range("2026-01-01", periods=2))
        mock_instance.history.return_value = sample_df
        mock_ticker_cls.return_value = mock_instance

        df = get_historical_stock_data("AAPL", period="5d")
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 2)
        self.assertIn("Date", df.columns)
        self.assertIn("Close", df.columns)

    @patch("data_fetcher.requests.get")
    def test_sec_open_api_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "proj_name_th": "กองทุนเปิดไทยพาณิชย์หุ้นปันผล",
                "last_val": 12.3456,
                "previous_val": 12.2000,
                "nav_date": "2026-08-20",
                "unique_id": "SCBAM",
            }
        ]
        mock_get.return_value = mock_resp

        res = _fetch_sec_open_api_nav("SCBDV", sec_api_key="test_api_key")
        self.assertIsNotNone(res)
        self.assertTrue(res["success"])
        self.assertEqual(res["nav"], 12.3456)
        self.assertEqual(res["source"], "SEC_Open_API")

    @patch("data_fetcher.requests.get")
    def test_finnomena_fallback_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": True,
            "data": {
                "fund_name": "SCB Dividend Stock Fund",
                "nav": 11.895,
                "nav_date": "2026-08-20",
                "change": 0.05,
                "change_percent": 0.42,
                "amc_name": "SCBAM"
            }
        }
        mock_get.return_value = mock_resp

        res = _fetch_finnomena_fallback_nav("SCBDV")
        self.assertIsNotNone(res)
        self.assertTrue(res["success"])
        self.assertEqual(res["nav"], 11.895)
        self.assertEqual(res["source"], "Finnomena_Public_API")

    @patch("data_fetcher._fetch_pythainav_fallback")
    def test_pythainav_fallback_success(self, mock_pythainav):
        mock_pythainav.return_value = {
            "fund_code": "SCBDV",
            "fund_name": "Thai Mutual Fund (SCBDV)",
            "nav": 8.5505,
            "previous_nav": 8.5505,
            "change": 0.0,
            "change_percent": 0.0,
            "nav_date": "2026-08-20",
            "amc_name": "",
            "source": "PyThaiNAV_Public",
            "success": True,
            "error": None,
        }
        res = get_thai_fund_nav("SCBDV")
        self.assertTrue(res["success"])
        self.assertEqual(res["nav"], 8.5505)
        self.assertEqual(res["source"], "PyThaiNAV_Public")

    @patch("data_fetcher._fetch_sec_open_api_nav", return_value=None)
    @patch("data_fetcher._fetch_pythainav_fallback", return_value=None)
    @patch("data_fetcher._fetch_finnomena_fallback_nav", return_value=None)
    def test_thai_fund_default_fallback(self, mock_finn, mock_pythainav, mock_sec):
        res = get_thai_fund_nav("UNKNOWN_FUND")
        self.assertFalse(res["success"])
        self.assertEqual(res["source"], "Fallback_Default")
        self.assertEqual(res["fund_code"], "UNKNOWN_FUND")
        self.assertIn("No live NAV found", res["error"])

    @patch("data_fetcher.get_stock_price")
    def test_get_batch_stock_prices(self, mock_get_price):
        mock_get_price.side_effect = [
            {"ticker": "AAPL", "current_price": 185.0, "currency": "USD", "change": 1.0, "change_percent": 0.54, "volume": 1000, "previous_close": 184.0, "success": True},
            {"ticker": "PTT.BK", "current_price": 34.5, "currency": "THB", "change": 0.25, "change_percent": 0.73, "volume": 5000, "previous_close": 34.25, "success": True},
        ]
        df = get_batch_stock_prices(["AAPL", "PTT.BK"])
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["ticker"]), ["AAPL", "PTT.BK"])

    @patch("data_fetcher.get_usd_thb_rate")
    @patch("data_fetcher.get_stock_price")
    @patch("data_fetcher.get_thai_fund_nav")
    def test_get_portfolio_data(self, mock_fund, mock_stock, mock_fx):
        mock_fx.return_value = {"pair": "USD/THB", "rate": 35.5, "success": True}
        mock_stock.return_value = {"ticker": "AAPL", "current_price": 180.0, "success": True}
        mock_fund.return_value = {"fund_code": "SCBDV", "nav": 12.5, "success": True}

        res = get_portfolio_data(["AAPL"], ["SCBDV"])
        self.assertIn("timestamp", res)
        self.assertEqual(res["exchange_rate"]["rate"], 35.5)
        self.assertEqual(len(res["stocks"]), 1)
        self.assertEqual(len(res["funds"]), 1)


class TestSymbolValidation(unittest.TestCase):
    """Test validate_symbol and validate_symbol_detailed."""

    @patch("data_fetcher.get_stock_price")
    def test_validate_valid_us_stock(self, mock_get_price):
        mock_get_price.return_value = {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "current_price": 185.5,
            "currency": "USD",
            "success": True,
            "error": None,
        }
        self.assertTrue(validate_symbol("AAPL", asset_type="US_STOCK"))
        res = validate_symbol_detailed("AAPL", asset_type="US_STOCK")
        self.assertTrue(res["valid"])
        self.assertEqual(res["symbol"], "AAPL")
        self.assertEqual(res["price"], 185.5)

    @patch("data_fetcher.get_stock_price")
    def test_validate_valid_thai_stock(self, mock_get_price):
        mock_get_price.return_value = {
            "ticker": "PTT.BK",
            "name": "PTT Public Company Limited",
            "current_price": 34.0,
            "currency": "THB",
            "success": True,
            "error": None,
        }
        self.assertTrue(validate_symbol("PTT.BK", asset_type="TH_STOCK"))

    @patch("data_fetcher.get_thai_fund_nav")
    def test_validate_valid_thai_fund(self, mock_get_nav):
        mock_get_nav.return_value = {
            "fund_code": "SCBDV",
            "fund_name": "SCB Dividend Stock",
            "nav": 12.45,
            "success": True,
            "error": None,
        }
        self.assertTrue(validate_symbol("SCBDV", asset_type="TH_MUTUAL_FUND"))

    @patch("data_fetcher.get_stock_price")
    @patch("data_fetcher.get_thai_fund_nav")
    def test_validate_invalid_symbol(self, mock_fund, mock_stock):
        mock_stock.return_value = {
            "ticker": "INVALID_XYZ",
            "current_price": None,
            "success": False,
            "error": "Symbol not found.",
        }
        mock_fund.return_value = {
            "fund_code": "INVALID_XYZ",
            "nav": None,
            "success": False,
            "error": "No live NAV found",
        }
        self.assertFalse(validate_symbol("INVALID_XYZ", asset_type="US_STOCK"))
        res = validate_symbol_detailed("INVALID_XYZ", asset_type="US_STOCK")
        self.assertFalse(res["valid"])
        self.assertIsNotNone(res["error"])

    def test_validate_empty_symbol(self):
        self.assertFalse(validate_symbol(""))
        self.assertFalse(validate_symbol(None))
        res = validate_symbol_detailed("")
        self.assertFalse(res["valid"])


if __name__ == "__main__":
    unittest.main()
