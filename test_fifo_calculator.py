"""
test_fifo_calculator.py
=======================
Unit test suite for fifo_calculator.py.
Tests FIFO lot matching, Realized P&L calculations, remaining holdings tracking,
exception handling for overselling, and DataFrame conversions.
"""

import unittest
from datetime import date
import pandas as pd

from fifo_calculator import (
    BuyLot,
    FIFOCalculator,
    FIFOError,
    InsufficientSharesError,
    InvalidTransactionError,
    MatchedLot,
    SaleExecution,
    calculate_fifo_portfolio,
    calculate_realized_pnl,
)


class TestFIFOBasicCalculations(unittest.TestCase):
    """Test standard FIFO lot matching and Realized P&L scenarios."""

    def test_single_buy_and_full_sell(self):
        """Test buying 10 shares and selling all 10 shares."""
        txs = [
            {"type": "BUY", "symbol": "AAPL", "quantity": 10, "cost_per_share": 150.0, "purchase_date": "2023-01-01"},
            {"type": "SELL", "symbol": "AAPL", "quantity": 10, "cost_per_share": 200.0, "purchase_date": "2023-02-01"},
        ]
        result = calculate_fifo_portfolio(txs)
        
        # Remaining holdings should have 0 active shares
        self.assertEqual(result["remaining_holdings"], {})
        
        # Realized P&L = (200 - 150) * 10 = +500
        pnl = result["realized_pnl"]
        self.assertEqual(pnl["overall_total"], 500.0)
        self.assertEqual(pnl["by_symbol"]["AAPL"], 500.0)
        self.assertEqual(pnl["total_proceeds"], 2000.0)
        self.assertEqual(pnl["total_cost_basis"], 1500.0)
        self.assertEqual(pnl["overall_pnl_percent"], round((500.0 / 1500.0) * 100, 4))
        self.assertEqual(len(pnl["trades"]), 1)

    def test_multi_lot_fifo_consumption(self):
        """Test FIFO order across multiple BUY lots:
        - Lot 1: 10 shares @ $100 on 2023-01-01 (Cost $1000)
        - Lot 2: 20 shares @ $150 on 2023-02-01 (Cost $3000)
        - SELL 15 shares @ $200 on 2023-03-01:
          - Consumes 10 from Lot 1: P&L = (200 - 100) * 10 = 1000
          - Consumes 5 from Lot 2:  P&L = (200 - 150) * 5  = 250
          - Total Realized P&L = 1250
          - Remaining in Lot 2: 15 shares @ $150 (Total Cost $2250)
        """
        txs = [
            {"transaction_type": "BUY", "symbol": "AAPL", "quantity": 10, "cost_per_share": 100.0, "purchase_date": "2023-01-01"},
            {"transaction_type": "BUY", "symbol": "AAPL", "quantity": 20, "cost_per_share": 150.0, "purchase_date": "2023-02-01"},
            {"transaction_type": "SELL", "symbol": "AAPL", "quantity": 15, "cost_per_share": 200.0, "purchase_date": "2023-03-01"},
        ]
        calc = FIFOCalculator(txs)
        res = calc.get_structured_result()

        # Check remaining holdings
        holdings = res["remaining_holdings"]
        self.assertIn("AAPL", holdings)
        aapl = holdings["AAPL"]
        self.assertEqual(aapl["total_quantity"], 15.0)
        self.assertEqual(aapl["total_cost"], 2250.0)
        self.assertEqual(aapl["avg_cost_per_share"], 150.0)
        self.assertEqual(aapl["lot_count"], 1)
        self.assertEqual(aapl["lots"][0]["quantity"], 15.0)
        self.assertEqual(aapl["lots"][0]["cost_per_share"], 150.0)
        self.assertEqual(aapl["lots"][0]["purchase_date"], "2023-02-01")

        # Check Realized P&L
        pnl = res["realized_pnl"]
        self.assertEqual(pnl["overall_total"], 1250.0)
        self.assertEqual(pnl["by_symbol"]["AAPL"], 1250.0)
        self.assertEqual(pnl["total_proceeds"], 3000.0)
        self.assertEqual(pnl["total_cost_basis"], 1750.0) # (10*100) + (5*150) = 1000 + 750 = 1750

        # Check matched lots breakdown inside sale trade
        trades = pnl["trades"]
        self.assertEqual(len(trades), 1)
        matched = trades[0]["matched_lots"]
        self.assertEqual(len(matched), 2)
        self.assertEqual(matched[0]["purchase_date"], "2023-01-01")
        self.assertEqual(matched[0]["matched_quantity"], 10.0)
        self.assertEqual(matched[0]["realized_pnl"], 1000.0)
        self.assertEqual(matched[1]["purchase_date"], "2023-02-01")
        self.assertEqual(matched[1]["matched_quantity"], 5.0)
        self.assertEqual(matched[1]["realized_pnl"], 250.0)

    def test_loss_and_breakeven_sales(self):
        """Test sales resulting in realized capital loss and break-even."""
        txs = [
            {"type": "BUY", "symbol": "TSLA", "quantity": 10, "cost_per_share": 300.0, "purchase_date": "2023-01-01"},
            {"type": "SELL", "symbol": "TSLA", "quantity": 4, "cost_per_share": 250.0, "purchase_date": "2023-02-01"}, # Loss: (250-300)*4 = -200
            {"type": "SELL", "symbol": "TSLA", "quantity": 2, "cost_per_share": 300.0, "purchase_date": "2023-03-01"}, # Breakeven: 0
        ]
        res = calculate_fifo_portfolio(txs)
        pnl = res["realized_pnl"]
        self.assertEqual(pnl["by_symbol"]["TSLA"], -200.0)
        self.assertEqual(pnl["overall_total"], -200.0)
        
        # Remaining: 4 shares @ 300 = 1200
        holdings = res["remaining_holdings"]["TSLA"]
        self.assertEqual(holdings["total_quantity"], 4.0)
        self.assertEqual(holdings["total_cost"], 1200.0)

    def test_fractional_shares(self):
        """Test fractional share purchases and sales with precision."""
        txs = [
            {"type": "BUY", "symbol": "NVDA", "quantity": 2.5, "cost_per_share": 400.0, "purchase_date": "2023-01-01"},
            {"type": "BUY", "symbol": "NVDA", "quantity": 1.75, "cost_per_share": 420.0, "purchase_date": "2023-01-15"},
            {"type": "SELL", "symbol": "NVDA", "quantity": 3.0, "cost_per_share": 500.0, "purchase_date": "2023-02-01"},
        ]
        # Match:
        # 2.5 @ 400 = Cost 1000, Proceeds 1250, P&L = +250
        # 0.5 @ 420 = Cost 210, Proceeds 250, P&L = +40
        # Total P&L = +290
        # Remaining NVDA: 1.25 @ 420 = Cost 525.0
        res = calculate_fifo_portfolio(txs)
        pnl = res["realized_pnl"]
        self.assertEqual(pnl["overall_total"], 290.0)
        
        nvda = res["remaining_holdings"]["NVDA"]
        self.assertEqual(nvda["total_quantity"], 1.25)
        self.assertEqual(nvda["total_cost"], 525.0)


