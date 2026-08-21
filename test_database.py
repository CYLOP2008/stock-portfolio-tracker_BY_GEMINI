"""
test_database.py
================
Comprehensive unit test suite for SQLAlchemy database module (database.py).
Tests database connection URL resolution, PostgreSQL compatibility, schema creation,
CRUD operations, input validation, and DataFrame returns.
"""

import os
import tempfile
import unittest
import pandas as pd

from database import (
    DEFAULT_DB_PATH,
    DatabaseError,
    PortfolioDB,
    ValidationError,
    _validate_and_normalize_inputs,
    add_transaction,
    clear_all_transactions,
    close_all_engines,
    delete_transaction,
    fetch_all_transactions,
    get_all_transactions,
    get_database_url,
    get_portfolio_summary_holdings,
    get_transaction_by_id,
    get_transactions_by_asset_type,
    get_transactions_by_symbol,
    init_db,
    update_transaction,
)


class TestInputValidation(unittest.TestCase):
    """Test validation and normalization helper."""

    def test_valid_inputs_explicit_currency(self):
        res = _validate_and_normalize_inputs(
            symbol="aapl",
            asset_type="us_stock",
            quantity=10,
            cost_per_share=150.5,
            currency="usd",
            purchase_date="2024-01-15",
        )
        self.assertEqual(res["symbol"], "AAPL")
        self.assertEqual(res["asset_type"], "US_STOCK")
        self.assertEqual(res["quantity"], 10.0)
        self.assertEqual(res["cost_per_share"], 150.5)
        self.assertEqual(res["currency"], "USD")
        self.assertEqual(res["purchase_date"], "2024-01-15")

    def test_currency_inference_us_stock(self):
        res = _validate_and_normalize_inputs(
            symbol="NVDA",
            asset_type="US_STOCK",
            quantity=5,
            cost_per_share=450.0,
            currency=None,
            purchase_date="2024-02-01",
        )
        self.assertEqual(res["currency"], "USD")

    def test_currency_inference_thai_assets(self):
        res_stock = _validate_and_normalize_inputs(
            symbol="PTT.BK",
            asset_type="TH_STOCK",
            quantity=100,
            cost_per_share=34.0,
            currency=None,
            purchase_date="2024-02-01",
        )
        self.assertEqual(res_stock["currency"], "THB")

        res_fund = _validate_and_normalize_inputs(
            symbol="K-USA-A(A)",
            asset_type="TH_MUTUAL_FUND",
            quantity=500,
            cost_per_share=15.2,
            currency=None,
            purchase_date="2024-02-01",
        )
        self.assertEqual(res_fund["currency"], "THB")

    def test_invalid_symbol(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("", "US_STOCK", 10, 100, "USD", "2024-01-01")
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("   ", "US_STOCK", 10, 100, "USD", "2024-01-01")

    def test_invalid_asset_type(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("BTC", "CRYPTO", 1, 50000, "USD", "2024-01-01")

    def test_invalid_quantity(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 0, 100, "USD", "2024-01-01")
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", -5, 100, "USD", "2024-01-01")
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", "invalid", 100, "USD", "2024-01-01")

    def test_invalid_cost_per_share(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, -1, "USD", "2024-01-01")
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, "abc", "USD", "2024-01-01")

    def test_invalid_currency(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, 100, "EUR", "2024-01-01")

    def test_invalid_purchase_date(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, 100, "USD", "invalid-date")


class TestDatabaseUrlResolution(unittest.TestCase):
    """Test resolution of PostgreSQL (Supabase) and SQLite connection URLs."""

    def test_supabase_postgres_url_normalization(self):
        supabase_url = "postgres://postgres.abc:password@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
        resolved = get_database_url(supabase_url)
        self.assertTrue(resolved.startswith("postgresql+psycopg2://"))

    def test_standard_postgresql_url(self):
        pg_url = "postgresql://user:pass@localhost:5432/mydb"
        resolved = get_database_url(pg_url)
        self.assertTrue(resolved.startswith("postgresql+psycopg2://"))

    def test_sqlite_file_path_conversion(self):
        resolved = get_database_url("my_portfolio.db")
        self.assertEqual(resolved, "sqlite:///my_portfolio.db")

    def test_sqlite_memory_conversion(self):
        resolved = get_database_url(":memory:")
        self.assertEqual(resolved, "sqlite:///:memory:")


class TestDatabaseCRUD(unittest.TestCase):
    """Test SQLAlchemy CRUD operations in isolated temporary database files."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_portfolio.db")
        self.db_url = f"sqlite:///{self.db_path}"
        init_db(self.db_url)

    def tearDown(self):
        close_all_engines()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_init_db_creates_table(self):
        txs = fetch_all_transactions(db_url=self.db_url)
        self.assertEqual(txs, [])

    def test_add_transaction_success(self):
        tx_id = add_transaction(
            symbol="AAPL",
            asset_type="US_STOCK",
            quantity=10,
            cost_per_share=175.50,
            currency="USD",
            purchase_date="2024-01-10",
            db_url=self.db_url,
        )
        self.assertEqual(tx_id, 1)

        tx = get_transaction_by_id(tx_id, db_url=self.db_url)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["id"], 1)
        self.assertEqual(tx["symbol"], "AAPL")
        self.assertEqual(tx["asset_type"], "US_STOCK")
        self.assertEqual(tx["quantity"], 10.0)
        self.assertEqual(tx["cost_per_share"], 175.50)
        self.assertEqual(tx["currency"], "USD")
        self.assertEqual(tx["purchase_date"], "2024-01-10")

    def test_add_multiple_transactions_and_fetch_all(self):
        id1 = add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", "2024-01-01", db_url=self.db_url)
        id2 = add_transaction("PTT.BK", "TH_STOCK", 200, 32.0, "THB", "2024-01-05", db_url=self.db_url)
        id3 = add_transaction("SCBDV", "TH_MUTUAL_FUND", 1000, 12.5, "THB", "2024-01-15", db_url=self.db_url)

        self.assertEqual(id1, 1)
        self.assertEqual(id2, 2)
        self.assertEqual(id3, 3)

        all_txs = fetch_all_transactions(db_url=self.db_url)
        self.assertEqual(len(all_txs), 3)
        self.assertEqual(all_txs[0]["symbol"], "AAPL")
        self.assertEqual(all_txs[1]["symbol"], "PTT.BK")
        self.assertEqual(all_txs[2]["symbol"], "SCBDV")

    def test_fetch_all_as_dataframe(self):
        add_transaction("NVDA", "US_STOCK", 5, 450.0, "USD", "2024-02-01", db_url=self.db_url)
        df = get_all_transactions(db_url=self.db_url, as_dataframe=True)

        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 1)
        self.assertIn("symbol", df.columns)
        self.assertEqual(df.iloc[0]["symbol"], "NVDA")

    def test_fetch_all_empty_dataframe(self):
        df = get_all_transactions(db_url=self.db_url, as_dataframe=True)
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 0)
        self.assertIn("symbol", df.columns)
        self.assertIn("cost_per_share", df.columns)

    def test_delete_transaction(self):
        tx_id = add_transaction("MSFT", "US_STOCK", 8, 400.0, "USD", "2024-01-20", db_url=self.db_url)
        self.assertEqual(len(get_all_transactions(db_url=self.db_url, as_dataframe=False)), 1)

        # Successful deletion
        result = delete_transaction(tx_id, db_url=self.db_url)
        self.assertTrue(result)
        self.assertEqual(len(get_all_transactions(db_url=self.db_url, as_dataframe=False)), 0)

        # Deleting non-existing transaction
        result_non_existent = delete_transaction(999, db_url=self.db_url)
        self.assertFalse(result_non_existent)

    def test_update_transaction(self):
        tx_id = add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", "2024-01-01", db_url=self.db_url)

        # Update quantity and cost_per_share
        updated = update_transaction(tx_id, quantity=15, cost_per_share=160.0, db_url=self.db_url)
        self.assertTrue(updated)

        tx = get_transaction_by_id(tx_id, db_url=self.db_url)
        self.assertEqual(tx["quantity"], 15.0)
        self.assertEqual(tx["cost_per_share"], 160.0)
        self.assertEqual(tx["symbol"], "AAPL")

        # Updating non-existent transaction returns False
        updated_fake = update_transaction(999, quantity=20, db_url=self.db_url)
        self.assertFalse(updated_fake)

    def test_get_transactions_by_symbol(self):
        add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", "2024-01-01", db_url=self.db_url)
        add_transaction("AAPL", "US_STOCK", 5, 170.0, "USD", "2024-02-01", db_url=self.db_url)
        add_transaction("PTT.BK", "TH_STOCK", 100, 32.0, "THB", "2024-02-01", db_url=self.db_url)

        aapl_txs = get_transactions_by_symbol("aapl", db_url=self.db_url)
        self.assertEqual(len(aapl_txs), 2)
        self.assertEqual(aapl_txs[0]["quantity"], 10.0)
        self.assertEqual(aapl_txs[1]["quantity"], 5.0)

    def test_get_transactions_by_asset_type(self):
        add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", "2024-01-01", db_url=self.db_url)
        add_transaction("NVDA", "US_STOCK", 5, 450.0, "USD", "2024-01-02", db_url=self.db_url)
        add_transaction("PTT.BK", "TH_STOCK", 100, 32.0, "THB", "2024-01-03", db_url=self.db_url)

        us_stocks = get_transactions_by_asset_type("US_STOCK", db_url=self.db_url)
        self.assertEqual(len(us_stocks), 2)

        th_stocks = get_transactions_by_asset_type("TH_STOCK", db_url=self.db_url)
        self.assertEqual(len(th_stocks), 1)

        funds = get_transactions_by_asset_type("TH_MUTUAL_FUND", db_url=self.db_url)
        self.assertEqual(len(funds), 0)

    def test_get_portfolio_summary_holdings(self):
        # 10 shares @ 100 = 1000
        add_transaction("AAPL", "US_STOCK", 10, 100.0, "USD", "2024-01-01", db_url=self.db_url)
        # 20 shares @ 130 = 2600
        add_transaction("AAPL", "US_STOCK", 20, 130.0, "USD", "2024-01-10", db_url=self.db_url)
        # 500 shares @ 30.0 = 15000 THB
        add_transaction("PTT.BK", "TH_STOCK", 500, 30.0, "THB", "2024-01-15", db_url=self.db_url)

        summary = get_portfolio_summary_holdings(db_url=self.db_url)
        self.assertEqual(len(summary), 2)

        aapl_summary = next(s for s in summary if s["symbol"] == "AAPL")
        self.assertEqual(aapl_summary["total_quantity"], 30.0)
        self.assertEqual(aapl_summary["total_cost"], 3600.0)
        self.assertAlmostEqual(aapl_summary["avg_cost_per_share"], 120.0, places=2)
        self.assertEqual(aapl_summary["transaction_count"], 2)

        ptt_summary = next(s for s in summary if s["symbol"] == "PTT.BK")
        self.assertEqual(ptt_summary["total_quantity"], 500.0)
        self.assertEqual(ptt_summary["total_cost"], 15000.0)
        self.assertEqual(ptt_summary["avg_cost_per_share"], 30.0)
        self.assertEqual(ptt_summary["transaction_count"], 1)

    def test_clear_all_transactions(self):
        add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", "2024-01-01", db_url=self.db_url)
        add_transaction("PTT.BK", "TH_STOCK", 100, 32.0, "THB", "2024-01-02", db_url=self.db_url)
        self.assertEqual(len(get_all_transactions(db_url=self.db_url, as_dataframe=False)), 2)

        deleted = clear_all_transactions(db_url=self.db_url)
        self.assertEqual(deleted, 2)
        self.assertEqual(len(get_all_transactions(db_url=self.db_url, as_dataframe=False)), 0)


class TestPortfolioDBClass(unittest.TestCase):
    """Test PortfolioDB class object wrapper."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "class_test_portfolio.db")
        self.db = PortfolioDB(db_path=self.db_path)

    def tearDown(self):
        close_all_engines()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_class_lifecycle_and_methods(self):
        # Add via class
        tx_id = self.db.add("TSLA", "US_STOCK", 10, 200.0, "USD", "2024-01-10")
        self.assertEqual(tx_id, 1)

        # Get by id
        tx = self.db.get_by_id(tx_id)
        self.assertEqual(tx["symbol"], "TSLA")

        # Get all (as list)
        all_tx = self.db.get_all(as_dataframe=False)
        self.assertEqual(len(all_tx), 1)

        # Update
        self.db.update(tx_id, cost_per_share=210.0)
        tx_updated = self.db.get_by_id(tx_id)
        self.assertEqual(tx_updated["cost_per_share"], 210.0)

        # Get by symbol and asset type
        self.assertEqual(len(self.db.get_by_symbol("TSLA")), 1)
        self.assertEqual(len(self.db.get_by_asset_type("US_STOCK")), 1)

        # Holdings summary
        summary = self.db.get_holdings_summary()
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["total_cost"], 2100.0)

        # Context manager
        with PortfolioDB(db_path=self.db_path) as ctx_db:
            self.assertEqual(len(ctx_db.get_all(as_dataframe=False)), 1)

        # Delete & Clear
        self.assertTrue(self.db.delete(tx_id))
        self.assertEqual(len(self.db.get_all(as_dataframe=False)), 0)


if __name__ == "__main__":
    unittest.main()
