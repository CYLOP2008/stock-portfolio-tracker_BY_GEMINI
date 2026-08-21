"""
data_fetcher.py
================
Financial Data Fetching Module for portfolio tracking.
Supports:
1. US Stocks (e.g., AAPL, NVDA) & Thai Stocks (e.g., PTT.BK, CPALL.BK) via yfinance.
2. Real-time USD/THB exchange rate (THB=X) via yfinance.
3. Thai Mutual Fund NAV via SEC Thailand Open API with web scraping/public endpoint fallbacks.
4. Structured Python dictionaries and Pandas DataFrames with robust error handling.
"""

from datetime import datetime, timedelta
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union
import pandas as pd
import requests
import yfinance as yf

# Configure module logger
logger = logging.getLogger("data_fetcher")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default request timeout in seconds
REQUEST_TIMEOUT = 4
PRICE_CACHE_TTL_SECONDS = 120

# In-memory fast cache storage
_PRICE_CACHE: Dict[str, Dict[str, Any]] = {}
_PRICE_CACHE_EXPIRY: Dict[str, float] = {}


def clear_price_cache() -> None:
    """Clear all in-memory market price caches."""
    global _PRICE_CACHE, _PRICE_CACHE_EXPIRY
    _PRICE_CACHE.clear()
    _PRICE_CACHE_EXPIRY.clear()


def format_ticker_symbol(ticker: str) -> str:
    """Format and normalize ticker symbols.

    Ensures uppercase and cleans whitespace.
    """
    if not ticker or not isinstance(ticker, str):
        return ""
    return ticker.strip().upper()


