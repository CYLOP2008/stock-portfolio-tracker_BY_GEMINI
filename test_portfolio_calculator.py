"""
test_portfolio_calculator.py
============================
Unit test suite for portfolio_calculator.py.
Tests transaction grouping, weighted average cost basis calculation,
USD to THB currency conversion, P&L calculations, and asset allocation percentages.
"""

import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

from portfolio_calculator import (
    PortfolioCalculator,
    aggregate_transactions,
    calculate_portfolio_summary,
    get_portfolio_metrics_dataframe,
)


class TestPortfolioAggregation(unittest.TestCase):
    """Test grouping and aggregation logic."""

    def test_aggregate_empty_transactions(self):
        self.assertEqual(aggregate_transactions([]), [])
        self.assertEqual(aggregate_transactions(pd.DataFrame()), [])

    def test_aggregate_single_symbol_multiple_buys(self):
        # 10 shares @ 100 = 1000 USD
        # 20 shares @ 130 = 2600 USD
        # Total: 30 shares, Total Cost: 3600 USD, Avg Cost: 120 USD/share
        txs = [
            {"symbol": "AAPL", "asset_type": "US_STOCK", "quantity": 10, "cost_per_share": 100.0, "currency": "USD"},
            {"symbol": "AAPL", "asset_type": "US_STOCK", "quantity": 20, "cost_per_share": 130.0, "currency": "USD"},
        ]
        res = aggregate_transactions(txs)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["symbol"], "AAPL")
        self.assertEqual(res[0]["total_quantity"], 30.0)
        self.assertEqual(res[0]["total_cost"], 3600.0)
        self.assertAlmostEqual(res[0]["avg_cost_per_share"], 120.0, places=2)
        self.assertEqual(res[0]["transaction_count"], 2)

    def test_aggregate_multi_asset_types(self):
        txs = [
            {"symbol": "AAPL", "asset_type": "US_STOCK", "quantity": 10, "cost_per_share": 150.0, "currency": "USD"},
            {"symbol": "PTT.BK", "asset_type": "TH_STOCK", "quantity": 500, "cost_per_share": 32.0, "currency": "THB"},
            {"symbol": "SCBDV", "asset_type": "TH_MUTUAL_FUND", "quantity": 1000, "cost_per_share": 12.0, "currency": "THB"},
        ]
        res = aggregate_transactions(txs)
        self.assertEqual(len(res), 3)
        symbols = [r["symbol"] for r in res]
        self.assertIn("AAPL", symbols)
        self.assertIn("PTT.BK", symbols)
        self.assertIn("SCBDV", symbols)

    def test_aggregate_dataframe_input(self):
        df = pd.DataFrame([
            {"symbol": "NVDA", "asset_type": "US_STOCK", "quantity": 5, "cost_per_share": 400.0, "currency": "USD"},
            {"symbol": "NVDA", "asset_type": "US_STOCK", "quantity": 5, "cost_per_share": 500.0, "currency": "USD"},
        ])
        res = aggregate_transactions(df)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["symbol"], "NVDA")
        self.assertEqual(res[0]["total_quantity"], 10.0)
        self.assertEqual(res[0]["total_cost"], 4500.0)
        self.assertEqual(res[0]["avg_cost_per_share"], 450.0)


