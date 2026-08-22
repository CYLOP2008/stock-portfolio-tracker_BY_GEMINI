"""
fifo_calculator.py
==================
FIFO (First-In, First-Out) Cost Basis & Realized P&L Calculator.

This module processes stock and fund transactions ordered chronologically to calculate:
1. Open lot queues per ticker symbol using First-In, First-Out (FIFO) matching.
2. Realized Profit / Loss (P&L) for each sale transaction:
       Realized P&L = (Sell Price - Matched Buy Cost) * Matched Quantity
3. Remaining active holdings with exact lot breakdowns, remaining quantities,
   and cost basis per symbol.
4. Comprehensive portfolio summary and structured outputs.

Exceptions:
- FIFOError: Base module exception.
- InsufficientSharesError: Raised when attempting to sell more shares than currently owned.
- InvalidTransactionError: Raised when transaction records are malformed or invalid.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
import logging
from typing import Any, Deque, Dict, Iterable, List, Optional, Union
import pandas as pd

# Configure module logger
logger = logging.getLogger("fifo_calculator")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Floating point tolerance for zero-quantity checks
FLOAT_TOLERANCE = 1e-8


# ==============================================================================
# EXCEPTIONS
# ==============================================================================

class FIFOError(Exception):
    """Base exception for all FIFO calculator operations."""
    pass


class InsufficientSharesError(FIFOError, ValueError):
    """Exception raised when a SELL transaction attempts to sell more shares
    than currently available in the open BUY lots for a symbol.
    """

    def __init__(
        self,
        symbol: str,
        requested_quantity: float,
        available_quantity: float,
        transaction_date: Optional[Union[str, date, datetime]] = None,
        message: Optional[str] = None,
    ):
        self.symbol = symbol
        self.requested_quantity = requested_quantity
        self.available_quantity = available_quantity
        self.transaction_date = str(transaction_date) if transaction_date else "N/A"
        
        if not message:
            shortfall = max(0.0, requested_quantity - available_quantity)
            message = (
                f"Insufficient shares for symbol '{symbol}' on {self.transaction_date}: "
                f"attempted to SELL {requested_quantity:g} shares, but only {available_quantity:g} "
                f"shares are currently held in open lots (Shortfall: {shortfall:g} shares)."
            )
        super().__init__(message)


class InvalidTransactionError(FIFOError, ValueError):
    """Exception raised when a transaction dictionary contains invalid, missing,
    or logically inconsistent data fields.
    """
    pass


# ==============================================================================
# DATA MODELS / DATACLASSES
# ==============================================================================

@dataclass
class BuyLot:
    """Represents an open or partially consumed BUY lot."""
    symbol: str
    quantity: float
    cost_per_share: float
    purchase_date: str
    original_quantity: float = field(default=0.0)
    asset_type: str = "US_STOCK"
    currency: str = "USD"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.original_quantity <= 0.0:
            self.original_quantity = self.quantity
        self.symbol = self.symbol.strip().upper()
        self.quantity = float(self.quantity)
        self.cost_per_share = float(self.cost_per_share)
        self.original_quantity = float(self.original_quantity)

    @property
    def total_cost(self) -> float:
        """Total cost basis for remaining quantity in this lot."""
        return round(self.quantity * self.cost_per_share, 4)

    @property
    def is_depleted(self) -> bool:
        """Check if lot has been fully consumed."""
        return self.quantity <= FLOAT_TOLERANCE

    def to_dict(self) -> Dict[str, Any]:
        """Convert lot to dictionary representation."""
        return {
            "symbol": self.symbol,
            "quantity": round(self.quantity, 8),
            "cost_per_share": round(self.cost_per_share, 6),
            "purchase_date": str(self.purchase_date),
            "original_quantity": round(self.original_quantity, 8),
            "total_cost": self.total_cost,
            "asset_type": self.asset_type,
            "currency": self.currency,
        }


@dataclass
class MatchedLot:
    """Represents a portion of a BUY lot matched and consumed by a SELL order."""
    purchase_date: str
    matched_quantity: float
    buy_cost_per_share: float
    total_buy_cost: float
    sell_price_per_share: float
    total_sell_proceeds: float
    realized_pnl: float
    realized_pnl_percent: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "purchase_date": str(self.purchase_date),
            "matched_quantity": round(self.matched_quantity, 8),
            "buy_cost_per_share": round(self.buy_cost_per_share, 6),
            "total_buy_cost": round(self.total_buy_cost, 4),
            "sell_price_per_share": round(self.sell_price_per_share, 6),
            "total_sell_proceeds": round(self.total_sell_proceeds, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "realized_pnl_percent": round(self.realized_pnl_percent, 4),
        }


@dataclass
class SaleExecution:
    """Represents a completed SELL transaction execution with FIFO matched lots."""
    symbol: str
    sale_date: str
    quantity: float
    sell_price: float
    total_proceeds: float
    total_cost_basis: float
    realized_pnl: float
    realized_pnl_percent: float
    currency: str = "USD"
    asset_type: str = "US_STOCK"
    matched_lots: List[MatchedLot] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sale_date": str(self.sale_date),
            "quantity": round(self.quantity, 8),
            "sell_price": round(self.sell_price, 6),
            "total_proceeds": round(self.total_proceeds, 4),
            "total_cost_basis": round(self.total_cost_basis, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "realized_pnl_percent": round(self.realized_pnl_percent, 4),
            "currency": self.currency,
            "asset_type": self.asset_type,
            "matched_lots": [m.to_dict() for m in self.matched_lots],
        }


# ==============================================================================
# FIFO CALCULATOR ENGINE CLASS
# ==============================================================================

class FIFOCalculator:
    """FIFO Portfolio Calculator engine.
    
    Maintains a FIFO queue of open BUY lots per ticker symbol, processes
    transactions in chronological order, computes realized P&L on sales,
    and calculates remaining unconsumed holdings and exact cost bases.
    """

    def __init__(self, transactions: Optional[Union[List[Dict[str, Any]], pd.DataFrame]] = None, auto_sort: bool = False):
        """Initialize the FIFO calculator.
        
        Args:
            transactions (List[Dict[str, Any]] | pd.DataFrame, optional):
                Initial transaction history to process.
            auto_sort (bool): If True, transactions will be sorted chronologically
                by purchase_date / date before processing.
        """
        # Map: symbol -> Deque[BuyLot]
        self._open_lots: Dict[str, Deque[BuyLot]] = {}
        # List of all completed sale executions
        self._sale_executions: List[SaleExecution] = []
        # Total transaction count processed
        self._processed_count: int = 0
        # Symbol metadata cache (asset_type, currency)
        self._symbol_metadata: Dict[str, Dict[str, str]] = {}

        if transactions is not None:
            self.process_transactions(transactions, auto_sort=auto_sort)

    def reset(self) -> None:
        """Clear all open lots, trade history, and state."""
        self._open_lots.clear()
        self._sale_executions.clear()
        self._processed_count = 0
        self._symbol_metadata.clear()

    @property
    def open_lots(self) -> Dict[str, List[BuyLot]]:
        """Return a copy of current open lots grouped by symbol."""
        return {sym: list(q) for sym, q in self._open_lots.items() if q}

    @property
    def sale_executions(self) -> List[SaleExecution]:
        """Return a list of all executed sales with realized P&L details."""
        return list(self._sale_executions)

    def get_symbol_quantity(self, symbol: str) -> float:
        """Get the total available quantity currently held in open lots for a symbol."""
        sym = symbol.strip().upper()
        if sym not in self._open_lots:
            return 0.0
        return sum(lot.quantity for lot in self._open_lots[sym])

    def _normalize_transaction(self, raw_tx: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and validate required fields from various transaction dictionary formats."""
        if not isinstance(raw_tx, dict):
            raise InvalidTransactionError(f"Transaction must be a dict, got {type(raw_tx).__name__}")

        # Transaction type: 'BUY' or 'SELL' (defaults to 'BUY' if omitted)
        raw_type = (
            raw_tx.get("transaction_type")
            if "transaction_type" in raw_tx and raw_tx["transaction_type"]
            else raw_tx.get("type")
            if "type" in raw_tx and raw_tx["type"]
            else raw_tx.get("action")
            if "action" in raw_tx and raw_tx["action"]
            else "BUY"
        )
        tx_type = str(raw_type).strip().upper()
        if tx_type not in ("BUY", "SELL"):
            raise InvalidTransactionError(
                f"Invalid transaction type '{raw_type}'. Expected 'BUY' or 'SELL'."
            )

        # Symbol
        raw_symbol = raw_tx.get("symbol") or raw_tx.get("ticker") or ""
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise InvalidTransactionError(f"Transaction missing valid 'symbol': {raw_tx}")

        # Quantity
        raw_qty = (
            raw_tx.get("quantity")
            if "quantity" in raw_tx
            else raw_tx.get("qty")
            if "qty" in raw_tx
            else raw_tx.get("shares")
            if "shares" in raw_tx
            else raw_tx.get("units")
        )
        if raw_qty is None:
            raise InvalidTransactionError(f"Transaction missing quantity: {raw_tx}")
        try:
            quantity = float(raw_qty)
        except (ValueError, TypeError):
            raise InvalidTransactionError(f"Invalid quantity '{raw_qty}' in transaction: {raw_tx}")

        if quantity <= 0:
            raise InvalidTransactionError(f"Transaction quantity must be positive, got {quantity}")

        # Price / Cost per share
        raw_price = (
            raw_tx.get("cost_per_share")
            if "cost_per_share" in raw_tx
            else raw_tx.get("price")
            if "price" in raw_tx
            else raw_tx.get("price_per_share")
            if "price_per_share" in raw_tx
            else raw_tx.get("sell_price")
            if "sell_price" in raw_tx
            else raw_tx.get("cost")
        )
        if raw_price is None:
            raise InvalidTransactionError(f"Transaction missing price/cost_per_share: {raw_tx}")
        try:
            price = float(raw_price)
        except (ValueError, TypeError):
            raise InvalidTransactionError(f"Invalid price '{raw_price}' in transaction: {raw_tx}")

        if price < 0:
            raise InvalidTransactionError(f"Transaction price cannot be negative, got {price}")

        # Date
        raw_date = (
            raw_tx.get("purchase_date")
            or raw_tx.get("transaction_date")
            or raw_tx.get("date")
            or raw_tx.get("created_at")
            or ""
        )
        tx_date = str(raw_date).strip() if raw_date is not None else ""
        if not tx_date:
            tx_date = datetime.now().strftime("%Y-%m-%d")

        # Optional metadata
        asset_type = str(raw_tx.get("asset_type") or "US_STOCK").strip().upper()
        currency = str(raw_tx.get("currency") or ("THB" if asset_type in ("TH_STOCK", "TH_MUTUAL_FUND") else "USD")).strip().upper()

        return {
            "type": tx_type,
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "date": tx_date,
            "asset_type": asset_type,
            "currency": currency,
            "raw": raw_tx,
        }

    def process_transaction(self, transaction: Dict[str, Any]) -> Optional[SaleExecution]:
        """Process a single BUY or SELL transaction according to FIFO rules.
        
        Args:
            transaction (Dict[str, Any]): Transaction dictionary.
            
        Returns:
            Optional[SaleExecution]: SaleExecution object if transaction is a SELL,
                or None if transaction is a BUY.
                
        Raises:
            InsufficientSharesError: If a SELL exceeds currently open shares.
            InvalidTransactionError: If transaction data is malformed.
        """
        tx = self._normalize_transaction(transaction)
        symbol = tx["symbol"]
        tx_type = tx["type"]
        qty = tx["quantity"]
        price = tx["price"]
        tx_date = tx["date"]
        asset_type = tx["asset_type"]
        currency = tx["currency"]

        # Cache symbol metadata
        self._symbol_metadata[symbol] = {
            "asset_type": asset_type,
            "currency": currency,
        }

        if symbol not in self._open_lots:
            self._open_lots[symbol] = deque()

        if tx_type == "BUY":
            # Add new lot to the tail of the symbol queue
            lot = BuyLot(
                symbol=symbol,
                quantity=qty,
                cost_per_share=price,
                purchase_date=tx_date,
                original_quantity=qty,
                asset_type=asset_type,
                currency=currency,
                metadata={"raw_tx": tx["raw"]},
            )
            self._open_lots[symbol].append(lot)
            self._processed_count += 1
            return None

        elif tx_type == "SELL":
            # Check if sufficient shares exist in the open lots
            available_qty = sum(lot.quantity for lot in self._open_lots[symbol])
            if qty > available_qty + FLOAT_TOLERANCE:
                raise InsufficientSharesError(
                    symbol=symbol,
                    requested_quantity=qty,
                    available_quantity=available_qty,
                    transaction_date=tx_date,
                )

            # Consume open lots in FIFO order (from head of deque)
            remaining_to_sell = qty
            matched_lots: List[MatchedLot] = []
            total_sell_proceeds = round(qty * price, 4)
            total_cost_basis = 0.0

            while remaining_to_sell > FLOAT_TOLERANCE and self._open_lots[symbol]:
                oldest_lot = self._open_lots[symbol][0]
                
                if oldest_lot.quantity <= remaining_to_sell + FLOAT_TOLERANCE:
                    # Fully consume this lot
                    consumed_qty = oldest_lot.quantity
                    remaining_to_sell -= consumed_qty
                    self._open_lots[symbol].popleft()
                else:
                    # Partially consume this lot
                    consumed_qty = remaining_to_sell
                    oldest_lot.quantity -= consumed_qty
                    remaining_to_sell = 0.0

                matched_buy_cost = round(consumed_qty * oldest_lot.cost_per_share, 4)
                matched_proceeds = round(consumed_qty * price, 4)
                matched_pnl = round(matched_proceeds - matched_buy_cost, 4)
                matched_pnl_pct = (
                    round((matched_pnl / matched_buy_cost) * 100.0, 4)
                    if matched_buy_cost > 0
                    else 0.0
                )

                total_cost_basis += matched_buy_cost

                matched_lots.append(
                    MatchedLot(
                        purchase_date=oldest_lot.purchase_date,
                        matched_quantity=round(consumed_qty, 8),
                        buy_cost_per_share=round(oldest_lot.cost_per_share, 6),
                        total_buy_cost=matched_buy_cost,
                        sell_price_per_share=round(price, 6),
                        total_sell_proceeds=matched_proceeds,
                        realized_pnl=matched_pnl,
                        realized_pnl_percent=matched_pnl_pct,
                    )
                )

            total_realized_pnl = round(total_sell_proceeds - total_cost_basis, 4)
            total_pnl_pct = (
                round((total_realized_pnl / total_cost_basis) * 100.0, 4)
                if total_cost_basis > 0
                else 0.0
            )

            sale_exec = SaleExecution(
                symbol=symbol,
                sale_date=tx_date,
                quantity=round(qty, 8),
                sell_price=round(price, 6),
                total_proceeds=total_sell_proceeds,
                total_cost_basis=round(total_cost_basis, 4),
                realized_pnl=total_realized_pnl,
                realized_pnl_percent=total_pnl_pct,
                currency=currency,
                asset_type=asset_type,
                matched_lots=matched_lots,
            )
            self._sale_executions.append(sale_exec)
            self._processed_count += 1
            return sale_exec

    def process_transactions(
        self,
        transactions: Union[List[Dict[str, Any]], pd.DataFrame, Iterable[Dict[str, Any]]],
        auto_sort: bool = False,
    ) -> Dict[str, Any]:
        """Process a collection of transactions sequentially and return the final portfolio state.
        
        Args:
            transactions (List[Dict[str, Any]] | pd.DataFrame): Transactions list or DataFrame.
            auto_sort (bool): If True, sort transactions chronologically before processing.
                Default False (assumes list is already sorted by date ascending).

        Returns:
            Dict[str, Any]: Structured dictionary with `remaining_holdings`, `realized_pnl`, and `summary`.
        """
        # Convert DataFrame to list of dicts if needed
        if isinstance(transactions, pd.DataFrame):
            if transactions.empty:
                return self.get_structured_result()
            tx_list = transactions.to_dict(orient="records")
        elif isinstance(transactions, list):
            tx_list = list(transactions)
        else:
            try:
                tx_list = list(transactions)
            except TypeError:
                raise InvalidTransactionError(f"Unsupported transactions format: {type(transactions)}")

        if not tx_list:
            return self.get_structured_result()

        if auto_sort:
            def _extract_sort_date(item: Dict[str, Any]) -> str:
                raw_d = (
                    item.get("purchase_date")
                    or item.get("transaction_date")
                    or item.get("date")
                    or ""
                )
                return str(raw_d)
            tx_list = sorted(tx_list, key=_extract_sort_date)

        for tx in tx_list:
            self.process_transaction(tx)

        return self.get_structured_result()

    def get_remaining_holdings(self) -> Dict[str, Dict[str, Any]]:
        """Calculate and return the current active portfolio holdings with remaining
        quantities, total cost bases, and lot breakdowns per symbol.
        
        Returns:
            Dict[str, Dict[str, Any]]: Map of symbol to holding details.
        """
        holdings: Dict[str, Dict[str, Any]] = {}

        for symbol, queue in self._open_lots.items():
            active_lots = [lot for lot in queue if not lot.is_depleted]
            if not active_lots:
                continue

            total_qty = sum(lot.quantity for lot in active_lots)
            total_cost = sum(lot.total_cost for lot in active_lots)
            avg_cost = round(total_cost / total_qty, 6) if total_qty > 0 else 0.0

            meta = self._symbol_metadata.get(symbol, {})
            asset_type = active_lots[0].asset_type or meta.get("asset_type", "US_STOCK")
            currency = active_lots[0].currency or meta.get("currency", "USD")

            holdings[symbol] = {
                "symbol": symbol,
                "total_quantity": round(total_qty, 8),
                "total_cost": round(total_cost, 4),
                "avg_cost_per_share": avg_cost,
                "asset_type": asset_type,
                "currency": currency,
                "lot_count": len(active_lots),
                "lots": [lot.to_dict() for lot in active_lots],
            }

        return holdings

    def get_realized_pnl(self) -> Dict[str, Any]:
        """Calculate and return realized profit/loss grouped by symbol and overall total.
        
        Returns:
            Dict[str, Any]: Realized P&L dictionary containing:
                - overall_total (float): Total realized P&L across all symbols.
                - total (float): Alias for overall_total.
                - by_symbol (Dict[str, float]): Map of symbol -> total realized P&L.
                - symbols (Dict[str, Dict]): Detailed stats per symbol (proceeds, cost, pnl).
                - trades (List[Dict]): List of executed sale events.
        """
        by_symbol_pnl: Dict[str, float] = {}
        symbol_details: Dict[str, Dict[str, Any]] = {}
        overall_total_pnl = 0.0
        overall_total_proceeds = 0.0
        overall_total_cost_basis = 0.0

        for sale in self._sale_executions:
            sym = sale.symbol
            if sym not in by_symbol_pnl:
                by_symbol_pnl[sym] = 0.0
                symbol_details[sym] = {
                    "symbol": sym,
                    "realized_pnl": 0.0,
                    "total_sold_quantity": 0.0,
                    "total_proceeds": 0.0,
                    "total_cost_basis": 0.0,
                    "trade_count": 0,
                    "currency": sale.currency,
                    "asset_type": sale.asset_type,
                }

            by_symbol_pnl[sym] = round(by_symbol_pnl[sym] + sale.realized_pnl, 4)
            symbol_details[sym]["realized_pnl"] = round(symbol_details[sym]["realized_pnl"] + sale.realized_pnl, 4)
            symbol_details[sym]["total_sold_quantity"] = round(symbol_details[sym]["total_sold_quantity"] + sale.quantity, 8)
            symbol_details[sym]["total_proceeds"] = round(symbol_details[sym]["total_proceeds"] + sale.total_proceeds, 4)
            symbol_details[sym]["total_cost_basis"] = round(symbol_details[sym]["total_cost_basis"] + sale.total_cost_basis, 4)
            symbol_details[sym]["trade_count"] += 1

            overall_total_pnl += sale.realized_pnl
            overall_total_proceeds += sale.total_proceeds
            overall_total_cost_basis += sale.total_cost_basis

        # Calculate percentage return per symbol
        for sym, detail in symbol_details.items():
            cost = detail["total_cost_basis"]
            pnl = detail["realized_pnl"]
            detail["realized_pnl_percent"] = round((pnl / cost) * 100.0, 4) if cost > 0 else 0.0

        overall_total_pnl = round(overall_total_pnl, 4)
        overall_total_proceeds = round(overall_total_proceeds, 4)
        overall_total_cost_basis = round(overall_total_cost_basis, 4)
        overall_pct = (
            round((overall_total_pnl / overall_total_cost_basis) * 100.0, 4)
            if overall_total_cost_basis > 0
            else 0.0
        )

        return {
            "overall_total": overall_total_pnl,
            "total": overall_total_pnl,
            "total_proceeds": overall_total_proceeds,
            "total_cost_basis": overall_total_cost_basis,
            "overall_pnl_percent": overall_pct,
            "by_symbol": by_symbol_pnl,
            "symbols": symbol_details,
            "trades": [sale.to_dict() for sale in self._sale_executions],
        }

    def get_summary(self) -> Dict[str, Any]:
        """Compute high-level summary of the entire FIFO calculation state."""
        holdings = self.get_remaining_holdings()
        pnl = self.get_realized_pnl()

        total_remaining_cost = round(sum(h["total_cost"] for h in holdings.values()), 4)
        total_remaining_lots = sum(h["lot_count"] for h in holdings.values())

        return {
            "total_realized_pnl": pnl["overall_total"],
            "total_remaining_cost": total_remaining_cost,
            "active_symbols_count": len(holdings),
            "total_remaining_lots": total_remaining_lots,
            "total_sales_count": len(self._sale_executions),
            "total_transactions_processed": self._processed_count,
        }

    def get_structured_result(self) -> Dict[str, Any]:
        """Return the complete structured dictionary required by the FIFO specification.
        
        Returns:
            Dict[str, Any]: Dictionary with `remaining_holdings`, `realized_pnl`, and `summary`.
        """
        return {
            "remaining_holdings": self.get_remaining_holdings(),
            "realized_pnl": self.get_realized_pnl(),
            "summary": self.get_summary(),
        }

    # ==========================================================================
    # DATAFRAME CONVERSION UTILITIES
    # ==========================================================================

    def holdings_to_dataframe(self) -> pd.DataFrame:
        """Convert remaining holdings to a flat Pandas DataFrame."""
        holdings = self.get_remaining_holdings()
        if not holdings:
            return pd.DataFrame(
                columns=["symbol", "asset_type", "currency", "total_quantity", "avg_cost_per_share", "total_cost", "lot_count"]
            )
        rows = [
            {
                "symbol": h["symbol"],
                "asset_type": h["asset_type"],
                "currency": h["currency"],
                "total_quantity": h["total_quantity"],
                "avg_cost_per_share": h["avg_cost_per_share"],
                "total_cost": h["total_cost"],
                "lot_count": h["lot_count"],
            }
            for h in holdings.values()
        ]
        return pd.DataFrame(rows)

    def lots_to_dataframe(self) -> pd.DataFrame:
        """Convert all active unconsumed lots to a Pandas DataFrame."""
        holdings = self.get_remaining_holdings()
        rows = []
        for sym, h in holdings.items():
            for lot in h["lots"]:
                rows.append(lot)
        if not rows:
            return pd.DataFrame(
                columns=["symbol", "purchase_date", "quantity", "cost_per_share", "total_cost", "asset_type", "currency"]
            )
        return pd.DataFrame(rows)

    def realized_pnl_to_dataframe(self) -> pd.DataFrame:
        """Convert realized P&L per symbol to a Pandas DataFrame."""
        pnl = self.get_realized_pnl()
        symbols = pnl.get("symbols", {})
        if not symbols:
            return pd.DataFrame(
                columns=["symbol", "total_sold_quantity", "total_proceeds", "total_cost_basis", "realized_pnl", "realized_pnl_percent", "trade_count"]
            )
        rows = list(symbols.values())
        return pd.DataFrame(rows)

    def trades_to_dataframe(self) -> pd.DataFrame:
        """Convert executed sale trades to a Pandas DataFrame."""
        trades = self._sale_executions
        if not trades:
            return pd.DataFrame(
                columns=["symbol", "sale_date", "quantity", "sell_price", "total_proceeds", "total_cost_basis", "realized_pnl", "realized_pnl_percent"]
            )
        rows = [
            {
                "symbol": t.symbol,
                "sale_date": t.sale_date,
                "quantity": t.quantity,
                "sell_price": t.sell_price,
                "total_proceeds": t.total_proceeds,
                "total_cost_basis": t.total_cost_basis,
                "realized_pnl": t.realized_pnl,
                "realized_pnl_percent": t.realized_pnl_percent,
                "currency": t.currency,
                "asset_type": t.asset_type,
            }
            for t in trades
        ]
        return pd.DataFrame(rows)