class TestFIFOMultiSymbolAndTypes(unittest.TestCase):
    """Test portfolio containing multiple symbols across US stocks, Thai stocks, and funds."""

    def test_multi_symbol_independence(self):
        """Ensure lots and P&L for different symbols are strictly isolated."""
        txs = [
            {"type": "BUY", "symbol": "AAPL", "quantity": 10, "cost_per_share": 150.0, "purchase_date": "2023-01-01", "currency": "USD"},
            {"type": "BUY", "symbol": "PTT.BK", "quantity": 1000, "cost_per_share": 30.0, "purchase_date": "2023-01-02", "currency": "THB"},
            {"type": "BUY", "symbol": "SCBDV", "quantity": 500, "cost_per_share": 10.0, "purchase_date": "2023-01-03", "currency": "THB"},
            {"type": "SELL", "symbol": "AAPL", "quantity": 5, "cost_per_share": 180.0, "purchase_date": "2023-02-01"}, # PnL: (180-150)*5 = +150
            {"type": "SELL", "symbol": "PTT.BK", "quantity": 400, "cost_per_share": 35.0, "purchase_date": "2023-02-02"}, # PnL: (35-30)*400 = +2000
        ]
        res = calculate_fifo_portfolio(txs)
        
        # PnL checks
        pnl = res["realized_pnl"]
        self.assertEqual(pnl["by_symbol"]["AAPL"], 150.0)
        self.assertEqual(pnl["by_symbol"]["PTT.BK"], 2000.0)
        self.assertEqual(pnl["overall_total"], 2150.0)
        
        # Remaining checks
        holdings = res["remaining_holdings"]
        self.assertEqual(len(holdings), 3)
        self.assertEqual(holdings["AAPL"]["total_quantity"], 5.0)
        self.assertEqual(holdings["PTT.BK"]["total_quantity"], 600.0)
        self.assertEqual(holdings["SCBDV"]["total_quantity"], 500.0)


