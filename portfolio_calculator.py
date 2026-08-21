"""
portfolio_calculator.py
=======================
Portfolio Calculations & Analytics Module.
Calculates portfolio summary metrics from a list of transactions retrieved from SQLite.

Key Capabilities:
1. Groups transaction history by symbol into consolidated holdings (total quantity, weighted average cost basis).
2. Fetches real-time market prices and USD/THB exchange rate via data_fetcher module.
3. Converts all US asset valuations, costs, and P&L into Thai Baht (THB).
4. Calculates portfolio totals (value in THB, cost basis in THB, unrealized P&L in THB and %, allocation weights per symbol).
5. Provides allocation breakdowns by Asset Category and Currency.
6. Exports calculations to Python dictionaries and structured Pandas DataFrames.
"""

from collections import defaultdict
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional, Union
import pandas as pd

from data_fetcher import (
    format_ticker_symbol,
    get_stock_price,
    get_thai_fund_nav,
    get_usd_thb_rate,
)
from database import (
    DEFAULT_DB_PATH,
    get_all_transactions,
)

# Configure module logger
logger = logging.getLogger("portfolio_calculator")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default fallback USD/THB rate if live rate fetching fails
DEFAULT_FALLBACK_USD_THB_RATE = 35.50


def aggregate_transactions(
    transactions: Union[List[Dict[str, Any]], pd.DataFrame]
) -> List[Dict[str, Any]]:
    """Group a list or DataFrame of transactions by symbol to calculate total quantity,

    total invested cost, and weighted average cost per share.

    Args:
        transactions (List[Dict[str, Any]] | pd.DataFrame): Transaction records.

    Returns:
        List[Dict[str, Any]]: Aggregated holdings list sorted by asset type and symbol.
    """
    if isinstance(transactions, pd.DataFrame):
        if transactions.empty:
            return []
        tx_list = transactions.to_dict(orient="records")
    elif isinstance(transactions, list):
        tx_list = transactions
    else:
        logger.warning(f"Unsupported transactions data type: {type(transactions)}")
        return []

    if not tx_list:
        return []

    # Grouping key: (symbol, asset_type, currency)
    groups = defaultdict(lambda: {"total_quantity": 0.0, "total_cost": 0.0, "transaction_count": 0})

    for tx in tx_list:
        raw_symbol = tx.get("symbol", "")
        symbol = format_ticker_symbol(raw_symbol)
        if not symbol:
            continue

        raw_asset_type = str(tx.get("asset_type", "")).strip().upper()
        raw_currency = str(tx.get("currency", "")).strip().upper()
        if not raw_currency:
            raw_currency = "USD" if raw_asset_type == "US_STOCK" else "THB"

        try:
            qty = float(tx.get("quantity", 0.0))
            cost_per_share = float(tx.get("cost_per_share", 0.0))
        except (ValueError, TypeError):
            continue

        if qty <= 0:
            continue

        key = (symbol, raw_asset_type, raw_currency)
        groups[key]["total_quantity"] += qty
        groups[key]["total_cost"] += (qty * cost_per_share)
        groups[key]["transaction_count"] += 1

    aggregated = []
    for (symbol, asset_type, currency), data in groups.items():
        total_qty = round(data["total_quantity"], 8)
        total_cost = round(data["total_cost"], 4)
        avg_cost = round(total_cost / total_qty, 6) if total_qty > 0 else 0.0

        aggregated.append({
            "symbol": symbol,
            "asset_type": asset_type,
            "currency": currency,
            "total_quantity": total_qty,
            "total_cost": total_cost,
            "avg_cost_per_share": avg_cost,
            "transaction_count": data["transaction_count"],
        })

    # Sort by asset_type, then symbol
    aggregated.sort(key=lambda x: (x["asset_type"], x["symbol"]))
    return aggregated