class TestPortfolioCalculations(unittest.TestCase):
    """Test full portfolio calculation math and conversions."""

    def test_empty_portfolio_summary(self):
        summary = calculate_portfolio_summary(transactions=[], custom_usd_thb_rate=35.0)
        self.assertEqual(summary["total_value_thb"], 0.0)
        self.assertEqual(summary["total_cost_thb"], 0.0)
        self.assertEqual(summary["total_unrealized_pnl_thb"], 0.0)
        self.assertEqual(summary["total_unrealized_pnl_percent"], 0.0)
        self.assertEqual(summary["holdings_count"], 0)
        self.assertEqual(summary["holdings"], [])

    def test_multi_asset_portfolio_calculations(self):
        """Test exact math with known prices:

        US Stock:
          - AAPL: 10 shares @ 150 USD cost = 1500 USD cost.
                  Price = 180 USD -> Value = 1800 USD.
                  USD/THB = 35.0.
                  Cost THB = 1500 * 35 = 52,500 THB.
                  Value THB = 1800 * 35 = 63,000 THB.
                  P&L THB = +10,500 THB (+20.0%).

        Thai Stock:
          - PTT.BK: 1,000 shares @ 30 THB cost = 30,000 THB cost.
                    Price = 35 THB -> Value = 35,000 THB.
                    Cost THB = 30,000 THB.
                    Value THB = 35,000 THB.
                    P&L THB = +5,000 THB (+16.67%).

        Thai Mutual Fund:
          - SCBDV: 2,000 units @ 10 THB cost = 20,000 THB cost.
                   NAV = 11 THB -> Value = 22,000 THB.
                   Cost THB = 20,000 THB.
                   Value THB = 22,000 THB.
                   P&L THB = +2,000 THB (+10.0%).

        Portfolio Totals:
          - Total Cost THB = 52,500 + 30,000 + 20,000 = 102,500 THB.
          - Total Value THB = 63,000 + 35,000 + 22,000 = 120,000 THB.
          - Total P&L THB = +17,500 THB.
          - Total P&L % = (17500 / 102500) * 100 = 17.07%.

        Weights:
          - AAPL weight: 63,000 / 120,000 = 52.50%
          - PTT.BK weight: 35,000 / 120,000 = 29.17%
          - SCBDV weight: 22,000 / 120,000 = 18.33%
          - Total weight sum = 100.0%
        """
        txs = [
            {"symbol": "AAPL", "asset_type": "US_STOCK", "quantity": 10, "cost_per_share": 150.0, "currency": "USD"},
            {"symbol": "PTT.BK", "asset_type": "TH_STOCK", "quantity": 1000, "cost_per_share": 30.0, "currency": "THB"},
            {"symbol": "SCBDV", "asset_type": "TH_MUTUAL_FUND", "quantity": 2000, "cost_per_share": 10.0, "currency": "THB"},
        ]

        custom_prices = {
            "AAPL": 180.0,
            "PTT.BK": 35.0,
            "SCBDV": 11.0,
        }

        summary = calculate_portfolio_summary(
            transactions=txs,
            custom_usd_thb_rate=35.0,
            custom_prices=custom_prices,
        )

        self.assertEqual(summary["total_cost_thb"], 102500.0)
        self.assertEqual(summary["total_value_thb"], 120000.0)
        self.assertEqual(summary["total_unrealized_pnl_thb"], 17500.0)
        self.assertAlmostEqual(summary["total_unrealized_pnl_percent"], 17.07, places=2)

        # Check holdings details
        holdings = {h["symbol"]: h for h in summary["holdings"]}

        # AAPL
        aapl = holdings["AAPL"]
        self.assertEqual(aapl["cost_basis_local"], 1500.0)
        self.assertEqual(aapl["market_value_local"], 1800.0)
        self.assertEqual(aapl["unrealized_pnl_local"], 300.0)
        self.assertEqual(aapl["cost_basis_thb"], 52500.0)
        self.assertEqual(aapl["market_value_thb"], 63000.0)
        self.assertEqual(aapl["unrealized_pnl_thb"], 10500.0)
        self.assertAlmostEqual(aapl["unrealized_pnl_percent"], 20.0, places=2)
        self.assertAlmostEqual(aapl["weight_percent"], 52.50, places=2)

        # PTT.BK
        ptt = holdings["PTT.BK"]
        self.assertEqual(ptt["cost_basis_thb"], 30000.0)
        self.assertEqual(ptt["market_value_thb"], 35000.0)
        self.assertEqual(ptt["unrealized_pnl_thb"], 5000.0)
        self.assertAlmostEqual(ptt["unrealized_pnl_percent"], 16.67, places=2)
        self.assertAlmostEqual(ptt["weight_percent"], 29.17, places=2)

        # SCBDV
        scbdv = holdings["SCBDV"]
        self.assertEqual(scbdv["cost_basis_thb"], 20000.0)
        self.assertEqual(scbdv["market_value_thb"], 22000.0)
        self.assertEqual(scbdv["unrealized_pnl_thb"], 2000.0)
        self.assertAlmostEqual(scbdv["unrealized_pnl_percent"], 10.0, places=2)
        self.assertAlmostEqual(scbdv["weight_percent"], 18.33, places=2)

        # Weights sum to 100%
        total_weights = sum(h["weight_percent"] for h in summary["holdings"])
        self.assertAlmostEqual(total_weights, 100.0, delta=0.05)

        # Allocation by asset type
        by_type = summary["allocation_by_asset_type"]
        self.assertEqual(by_type["US_STOCK"]["value_thb"], 63000.0)
        self.assertEqual(by_type["TH_STOCK"]["value_thb"], 35000.0)
        self.assertEqual(by_type["TH_MUTUAL_FUND"]["value_thb"], 22000.0)

        # Allocation by currency
        by_currency = summary["allocation_by_currency"]
        self.assertEqual(by_currency["USD"]["value_thb"], 63000.0)
        self.assertEqual(by_currency["THB"]["value_thb"], 57000.0)

    @patch("portfolio_calculator.get_usd_thb_rate")
    @patch("portfolio_calculator.get_stock_price")
    @patch("portfolio_calculator.get_thai_fund_nav")
    def test_mocked_live_fetchers(self, mock_fund, mock_stock, mock_fx):
        mock_fx.return_value = {"success": True, "rate": 36.0}
        mock_stock.side_effect = lambda sym: {
            "ticker": sym,
            "name": f"{sym} Corp",
            "current_price": 200.0 if sym == "TSLA" else 40.0,
            "change": 5.0,
            "change_percent": 2.5,
            "success": True,
        }
        mock_fund.return_value = {
            "fund_code": "K-USA-A(A)",
            "fund_name": "K USA Fund",
            "nav": 15.0,
            "change": 0.2,
            "change_percent": 1.35,
            "success": True,
        }

        txs = [
            {"symbol": "TSLA", "asset_type": "US_STOCK", "quantity": 10, "cost_per_share": 180.0, "currency": "USD"},
            {"symbol": "K-USA-A(A)", "asset_type": "TH_MUTUAL_FUND", "quantity": 1000, "cost_per_share": 14.0, "currency": "THB"},
        ]

        summary = calculate_portfolio_summary(transactions=txs)
        self.assertEqual(summary["usd_thb_rate"], 36.0)
        # TSLA: 10 * 200 * 36 = 72,000 THB Value (Cost = 10 * 180 * 36 = 64,800 THB)
        # K-USA: 1000 * 15 = 15,000 THB Value (Cost = 1000 * 14 = 14,000 THB)
        # Total Value = 87,000 THB, Total Cost = 78,800 THB
        self.assertEqual(summary["total_value_thb"], 87000.0)
        self.assertEqual(summary["total_cost_thb"], 78800.0)
        self.assertEqual(summary["total_unrealized_pnl_thb"], 8200.0)


