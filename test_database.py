"""
test_database.py
================
Comprehensive unit test suite for SQLAlchemy database module (database.py).
Tests database connection URL resolution, PostgreSQL compatibility, schema creation,
CRUD operations, input validation, multi-user authentication, and multi-portfolio management.
"""

import os
import tempfile
import unittest
import pandas as pd

from database import (
    DEFAULT_DB_PATH,
    AuthenticationError,
    DatabaseError,
    PortfolioDB,
    ValidationError,
    _validate_and_normalize_inputs,
    add_transaction,
    authenticate_user,
    clear_all_transactions,
    close_all_engines,
    create_portfolio,
    delete_portfolio,
    delete_transaction,
    fetch_all_transactions,
    get_all_transactions,
    get_database_url,
    get_portfolio_by_id,
    get_portfolio_summary_holdings,
    get_transaction_by_id,
    get_transactions_by_asset_type,
    get_transactions_by_symbol,
    get_user_by_id,
    get_user_portfolios,
    hash_password,
    init_db,
    register_user,
    update_portfolio,
    update_transaction,
    verify_password,
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
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, -50, "USD", "2024-01-01")
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, "free", "USD", "2024-01-01")

    def test_invalid_currency(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, 100, "EUR", "2024-01-01")

    def test_invalid_purchase_date(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, 100, "USD", "invalid-date")

    def test_valid_transaction_types(self):
        res_buy = _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, 100, "USD", "2024-01-01", transaction_type="BUY")
        self.assertEqual(res_buy["transaction_type"], "BUY")

        res_sell = _validate_and_normalize_inputs("AAPL", "US_STOCK", 5, 120, "USD", "2024-02-01", transaction_type="SELL")
        self.assertEqual(res_sell["transaction_type"], "SELL")

        res_lower = _validate_and_normalize_inputs("AAPL", "US_STOCK", 5, 120, "USD", "2024-02-01", transaction_type="sell")
        self.assertEqual(res_lower["transaction_type"], "SELL")

    def test_invalid_transaction_type(self):
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, 100, "USD", "2024-01-01", transaction_type="HOLD")
        with self.assertRaises(ValidationError):
            _validate_and_normalize_inputs("AAPL", "US_STOCK", 10, 100, "USD", "2024-01-01", transaction_type="DIVIDEND")


class TestDatabaseUrlResolution(unittest.TestCase):
    """Test resolution of PostgreSQL, SQLite, and Supabase connection strings."""

    def test_supabase_postgres_url_normalization(self):
        raw_url = "postgres://postgres.abcdef:secretpass@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
        norm = get_database_url(raw_url)
        self.assertTrue(norm.startswith("postgresql+psycopg2://"))
        self.assertIn("aws-0-ap-southeast-1.pooler.supabase.com", norm)

    def test_standard_postgresql_url(self):
        raw_url = "postgresql://user:pass@localhost:5432/testdb"
        norm = get_database_url(raw_url)
        self.assertTrue(norm.startswith("postgresql+psycopg2://"))

    def test_sqlite_file_path_conversion(self):
        norm = get_database_url("my_custom.db")
        self.assertEqual(norm, "sqlite:///my_custom.db")

    def test_sqlite_memory_conversion(self):
        norm = get_database_url(":memory:")
        self.assertEqual(norm, "sqlite:///:memory:")


