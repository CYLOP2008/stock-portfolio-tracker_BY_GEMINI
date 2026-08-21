"""
database.py
===========
SQLAlchemy Database Management Module for Stock & Fund Portfolio Tracker.
Connects to Supabase PostgreSQL or local SQLite.

Requirements:
1. Reads DATABASE_URL from st.secrets["DATABASE_URL"] with fallback to os.getenv("DATABASE_URL").
2. Database engine created using sqlalchemy.create_engine().
3. Schema:
   - id (SERIAL PRIMARY KEY)
   - symbol (VARCHAR(20))
   - asset_type (VARCHAR(20))
   - quantity (NUMERIC)
   - cost_per_share (NUMERIC)
   - currency (VARCHAR(10))
   - purchase_date (DATE)
   - created_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
4. CRUD Functions:
   - init_db(): Automatically create the transactions table if it doesn't exist.
   - add_transaction(symbol, asset_type, quantity, cost_per_share, currency, purchase_date): Insert a new transaction.
   - delete_transaction(transaction_id): Remove a transaction by ID.
   - get_all_transactions(): Fetch all transactions and return as a Pandas DataFrame.
"""

from collections import defaultdict
from datetime import datetime, date
import logging
import os
from typing import Any, Dict, List, Optional, Union
import pandas as pd
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Configure module logger
logger = logging.getLogger("portfolio_database")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default fallback SQLite path and URL
DEFAULT_DB_PATH = "portfolio.db"
DEFAULT_DB_URL = "sqlite:///portfolio.db"

# Valid Enumerations
VALID_ASSET_TYPES = {"US_STOCK", "TH_STOCK", "TH_MUTUAL_FUND"}
VALID_CURRENCIES = {"USD", "THB"}

# SQLAlchemy Declarative Base
Base = declarative_base()


class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


class ValidationError(ValueError):
    """Exception raised for invalid transaction inputs."""
    pass


class Transaction(Base):
    """SQLAlchemy ORM Model representing a financial portfolio transaction.

    Schema:
        - id: SERIAL PRIMARY KEY
        - symbol: VARCHAR(20)
        - asset_type: VARCHAR(20)
        - quantity: NUMERIC
        - cost_per_share: NUMERIC
        - currency: VARCHAR(10)
        - purchase_date: DATE
        - created_at: TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    """

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    asset_type = Column(String(20), nullable=False, index=True)
    quantity = Column(Numeric(18, 8), nullable=False)
    cost_per_share = Column(Numeric(18, 6), nullable=False)
    currency = Column(String(10), nullable=False)
    purchase_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_tx_symbol_asset", "symbol", "asset_type"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert ORM model instance to dictionary."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "quantity": float(self.quantity) if self.quantity is not None else 0.0,
            "cost_per_share": float(self.cost_per_share) if self.cost_per_share is not None else 0.0,
            "currency": self.currency,
            "purchase_date": str(self.purchase_date),
            "created_at": str(self.created_at) if self.created_at else None,
        }


# Global engine cache to manage connection pools
_ENGINE_CACHE: Dict[str, Engine] = {}