class TestPortfolioDataframeAndClass(unittest.TestCase):
    """Test DataFrame conversion and PortfolioCalculator OOP class."""

    def test_dataframe_generation(self):
        txs = [
            {"symbol": "AAPL", "asset_type": "US_STOCK", "quantity": 10, "cost_per_share": 150.0, "currency": "USD"},
        ]
        df = get_portfolio_metrics_dataframe(transactions=txs, custom_usd_thb_rate=35.0, custom_prices={"AAPL": 170.0})
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["symbol"], "AAPL")
        self.assertEqual(df.iloc[0]["market_value_thb"], 59500.0)
        self.assertEqual(df.iloc[0]["weight_percent"], 100.0)

    def test_empty_dataframe_generation(self):
        df = get_portfolio_metrics_dataframe(transactions=[])
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 0)
        self.assertIn("symbol", df.columns)
        self.assertIn("market_value_thb", df.columns)

    def test_portfolio_calculator_class(self):
        calc = PortfolioCalculator(custom_usd_thb_rate=35.0)
        txs = [
            {"symbol": "AAPL", "asset_type": "US_STOCK", "quantity": 10, "cost_per_share": 150.0, "currency": "USD"},
        ]
        summary = calc.calculate(transactions=txs, custom_prices={"AAPL": 160.0})
        self.assertIsNotNone(calc.last_summary)
        self.assertEqual(summary["total_value_thb"], 56000.0)

        df = calc.get_dataframe(transactions=txs, custom_prices={"AAPL": 160.0})
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["symbol"], "AAPL")


if __name__ == "__main__":
    unittest.main()