class TestUserAuthentication(unittest.TestCase):
    """Test user registration, password hashing, and authentication."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_auth.db")
        self.db_url = f"sqlite:///{self.db_path}"
        init_db(self.db_url)

    def tearDown(self):
        close_all_engines()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_password_hashing_and_verification(self):
        pwd = "SecretPassword123!"
        hashed = hash_password(pwd)
        self.assertIn("$", hashed)
        self.assertTrue(verify_password(pwd, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))
        self.assertFalse(verify_password("", hashed))
        self.assertFalse(verify_password(pwd, "invalid_hash_string"))

    def test_register_and_authenticate_user(self):
        user = register_user("alice", "alice@example.com", "mypassword123", db_url=self.db_url)
        self.assertEqual(user["username"], "alice")
        self.assertEqual(user["email"], "alice@example.com")
        self.assertIn("default_portfolio_id", user)

        # Authenticate by username
        auth_by_username = authenticate_user("alice", "mypassword123", db_url=self.db_url)
        self.assertIsNotNone(auth_by_username)
        self.assertEqual(auth_by_username["id"], user["id"])

        # Authenticate by email
        auth_by_email = authenticate_user("alice@example.com", "mypassword123", db_url=self.db_url)
        self.assertIsNotNone(auth_by_email)
        self.assertEqual(auth_by_email["id"], user["id"])

        # Invalid password
        self.assertIsNone(authenticate_user("alice", "wrong_password", db_url=self.db_url))
        # Non-existent user
        self.assertIsNone(authenticate_user("bob", "mypassword123", db_url=self.db_url))

    def test_duplicate_user_registration(self):
        register_user("bob", "bob@example.com", "password123", db_url=self.db_url)
        # Duplicate username
        with self.assertRaises(AuthenticationError):
            register_user("bob", "bob_other@example.com", "password123", db_url=self.db_url)
        # Duplicate email
        with self.assertRaises(AuthenticationError):
            register_user("bob2", "bob@example.com", "password123", db_url=self.db_url)


class TestMultiPortfolioManagement(unittest.TestCase):
    """Test creating, switching, updating, and deleting multiple portfolios."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_portfolios.db")
        self.db_url = f"sqlite:///{self.db_path}"
        init_db(self.db_url)

        self.user = register_user("portfolio_tester", "ptester@example.com", "pass12345", db_url=self.db_url)
        self.user_id = self.user["id"]
        self.default_pf_id = self.user["default_portfolio_id"]

    def tearDown(self):
        close_all_engines()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_create_and_get_portfolios(self):
        pf2 = create_portfolio(self.user_id, "Tech Growth", "High-beta US tech", db_url=self.db_url)
        self.assertEqual(pf2["name"], "Tech Growth")

        portfolios = get_user_portfolios(self.user_id, db_url=self.db_url)
        self.assertEqual(len(portfolios), 2)
        names = [p["name"] for p in portfolios]
        self.assertIn("Main Portfolio", names)
        self.assertIn("Tech Growth", names)

    def test_update_portfolio(self):
        pf = create_portfolio(self.user_id, "Old Name", db_url=self.db_url)
        success = update_portfolio(pf["id"], self.user_id, name="New Name", description="Updated desc", db_url=self.db_url)
        self.assertTrue(success)

        updated = get_portfolio_by_id(pf["id"], self.user_id, db_url=self.db_url)
        self.assertEqual(updated["name"], "New Name")
        self.assertEqual(updated["description"], "Updated desc")

    def test_delete_portfolio_cascades_transactions(self):
        pf = create_portfolio(self.user_id, "Short Term", db_url=self.db_url)
        pf_id = pf["id"]

        add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", portfolio_id=pf_id, user_id=self.user_id, db_url=self.db_url)
        self.assertEqual(len(get_all_transactions(portfolio_id=pf_id, db_url=self.db_url, as_dataframe=False)), 1)

        # Delete portfolio
        self.assertTrue(delete_portfolio(pf_id, self.user_id, db_url=self.db_url))
        self.assertEqual(len(get_all_transactions(portfolio_id=pf_id, db_url=self.db_url, as_dataframe=False)), 0)

    def test_transaction_isolation_between_portfolios(self):
        pf_us = create_portfolio(self.user_id, "US Portfolio", db_url=self.db_url)
        pf_th = create_portfolio(self.user_id, "Thai Portfolio", db_url=self.db_url)

        add_transaction("NVDA", "US_STOCK", 5, 450.0, "USD", portfolio_id=pf_us["id"], user_id=self.user_id, db_url=self.db_url)
        add_transaction("PTT.BK", "TH_STOCK", 500, 32.0, "THB", portfolio_id=pf_th["id"], user_id=self.user_id, db_url=self.db_url)

        us_txs = get_all_transactions(portfolio_id=pf_us["id"], db_url=self.db_url, as_dataframe=False)
        th_txs = get_all_transactions(portfolio_id=pf_th["id"], db_url=self.db_url, as_dataframe=False)

        self.assertEqual(len(us_txs), 1)
        self.assertEqual(us_txs[0]["symbol"], "NVDA")

        self.assertEqual(len(th_txs), 1)
        self.assertEqual(th_txs[0]["symbol"], "PTT.BK")


