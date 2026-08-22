"""
database.py
===========
SQLAlchemy Database Management Module for Stock & Fund Portfolio Tracker.
Supports PostgreSQL (Supabase) and local SQLite with multi-user authentication
and multi-portfolio management.

Key Features:
1. Multi-User Authentication:
   - User registration & login with secure PBKDF2-HMAC-SHA256 salted password hashing.
   - Automatic provisioning of a default 'Main Portfolio' upon registration.
2. Multi-Portfolio Architecture:
   - Users can create, switch, rename, and delete multiple distinct portfolios.
   - Transactions are scoped to individual portfolios.
3. Robust Connection Management:
   - Reads DATABASE_URL from st.secrets or environment variables with fallback to SQLite.
   - Automated schema migrations for SQLite and PostgreSQL.
"""

from collections import defaultdict
from datetime import datetime, date
import hashlib
import hmac
import logging
import os
import secrets
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
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
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

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
VALID_TRANSACTION_TYPES = {"BUY", "SELL"}

# SQLAlchemy Declarative Base
Base = declarative_base()


# ==============================================================================
# EXCEPTIONS
# ==============================================================================

class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


class ValidationError(ValueError):
    """Exception raised for invalid transaction or input validation."""
    pass


class AuthenticationError(Exception):
    """Exception raised for authentication and registration failures."""
    pass


# ==============================================================================
# PASSWORD SECURITY & HASHING (PBKDF2-HMAC-SHA256)
# ==============================================================================

def hash_password(password: str) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256 with a cryptographically secure random salt.

    Args:
        password (str): Plaintext password to hash.

    Returns:
        str: Salted password hash in format 'salt$iterations$hash_hex'.
    """
    if not password or not isinstance(password, str):
        raise ValidationError("Password must be a non-empty string.")

    salt = secrets.token_hex(16)
    iterations = 100000
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return f"{salt}${iterations}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored salted hash using constant-time comparison.

    Args:
        password (str): Plaintext password to verify.
        stored_hash (str): Salted password hash from database.

    Returns:
        bool: True if password matches, False otherwise.
    """
    if not password or not stored_hash or "$" not in stored_hash:
        return False

    try:
        parts = stored_hash.split("$")
        if len(parts) != 3:
            return False
        salt, iterations_str, hash_hex = parts
        iterations = int(iterations_str)

        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return hmac.compare_digest(derived.hex(), hash_hex)
    except Exception:
        return False


# ==============================================================================
# SQLALCHEMY ORM DATA MODELS
# ==============================================================================

class User(Base):
    """SQLAlchemy ORM Model representing an application user."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    portfolios = relationship("Portfolio", back_populates="user", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "created_at": str(self.created_at) if self.created_at else None,
        }


class Portfolio(Base):
    """SQLAlchemy ORM Model representing a user's portfolio container."""

    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    user = relationship("User", back_populates="portfolios")
    transactions = relationship("Transaction", back_populates="portfolio", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description or "",
            "created_at": str(self.created_at) if self.created_at else None,
        }


class Transaction(Base):
    """SQLAlchemy ORM Model representing a financial transaction."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    transaction_type = Column(String(10), nullable=False, default="BUY", server_default="BUY", index=True)
    symbol = Column(String(20), nullable=False, index=True)
    asset_type = Column(String(20), nullable=False, index=True)
    quantity = Column(Numeric(18, 8), nullable=False)
    cost_per_share = Column(Numeric(18, 6), nullable=False)
    currency = Column(String(10), nullable=False)
    purchase_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    portfolio = relationship("Portfolio", back_populates="transactions")
    user = relationship("User", back_populates="transactions")

    __table_args__ = (
        Index("idx_tx_symbol_asset", "symbol", "asset_type"),
        Index("idx_tx_portfolio_symbol", "portfolio_id", "symbol"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert ORM model instance to dictionary."""
        return {
            "id": self.id,
            "portfolio_id": self.portfolio_id,
            "user_id": self.user_id,
            "transaction_type": self.transaction_type or "BUY",
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "quantity": float(self.quantity) if self.quantity is not None else 0.0,
            "cost_per_share": float(self.cost_per_share) if self.cost_per_share is not None else 0.0,
            "currency": self.currency,
            "purchase_date": str(self.purchase_date),
            "created_at": str(self.created_at) if self.created_at else None,
        }