def calculate_portfolio_summary(
    transactions: Optional[Union[List[Dict[str, Any]], pd.DataFrame]] = None,
    db_path: str = DEFAULT_DB_PATH,
    custom_usd_thb_rate: Optional[float] = None,
    custom_prices: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Calculate portfolio summary metrics from transactions and live market data.

    Steps:
    1. Retrieve transactions from SQLite database if not explicitly provided.
    2. Group transactions into consolidated holdings (quantity, weighted average cost basis).
    3. Fetch latest market price for each symbol and the USD/THB exchange rate.
    4. Convert US asset valuations, costs, and unrealized P&L into Thai Baht (THB).
    5. Compute total portfolio value, total cost, total unrealized P&L (THB and %),
       and asset allocation weight (%) per symbol, category, and currency.

    Args:
        transactions (List[Dict[str, Any]] | pd.DataFrame, optional): Transactions to calculate.
            If None, transactions are fetched from SQLite db_path.
        db_path (str): Database path to load transactions from if transactions is None.
        custom_usd_thb_rate (float, optional): Custom exchange rate override for testing/simulation.
        custom_prices (Dict[str, float], optional): Map of {symbol: price} to override live fetching.

    Returns:
        Dict[str, Any]: Portfolio summary dictionary containing:
            - timestamp (str)
            - usd_thb_rate (float)
            - total_value_thb (float)
            - total_cost_thb (float)
            - total_unrealized_pnl_thb (float)
            - total_unrealized_pnl_percent (float)
            - holdings_count (int)
            - holdings (List[Dict[str, Any]])
            - allocation_by_asset_type (Dict[str, Dict[str, float]])
            - allocation_by_currency (Dict[str, Dict[str, float]])
    """
    # 1. Retrieve transactions
    if transactions is None:
        raw_txs = get_all_transactions(db_path=db_path, as_dataframe=False)
    elif isinstance(transactions, pd.DataFrame):
        raw_txs = transactions.to_dict(orient="records")
    else:
        raw_txs = transactions

    # 2. Determine USD/THB Exchange Rate
    usd_thb_rate = custom_usd_thb_rate
    fx_rate_info = None
    if usd_thb_rate is None or usd_thb_rate <= 0:
        fx_resp = get_usd_thb_rate()
        if fx_resp.get("success") and fx_resp.get("rate"):
            usd_thb_rate = float(fx_resp["rate"])
            fx_rate_info = fx_resp
        else:
            logger.warning(
                f"Failed to fetch live USD/THB rate. Using fallback rate of {DEFAULT_FALLBACK_USD_THB_RATE}."
            )
            usd_thb_rate = DEFAULT_FALLBACK_USD_THB_RATE
    usd_thb_rate = round(float(usd_thb_rate), 4)

    # Empty portfolio case
    if not raw_txs:
        return {
            "timestamp": datetime.now().isoformat(),
            "usd_thb_rate": usd_thb_rate,
            "total_value_thb": 0.0,
            "total_cost_thb": 0.0,
            "total_unrealized_pnl_thb": 0.0,
            "total_unrealized_pnl_percent": 0.0,
            "holdings_count": 0,
            "holdings": [],
            "allocation_by_asset_type": {},
            "allocation_by_currency": {},
        }

    # 3. Aggregate holdings
    aggregated_holdings = aggregate_transactions(raw_txs)
    if not aggregated_holdings:
        return {
            "timestamp": datetime.now().isoformat(),
            "usd_thb_rate": usd_thb_rate,
            "total_value_thb": 0.0,
            "total_cost_thb": 0.0,
            "total_unrealized_pnl_thb": 0.0,
            "total_unrealized_pnl_percent": 0.0,
            "holdings_count": 0,
            "holdings": [],
            "allocation_by_asset_type": {},
            "allocation_by_currency": {},
        }

    custom_prices_map = custom_prices or {}
    holdings_metrics: List[Dict[str, Any]] = []

    # 4. Fetch Market Prices in Parallel for Maximum Performance
    def _fetch_single_holding_price(holding_item: Dict[str, Any]) -> Dict[str, Any]:
        sym = holding_item["symbol"]
        atype = holding_item["asset_type"]
        avg_cost = holding_item["avg_cost_per_share"]

        if sym in custom_prices_map:
            return {
                "symbol": sym,
                "price": float(custom_prices_map[sym]),
                "name": sym,
                "change": 0.0,
                "change_percent": 0.0,
            }

        if atype in ("US_STOCK", "TH_STOCK"):
            stock_data = get_stock_price(sym)
            if stock_data.get("success") and stock_data.get("current_price") is not None:
                return {
                    "symbol": sym,
                    "price": float(stock_data["current_price"]),
                    "name": stock_data.get("name", sym),
                    "change": float(stock_data.get("change") or 0.0),
                    "change_percent": float(stock_data.get("change_percent") or 0.0),
                }
            return {
                "symbol": sym,
                "price": avg_cost,
                "name": sym,
                "change": 0.0,
                "change_percent": 0.0,
            }
        elif atype == "TH_MUTUAL_FUND":
            fund_data = get_thai_fund_nav(sym)
            if fund_data.get("success") and fund_data.get("nav") is not None:
                return {
                    "symbol": sym,
                    "price": float(fund_data["nav"]),
                    "name": fund_data.get("fund_name", sym),
                    "change": float(fund_data.get("change") or 0.0),
                    "change_percent": float(fund_data.get("change_percent") or 0.0),
                }
            return {
                "symbol": sym,
                "price": avg_cost,
                "name": sym,
                "change": 0.0,
                "change_percent": 0.0,
            }
        return {
            "symbol": sym,
            "price": avg_cost,
            "name": sym,
            "change": 0.0,
            "change_percent": 0.0,
        }

    # Execute concurrent price fetches
    import concurrent.futures
    fetched_data_map: Dict[str, Dict[str, Any]] = {}
    if aggregated_holdings:
        max_workers = min(10, len(aggregated_holdings))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            fetched_results = list(executor.map(_fetch_single_holding_price, aggregated_holdings))
            for f_res in fetched_results:
                fetched_data_map[f_res["symbol"]] = f_res

    for holding in aggregated_holdings:
        symbol = holding["symbol"]
        asset_type = holding["asset_type"]
        currency = holding["currency"]
        qty = holding["total_quantity"]
        cost_basis_local = holding["total_cost"]
        avg_cost_local = holding["avg_cost_per_share"]

        price_info = fetched_data_map.get(symbol, {
            "price": avg_cost_local,
            "name": symbol,
            "change": 0.0,
            "change_percent": 0.0,
        })

        market_price = price_info["price"]
        asset_name = price_info["name"]
        price_change_local = price_info["change"]
        price_change_percent = price_info["change_percent"]

        market_price = round(market_price, 4)

        # Value and P&L in local currency
        market_value_local = round(qty * market_price, 4)
        unrealized_pnl_local = round(market_value_local - cost_basis_local, 4)
        unrealized_pnl_percent = (
            round((unrealized_pnl_local / cost_basis_local) * 100, 2)
            if cost_basis_local > 0
            else 0.0
        )

        # FX Conversion to THB
        # If US asset (USD), multiply by usd_thb_rate. If Thai asset (THB), factor is 1.0.
        fx_factor = usd_thb_rate if currency == "USD" else 1.0

        current_price_thb = round(market_price * fx_factor, 4)
        avg_cost_thb = round(avg_cost_local * fx_factor, 4)
        market_value_thb = round(market_value_local * fx_factor, 2)
        cost_basis_thb = round(cost_basis_local * fx_factor, 2)
        unrealized_pnl_thb = round(market_value_thb - cost_basis_thb, 2)

        holding_detail = {
            "symbol": symbol,
            "name": asset_name,
            "asset_type": asset_type,
            "currency": currency,
            "quantity": qty,
            "total_quantity": qty,
            "avg_cost_per_share": avg_cost_local,
            "current_price": market_price,
            "cost_basis_local": cost_basis_local,
            "market_value_local": market_value_local,
            "unrealized_pnl_local": unrealized_pnl_local,
            "current_price_thb": current_price_thb,
            "avg_cost_per_share_thb": avg_cost_thb,
            "cost_basis_thb": cost_basis_thb,
            "total_cost_thb": cost_basis_thb,
            "market_value_thb": market_value_thb,
            "unrealized_pnl_thb": unrealized_pnl_thb,
            "unrealized_pnl_percent": unrealized_pnl_percent,
            "price_change": round(price_change_local, 4),
            "price_change_percent": round(price_change_percent, 2),
            "weight_percent": 0.0,  # Computed in next step
            "transaction_count": holding["transaction_count"],
        }
        holdings_metrics.append(holding_detail)

    # 5. Compute Portfolio-level Totals
    total_value_thb = round(sum(h["market_value_thb"] for h in holdings_metrics), 2)
    total_cost_thb = round(sum(h["cost_basis_thb"] for h in holdings_metrics), 2)
    total_unrealized_pnl_thb = round(total_value_thb - total_cost_thb, 2)
    total_unrealized_pnl_percent = (
        round((total_unrealized_pnl_thb / total_cost_thb) * 100, 2)
        if total_cost_thb > 0
        else 0.0
    )

    # 6. Compute Asset Allocation Weights per symbol and group breakdowns
    type_allocation = defaultdict(lambda: {"value_thb": 0.0, "cost_thb": 0.0, "unrealized_pnl_thb": 0.0, "weight_percent": 0.0})
    currency_allocation = defaultdict(lambda: {"value_thb": 0.0, "weight_percent": 0.0})

    for h in holdings_metrics:
        # Symbol weight
        sym_weight = (
            round((h["market_value_thb"] / total_value_thb) * 100, 2)
            if total_value_thb > 0
            else 0.0
        )
        h["weight_percent"] = sym_weight

        # Asset type aggregation
        atype = h["asset_type"]
        type_allocation[atype]["value_thb"] += h["market_value_thb"]
        type_allocation[atype]["cost_thb"] += h["cost_basis_thb"]
        type_allocation[atype]["unrealized_pnl_thb"] += h["unrealized_pnl_thb"]

        # Currency aggregation
        curr = h["currency"]
        currency_allocation[curr]["value_thb"] += h["market_value_thb"]

    # Finalize allocation breakdowns
    allocation_by_asset_type: Dict[str, Dict[str, float]] = {}
    for atype, data in type_allocation.items():
        val = round(data["value_thb"], 2)
        weight = round((val / total_value_thb) * 100, 2) if total_value_thb > 0 else 0.0
        allocation_by_asset_type[atype] = {
            "value_thb": val,
            "cost_thb": round(data["cost_thb"], 2),
            "unrealized_pnl_thb": round(data["unrealized_pnl_thb"], 2),
            "weight_percent": weight,
        }

    allocation_by_currency: Dict[str, Dict[str, float]] = {}
    for curr, data in currency_allocation.items():
        val = round(data["value_thb"], 2)
        weight = round((val / total_value_thb) * 100, 2) if total_value_thb > 0 else 0.0
        allocation_by_currency[curr] = {
            "value_thb": val,
            "weight_percent": weight,
        }

    return {
        "timestamp": datetime.now().isoformat(),
        "usd_thb_rate": usd_thb_rate,
        "total_value_thb": total_value_thb,
        "total_cost_thb": total_cost_thb,
        "total_unrealized_pnl_thb": total_unrealized_pnl_thb,
        "total_unrealized_pnl_percent": total_unrealized_pnl_percent,
        "holdings_count": len(holdings_metrics),
        "holdings": holdings_metrics,
        "allocation_by_asset_type": allocation_by_asset_type,
        "allocation_by_currency": allocation_by_currency,
    }


def get_portfolio_metrics_dataframe(
    summary: Optional[Dict[str, Any]] = None,
    transactions: Optional[Union[List[Dict[str, Any]], pd.DataFrame]] = None,
    db_path: str = DEFAULT_DB_PATH,
    custom_usd_thb_rate: Optional[float] = None,
    custom_prices: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Generate a clean Pandas DataFrame of portfolio holdings metrics.

    Args:
        summary (Dict[str, Any], optional): Pre-calculated portfolio summary dictionary.
            If None, calculate_portfolio_summary() is executed with provided kwargs.

    Returns:
        pd.DataFrame: Formatted DataFrame of holdings.
    """
    if summary is None:
        summary = calculate_portfolio_summary(
            transactions=transactions,
            db_path=db_path,
            custom_usd_thb_rate=custom_usd_thb_rate,
            custom_prices=custom_prices,
        )

    holdings = summary.get("holdings", [])
    if not holdings:
        return pd.DataFrame(
            columns=[
                "symbol",
                "name",
                "asset_type",
                "currency",
                "quantity",
                "avg_cost_per_share",
                "current_price",
                "cost_basis_thb",
                "market_value_thb",
                "unrealized_pnl_thb",
                "unrealized_pnl_percent",
                "weight_percent",
            ]
        )

    df = pd.DataFrame(holdings)
    # Select and order user-friendly columns
    columns_order = [
        "symbol",
        "name",
        "asset_type",
        "currency",
        "quantity",
        "avg_cost_per_share",
        "current_price",
        "cost_basis_local",
        "market_value_local",
        "unrealized_pnl_local",
        "cost_basis_thb",
        "market_value_thb",
        "unrealized_pnl_thb",
        "unrealized_pnl_percent",
        "weight_percent",
        "price_change_percent",
    ]
    existing_cols = [c for c in columns_order if c in df.columns]
    return df[existing_cols]


class PortfolioCalculator:
    """Object-Oriented Portfolio Calculator."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        custom_usd_thb_rate: Optional[float] = None,
    ):
        """Initialize the PortfolioCalculator.

        Args:
            db_path (str): Database path to load transactions from.
            custom_usd_thb_rate (float, optional): Custom exchange rate override.
        """
        self.db_path = db_path
        self.custom_usd_thb_rate = custom_usd_thb_rate
        self.last_summary: Optional[Dict[str, Any]] = None

    def calculate(
        self,
        transactions: Optional[Union[List[Dict[str, Any]], pd.DataFrame]] = None,
        custom_prices: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Perform portfolio metrics calculation and cache the latest summary."""
        summary = calculate_portfolio_summary(
            transactions=transactions,
            db_path=self.db_path,
            custom_usd_thb_rate=self.custom_usd_thb_rate,
            custom_prices=custom_prices,
        )
        self.last_summary = summary
        return summary

    def get_dataframe(
        self,
        transactions: Optional[Union[List[Dict[str, Any]], pd.DataFrame]] = None,
        custom_prices: Optional[Dict[str, float]] = None,
    ) -> pd.DataFrame:
        """Get calculated portfolio holdings as a pandas DataFrame."""
        summary = self.calculate(transactions=transactions, custom_prices=custom_prices)
        return get_portfolio_metrics_dataframe(summary=summary)


if __name__ == "__main__":
    import json

    print("=" * 70)
    print("PORTFOLIO CALCULATOR DEMO")
    print("=" * 70)

    # Sample transaction history
    sample_txs = [
        {"symbol": "AAPL", "asset_type": "US_STOCK", "quantity": 10, "cost_per_share": 150.0, "currency": "USD", "purchase_date": "2024-01-10"},
        {"symbol": "AAPL", "asset_type": "US_STOCK", "quantity": 5, "cost_per_share": 170.0, "currency": "USD", "purchase_date": "2024-02-15"},
        {"symbol": "PTT.BK", "asset_type": "TH_STOCK", "quantity": 1000, "cost_per_share": 33.0, "currency": "THB", "purchase_date": "2024-03-01"},
        {"symbol": "SCBDV", "asset_type": "TH_MUTUAL_FUND", "quantity": 2000, "cost_per_share": 11.5, "currency": "THB", "purchase_date": "2024-03-10"},
    ]

    calc = PortfolioCalculator(custom_usd_thb_rate=36.0)
    # Using deterministic mock prices for demonstration
    demo_prices = {"AAPL": 185.0, "PTT.BK": 35.0, "SCBDV": 12.8}
    result = calc.calculate(transactions=sample_txs, custom_prices=demo_prices)

    print("\n1. Portfolio Totals:")
    print(f"   - USD/THB FX Rate:        {result['usd_thb_rate']:.2f} THB")
    print(f"   - Total Portfolio Value:  {result['total_value_thb']:,.2f} THB")
    print(f"   - Total Invested Cost:    {result['total_cost_thb']:,.2f} THB")
    print(f"   - Total Unrealized P&L:   {result['total_unrealized_pnl_thb']:+,.2f} THB ({result['total_unrealized_pnl_percent']:+.2f}%)")

    print("\n2. Asset Allocation Breakdown:")
    for cat, data in result["allocation_by_asset_type"].items():
        print(f"   - {cat:<16}: {data['value_thb']:>12,.2f} THB ({data['weight_percent']:>6.2f}%)")

    print("\n3. Holdings Table:")
    df_metrics = calc.get_dataframe(transactions=sample_txs, custom_prices=demo_prices)
    print(df_metrics[["symbol", "currency", "quantity", "avg_cost_per_share", "current_price", "market_value_thb", "unrealized_pnl_thb", "unrealized_pnl_percent", "weight_percent"]])
    print("\n" + "=" * 70)