class TestDatabaseCRUD(unittest.TestCase):
    """Test CRUD operations on SQLAlchemy database."""

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
        df = get_all_transactions(db_url=self.db_url, as_dataframe=True)
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 0)
        self.assertIn("symbol", df.columns)
        self.assertIn("created_at", df.columns)

    def test_add_and_get_transaction_dataframe(self):
        tx_id = add_transaction(
            symbol="AAPL",
            asset_type="US_STOCK",
            quantity=10,
            cost_per_share=150.0,
            currency="USD",
            purchase_date="2024-01-15",
            transaction_type="BUY",
            db_url=self.db_url,
        )
        self.assertGreater(tx_id, 0)

        df = get_all_transactions(db_url=self.db_url, as_dataframe=True)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["symbol"], "AAPL")
        self.assertEqual(df.iloc[0]["quantity"], 10.0)
        self.assertEqual(df.iloc[0]["cost_per_share"], 150.0)
        self.assertEqual(df.iloc[0]["transaction_type"], "BUY")

    def test_add_buy_and_sell_transactions(self):
        buy_id = add_transaction(
            symbol="AAPL",
            asset_type="US_STOCK",
            quantity=10,
            cost_per_share=150.0,
            currency="USD",
            purchase_date="2024-01-10",
            transaction_type="BUY",
            db_url=self.db_url,
        )
        sell_id = add_transaction(
            symbol="AAPL",
            asset_type="US_STOCK",
            quantity=4,
            cost_per_share=180.0,
            currency="USD",
            purchase_date="2024-01-20",
            transaction_type="SELL",
            db_url=self.db_url,
        )
        self.assertGreater(buy_id, 0)
        self.assertGreater(sell_id, 0)

        txs = get_all_transactions(db_url=self.db_url, as_dataframe=False)
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0]["transaction_type"], "BUY")
        self.assertEqual(txs[1]["transaction_type"], "SELL")

    def test_get_all_transactions_chronological_ordering(self):
        # Insert out of chronological order
        add_transaction("AAPL", "US_STOCK", 5, 180.0, "USD", "2024-03-01", transaction_type="SELL", db_url=self.db_url)
        add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", "2024-01-01", transaction_type="BUY", db_url=self.db_url)
        add_transaction("AAPL", "US_STOCK", 20, 160.0, "USD", "2024-02-01", transaction_type="BUY", db_url=self.db_url)

        txs = get_all_transactions(db_url=self.db_url, as_dataframe=False)
        self.assertEqual(len(txs), 3)
        self.assertEqual(txs[0]["purchase_date"], "2024-01-01")
        self.assertEqual(txs[0]["transaction_type"], "BUY")
        self.assertEqual(txs[1]["purchase_date"], "2024-02-01")
        self.assertEqual(txs[1]["transaction_type"], "BUY")
        self.assertEqual(txs[2]["purchase_date"], "2024-03-01")
        self.assertEqual(txs[2]["transaction_type"], "SELL")

    def test_direct_fifo_calculator_ingestion(self):
        from fifo_calculator import calculate_fifo_portfolio

        add_transaction("AAPL", "US_STOCK", 10, 100.0, "USD", "2024-01-01", transaction_type="BUY", db_url=self.db_url)
        add_transaction("AAPL", "US_STOCK", 20, 150.0, "USD", "2024-02-01", transaction_type="BUY", db_url=self.db_url)
        add_transaction("AAPL", "US_STOCK", 15, 200.0, "USD", "2024-03-01", transaction_type="SELL", db_url=self.db_url)

        # Fetch transactions sorted chronologically
        txs = get_all_transactions(db_url=self.db_url, as_dataframe=False)
        fifo_res = calculate_fifo_portfolio(txs)

        # Realized PnL: (200-100)*10 + (200-150)*5 = 1250.0
        self.assertEqual(fifo_res["realized_pnl"]["overall_total"], 1250.0)
        self.assertEqual(fifo_res["remaining_holdings"]["AAPL"]["total_quantity"], 15.0)
        self.assertEqual(fifo_res["remaining_holdings"]["AAPL"]["total_cost"], 2250.0)

    def test_delete_transaction(self):
        tx_id = add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", "2024-01-01", db_url=self.db_url)
        self.assertEqual(len(get_all_transactions(db_url=self.db_url, as_dataframe=False)), 1)

        result = delete_transaction(tx_id, db_url=self.db_url)
        self.assertTrue(result)
        self.assertEqual(len(get_all_transactions(db_url=self.db_url, as_dataframe=False)), 0)

    def test_update_transaction(self):
        tx_id = add_transaction("AAPL", "US_STOCK", 10, 150.0, "USD", "2024-01-01", db_url=self.db_url)
        success = update_transaction(tx_id, quantity=25, cost_per_share=160.0, transaction_type="SELL", db_url=self.db_url)
        self.assertTrue(success)

        tx = get_transaction_by_id(tx_id, db_url=self.db_url)
        self.assertEqual(tx["quantity"], 25.0)
        self.assertEqual(tx["cost_per_share"], 160.0)
        self.assertEqual(tx["transaction_type"], "SELL")


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
        tx_id = self.db.add("TSLA", "US_STOCK", 10, 200.0, "USD", "2024-01-10", transaction_type="BUY")
        self.assertEqual(tx_id, 1)

        tx = self.db.get_by_id(tx_id)
        self.assertEqual(tx["symbol"], "TSLA")
        self.assertEqual(tx["transaction_type"], "BUY")

        all_tx = self.db.get_all(as_dataframe=False)
        self.assertEqual(len(all_tx), 1)

        self.db.update(tx_id, cost_per_share=210.0, transaction_type="SELL")
        tx_updated = self.db.get_by_id(tx_id)
        self.assertEqual(tx_updated["cost_per_share"], 210.0)
        self.assertEqual(tx_updated["transaction_type"], "SELL")

        self.assertTrue(self.db.delete(tx_id))
        self.assertEqual(len(self.db.get_all(as_dataframe=False)), 0)


if __name__ == "__main__":
    unittest.main()