# ==============================================================================
# DATABASE CONNECTION MANAGEMENT
# ==============================================================================

_ENGINE_CACHE: Dict[str, Engine] = {}


def get_database_url(db_url: Optional[str] = None) -> str:
    """Resolve database URL from st.secrets, environment variables, or local fallback."""
    resolved_url = db_url

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

    # Supabase compatibility: convert postgres:// to postgresql+psycopg2://
    if resolved_url.startswith("postgres://"):
        resolved_url = resolved_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif resolved_url.startswith("postgresql://") and "+psycopg2" not in resolved_url:
        resolved_url = resolved_url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return resolved_url


def get_engine(db_url: Optional[str] = None) -> Engine:
    """Create and return a configured SQLAlchemy Engine."""
    url = get_database_url(db_url)

    if url not in _ENGINE_CACHE:
        engine_args: Dict[str, Any] = {}
        if url.startswith("sqlite"):
            engine_args["connect_args"] = {"check_same_thread": False}
        else:
            engine_args["pool_size"] = 5
            engine_args["max_overflow"] = 10
            engine_args["pool_pre_ping"] = True
            engine_args["connect_args"] = {"connect_timeout": 10}

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
    """Create and return a new SQLAlchemy Session."""
    engine = get_engine(db_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return session_factory()


def init_db(db_url: Optional[str] = None) -> None:
    """Automatically create all tables and apply necessary migrations."""
    try:
        engine = get_engine(db_url)
        Base.metadata.create_all(bind=engine)

        # Migration helpers: ensure newly added columns exist in older database tables
        try:
            with engine.begin() as conn:
                if engine.dialect.name == "sqlite":
                    tx_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(transactions)")).fetchall()]
                    if tx_cols:
                        if "created_at" not in tx_cols:
                            conn.execute(text("ALTER TABLE transactions ADD COLUMN created_at TIMESTAMP"))
                            conn.execute(text("UPDATE transactions SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
                        if "portfolio_id" not in tx_cols:
                            conn.execute(text("ALTER TABLE transactions ADD COLUMN portfolio_id INTEGER"))
                        if "user_id" not in tx_cols:
                            conn.execute(text("ALTER TABLE transactions ADD COLUMN user_id INTEGER"))
                        if "transaction_type" not in tx_cols:
                            conn.execute(text("ALTER TABLE transactions ADD COLUMN transaction_type VARCHAR(10) DEFAULT 'BUY'"))
                            conn.execute(text("UPDATE transactions SET transaction_type = 'BUY' WHERE transaction_type IS NULL"))
                elif engine.dialect.name == "postgresql":
                    conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
                    conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS portfolio_id INTEGER REFERENCES portfolios(id) ON DELETE CASCADE"))
                    conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"))
                    conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS transaction_type VARCHAR(10) DEFAULT 'BUY'"))
        except Exception:
            pass

        logger.info(f"Database schema initialized successfully for '{engine.url}'.")
    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")
        raise DatabaseError(f"Database schema initialization failed: {e}") from e


# ==============================================================================
# MULTI-USER AUTHENTICATION FUNCTIONS
# ==============================================================================

def register_user(
    username: str,
    email: str,
    password: str,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a new user and automatically create their default 'Main Portfolio'.

    Args:
        username (str): Unique username (3-50 chars).
        email (str): Unique email address.
        password (str): Plaintext password (min 6 chars).
        db_path / db_url: Database connection target.

    Returns:
        Dict[str, Any]: Created user record with default portfolio info.
    """
    target_url = db_url or db_path
    init_db(target_url)

    # 1. Validation
    if not username or not isinstance(username, str) or len(username.strip()) < 3:
        raise ValidationError("Username must be at least 3 characters long.")
    norm_username = username.strip()

    if not email or not isinstance(email, str) or "@" not in email:
        raise ValidationError("Please provide a valid email address.")
    norm_email = email.strip().lower()

    if not password or not isinstance(password, str) or len(password) < 6:
        raise ValidationError("Password must be at least 6 characters long.")

    session = get_session(target_url)
    try:
        # Check if username or email already exists
        existing_user = session.scalars(
            select(User).where((User.username == norm_username) | (User.email == norm_email))
        ).first()

        if existing_user:
            if existing_user.username.lower() == norm_username.lower():
                raise AuthenticationError(f"Username '{norm_username}' is already taken.")
            raise AuthenticationError(f"Email '{norm_email}' is already registered.")

        # Create new user
        hashed_pwd = hash_password(password)
        new_user = User(
            username=norm_username,
            email=norm_email,
            password_hash=hashed_pwd,
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)

        # Automatically create default 'Main Portfolio'
        default_portfolio = Portfolio(
            user_id=new_user.id,
            name="Main Portfolio",
            description="Default multi-asset portfolio",
        )
        session.add(default_portfolio)
        session.commit()
        session.refresh(default_portfolio)

        user_data = new_user.to_dict()
        user_data["default_portfolio_id"] = default_portfolio.id
        logger.info(f"Registered user '{norm_username}' with default portfolio #{default_portfolio.id}.")
        return user_data
    except (ValidationError, AuthenticationError):
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to register user '{username}': {e}")
        raise DatabaseError(f"User registration error: {e}") from e
    finally:
        session.close()


def authenticate_user(
    username_or_email: str,
    password: str,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Authenticate a user by username or email and password.

    Args:
        username_or_email (str): Username or email.
        password (str): Plaintext password.

    Returns:
        Optional[Dict[str, Any]]: User profile dict if valid, None otherwise.
    """
    if not username_or_email or not password:
        return None

    target_url = db_url or db_path
    init_db(target_url)
    identifier = username_or_email.strip()

    session = get_session(target_url)
    try:
        user = session.scalars(
            select(User).where((User.username == identifier) | (User.email == identifier.lower()))
        ).first()

        if not user:
            return None

        if verify_password(password, user.password_hash):
            logger.info(f"User '{user.username}' authenticated successfully.")
            return user.to_dict()
        return None
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise DatabaseError(f"Authentication error: {e}") from e
    finally:
        session.close()


def get_user_by_id(
    user_id: int,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch user profile by primary key ID."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        user = session.get(User, user_id)
        return user.to_dict() if user else None
    finally:
        session.close()


# ==============================================================================
# MULTI-PORTFOLIO MANAGEMENT FUNCTIONS
# ==============================================================================

def create_portfolio(
    user_id: int,
    name: str,
    description: Optional[str] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new portfolio for a specified user."""
    target_url = db_url or db_path
    init_db(target_url)

    if not name or not isinstance(name, str) or not name.strip():
        raise ValidationError("Portfolio name must be a non-empty string.")
    norm_name = name.strip()

    session = get_session(target_url)
    try:
        user = session.get(User, user_id)
        if not user:
            raise ValidationError(f"User #{user_id} not found.")

        new_pf = Portfolio(
            user_id=user_id,
            name=norm_name,
            description=description.strip() if description else None,
        )
        session.add(new_pf)
        session.commit()
        session.refresh(new_pf)
        logger.info(f"Created portfolio #{new_pf.id} ('{norm_name}') for user #{user_id}.")
        return new_pf.to_dict()
    except ValidationError:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to create portfolio '{name}': {e}")
        raise DatabaseError(f"Portfolio creation error: {e}") from e
    finally:
        session.close()


def get_user_portfolios(
    user_id: int,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all portfolios owned by a specific user."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        stmt = select(Portfolio).where(Portfolio.user_id == user_id).order_by(Portfolio.id.asc())
        portfolios = session.scalars(stmt).all()

        # If user has no portfolios (e.g. legacy user), automatically create a default one
        if not portfolios:
            user = session.get(User, user_id)
            if user:
                default_pf = Portfolio(
                    user_id=user_id,
                    name="Main Portfolio",
                    description="Default portfolio",
                )
                session.add(default_pf)
                session.commit()
                session.refresh(default_pf)
                return [default_pf.to_dict()]

        return [pf.to_dict() for pf in portfolios]
    except Exception as e:
        logger.error(f"Failed to retrieve portfolios for user #{user_id}: {e}")
        raise DatabaseError(f"Portfolio retrieval error: {e}") from e
    finally:
        session.close()


def get_portfolio_by_id(
    portfolio_id: int,
    user_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Get a portfolio by ID, optionally verifying ownership by user_id."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        pf = session.get(Portfolio, portfolio_id)
        if not pf:
            return None
        if user_id is not None and pf.user_id != user_id:
            return None
        return pf.to_dict()
    finally:
        session.close()


def update_portfolio(
    portfolio_id: int,
    user_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> bool:
    """Update name or description of an existing portfolio owned by the user."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        pf = session.get(Portfolio, portfolio_id)
        if not pf or pf.user_id != user_id:
            logger.warning(f"Portfolio #{portfolio_id} not found or not owned by user #{user_id}.")
            return False

        if name is not None:
            if not name.strip():
                raise ValidationError("Portfolio name cannot be empty.")
            pf.name = name.strip()
        if description is not None:
            pf.description = description.strip()

        session.commit()
        logger.info(f"Updated portfolio #{portfolio_id}.")
        return True
    except ValidationError:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to update portfolio #{portfolio_id}: {e}")
        raise DatabaseError(f"Portfolio update error: {e}") from e
    finally:
        session.close()


def delete_portfolio(
    portfolio_id: int,
    user_id: int,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> bool:
    """Delete a portfolio and all its associated transactions."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        pf = session.get(Portfolio, portfolio_id)
        if not pf or pf.user_id != user_id:
            logger.warning(f"Portfolio #{portfolio_id} not found or not owned by user #{user_id}.")
            return False

        session.delete(pf)
        session.commit()
        logger.info(f"Deleted portfolio #{portfolio_id} for user #{user_id}.")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to delete portfolio #{portfolio_id}: {e}")
        raise DatabaseError(f"Portfolio delete error: {e}") from e
    finally:
        session.close()


# ==============================================================================
# TRANSACTION VALIDATION & CRUD FUNCTIONS
# ==============================================================================

def _validate_and_normalize_inputs(
    symbol: str,
    asset_type: str,
    quantity: Union[int, float],
    cost_per_share: Union[int, float],
    currency: Optional[str] = None,
    purchase_date: Optional[Union[str, date]] = None,
    transaction_type: str = "BUY",
) -> Dict[str, Any]:
    """Validate and clean transaction input parameters."""
    # 0. Transaction type validation
    if transaction_type is None or not str(transaction_type).strip():
        norm_tx_type = "BUY"
    else:
        norm_tx_type = str(transaction_type).strip().upper()
    if norm_tx_type not in VALID_TRANSACTION_TYPES:
        raise ValidationError(
            f"Invalid transaction_type '{norm_tx_type}'. Must be one of {sorted(VALID_TRANSACTION_TYPES)}."
        )

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
        "transaction_type": norm_tx_type,
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
    transaction_type: str = "BUY",
    portfolio_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> int:
    """Insert a new transaction into the PostgreSQL / SQLite database."""
    target_url = db_url or db_path
    init_db(target_url)

    clean_params = _validate_and_normalize_inputs(
        symbol=symbol,
        asset_type=asset_type,
        quantity=quantity,
        cost_per_share=cost_per_share,
        currency=currency,
        purchase_date=purchase_date,
        transaction_type=transaction_type,
    )

    session = get_session(target_url)
    try:
        new_tx = Transaction(
            portfolio_id=portfolio_id,
            user_id=user_id,
            transaction_type=clean_params["transaction_type"],
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
        logger.info(f"Added {clean_params['transaction_type']} transaction #{new_id} for symbol '{clean_params['symbol']}' in portfolio #{portfolio_id}.")
        return new_id
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to add transaction for {symbol}: {e}")
        raise DatabaseError(f"Database insert error: {e}") from e
    finally:
        session.close()


def delete_transaction(
    transaction_id: int,
    portfolio_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> bool:
    """Remove a transaction by ID, optionally verifying portfolio_id or user_id."""
    target_url = db_url or db_path
    init_db(target_url)

    session = get_session(target_url)
    try:
        tx = session.get(Transaction, transaction_id)
        if not tx:
            logger.warning(f"Transaction #{transaction_id} not found for deletion.")
            return False

        if portfolio_id is not None and tx.portfolio_id is not None and tx.portfolio_id != portfolio_id:
            logger.warning(f"Transaction #{transaction_id} does not belong to portfolio #{portfolio_id}.")
            return False

        if user_id is not None and tx.user_id is not None and tx.user_id != user_id:
            logger.warning(f"Transaction #{transaction_id} does not belong to user #{user_id}.")
            return False

        session.delete(tx)
        session.commit()
        logger.info(f"Deleted transaction #{transaction_id}.")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to delete transaction #{transaction_id}: {e}")
        raise DatabaseError(f"Database delete error: {e}") from e
    finally:
        session.close()


def get_all_transactions(
    portfolio_id: Optional[Union[int, str]] = None,
    user_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
    as_dataframe: bool = True,
) -> Union[pd.DataFrame, List[Dict[str, Any]]]:
    """Fetch all transactions scoped by portfolio_id or user_id, returning pd.DataFrame by default."""
    actual_pf_id = portfolio_id
    if isinstance(portfolio_id, str) and ("/" in portfolio_id or portfolio_id.endswith(".db") or "sqlite" in portfolio_id or "postgres" in portfolio_id):
        target_url = portfolio_id
        actual_pf_id = None
    else:
        target_url = db_url or db_path

    init_db(target_url)

    session = get_session(target_url)
    try:
        stmt = select(Transaction)
        if actual_pf_id is not None:
            stmt = stmt.where(Transaction.portfolio_id == int(actual_pf_id))
        elif user_id is not None:
            stmt = stmt.where(Transaction.user_id == user_id)

        stmt = stmt.order_by(Transaction.purchase_date.asc(), Transaction.id.asc())
        results = session.scalars(stmt).all()
        records = [tx.to_dict() for tx in results]

        if as_dataframe:
            if not records:
                return pd.DataFrame(columns=[
                    "id", "portfolio_id", "user_id", "transaction_type", "symbol", "asset_type", "quantity",
                    "cost_per_share", "currency", "purchase_date", "created_at"
                ])
            return pd.DataFrame(records)
        return records
    except Exception as e:
        logger.error(f"Failed to retrieve transactions: {e}")
        raise DatabaseError(f"Database query error: {e}") from e
    finally:
        session.close()


def fetch_all_transactions(
    portfolio_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all transactions formatted as a Python list of dictionaries."""
    return get_all_transactions(portfolio_id=portfolio_id, user_id=user_id, db_path=db_path, db_url=db_url, as_dataframe=False)


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
    portfolio_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all transactions matching a given ticker symbol."""
    target_url = db_url or db_path
    init_db(target_url)
    norm_symbol = symbol.strip().upper()

    session = get_session(target_url)
    try:
        stmt = select(Transaction).where(Transaction.symbol == norm_symbol)
        if portfolio_id is not None:
            stmt = stmt.where(Transaction.portfolio_id == portfolio_id)
        stmt = stmt.order_by(Transaction.purchase_date.asc(), Transaction.id.asc())
        results = session.scalars(stmt).all()
        return [tx.to_dict() for tx in results]
    except Exception as e:
        logger.error(f"Failed to get transactions for symbol '{symbol}': {e}")
        raise DatabaseError(f"Database query error: {e}") from e
    finally:
        session.close()


def get_transactions_by_asset_type(
    asset_type: str,
    portfolio_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all transactions matching an asset type."""
    target_url = db_url or db_path
    init_db(target_url)
    norm_type = asset_type.strip().upper()

    session = get_session(target_url)
    try:
        stmt = select(Transaction).where(Transaction.asset_type == norm_type)
        if portfolio_id is not None:
            stmt = stmt.where(Transaction.portfolio_id == portfolio_id)
        stmt = stmt.order_by(Transaction.purchase_date.asc(), Transaction.id.asc())
        results = session.scalars(stmt).all()
        return [tx.to_dict() for tx in results]
    except Exception as e:
        logger.error(f"Failed to get transactions for asset_type '{asset_type}': {e}")
        raise DatabaseError(f"Database query error: {e}") from e
    finally:
        session.close()


def get_portfolio_summary_holdings(
    portfolio_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Group transactions by symbol to calculate total quantity and average cost basis."""
    transactions = fetch_all_transactions(portfolio_id=portfolio_id, user_id=user_id, db_path=db_path, db_url=db_url)
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
    transaction_type: Optional[str] = None,
    portfolio_id: Optional[int] = None,
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
        new_tx_type = transaction_type if transaction_type is not None else (tx.transaction_type or "BUY")

        clean_params = _validate_and_normalize_inputs(
            symbol=new_sym,
            asset_type=new_type,
            quantity=new_qty,
            cost_per_share=new_cost,
            currency=new_curr,
            purchase_date=new_date,
            transaction_type=new_tx_type,
        )

        tx.symbol = clean_params["symbol"]
        tx.asset_type = clean_params["asset_type"]
        tx.quantity = clean_params["quantity"]
        tx.cost_per_share = clean_params["cost_per_share"]
        tx.currency = clean_params["currency"]
        tx.purchase_date = clean_params["purchase_date_obj"]
        tx.transaction_type = clean_params["transaction_type"]
        if portfolio_id is not None:
            tx.portfolio_id = portfolio_id

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
    portfolio_id: Optional[Union[int, str]] = None,
    user_id: Optional[int] = None,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> int:
    """Delete all transactions from the database or specified portfolio."""
    actual_pf_id = portfolio_id
    if isinstance(portfolio_id, str) and ("/" in portfolio_id or portfolio_id.endswith(".db") or "sqlite" in portfolio_id or "postgres" in portfolio_id):
        target_url = portfolio_id
        actual_pf_id = None
    else:
        target_url = db_url or db_path

    init_db(target_url)

    session = get_session(target_url)
    try:
        stmt = select(Transaction)
        if actual_pf_id is not None:
            stmt = stmt.where(Transaction.portfolio_id == int(actual_pf_id))
        elif user_id is not None:
            stmt = stmt.where(Transaction.user_id == user_id)

        all_txs = session.scalars(stmt).all()
        count = len(all_txs)
        for tx in all_txs:
            session.delete(tx)
        session.commit()
        logger.info(f"Cleared {count} transactions from database/portfolio.")
        return count
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to clear transactions: {e}")
        raise DatabaseError(f"Database clear error: {e}") from e
    finally:
        session.close()


# ==============================================================================
# CLASS INTERFACE
# ==============================================================================

class PortfolioDB:
    """Object-oriented wrapper around SQLAlchemy database operations."""

    def __init__(self, db_url: Optional[str] = None, db_path: Optional[str] = None):
        self.db_url = get_database_url(db_url or db_path)
        init_db(self.db_url)

    def register(self, username: str, email: str, password: str) -> Dict[str, Any]:
        return register_user(username=username, email=email, password=password, db_url=self.db_url)

    def authenticate(self, username_or_email: str, password: str) -> Optional[Dict[str, Any]]:
        return authenticate_user(username_or_email=username_or_email, password=password, db_url=self.db_url)

    def create_portfolio(self, user_id: int, name: str, description: Optional[str] = None) -> Dict[str, Any]:
        return create_portfolio(user_id=user_id, name=name, description=description, db_url=self.db_url)

    def get_portfolios(self, user_id: int) -> List[Dict[str, Any]]:
        return get_user_portfolios(user_id=user_id, db_url=self.db_url)

    def add(
        self,
        symbol: str,
        asset_type: str,
        quantity: Union[int, float],
        cost_per_share: Union[int, float],
        currency: Optional[str] = None,
        purchase_date: Optional[Union[str, date]] = None,
        transaction_type: str = "BUY",
        portfolio_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> int:
        return add_transaction(
            symbol=symbol,
            asset_type=asset_type,
            quantity=quantity,
            cost_per_share=cost_per_share,
            currency=currency,
            purchase_date=purchase_date,
            transaction_type=transaction_type,
            portfolio_id=portfolio_id,
            user_id=user_id,
            db_url=self.db_url,
        )

    def add_transaction(self, *args, **kwargs) -> int:
        return self.add(*args, **kwargs)

    def delete(self, transaction_id: int, portfolio_id: Optional[int] = None) -> bool:
        return delete_transaction(transaction_id, portfolio_id=portfolio_id, db_url=self.db_url)

    def delete_transaction(self, transaction_id: int, *args, **kwargs) -> bool:
        return self.delete(transaction_id, *args, **kwargs)

    def get_all(self, portfolio_id: Optional[int] = None, as_dataframe: bool = False) -> Union[List[Dict[str, Any]], pd.DataFrame]:
        return get_all_transactions(portfolio_id=portfolio_id, db_url=self.db_url, as_dataframe=as_dataframe)

    def get_all_transactions(self, portfolio_id: Optional[int] = None, as_dataframe: bool = True) -> Union[pd.DataFrame, List[Dict[str, Any]]]:
        return get_all_transactions(portfolio_id=portfolio_id, db_url=self.db_url, as_dataframe=as_dataframe)

    def get_by_id(self, transaction_id: int) -> Optional[Dict[str, Any]]:
        return get_transaction_by_id(transaction_id, db_url=self.db_url)

    def get_transaction_by_id(self, transaction_id: int) -> Optional[Dict[str, Any]]:
        return self.get_by_id(transaction_id)

    def get_by_symbol(self, symbol: str, portfolio_id: Optional[int] = None) -> List[Dict[str, Any]]:
        return get_transactions_by_symbol(symbol, portfolio_id=portfolio_id, db_url=self.db_url)

    def get_by_asset_type(self, asset_type: str, portfolio_id: Optional[int] = None) -> List[Dict[str, Any]]:
        return get_transactions_by_asset_type(asset_type, portfolio_id=portfolio_id, db_url=self.db_url)

    def get_holdings_summary(self, portfolio_id: Optional[int] = None) -> List[Dict[str, Any]]:
        return get_portfolio_summary_holdings(portfolio_id=portfolio_id, db_url=self.db_url)

    def update(
        self,
        transaction_id: int,
        symbol: Optional[str] = None,
        asset_type: Optional[str] = None,
        quantity: Optional[Union[int, float]] = None,
        cost_per_share: Optional[Union[int, float]] = None,
        currency: Optional[str] = None,
        purchase_date: Optional[Union[str, date]] = None,
        transaction_type: Optional[str] = None,
        portfolio_id: Optional[int] = None,
    ) -> bool:
        return update_transaction(
            transaction_id=transaction_id,
            symbol=symbol,
            asset_type=asset_type,
            quantity=quantity,
            cost_per_share=cost_per_share,
            currency=currency,
            purchase_date=purchase_date,
            transaction_type=transaction_type,
            portfolio_id=portfolio_id,
            db_url=self.db_url,
        )

    def update_transaction(self, *args, **kwargs) -> bool:
        return self.update(*args, **kwargs)

    def clear_all(self, portfolio_id: Optional[int] = None) -> int:
        return clear_all_transactions(portfolio_id=portfolio_id, db_url=self.db_url)

    def clear_all_transactions(self, portfolio_id: Optional[int] = None) -> int:
        return self.clear_all(portfolio_id=portfolio_id)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