def get_stock_price(ticker: str) -> Dict[str, Any]:
    """Fetch current and daily market price data for a given stock ticker.

    Supports:
        - US Stocks (e.g., 'AAPL', 'NVDA', 'MSFT', 'TSLA')
        - Thai Stocks on the SET (using .BK suffix, e.g., 'PTT.BK', 'CPALL.BK', 'BDMS.BK')

    Args:
        ticker (str): Ticker symbol.

    Returns:
        Dict[str, Any]: Standardized dictionary containing:
            - ticker (str): Normalized ticker symbol
            - name (str): Company name or short name
            - current_price (float): Latest traded price
            - currency (str): Trading currency (e.g., 'USD', 'THB')
            - open (float): Market open price
            - day_high (float): Intraday high
            - day_low (float): Intraday low
            - previous_close (float): Previous trading session close
            - change (float): Price change (current - prev_close)
            - change_percent (float): Percentage price change
            - volume (int): Trading volume
            - fifty_two_week_high (float): 52-week high price
            - fifty_two_week_low (float): 52-week low price
            - timestamp (str): ISO timestamp of the fetch
            - success (bool): True if data was fetched successfully
            - error (str | None): Error message if fetch failed
    """
    normalized_ticker = format_ticker_symbol(ticker)
    if not normalized_ticker:
        return {
            "ticker": ticker,
            "success": False,
            "error": "Invalid or empty ticker symbol provided.",
        }

    # Fast in-memory cache check
    cache_key = f"stock:{normalized_ticker}"
    now_ts = time.time()
    if cache_key in _PRICE_CACHE and _PRICE_CACHE_EXPIRY.get(cache_key, 0) > now_ts:
        return _PRICE_CACHE[cache_key]

    try:
        yf_ticker = yf.Ticker(normalized_ticker)

        # Attempt to retrieve market data via fast_info first (faster, reliable)
        fast_info = getattr(yf_ticker, "fast_info", None)
        current_price = None
        prev_close = None
        open_price = None
        day_high = None
        day_low = None
        volume = None
        currency = None
        year_high = None
        year_low = None
        name = normalized_ticker

        if fast_info:
            try:
                current_price = fast_info.last_price
                prev_close = fast_info.previous_close
                open_price = fast_info.open
                day_high = fast_info.day_high
                day_low = fast_info.day_low
                volume = fast_info.last_volume
                currency = getattr(fast_info, "currency", None)
                year_high = getattr(fast_info, "year_high", None)
                year_low = getattr(fast_info, "year_low", None)
            except Exception as e:
                logger.debug(
                    f"fast_info attribute access error for {normalized_ticker}: {e}"
                )

        # Fallback to history if fast_info is missing essential fields
        if current_price is None or pd.isna(current_price):
            hist = yf_ticker.history(period="5d")
            if not hist.empty:
                current_price = float(hist["Close"].iloc[-1])
                open_price = float(hist["Open"].iloc[-1])
                day_high = float(hist["High"].iloc[-1])
                day_low = float(hist["Low"].iloc[-1])
                volume = int(hist["Volume"].iloc[-1])
                if len(hist) > 1:
                    prev_close = float(hist["Close"].iloc[-2])
                else:
                    prev_close = open_price

        # Fast name lookup from local registry to avoid slow remote info scraping
        try:
            from ticker_registry import get_symbol_info
            sym_info = get_symbol_info(normalized_ticker)
            if sym_info and sym_info.get("name"):
                name = sym_info["name"]
        except Exception:
            pass

        # Default currency deduction
        if not currency:
            currency = (
                "THB"
                if normalized_ticker.endswith(".BK")
                else "USD"
            )

        if current_price is None or pd.isna(current_price):
            return {
                "ticker": normalized_ticker,
                "success": False,
                "error": f"No market price data found for ticker '{normalized_ticker}'.",
            }

        # Calculate changes safely
        current_price = round(float(current_price), 4)
        prev_close = (
            round(float(prev_close), 4)
            if prev_close is not None and not pd.isna(prev_close)
            else current_price
        )
        open_price = (
            round(float(open_price), 4)
            if open_price is not None and not pd.isna(open_price)
            else current_price
        )
        day_high = (
            round(float(day_high), 4)
            if day_high is not None and not pd.isna(day_high)
            else current_price
        )
        day_low = (
            round(float(day_low), 4)
            if day_low is not None and not pd.isna(day_low)
            else current_price
        )
        volume = int(volume) if volume is not None and not pd.isna(volume) else 0

        change = round(current_price - prev_close, 4)
        change_percent = (
            round((change / prev_close) * 100.0, 2) if prev_close != 0 else 0.0
        )

        result = {
            "ticker": normalized_ticker,
            "name": name,
            "current_price": current_price,
            "currency": currency,
            "open": open_price,
            "day_high": day_high,
            "day_low": day_low,
            "previous_close": prev_close,
            "change": change,
            "change_percent": change_percent,
            "volume": volume,
            "fifty_two_week_high": year_high,
            "fifty_two_week_low": year_low,
            "timestamp": datetime.now().isoformat(),
            "success": True,
            "error": None,
        }

        # Save to memory cache
        _PRICE_CACHE[cache_key] = result
        _PRICE_CACHE_EXPIRY[cache_key] = now_ts + PRICE_CACHE_TTL_SECONDS
        return result

    except Exception as e:
        logger.error(
            f"Failed to fetch market price for ticker '{normalized_ticker}': {str(e)}"
        )
        return {
            "ticker": normalized_ticker,
            "success": False,
            "error": f"Exception occurred while fetching data: {str(e)}",
        }