class TestFIFOEdgeCasesAndExceptions(unittest.TestCase):
    """Test overselling and invalid transaction edge cases."""

    def test_oversell_more_than_owned_raises_insufficient_shares(self):
        """Selling more shares than available in open lots must raise InsufficientSharesError."""
        txs = [
            {"type": "BUY", "symbol": "MSFT", "quantity": 10, "cost_per_share": 200.0, "purchase_date": "2023-01-01"},
            {"type": "SELL", "symbol": "MSFT", "quantity": 15, "cost_per_share": 250.0, "purchase_date": "2023-02-01"},
        ]
        with self.assertRaises(InsufficientSharesError) as ctx:
            calculate_fifo_portfolio(txs)

        err = ctx.exception
        self.assertEqual(err.symbol, "MSFT")
        self.assertEqual(err.requested_quantity, 15.0)
        self.assertEqual(err.available_quantity, 10.0)
        self.assertIn("Insufficient shares for symbol 'MSFT'", str(err))

    def test_sell_with_zero_lots_raises_insufficient_shares(self):
        """Selling when 0 shares are held must raise InsufficientSharesError."""
        txs = [
            {"type": "SELL", "symbol": "GOOGL", "quantity": 5, "cost_per_share": 140.0, "purchase_date": "2023-01-01"},
        ]
        with self.assertRaises(InsufficientSharesError) as ctx:
            calculate_fifo_portfolio(txs)
        
        err = ctx.exception
        self.assertEqual(err.symbol, "GOOGL")
        self.assertEqual(err.requested_quantity, 5.0)
        self.assertEqual(err.available_quantity, 0.0)

    def test_invalid_transaction_type_raises_error(self):
        """Transaction types other than BUY or SELL must raise InvalidTransactionError."""
        txs = [
            {"type": "DIVIDEND", "symbol": "AAPL", "quantity": 10, "cost_per_share": 100.0},
        ]
        with self.assertRaises(InvalidTransactionError):
            calculate_fifo_portfolio(txs)

    def test_negative_or_zero_quantity_raises_error(self):
        """Zero or negative quantity must raise InvalidTransactionError."""
        with self.assertRaises(InvalidTransactionError):
            calculate_fifo_portfolio([{"type": "BUY", "symbol": "AAPL", "quantity": -5, "price": 100.0}])
        with self.assertRaises(InvalidTransactionError):
            calculate_fifo_portfolio([{"type": "BUY", "symbol": "AAPL", "quantity": 0, "price": 100.0}])

    def test_negative_price_raises_error(self):
        """Negative price must raise InvalidTransactionError."""
        with self.assertRaises(InvalidTransactionError):
            calculate_fifo_portfolio([{"type": "BUY", "symbol": "AAPL", "quantity": 5, "price": -100.0}])

    def test_missing_symbol_raises_error(self):
        """Missing symbol must raise InvalidTransactionError."""
        with self.assertRaises(InvalidTransactionError):
            calculate_fifo_portfolio([{"type": "BUY", "quantity": 5, "price": 100.0}])