# ==============================================================================
# FUNCTIONAL CONVENIENCE API
# ==============================================================================

def calculate_fifo_portfolio(
    transactions: Union[List[Dict[str, Any]], pd.DataFrame, Iterable[Dict[str, Any]]],
    auto_sort: bool = False,
) -> Dict[str, Any]:
    """Calculate portfolio holdings and realized P&L using First-In, First-Out (FIFO) method.

    Args:
        transactions (List[Dict[str, Any]] | pd.DataFrame):
            List of transaction dictionaries or Pandas DataFrame.
            Each transaction must contain:
            - 'type' or 'transaction_type': 'BUY' or 'SELL'
            - 'symbol' or 'ticker': ticker string
            - 'quantity' or 'qty': positive number
            - 'cost_per_share' or 'price' / 'sell_price': positive number
            - 'purchase_date' or 'date': transaction date string / date
            Optional fields: 'asset_type', 'currency'.
        auto_sort (bool): If True, sort transactions chronologically before processing.

    Returns:
        Dict[str, Any]: Structured dictionary containing:
            - remaining_holdings: Current active portfolio with remaining quantities and total cost per symbol.
            - realized_pnl: Total realized profit/loss grouped by symbol and overall total.
            - summary: Overall portfolio counts and cost totals.

    Raises:
        InsufficientSharesError: When a SELL transaction exceeds currently available open shares.
        InvalidTransactionError: When transaction structure is malformed.
    """
    calculator = FIFOCalculator(transactions=transactions, auto_sort=auto_sort)
    return calculator.get_structured_result()


def calculate_realized_pnl(
    transactions: Union[List[Dict[str, Any]], pd.DataFrame, Iterable[Dict[str, Any]]],
    auto_sort: bool = False,
) -> Dict[str, Any]:
    """Convenience function returning only the realized P&L section from FIFO matching.

    Args:
        transactions (List[Dict[str, Any]] | pd.DataFrame): Transaction history.
        auto_sort (bool): If True, auto sort transactions by date.

    Returns:
        Dict[str, Any]: Realized P&L dictionary containing overall_total, by_symbol, and trades.
    """
    calculator = FIFOCalculator(transactions=transactions, auto_sort=auto_sort)
    return calculator.get_realized_pnl()