def get_historical_stock_data(
    ticker: str,
    period: str = "1mo",
    interval: str = "1d",
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch historical OHLCV data for a given ticker symbol.

    Args:
        ticker (str): Stock ticker (e.g., 'AAPL', 'PTT.BK').
        period (str): Data period ('1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', 'max', 'ytd').
        interval (str): Data interval ('1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo').
        start (str, optional): Start date string (YYYY-MM-DD).
        end (str, optional): End date string (YYYY-MM-DD).

    Returns:
        pd.DataFrame: Cleaned historical data with columns:
            ['Date', 'Open', 'High', 'Low', 'Close', 'Volume'] (and 'Adj Close' if available).
            Returns an empty DataFrame with appropriate index on error.
    """
    normalized_ticker = format_ticker_symbol(ticker)
    if not normalized_ticker:
        logger.warning("Empty ticker passed to get_historical_stock_data.")
        return pd.DataFrame()

    try:
        yf_ticker = yf.Ticker(normalized_ticker)
        if start and end:
            df = yf_ticker.history(start=start, end=end, interval=interval)
        elif start:
            df = yf_ticker.history(start=start, interval=interval)
        else:
            df = yf_ticker.history(period=period, interval=interval)

        if df.empty:
            logger.warning(
                f"No historical data returned for '{normalized_ticker}'."
            )
            return pd.DataFrame()

        # Reset index so 'Date' becomes an explicit column
        df = df.reset_index()
        if "index" in df.columns and "Date" not in df.columns:
            df = df.rename(columns={"index": "Date"})
        elif "Datetime" in df.columns and "Date" not in df.columns:
            df = df.rename(columns={"Datetime": "Date"})

        # Clean Date formatting
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)

        # Standardize numeric columns
        numeric_cols = [
            col
            for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
            if col in df.columns
        ]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    except Exception as e:
        logger.error(
            f"Error fetching historical data for '{normalized_ticker}': {str(e)}"
        )
        return pd.DataFrame()


def get_usd_thb_rate() -> Dict[str, Any]:
    """Fetch the real-time USD to THB exchange rate using yfinance symbol 'THB=X'."""
    cache_key = "fx:USD_THB"
    now_ts = time.time()
    if cache_key in _PRICE_CACHE and _PRICE_CACHE_EXPIRY.get(cache_key, 0) > now_ts:
        return _PRICE_CACHE[cache_key]

    symbol = "THB=X"
    try:
        ticker = yf.Ticker(symbol)
        fast_info = getattr(ticker, "fast_info", None)

        rate = None
        prev_close = None

        if fast_info:
            try:
                rate = fast_info.last_price
                prev_close = fast_info.previous_close
            except Exception as e:
                logger.debug(f"fast_info access error for {symbol}: {e}")

        if rate is None or pd.isna(rate):
            hist = ticker.history(period="5d")
            if not hist.empty:
                rate = float(hist["Close"].iloc[-1])
                if len(hist) > 1:
                    prev_close = float(hist["Close"].iloc[-2])
                else:
                    prev_close = rate

        if rate is None or pd.isna(rate):
            return {
                "pair": "USD/THB",
                "symbol": symbol,
                "success": False,
                "error": "Failed to retrieve USD/THB exchange rate.",
            }

        rate = round(float(rate), 4)
        prev_close = (
            round(float(prev_close), 4)
            if prev_close is not None and not pd.isna(prev_close)
            else rate
        )
        change = round(rate - prev_close, 4)
        change_percent = (
            round((change / prev_close) * 100, 2) if prev_close else 0.0
        )

        result = {
            "pair": "USD/THB",
            "symbol": symbol,
            "rate": rate,
            "previous_close": prev_close,
            "change": change,
            "change_percent": change_percent,
            "timestamp": datetime.now().isoformat(),
            "success": True,
            "error": None,
        }
        _PRICE_CACHE[cache_key] = result
        _PRICE_CACHE_EXPIRY[cache_key] = now_ts + PRICE_CACHE_TTL_SECONDS
        return result

    except Exception as e:
        logger.error(f"Error fetching USD/THB exchange rate: {str(e)}")
        return {
            "pair": "USD/THB",
            "symbol": symbol,
            "success": False,
            "error": str(e),
        }


def _fetch_sec_open_api_nav(
    fund_code: str, sec_api_key: str
) -> Optional[Dict[str, Any]]:
    """Internal helper to fetch NAV from official SEC Thailand Open API.

    Header requirement: 'Ocp-Apim-Subscription-Key': sec_api_key
    """
    try:
        headers = {
            "Ocp-Apim-Subscription-Key": sec_api_key,
            "User-Agent": "PortfolioTracker/1.0",
        }
        # SEC Thailand Open API endpoint for fund daily NAV info
        # Reference: https://api-portal.sec.or.th/
        url = f"https://api.sec.or.th/FundDailyInfo/{fund_code}/dailynav"
        response = requests.get(
            url, headers=headers, timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                latest_item = data[0]
                nav = float(
                    latest_item.get("last_val")
                    or latest_item.get("nav")
                    or 0.0
                )
                nav_date = latest_item.get("nav_date") or latest_item.get(
                    "nav_datetime"
                )
                prev_nav = float(
                    latest_item.get("previous_val") or nav
                )
                change = round(nav - prev_nav, 4)
                change_percent = (
                    round((change / prev_nav) * 100, 2)
                    if prev_nav
                    else 0.0
                )

                return {
                    "fund_code": fund_code,
                    "fund_name": latest_item.get("proj_name_en")
                    or latest_item.get("proj_name_th")
                    or fund_code,
                    "nav": nav,
                    "previous_nav": prev_nav,
                    "change": change,
                    "change_percent": change_percent,
                    "nav_date": str(nav_date),
                    "amc_name": latest_item.get("unique_id")
                    or latest_item.get("amc_name"),
                    "source": "SEC_Open_API",
                    "success": True,
                    "error": None,
                }
    except Exception as e:
        logger.debug(f"SEC Open API request failed for {fund_code}: {e}")

    return None


def _fetch_finnomena_fallback_nav(
    fund_code: str,
) -> Optional[Dict[str, Any]]:
    """Internal helper to fetch NAV from Finnomena public open fund API endpoint."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        # Finnomena public NAV average and fund details endpoint
        url = f"https://www.finnomena.com/fnservice/fund/nav/avg?fund={fund_code}"
        response = requests.get(
            url, headers=headers, timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data.get("status") is True:
                nav_data = data.get("data", {})
                nav = float(nav_data.get("nav", 0.0))
                if nav > 0:
                    nav_date = nav_data.get("nav_date")
                    change = float(nav_data.get("change", 0.0))
                    change_percent = float(
                        nav_data.get("change_percent", 0.0)
                    )
                    prev_nav = (
                        round(nav - change, 4)
                        if change != 0.0
                        else nav
                    )

                    return {
                        "fund_code": fund_code,
                        "fund_name": nav_data.get("fund_name")
                        or fund_code,
                        "nav": round(nav, 4),
                        "previous_nav": prev_nav,
                        "change": round(change, 4),
                        "change_percent": round(change_percent, 2),
                        "nav_date": str(nav_date),
                        "amc_name": nav_data.get("amc_name") or "",
                        "source": "Finnomena_Public_API",
                        "success": True,
                        "error": None,
                    }
    except Exception as e:
        logger.debug(
            f"Finnomena fallback NAV request failed for {fund_code}: {e}"
        )

    return None


def _fetch_pythainav_fallback(fund_code: str) -> Optional[Dict[str, Any]]:
    """Internal helper to fetch NAV using pythainav package."""
    try:
        import pythainav
        data = pythainav.get(fund_code.lower())
        if data and hasattr(data, "value") and data.value is not None:
            nav_val = float(data.value)
            nav_date_str = ""
            if hasattr(data, "updated") and data.updated:
                nav_date_str = (
                    data.updated.strftime("%Y-%m-%d")
                    if hasattr(data.updated, "strftime")
                    else str(data.updated)
                )
            else:
                nav_date_str = datetime.now().strftime("%Y-%m-%d")

            return {
                "fund_code": fund_code,
                "fund_name": f"Thai Mutual Fund ({fund_code})",
                "nav": round(nav_val, 4),
                "previous_nav": round(nav_val, 4),
                "change": 0.0,
                "change_percent": 0.0,
                "nav_date": nav_date_str,
                "amc_name": "",
                "source": "PyThaiNAV_Public",
                "success": True,
                "error": None,
            }
    except Exception as e:
        logger.debug(f"pythainav fallback failed for {fund_code}: {e}")
    return None


def get_thai_fund_nav(
    fund_code: str, sec_api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Fetch Net Asset Value (NAV) and metadata for Thai Mutual Funds."""
    clean_fund_code = format_ticker_symbol(fund_code)
    if not clean_fund_code:
        return {
            "fund_code": fund_code,
            "success": False,
            "error": "Invalid or empty fund code provided.",
        }

    cache_key = f"fund:{clean_fund_code}"
    now_ts = time.time()
    if cache_key in _PRICE_CACHE and _PRICE_CACHE_EXPIRY.get(cache_key, 0) > now_ts:
        return _PRICE_CACHE[cache_key]

    api_key = sec_api_key or os.environ.get("SEC_API_KEY")

    # Tier 1: Try official SEC Open API if key is present
    if api_key:
        sec_result = _fetch_sec_open_api_nav(clean_fund_code, api_key)
        if sec_result:
            sec_result["timestamp"] = datetime.now().isoformat()
            _PRICE_CACHE[cache_key] = sec_result
            _PRICE_CACHE_EXPIRY[cache_key] = now_ts + PRICE_CACHE_TTL_SECONDS
            return sec_result

    # Tier 2: Try PyThaiNAV public provider
    pythainav_result = _fetch_pythainav_fallback(clean_fund_code)
    if pythainav_result:
        pythainav_result["timestamp"] = datetime.now().isoformat()
        _PRICE_CACHE[cache_key] = pythainav_result
        _PRICE_CACHE_EXPIRY[cache_key] = now_ts + PRICE_CACHE_TTL_SECONDS
        return pythainav_result

    # Tier 3: Try Finnomena public endpoint fallback
    fallback_result = _fetch_finnomena_fallback_nav(clean_fund_code)
    if fallback_result:
        fallback_result["timestamp"] = datetime.now().isoformat()
        _PRICE_CACHE[cache_key] = fallback_result
        _PRICE_CACHE_EXPIRY[cache_key] = now_ts + PRICE_CACHE_TTL_SECONDS
        return fallback_result

    # Tier 4: Check if fund exists in registered master list
    fund_name = f"Thai Mutual Fund ({clean_fund_code})"
    try:
        from ticker_registry import get_symbol_info
        info = get_symbol_info(clean_fund_code)
        if info and info.get("name"):
            fund_name = info["name"]
    except Exception:
        pass

    # Tier 5: Default fallback JSON structure with informative status
    logger.info(
        f"Unable to retrieve live NAV for '{clean_fund_code}' via remote APIs. Returning default fallback structure."
    )
    fallback_resp = {
        "fund_code": clean_fund_code,
        "fund_name": fund_name,
        "nav": None,
        "previous_nav": None,
        "change": 0.0,
        "change_percent": 0.0,
        "nav_date": datetime.now().strftime("%Y-%m-%d"),
        "amc_name": "Unknown AMC",
        "source": "Fallback_Default",
        "timestamp": datetime.now().isoformat(),
        "success": False,
        "error": (
            f"No live NAV found for fund '{clean_fund_code}'. "
            "Please provide a valid SEC_API_KEY or verify the fund symbol."
        ),
    }
    _PRICE_CACHE[cache_key] = fallback_resp
    _PRICE_CACHE_EXPIRY[cache_key] = now_ts + 30  # Shorter TTL for failed attempts
    return fallback_resp


def get_batch_stock_prices(tickers: List[str]) -> pd.DataFrame:
    """Fetch current stock price data for multiple tickers and return as a consolidated Pandas DataFrame.

    Args:
        tickers (List[str]): List of stock tickers (US or Thai).

    Returns:
        pd.DataFrame: DataFrame containing standardized columns:
            ['ticker', 'name', 'current_price', 'currency', 'change', 'change_percent', 'volume', 'previous_close', 'success']
    """
    records = []
    for ticker in tickers:
        data = get_stock_price(ticker)
        records.append(data)

    df = pd.DataFrame(records)
    return df


def get_portfolio_data(
    stock_tickers: List[str], fund_codes: List[str]
) -> Dict[str, Any]:
    """Consolidate current market prices for stocks, Thai mutual fund NAVs,

    and the USD/THB exchange rate into a comprehensive portfolio snapshot.

    Args:
        stock_tickers (List[str]): List of stock ticker symbols (e.g. ['AAPL', 'PTT.BK']).
        fund_codes (List[str]): List of mutual fund codes (e.g. ['SCBDV', 'K-USA']).

    Returns:
        Dict[str, Any]: Consolidated dictionary with:
            - exchange_rate (dict): USD/THB rate details
            - stocks (List[dict]): List of stock price records
            - funds (List[dict]): List of mutual fund NAV records
            - timestamp (str): ISO timestamp of data generation
    """
    exchange_rate_data = get_usd_thb_rate()
    stocks_data = [get_stock_price(t) for t in stock_tickers]
    funds_data = [get_thai_fund_nav(f) for f in fund_codes]

    return {
        "timestamp": datetime.now().isoformat(),
        "exchange_rate": exchange_rate_data,
        "stocks": stocks_data,
        "funds": funds_data,
    }


def validate_symbol_detailed(
    symbol: str, asset_type: Optional[str] = None
) -> Dict[str, Any]:
    """Verify if a ticker symbol exists and returns valid market data.

    Args:
        symbol (str): Ticker or fund symbol (e.g., 'AAPL', 'PTT.BK', 'ONE-UGG-RA').
        asset_type (str, optional): 'US_STOCK', 'TH_STOCK', or 'TH_MUTUAL_FUND'.
            If None, type is inferred based on ticker suffix or lookup.

    Returns:
        Dict[str, Any]: Validation result containing:
            - valid (bool): True if verified on market, False otherwise
            - symbol (str): Normalized ticker
            - name (str | None): Asset/Company name if found
            - price (float | None): Latest traded price or NAV
            - currency (str | None): Trading currency
            - error (str | None): Validation error message if invalid
    """
    clean_symbol = format_ticker_symbol(symbol)
    if not clean_symbol:
        return {
            "valid": False,
            "symbol": symbol,
            "error": "Symbol cannot be empty.",
            "name": None,
            "price": None,
            "currency": None,
        }

    norm_type = asset_type.strip().upper() if asset_type else None

    # 1. Thai Mutual Fund validation
    if norm_type == "TH_MUTUAL_FUND":
        fund_res = get_thai_fund_nav(clean_symbol)
        if fund_res.get("success") and fund_res.get("nav") is not None and fund_res.get("nav") > 0:
            return {
                "valid": True,
                "symbol": clean_symbol,
                "name": fund_res.get("fund_name", clean_symbol),
                "price": fund_res.get("nav"),
                "currency": "THB",
                "error": None,
            }
        try:
            from ticker_registry import get_symbol_info
            info = get_symbol_info(clean_symbol)
            if info and info.get("asset_type") == "TH_MUTUAL_FUND":
                return {
                    "valid": True,
                    "symbol": clean_symbol,
                    "name": info.get("name", clean_symbol),
                    "price": fund_res.get("nav"),
                    "currency": "THB",
                    "error": None,
                }
        except Exception:
            pass

        return {
            "valid": False,
            "symbol": clean_symbol,
            "error": f"Invalid or unrecognized Thai Mutual Fund '{clean_symbol}'. No market NAV found.",
            "name": None,
            "price": None,
            "currency": None,
        }

    # 2. Stock validation (US or Thai stock via yfinance)
    stock_res = get_stock_price(clean_symbol)
    if stock_res.get("success") and stock_res.get("current_price") is not None and stock_res.get("current_price") > 0:
        return {
            "valid": True,
            "symbol": clean_symbol,
            "name": stock_res.get("name", clean_symbol),
            "price": stock_res.get("current_price"),
            "currency": stock_res.get("currency", "USD"),
            "error": None,
        }

    # If Thai stock without .BK suffix, check with .BK
    if not clean_symbol.endswith(".BK") and (norm_type == "TH_STOCK" or norm_type is None):
        thai_sym = f"{clean_symbol}.BK"
        thai_res = get_stock_price(thai_sym)
        if thai_res.get("success") and thai_res.get("current_price") is not None and thai_res.get("current_price") > 0:
            return {
                "valid": True,
                "symbol": thai_sym,
                "name": thai_res.get("name", thai_sym),
                "price": thai_res.get("current_price"),
                "currency": "THB",
                "error": None,
            }

    # If type is unspecified and stock check failed, test if fund
    if norm_type is None:
        fund_res = get_thai_fund_nav(clean_symbol)
        if fund_res.get("success") and fund_res.get("nav") is not None and fund_res.get("nav") > 0:
            return {
                "valid": True,
                "symbol": clean_symbol,
                "name": fund_res.get("fund_name", clean_symbol),
                "price": fund_res.get("nav"),
                "currency": "THB",
                "error": None,
            }

    return {
        "valid": False,
        "symbol": clean_symbol,
        "error": f"Ticker '{clean_symbol}' not found on market exchanges (yfinance / SEC).",
        "name": None,
        "price": None,
        "currency": None,
    }


def validate_symbol(symbol: str, asset_type: Optional[str] = None) -> bool:
    """Verify if a ticker symbol exists and returns valid market data using yfinance.

    Args:
        symbol (str): Ticker or fund symbol (e.g. 'AAPL', 'PTT.BK', 'ONE-UGG-RA').
        asset_type (str, optional): 'US_STOCK', 'TH_STOCK', or 'TH_MUTUAL_FUND'.

    Returns:
        bool: True if symbol is valid and active on market exchanges, False otherwise.
    """
    res = validate_symbol_detailed(symbol=symbol, asset_type=asset_type)
    return bool(res.get("valid", False))



if __name__ == "__main__":
    print("=" * 70)
    print("PORTFOLIO DATA FETCHER MODULE DEMONSTRATION")
    print("=" * 70)

    # 1. USD / THB Exchange Rate
    print("\n1. USD/THB Exchange Rate (THB=X):")
    fx = get_usd_thb_rate()
    print(json.dumps(fx, indent=2))

    # 2. US Stock Price Examples
    print("\n2. US Stock Price Examples (AAPL, NVDA):")
    for us_symbol in ["AAPL", "NVDA"]:
        stock = get_stock_price(us_symbol)
        print(
            f"   - {stock.get('ticker')}: {stock.get('current_price')} {stock.get('currency')} "
            f"({stock.get('change_percent')}%) | Prev Close: {stock.get('previous_close')}"
        )

    # 3. Thai Stock Price Examples (.BK suffix)
    print("\n3. Thai Stock Price Examples (PTT.BK, CPALL.BK):")
    for thai_symbol in ["PTT.BK", "CPALL.BK"]:
        stock = get_stock_price(thai_symbol)
        print(
            f"   - {stock.get('ticker')}: {stock.get('current_price')} {stock.get('currency')} "
            f"({stock.get('change_percent')}%) | Prev Close: {stock.get('previous_close')}"
        )

    # 4. Thai Mutual Funds NAV Examples
    print("\n4. Thai Mutual Fund NAV Examples (SCBDV, K-USA):")
    for fund_symbol in ["SCBDV", "K-USA"]:
        fund = get_thai_fund_nav(fund_symbol)
        print(
            f"   - {fund.get('fund_code')}: NAV {fund.get('nav')} THB (Date: {fund.get('nav_date')}) "
            f"| Source: {fund.get('source')} | Success: {fund.get('success')}"
        )

    # 5. Batch DataFrame
    print("\n5. Batch Stock Prices DataFrame:")
    batch_df = get_batch_stock_prices(["AAPL", "NVDA", "PTT.BK", "CPALL.BK"])
    print(batch_df[["ticker", "current_price", "currency", "change", "change_percent", "success"]])

    # 6. Historical Data Sample
    print("\n6. Historical Data Sample (AAPL - 5 Days):")
    hist_df = get_historical_stock_data("AAPL", period="5d")
    print(hist_df.tail())
    print("\n" + "=" * 70)