def get_database_url(db_url: Optional[str] = None) -> str:
    """Read DATABASE_URL from st.secrets['DATABASE_URL'] with fallback to os.getenv('DATABASE_URL').

    Normalizes 'postgres://' to 'postgresql+psycopg2://' for Supabase compatibility.
    Falls back to local SQLite ('sqlite:///portfolio.db') if no URL or placeholder is provided.

    Args:
        db_url (str, optional): Explicit database URL or file path.

    Returns:
        str: Fully qualified SQLAlchemy connection URL.
    """
    resolved_url = db_url

    # Helper to check if a URL contains unpopulated template placeholders
    def is_placeholder(u: str) -> bool:
        if not u or not isinstance(u, str):
            return True
        lower_u = u.lower()
        return any(p in lower_u for p in ("your-project", "your-password", "<password>", "placeholder", "user:password@host"))

    # 1. Check Streamlit secrets
    if not resolved_url:
        try:
            import streamlit as st
            if hasattr(st, "secrets"):
                if "DATABASE_URL" in st.secrets and not is_placeholder(st.secrets["DATABASE_URL"]):
                    resolved_url = st.secrets["DATABASE_URL"]
                elif "postgres" in st.secrets and isinstance(st.secrets["postgres"], dict) and "url" in st.secrets["postgres"] and not is_placeholder(st.secrets["postgres"]["url"]):
                    resolved_url = st.secrets["postgres"]["url"]
        except Exception:
            pass

    # 2. Check environment variable fallback
    if not resolved_url:
        env_url = (
            os.getenv("DATABASE_URL")
            or os.getenv("POSTGRES_URL")
            or os.getenv("SUPABASE_DB_URL")
        )
        if env_url and not is_placeholder(env_url):
            resolved_url = env_url

    # 3. Fallback to local SQLite database
    if not resolved_url:
        resolved_url = DEFAULT_DB_URL

    # Normalize file paths to SQLite URLs if needed
    if not ("://" in resolved_url or resolved_url.startswith("sqlite:")):
        if resolved_url == ":memory:":
            resolved_url = "sqlite:///:memory:"
        else:
            resolved_url = f"sqlite:///{resolved_url}"

    # Supabase & Heroku compatibility: convert postgres:// to postgresql+psycopg2://
    if resolved_url.startswith("postgres://"):
        resolved_url = resolved_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif resolved_url.startswith("postgresql://") and "+psycopg2" not in resolved_url:
        resolved_url = resolved_url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return resolved_url


def get_engine(db_url: Optional[str] = None) -> Engine:
    """Create and return a configured SQLAlchemy Engine.

    Args:
        db_url (str, optional): Database connection URL.

    Returns:
        Engine: SQLAlchemy Engine.
    """
    url = get_database_url(db_url)

    if url not in _ENGINE_CACHE:
        engine_args: Dict[str, Any] = {}
        if url.startswith("sqlite"):
            engine_args["connect_args"] = {"check_same_thread": False}
        else:
            # Pool configuration for PostgreSQL / Supabase
            engine_args["pool_size"] = 5
            engine_args["max_overflow"] = 10
            engine_args["pool_pre_ping"] = True

        _ENGINE_CACHE[url] = create_engine(url, **engine_args)

    return _ENGINE_CACHE[url]


def close_all_engines() -> None:
    """Dispose all cached SQLAlchemy engines and close active connection pools."""
    global _ENGINE_CACHE
    for url, eng in list(_ENGINE_CACHE.items()):
        try:
            eng.dispose()
        except Exception:
            pass
    _ENGINE_CACHE.clear()


