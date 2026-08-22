"""
test_app.py
===========
Unit and integration tests for app.py logic, search options, and Streamlit components.
"""

import os
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

from app import load_sample_portfolio_data
from database import clear_all_transactions, get_all_transactions, init_db, DEFAULT_DB_PATH
from portfolio_calculator import calculate_portfolio_summary, get_portfolio_metrics_dataframe
from ticker_registry import get_all_symbols, get_symbol_info, search_symbols, update_ticker_cache


class TestAppIntegration(unittest.TestCase):
    """Test app logic and seed data functions."""

    def setUp(self):
        init_db(DEFAULT_DB_PATH)
        update_ticker_cache(db_path=DEFAULT_DB_PATH)

    def test_load_sample_portfolio_data(self):
        clear_all_transactions(DEFAULT_DB_PATH)
        load_sample_portfolio_data()

        txs = get_all_transactions(DEFAULT_DB_PATH, as_dataframe=False)
        self.assertGreaterEqual(len(txs), 6)

        symbols = [t["symbol"] for t in txs]
        self.assertIn("AAPL", symbols)
        self.assertIn("NVDA", symbols)
        self.assertIn("PTT.BK", symbols)
        self.assertIn("CPALL.BK", symbols)
        self.assertIn("SCBDV", symbols)
        self.assertIn("K-USA-A(A)", symbols)

        # Verify summary calculations on sample data
        summary = calculate_portfolio_summary(
            transactions=txs,
            custom_usd_thb_rate=36.0,
            custom_prices={
                "AAPL": 185.0,
                "NVDA": 480.0,
                "PTT.BK": 34.0,
                "CPALL.BK": 58.0,
                "SCBDV": 13.0,
                "K-USA-A(A)": 16.5,
            },
        )

        self.assertGreater(summary["total_value_thb"], 0)
        self.assertGreater(summary["total_cost_thb"], 0)
        self.assertEqual(summary["holdings_count"], 6)

        # Verify Realized Performance metrics generated from FIFO sales
        self.assertGreater(summary["closed_trades_count"], 0)
        self.assertGreater(summary["total_realized_pnl_thb"], 0)
        self.assertGreater(len(summary["closed_trades"]), 0)

        # Check that AAPL has 10 remaining shares (15 bought - 5 sold)
        aapl_holding = next(h for h in summary["holdings"] if h["symbol"] == "AAPL")
        self.assertEqual(aapl_holding["quantity"], 10.0)

        # Check that PTT.BK has 700 remaining shares (1000 bought - 300 sold)
        ptt_holding = next(h for h in summary["holdings"] if h["symbol"] == "PTT.BK")
        self.assertEqual(ptt_holding["quantity"], 700.0)

        df = get_portfolio_metrics_dataframe(summary=summary)
        self.assertEqual(len(df), 6)
        self.assertIn("Symbol", [c.title() for c in df.columns])

    def test_search_select_options_integration(self):
        """Test search and select options generation for Streamlit selectbox."""
        all_syms = get_all_symbols(db_path=DEFAULT_DB_PATH)
        self.assertGreater(len(all_syms), 50)

        display_options = [f"{s['symbol']} - {s['name']}" for s in all_syms]
        self.assertTrue(any("AAPL - Apple" in opt for opt in display_options))
        self.assertTrue(any("PTT.BK - PTT" in opt for opt in display_options))
        self.assertTrue(any("ONE-UGG-RA" in opt for opt in display_options))


if __name__ == "__main__":
    unittest.main()