class TestFIFOFlexibilityAndDataFrames(unittest.TestCase):
    """Test flexibility with aliases, DataFrames, and sorting."""

    def test_field_aliases_support(self):
        """Support field aliases like action, ticker, shares, price, transaction_date."""
        txs = [
            {"action": "BUY", "ticker": "amzn", "shares": 10, "price": 100.0, "transaction_date": "2023-01-01"},
            {"action": "sell", "ticker": "AMZN", "units": 4, "sell_price": 120.0, "date": "2023-02-01"},
        ]
        res = calculate_fifo_portfolio(txs)
        self.assertEqual(res["realized_pnl"]["overall_total"], 80.0) # (120 - 100) * 4 = 80
        self.assertEqual(res["remaining_holdings"]["AMZN"]["total_quantity"], 6.0)

    def test_dataframe_input(self):
        """Support pandas DataFrame input seamlessly."""
        df = pd.DataFrame([
            {"type": "BUY", "symbol": "META", "quantity": 10, "cost_per_share": 200.0, "purchase_date": "2023-01-01"},
            {"type": "SELL", "symbol": "META", "quantity": 6, "cost_per_share": 300.0, "purchase_date": "2023-02-01"},
        ])
        res = calculate_fifo_portfolio(df)
        self.assertEqual(res["realized_pnl"]["overall_total"], 600.0)
        self.assertEqual(res["remaining_holdings"]["META"]["total_quantity"], 4.0)

    def test_auto_sort_unordered_transactions(self):
        """When auto_sort=True, transactions are sorted chronologically before processing."""
        unordered_txs = [
            {"type": "SELL", "symbol": "SPY", "quantity": 5, "price": 450.0, "date": "2023-02-01"},
            {"type": "BUY", "symbol": "SPY", "quantity": 10, "price": 400.0, "date": "2023-01-01"},
        ]
        # Without auto_sort, this would fail with InsufficientSharesError
        with self.assertRaises(InsufficientSharesError):
            calculate_fifo_portfolio(unordered_txs, auto_sort=False)

        # With auto_sort=True, BUY is processed first
        res = calculate_fifo_portfolio(unordered_txs, auto_sort=True)
        self.assertEqual(res["realized_pnl"]["overall_total"], 250.0) # (450 - 400) * 5
        self.assertEqual(res["remaining_holdings"]["SPY"]["total_quantity"], 5.0)

    def test_dataframe_export_methods(self):
        """Test FIFOCalculator DataFrame export utility methods."""
        txs = [
            {"type": "BUY", "symbol": "AAPL", "quantity": 10, "cost_per_share": 100.0, "purchase_date": "2023-01-01"},
            {"type": "BUY", "symbol": "AAPL", "quantity": 20, "cost_per_share": 150.0, "purchase_date": "2023-02-01"},
            {"type": "SELL", "symbol": "AAPL", "quantity": 15, "cost_per_share": 200.0, "purchase_date": "2023-03-01"},
        ]
        calc = FIFOCalculator(txs)
        
        df_holdings = calc.holdings_to_dataframe()
        self.assertIsInstance(df_holdings, pd.DataFrame)
        self.assertEqual(len(df_holdings), 1)
        self.assertEqual(df_holdings.iloc[0]["symbol"], "AAPL")
        self.assertEqual(df_holdings.iloc[0]["total_quantity"], 15.0)

        df_lots = calc.lots_to_dataframe()
        self.assertIsInstance(df_lots, pd.DataFrame)
        self.assertEqual(len(df_lots), 1)
        self.assertEqual(df_lots.iloc[0]["quantity"], 15.0)

        df_pnl = calc.realized_pnl_to_dataframe()
        self.assertIsInstance(df_pnl, pd.DataFrame)
        self.assertEqual(len(df_pnl), 1)
        self.assertEqual(df_pnl.iloc[0]["realized_pnl"], 1250.0)

        df_trades = calc.trades_to_dataframe()
        self.assertIsInstance(df_trades, pd.DataFrame)
        self.assertEqual(len(df_trades), 1)
        self.assertEqual(df_trades.iloc[0]["quantity"], 15.0)


if __name__ == "__main__":
    unittest.main()