def get_connection(db_path: str = DEFAULT_DB_PATH):
    """Create and return a raw SQLite connection for local sqlite-specific utilities."""
    import sqlite3
    clean_path = db_path.replace("sqlite:///", "") if str(db_path).startswith("sqlite:///") else str(db_path)
    conn = sqlite3.connect(clean_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_session(db_url: Optional[str] = None) -> Session:
    """Create and return a new SQLAlchemy Session.

    Args:
        db_url (str, optional): Database connection URL.

    Returns:
        Session: Configured SQLAlchemy Session.
    """
    engine = get_engine(db_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return session_factory()


def init_db(db_url: Optional[str] = None) -> None:
    """Automatically create the transactions table if it doesn't exist.

    Args:
        db_url (str, optional): Target database connection URL.
    """
    try:
        engine = get_engine(db_url)
        Base.metadata.create_all(bind=engine)

        # Migration helper: ensure created_at column exists if table was created previously
        try:
            with engine.begin() as conn:
                if engine.dialect.name == "sqlite":
                    cols = [row[1] for row in conn.execute(text("PRAGMA table_info(transactions)")).fetchall()]
                    if cols and "created_at" not in cols:
                        conn.execute(text("ALTER TABLE transactions ADD COLUMN created_at TIMESTAMP"))
                        conn.execute(text("UPDATE transactions SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
                elif engine.dialect.name == "postgresql":
                    conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
        except Exception:
            pass

        logger.info(f"Database schema initialized successfully for '{engine.url}'.")
    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")
        raise DatabaseError(f"Database schema initialization failed: {e}") from e


def _validate_and_normalize_inputs(
    symbol: str,
    asset_type: str,
    quantity: Union[int, float],
    cost_per_share: Union[int, float],
    currency: Optional[str],
    purchase_date: Optional[Union[str, date]],
) -> Dict[str, Any]:
    """Validate and clean transaction input parameters."""
    # 1. Symbol validation
    if not symbol or not isinstance(symbol, str) or not symbol.strip():
        raise ValidationError("Symbol must be a non-empty string.")
    norm_symbol = symbol.strip().upper()

    # 2. Asset type validation
    if not asset_type or not isinstance(asset_type, str):
        raise ValidationError("Asset type must be provided as a string.")
    norm_asset_type = asset_type.strip().upper()
    if norm_asset_type not in VALID_ASSET_TYPES:
        raise ValidationError(
            f"Invalid asset_type '{norm_asset_type}'. Must be one of {sorted(VALID_ASSET_TYPES)}."
        )

    # 3. Numeric validation
    try:
        norm_quantity = float(quantity)
    except (ValueError, TypeError):
        raise ValidationError(f"Quantity must be a valid number, got: {quantity}")
    if norm_quantity <= 0:
        raise ValidationError(f"Quantity must be strictly greater than 0, got: {norm_quantity}")

    try:
        norm_cost_per_share = float(cost_per_share)
    except (ValueError, TypeError):
        raise ValidationError(f"Cost per share must be a valid number, got: {cost_per_share}")
    if norm_cost_per_share < 0:
        raise ValidationError(f"Cost per share cannot be negative, got: {norm_cost_per_share}")

    # 4. Currency validation & inference
    if currency is None or (isinstance(currency, str) and not currency.strip()):
        norm_currency = "USD" if norm_asset_type == "US_STOCK" else "THB"
    else:
        norm_currency = str(currency).strip().upper()

    if norm_currency not in VALID_CURRENCIES:
        raise ValidationError(
            f"Invalid currency '{norm_currency}'. Must be one of {sorted(VALID_CURRENCIES)}."
        )

    # 5. Purchase Date validation
    if purchase_date is None or (isinstance(purchase_date, str) and not purchase_date.strip()):
        norm_date_str = datetime.now().strftime("%Y-%m-%d")
        norm_date = datetime.now().date()
    elif isinstance(purchase_date, date):
        norm_date_str = purchase_date.strftime("%Y-%m-%d")
        norm_date = purchase_date
    else:
        date_str = str(purchase_date).strip()
        try:
            parsed_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
            norm_date_str = parsed_date.strftime("%Y-%m-%d")
            norm_date = parsed_date.date()
        except ValueError:
            raise ValidationError(
                f"Invalid purchase_date format '{date_str}'. Expected 'YYYY-MM-DD'."
            )

    return {
        "symbol": norm_symbol,
        "asset_type": norm_asset_type,
        "quantity": norm_quantity,
        "cost_per_share": norm_cost_per_share,
        "currency": norm_currency,
        "purchase_date": norm_date_str,
        "purchase_date_obj": norm_date,
    }


def add_transaction(
    symbol: str,
    asset_type: str,
    quantity: Union[int, float],
    cost_per_share: Union[int, float],
    currency: Optional[str] = None,
    purchase_date: Optional[Union[str, date]] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> int:
    """Insert a new transaction into the PostgreSQL / SQLite database.

    Args:
        symbol (str): Ticker symbol (e.g. 'AAPL', 'PTT.BK', 'SCBDV').
        asset_type (str): 'US_STOCK', 'TH_STOCK', or 'TH_MUTUAL_FUND'.
        quantity (float): Number of units / shares purchased (> 0).
        cost_per_share (float): Purchase price per unit / share (>= 0).
        currency (str, optional): 'USD' or 'THB'. Auto-inferred if not provided.
        purchase_date (str | date, optional): Purchase date. Defaults to today.
        db_path (str, optional): Alias for db_url.
        db_url (str, optional): Target database connection URL.

    Returns:
        int: The primary key ID of the newly inserted transaction.
    """
    target_url = db_url or db_path
    init_db(target_url)

    clean_params = _validate_and_normalize_inputs(
        symbol=symbol,
        asset_type=asset_type,
        quantity=quantity,
        cost_per_share=cost_per_share,
        currency=currency,
        purchase_date=purchase_date,
    )

    session = get_session(target_url)
    try:
        new_tx = Transaction(
            symbol=clean_params["symbol"],
            asset_type=clean_params["asset_type"],
            quantity=clean_params["quantity"],
            cost_per_share=clean_params["cost_per_share"],
            currency=clean_params["currency"],
            purchase_date=clean_params["purchase_date_obj"],
        )
        session.add(new_tx)
        session.commit()
        session.refresh(new_tx)
        new_id = int(new_tx.id)
        logger.info(f"Added transaction #{new_id} for symbol '{clean_params['symbol']}'.")
        return new_id
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to add transaction for {symbol}: {e}")
        raise DatabaseError(f"Database insert error: {e}") from e
    finally:
        session.close()


def delete_transaction(
    transaction_id: int,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> bool:
    """Remove a transaction by ID.

    Args:
        transaction_id (int): Transaction ID to delete.
        db_path (str, optional): Alias for db_url.
        db_url (str, optional): Target database connection URL.

    Returns:
        bool: True if a record was found and deleted, False otherwise.
    """
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        tx = session.get(Transaction, transaction_id)
        if tx:
            session.delete(tx)
            session.commit()
            logger.info(f"Deleted transaction #{transaction_id}.")
            return True
        else:
            logger.warning(f"Transaction #{transaction_id} not found for deletion.")
            return False
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to delete transaction #{transaction_id}: {e}")
        raise DatabaseError(f"Database delete error: {e}") from e
    finally:
        session.close()


def get_all_transactions(
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
    as_dataframe: bool = True,
) -> Union[pd.DataFrame, List[Dict[str, Any]]]:
    """Fetch all transactions and return as a Pandas DataFrame by default.

    Args:
        db_path (str, optional): Alias for db_url.
        db_url (str, optional): Database connection URL.
        as_dataframe (bool): If True, returns a pd.DataFrame (default). If False, returns List[Dict].

    Returns:
        pd.DataFrame or List[Dict[str, Any]]: Collection of all transaction records.
    """
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        stmt = select(Transaction).order_by(Transaction.purchase_date.asc(), Transaction.id.asc())
        results = session.scalars(stmt).all()
        records = [tx.to_dict() for tx in results]

        if as_dataframe:
            if not records:
                return pd.DataFrame(columns=[
                    "id", "symbol", "asset_type", "quantity", "cost_per_share", "currency", "purchase_date", "created_at"
                ])
            return pd.DataFrame(records)
        return records
    except Exception as e:
        logger.error(f"Failed to retrieve transactions: {e}")
        raise DatabaseError(f"Database query error: {e}") from e
    finally:
        session.close()


def fetch_all_transactions(
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all transactions formatted as a Python list of dictionaries."""
    return get_all_transactions(db_path=db_path, db_url=db_url, as_dataframe=False)


def get_transaction_by_id(
    transaction_id: int,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve a single transaction by primary key ID."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        tx = session.get(Transaction, transaction_id)
        return tx.to_dict() if tx else None
    except Exception as e:
        logger.error(f"Failed to get transaction #{transaction_id}: {e}")
        raise DatabaseError(f"Database query error: {e}") from e
    finally:
        session.close()


def get_transactions_by_symbol(
    symbol: str,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all transactions matching a given ticker symbol."""
    target_url = db_url or db_path
    init_db(target_url)
    norm_symbol = symbol.strip().upper()

    session = get_session(target_url)
    try:
        stmt = (
            select(Transaction)
            .where(Transaction.symbol == norm_symbol)
            .order_by(Transaction.purchase_date.asc(), Transaction.id.asc())
        )
        results = session.scalars(stmt).all()
        return [tx.to_dict() for tx in results]
    except Exception as e:
        logger.error(f"Failed to get transactions for symbol '{symbol}': {e}")
        raise DatabaseError(f"Database query error: {e}") from e
    finally:
        session.close()


def get_transactions_by_asset_type(
    asset_type: str,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all transactions matching an asset type."""
    target_url = db_url or db_path
    init_db(target_url)
    norm_type = asset_type.strip().upper()

    session = get_session(target_url)
    try:
        stmt = (
            select(Transaction)
            .where(Transaction.asset_type == norm_type)
            .order_by(Transaction.purchase_date.asc(), Transaction.id.asc())
        )
        results = session.scalars(stmt).all()
        return [tx.to_dict() for tx in results]
    except Exception as e:
        logger.error(f"Failed to get transactions for asset_type '{asset_type}': {e}")
        raise DatabaseError(f"Database query error: {e}") from e
    finally:
        session.close()


def get_portfolio_summary_holdings(
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Group transactions by symbol to calculate total quantity and average cost basis."""
    transactions = fetch_all_transactions(db_path=db_path, db_url=db_url)
    if not transactions:
        return []

    groups = defaultdict(lambda: {"total_quantity": 0.0, "total_cost": 0.0, "transaction_count": 0})
    for tx in transactions:
        key = (tx["symbol"], tx["asset_type"], tx["currency"])
        qty = float(tx["quantity"])
        cost_per_share = float(tx["cost_per_share"])
        groups[key]["total_quantity"] += qty
        groups[key]["total_cost"] += (qty * cost_per_share)
        groups[key]["transaction_count"] += 1

    summary = []
    for (symbol, asset_type, currency), data in groups.items():
        total_qty = round(data["total_quantity"], 8)
        total_cost = round(data["total_cost"], 4)
        avg_cost = round(total_cost / total_qty, 6) if total_qty > 0 else 0.0

        summary.append({
            "symbol": symbol,
            "asset_type": asset_type,
            "currency": currency,
            "total_quantity": total_qty,
            "total_cost": total_cost,
            "avg_cost_per_share": avg_cost,
            "transaction_count": data["transaction_count"],
        })

    summary.sort(key=lambda x: (x["asset_type"], x["symbol"]))
    return summary


def update_transaction(
    transaction_id: int,
    symbol: Optional[str] = None,
    asset_type: Optional[str] = None,
    quantity: Optional[Union[int, float]] = None,
    cost_per_share: Optional[Union[int, float]] = None,
    currency: Optional[str] = None,
    purchase_date: Optional[Union[str, date]] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> bool:
    """Update an existing transaction record."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        tx = session.get(Transaction, transaction_id)
        if not tx:
            logger.warning(f"Cannot update: Transaction #{transaction_id} not found.")
            return False

        new_sym = symbol if symbol is not None else tx.symbol
        new_type = asset_type if asset_type is not None else tx.asset_type
        new_qty = quantity if quantity is not None else tx.quantity
        new_cost = cost_per_share if cost_per_share is not None else tx.cost_per_share
        new_curr = currency if currency is not None else tx.currency
        new_date = purchase_date if purchase_date is not None else tx.purchase_date

        clean_params = _validate_and_normalize_inputs(
            symbol=new_sym,
            asset_type=new_type,
            quantity=new_qty,
            cost_per_share=new_cost,
            currency=new_curr,
            purchase_date=new_date,
        )

        tx.symbol = clean_params["symbol"]
        tx.asset_type = clean_params["asset_type"]
        tx.quantity = clean_params["quantity"]
        tx.cost_per_share = clean_params["cost_per_share"]
        tx.currency = clean_params["currency"]
        tx.purchase_date = clean_params["purchase_date_obj"]

        session.commit()
        logger.info(f"Updated transaction #{transaction_id}.")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to update transaction #{transaction_id}: {e}")
        raise DatabaseError(f"Database update error: {e}") from e
    finally:
        session.close()


def clear_all_transactions(
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> int:
    """Delete all transactions from the database. Useful for resetting test environments."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        stmt = select(Transaction)
        all_txs = session.scalars(stmt).all()
        count = len(all_txs)
        for tx in all_txs:
            session.delete(tx)
        session.commit()
        logger.info(f"Cleared all {count} transactions from database.")
        return count
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to clear transactions: {e}")
        raise DatabaseError(f"Database clear error: {e}") from e
    finally:
        session.close()


class PortfolioDB:
    """Object-oriented wrapper around SQLAlchemy database operations."""

    def __init__(self, db_url: Optional[str] = None, db_path: Optional[str] = None):
        self.db_url = get_database_url(db_url or db_path)
        init_db(self.db_url)

    def add(
        self,
        symbol: str,
        asset_type: str,
        quantity: Union[int, float],
        cost_per_share: Union[int, float],
        currency: Optional[str] = None,
        purchase_date: Optional[Union[str, date]] = None,
    ) -> int:
        return add_transaction(
            symbol=symbol,
            asset_type=asset_type,
            quantity=quantity,
            cost_per_share=cost_per_share,
            currency=currency,
            purchase_date=purchase_date,
            db_url=self.db_url,
        )

    def add_transaction(self, *args, **kwargs) -> int:
        return self.add(*args, **kwargs)

    def delete(self, transaction_id: int) -> bool:
        return delete_transaction(transaction_id, db_url=self.db_url)

    def delete_transaction(self, transaction_id: int) -> bool:
        return self.delete(transaction_id)

    def get_all(self, as_dataframe: bool = False) -> Union[List[Dict[str, Any]], pd.DataFrame]:
        return get_all_transactions(db_url=self.db_url, as_dataframe=as_dataframe)

    def get_all_transactions(self, as_dataframe: bool = True) -> Union[pd.DataFrame, List[Dict[str, Any]]]:
        return get_all_transactions(db_url=self.db_url, as_dataframe=as_dataframe)

    def get_by_id(self, transaction_id: int) -> Optional[Dict[str, Any]]:
        return get_transaction_by_id(transaction_id, db_url=self.db_url)

    def get_transaction_by_id(self, transaction_id: int) -> Optional[Dict[str, Any]]:
        return self.get_by_id(transaction_id)

    def get_by_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        return get_transactions_by_symbol(symbol, db_url=self.db_url)

    def get_by_asset_type(self, asset_type: str) -> List[Dict[str, Any]]:
        return get_transactions_by_asset_type(asset_type, db_url=self.db_url)

    def get_holdings_summary(self) -> List[Dict[str, Any]]:
        return get_portfolio_summary_holdings(db_url=self.db_url)

    def update(
        self,
        transaction_id: int,
        symbol: Optional[str] = None,
        asset_type: Optional[str] = None,
        quantity: Optional[Union[int, float]] = None,
        cost_per_share: Optional[Union[int, float]] = None,
        currency: Optional[str] = None,
        purchase_date: Optional[Union[str, date]] = None,
    ) -> bool:
        return update_transaction(
            transaction_id=transaction_id,
            symbol=symbol,
            asset_type=asset_type,
            quantity=quantity,
            cost_per_share=cost_per_share,
            currency=currency,
            purchase_date=purchase_date,
            db_url=self.db_url,
        )

    def update_transaction(self, *args, **kwargs) -> bool:
        return self.update(*args, **kwargs)

    def clear_all(self) -> int:
        return clear_all_transactions(db_url=self.db_url)

    def clear_all_transactions(self) -> int:
        return self.clear_all()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
