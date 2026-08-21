"""
app.py
======
Streamlit Web Application for Multi-Asset Stock & Fund Portfolio Tracker.
Integrates SQLite transaction persistence, live market fetching via yfinance/SEC API,
USD/THB FX conversion, Plotly interactive visualizations, and complete Ticker Registry.
"""

from datetime import datetime, date
import os
from typing import Any, Dict, List, Optional
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_fetcher import (
    clear_price_cache,
    format_ticker_symbol,
    get_stock_price,
    get_thai_fund_nav,
    get_usd_thb_rate,
    validate_symbol,
    validate_symbol_detailed,
)
from database import (
    DEFAULT_DB_PATH,
    PortfolioDB,
    add_transaction,
    clear_all_transactions,
    delete_transaction,
    get_all_transactions,
    init_db,
)
from portfolio_calculator import (
    calculate_portfolio_summary,
    get_portfolio_metrics_dataframe,
)
from ticker_registry import (
    get_all_symbols,
    get_symbol_info,
    init_ticker_cache,
    search_symbols,
    update_ticker_cache,
)

# ------------------------------------------------------------------------------
# 1. Page Configuration & Layout
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="Portfolio Tracker",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------------------
# 2. Database & Schema Initialization on Startup
# ------------------------------------------------------------------------------
init_db()
init_ticker_cache(DEFAULT_DB_PATH)

# Retrieve sensitive credentials from st.secrets or environment
SEC_API_KEY = None
try:
    if hasattr(st, "secrets") and "SEC_API_KEY" in st.secrets:
        SEC_API_KEY = st.secrets["SEC_API_KEY"]
except Exception:
    pass
if not SEC_API_KEY:
    SEC_API_KEY = os.getenv("SEC_API_KEY")


# ------------------------------------------------------------------------------
# 3. External API Data Fetching with @st.cache_data(ttl=300)
# ------------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def get_cached_stock_price(symbol: str) -> Dict[str, Any]:
    """Fetch and cache stock market prices via yfinance for 5 minutes."""
    return get_stock_price(symbol)


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_usd_thb_rate() -> Dict[str, Any]:
    """Fetch and cache real-time USD/THB exchange rate for 5 minutes."""
    return get_usd_thb_rate()


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_thai_fund_nav(fund_code: str, sec_key: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and cache Thai mutual fund Net Asset Value (NAV) for 5 minutes."""
    return get_thai_fund_nav(fund_code, sec_api_key=sec_key or SEC_API_KEY)


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_portfolio_summary(
    tx_json_str: str,
    refresh_nonce: int,
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate and cache portfolio summary valuations for high UI responsiveness."""
    import json
    txs = json.loads(tx_json_str) if tx_json_str else []
    target_db = db_url or db_path or DEFAULT_DB_PATH
    return calculate_portfolio_summary(transactions=txs, db_path=target_db)

# Custom CSS for polished, modern UI styling
st.markdown(
    """
    <style>
    /* Metric Card styling */
    div[data-testid="stMetric"] {
        background-color: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.12);
        padding: 1rem 1.25rem;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    div[data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.25);
    }
    div[data-testid="stMetricLabel"] p {
        font-size: 0.9rem !important;
        font-weight: 600 !important;
        color: #94a3b8 !important;
    }
    div[data-testid="stMetricValue"] div {
        font-size: 1.8rem !important;
        font-weight: 700 !important;
    }
    
    /* Header styling */
    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(90deg, #38bdf8 0%, #818cf8 50%, #c084fc 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        color: #94a3b8;
        font-size: 1.0rem;
        margin-bottom: 1.5rem;
    }
    
    /* Selected asset info box */
    .asset-info-card {
        background: rgba(56, 189, 248, 0.08);
        border: 1px solid rgba(56, 189, 248, 0.25);
        border-radius: 8px;
        padding: 0.75rem;
        margin-bottom: 0.75rem;
        font-size: 0.85rem;
    }
    
    /* Sidebar header */
    .sidebar-header {
        font-size: 1.25rem;
        font-weight: 700;
        margin-bottom: 0.5rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


if "refresh_nonce" not in st.session_state:
    st.session_state.refresh_nonce = 0


def load_sample_portfolio_data():
    """Seed sample multi-asset transactions if user requests it."""
    samples = [
        ("AAPL", "US_STOCK", 15, 175.0, "USD", "2024-01-10"),
        ("NVDA", "US_STOCK", 5, 450.0, "USD", "2024-01-22"),
        ("PTT.BK", "TH_STOCK", 1000, 32.5, "THB", "2024-02-05"),
        ("CPALL.BK", "TH_STOCK", 500, 56.0, "THB", "2024-02-18"),
        ("SCBDV", "TH_MUTUAL_FUND", 2500, 12.4, "THB", "2024-03-01"),
        ("K-USA-A(A)", "TH_MUTUAL_FUND", 2000, 15.8, "THB", "2024-03-12"),
    ]
    for sym, atype, qty, price, curr, pdate in samples:
        add_transaction(
            symbol=sym,
            asset_type=atype,
            quantity=qty,
            cost_per_share=price,
            currency=curr,
            purchase_date=pdate,
            db_path=DEFAULT_DB_PATH,
        )
    clear_price_cache()
    st.cache_data.clear()
    st.session_state.refresh_nonce += 1


# ==============================================================================
# SIDEBAR: TRANSACTION INPUT & MANAGEMENT
# ==============================================================================
with st.sidebar:
    st.markdown('<div class="sidebar-header">➕ Add Transaction</div>', unsafe_allow_html=True)
    st.caption("Search by Ticker or Name, or manually input custom assets.")

    # 1. Entry Method Toggle
    entry_mode = st.radio(
        "Entry Mode",
        options=["search", "manual"],
        format_func=lambda x: "🔍 Search & Select" if x == "search" else "✏️ Manual Fallback",
        horizontal=True,
        label_visibility="collapsed",
    )

    selected_symbol = ""
    selected_asset_type = "US_STOCK"
    selected_currency = "USD"
    selected_name = ""
    selected_market = ""

    if entry_mode == "search":
        # Category Filter for Search
        cat_filter = st.selectbox(
            "Filter Category",
            options=["ALL", "US_STOCK", "TH_STOCK", "TH_MUTUAL_FUND"],
            format_func=lambda x: {
                "ALL": "🌐 All Markets (10,000+ Assets)",
                "US_STOCK": "🇺🇸 US Stocks & ETFs",
                "TH_STOCK": "🇹🇭 Thai Stocks (.BK)",
                "TH_MUTUAL_FUND": "🏦 Thai Mutual Funds",
            }[x],
        )

        filter_arg = None if cat_filter == "ALL" else cat_filter
        registered_symbols = get_all_symbols(db_path=DEFAULT_DB_PATH, asset_type=filter_arg)

        # Build dropdown options mapping {display_label: record}
        options_list = [f"{s['symbol']} - {s['name']}" for s in registered_symbols]
        options_map = {f"{s['symbol']} - {s['name']}": s for s in registered_symbols}

        # Searchable selectbox
        selected_option = st.selectbox(
            "Search Ticker or Company / Fund Name",
            options=options_list,
            index=0 if options_list else None,
            help="Type ticker (e.g. AAPL, CPALL.BK) or name (e.g. Apple Inc., CP ALL, ONE-UGG) to search.",
        )

        if selected_option and selected_option in options_map:
            asset_info = options_map[selected_option]
            selected_symbol = asset_info["symbol"]
            selected_asset_type = asset_info["asset_type"]
            selected_currency = asset_info["currency"]
            selected_name = asset_info["name"]
            selected_market = asset_info.get("market", "")

            # Display metadata badge preview
            st.markdown(
                f"""
                <div class="asset-info-card">
                    <b>{selected_symbol}</b>: {selected_name}<br>
                    <span style="color:#94a3b8;">Type: <b>{selected_asset_type}</b> | Currency: <b>{selected_currency}</b> | {selected_market}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # 2. Transaction Details Form
    with st.form("transaction_input_form", clear_on_submit=True):
        if entry_mode == "manual":
            manual_symbol = st.text_input(
                "Symbol / Ticker",
                placeholder="e.g. AAPL, PTT.BK, ONE-UGG-RA",
                help="For Thai stocks, include '.BK' (e.g. PTT.BK, CPALL.BK). For US stocks, use ticker (e.g. AAPL, NVDA).",
            )
            col_m_type, col_m_curr = st.columns(2)
            with col_m_type:
                manual_asset_type = st.selectbox(
                    "Asset Type",
                    options=["US_STOCK", "TH_STOCK", "TH_MUTUAL_FUND"],
                    format_func=lambda x: {
                        "US_STOCK": "🇺🇸 US Stock",
                        "TH_STOCK": "🇹🇭 Thai Stock (.BK)",
                        "TH_MUTUAL_FUND": "🏦 Thai Fund",
                    }[x],
                )
            with col_m_curr:
                def_curr_idx = 0 if manual_asset_type == "US_STOCK" else 1
                manual_currency = st.selectbox("Currency", options=["USD", "THB"], index=def_curr_idx)

        col_qty, col_price = st.columns(2)
        with col_qty:
            quantity = st.number_input(
                "Quantity",
                min_value=0.00000001,
                value=10.0,
                step=0.0001,
                format="%.8f",
                help="Supports fractional shares and fund units up to 8 decimal places.",
            )
        with col_price:
            cost_per_share = st.number_input(
                "Purchase Price / Share",
                min_value=0.0,
                value=100.0,
                step=0.0001,
                format="%.6f",
                help="Supports price precision up to 6 decimal places.",
            )

        purchase_date = st.date_input(
            "Purchase Date",
            value=date.today(),
            max_value=date.today(),
        )

        submitted = st.form_submit_button("Save Transaction", use_container_width=True, type="primary")

        if submitted:
            # Determine target values based on mode
            if entry_mode == "search":
                target_symbol = selected_symbol
                target_asset_type = selected_asset_type
                target_currency = selected_currency
            else:
                target_symbol = format_ticker_symbol(manual_symbol)
                target_asset_type = manual_asset_type
                target_currency = manual_currency
                if target_asset_type == "TH_STOCK" and not target_symbol.endswith(".BK"):
                    target_symbol += ".BK"

            if not target_symbol:
                st.error("Please provide or select a valid Symbol/Ticker.")
            else:
                # Validation layer using yfinance / market data check
                with st.spinner(f"Verifying ticker '{target_symbol}' with market data..."):
                    val_res = validate_symbol_detailed(target_symbol, asset_type=target_asset_type)

                if not val_res.get("valid", False):
                    err_msg = val_res.get("error", f"Invalid ticker '{target_symbol}'. Market data not found.")
                    st.error(f"❌ **Invalid Symbol**: {err_msg}\n\nTransaction was blocked to prevent saving broken entries.")
                else:
                    try:
                        new_id = add_transaction(
                            symbol=target_symbol,
                            asset_type=target_asset_type,
                            quantity=quantity,
                            cost_per_share=cost_per_share,
                            currency=target_currency,
                            purchase_date=purchase_date.strftime("%Y-%m-%d"),
                            db_path=DEFAULT_DB_PATH,
                        )
                        clear_price_cache()
                        st.cache_data.clear()
                        st.session_state.refresh_nonce += 1
                        st.success(f"Added transaction #{new_id} for **{target_symbol}** ({val_res.get('name', '')})!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error saving transaction: {e}")

    st.markdown("---")

    # Live FX Rate Card & Quick Controls
    st.markdown("### 💱 Live Market FX")
    try:
        fx_data = get_cached_usd_thb_rate()
        if fx_data.get("success") and fx_data.get("rate"):
            rate_val = fx_data["rate"]
            chg_pct = fx_data.get("change_percent", 0.0)
            st.metric(
                label="USD / THB (THB=X)",
                value=f"฿{rate_val:,.2f}",
                delta=f"{chg_pct:+.2f}%",
            )
        else:
            st.info("Live FX rate unavailable. Using default ฿35.50.")
    except Exception:
        st.info("Live FX rate: ฿35.50")

    if st.button("🔄 Refresh Market Prices", use_container_width=True):
        clear_price_cache()
        st.cache_data.clear()
        st.session_state.refresh_nonce += 1
        st.rerun()

    st.markdown("---")
    st.markdown("### ⚙️ Database & Ticker Tools")
    with st.expander("Database Tools & Seed Data", expanded=False):
        if st.button("📥 Load Sample Portfolio", use_container_width=True):
            load_sample_portfolio_data()
            st.success("Sample portfolio data loaded!")
            st.rerun()

        if st.button("🔄 Sync Symbol Database (SEC/NASDAQ)", use_container_width=True):
            with st.spinner("Updating ticker database from SEC/NASDAQ..."):
                cnt = update_ticker_cache(db_path=DEFAULT_DB_PATH, force=True)
                st.success(f"Symbol registry updated ({cnt:,} symbols cached)!")
                st.rerun()

        if st.button("🗑️ Clear All Transactions", use_container_width=True, type="secondary"):
            clear_all_transactions(DEFAULT_DB_PATH)
            clear_price_cache()
            st.cache_data.clear()
            st.session_state.refresh_nonce += 1
            st.warning("All transactions deleted.")
            st.rerun()


# ==============================================================================
# MAIN DASHBOARD CONTENT
# ==============================================================================

# Title and Header
st.markdown('<div class="main-title">Multi-Asset Portfolio Tracker</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">Real-time consolidated tracking for US Stocks, Thai Stocks (.BK), and Thai Mutual Funds in Thai Baht (THB).</div>',
    unsafe_allow_html=True,
)

# Fetch transactions and calculate metrics
transactions = get_all_transactions(as_dataframe=False)

if not transactions:
    st.info(
        "👋 **Welcome to Portfolio Tracker!** Your portfolio is currently empty.\n\n"
        "**To get started:**\n"
        "- ➕ **Add Transactions**: Use the transaction form in the sidebar on the left.\n"
        "- 📥 **Sample Portfolio**: Open **'Database Tools & Seed Data'** in the sidebar and click **'Load Sample Portfolio'** to explore with pre-loaded demo holdings."
    )
    st.stop()

# Perform fast cached portfolio calculation
import json
tx_json_str = json.dumps(transactions, sort_keys=True)

with st.spinner("Fetching live market data and computing portfolio valuations..."):
    summary = get_cached_portfolio_summary(
        tx_json_str=tx_json_str,
        refresh_nonce=st.session_state.refresh_nonce,
        db_path=DEFAULT_DB_PATH,
    )

total_val_thb = summary["total_value_thb"]
total_cost_thb = summary["total_cost_thb"]
total_pnl_thb = summary["total_unrealized_pnl_thb"]
total_return_pct = summary["total_unrealized_pnl_percent"]
usd_rate = summary["usd_thb_rate"]
holdings = summary.get("holdings", [])

# ==============================================================================
# TOP METRICS BAR (KPIs)
# ==============================================================================
kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)

with kpi1:
    st.metric(
        label="Total Portfolio Value",
        value=f"฿{total_val_thb:,.2f}",
        help="Current total value of all holdings converted into Thai Baht (THB).",
    )

with kpi2:
    st.metric(
        label="Total Invested Cost",
        value=f"฿{total_cost_thb:,.2f}",
        help="Total purchase cost basis across all holdings in THB.",
    )

with kpi3:
    st.metric(
        label="Total Unrealized P&L",
        value=f"฿{total_pnl_thb:+,.2f}",
        delta=f"{total_return_pct:+.2f}%",
        help="Total unrealized profit or loss in Thai Baht.",
    )

with kpi4:
    st.metric(
        label="Total Return",
        value=f"{total_return_pct:+.2f}%",
        delta=f"฿{total_pnl_thb:+,.0f}",
        help="Portfolio return percentage on invested cost.",
    )

with kpi5:
    st.metric(
        label="Active Holdings",
        value=f"{len(holdings)} Assets",
        help="Total distinct symbols currently held.",
    )

st.markdown("<br>", unsafe_allow_html=True)

# ==============================================================================
# DASHBOARD TABS
# ==============================================================================
tab_overview, tab_holdings, tab_history, tab_directory = st.tabs([
    "📊 Overview & Visualizations",
    "📋 Holdings & Performance",
    "📜 Transaction History",
    "🔍 Symbol Directory & Search",
])

# ------------------------------------------------------------------------------
# TAB 1: OVERVIEW & VISUALIZATIONS
# ------------------------------------------------------------------------------
with tab_overview:
    if not holdings:
        st.info("No holdings data available to visualize.")
    else:
        # Category Progress Summary Cards
        st.markdown("##### 🌐 Asset Allocation Summary")
        alloc_summary = summary.get("allocation_by_asset_type", {})
        if alloc_summary:
            cat_cols = st.columns(len(alloc_summary))
            cat_icons = {
                "US_STOCK": "🇺🇸 US Stocks",
                "TH_STOCK": "🇹🇭 Thai Stocks",
                "TH_MUTUAL_FUND": "🏦 Thai Funds",
            }
            for idx, (cat_key, cat_val) in enumerate(alloc_summary.items()):
                with cat_cols[idx]:
                    cat_label = cat_icons.get(cat_key, cat_key)
                    st.metric(
                        label=f"{cat_label} ({cat_val['weight_percent']:.1f}%)",
                        value=f"฿{cat_val['value_thb']:,.0f}",
                    )
                    st.progress(min(1.0, max(0.0, cat_val["weight_percent"] / 100.0)))

        st.markdown("<br>", unsafe_allow_html=True)

        # 2 Main Clean Visualizations Side-by-Side
        col_v1, col_v2 = st.columns(2)

        with col_v1:
            st.markdown("##### 🥧 Holdings Distribution")
            df_holdings = pd.DataFrame(holdings).sort_values(by="market_value_thb", ascending=False)
            
            # Clean Modern Donut Chart
            fig_alloc = px.pie(
                df_holdings,
                names="symbol",
                values="market_value_thb",
                hole=0.55,
                color_discrete_sequence=["#38bdf8", "#818cf8", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#fb923c"],
            )
            fig_alloc.update_traces(
                textposition="inside",
                textinfo="percent+label",
                hovertemplate="<b>%{label}</b><br>Value: ฿%{value:,.2f} THB<br>Weight: %{percent:.1%}<extra></extra>",
                marker=dict(line=dict(color="#1e293b", width=2)),
            )
            fig_alloc.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=True,
                legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
                height=340,
                annotations=[
                    dict(
                        text=f"<b>฿{total_val_thb:,.0f}</b><br><span style='font-size:11px;color:#94a3b8;'>Total THB</span>",
                        x=0.5,
                        y=0.5,
                        font_size=15,
                        showarrow=False,
                    )
                ],
            )
            st.plotly_chart(fig_alloc, use_container_width=True)

        with col_v2:
            st.markdown("##### 💰 Profit & Loss per Asset")
            df_pnl = pd.DataFrame(holdings).sort_values(by="unrealized_pnl_thb", ascending=True)
            pnl_colors = ["#10b981" if val >= 0 else "#f43f5e" for val in df_pnl["unrealized_pnl_thb"]]

            # Clean Horizontal Bar Chart
            fig_pnl = go.Figure(
                go.Bar(
                    x=df_pnl["unrealized_pnl_thb"],
                    y=df_pnl["symbol"],
                    orientation="h",
                    marker_color=pnl_colors,
                    text=[
                        f"฿{v:+,.0f} ({ret:+.1f}%)"
                        for v, ret in zip(df_pnl["unrealized_pnl_thb"], df_pnl["unrealized_pnl_percent"])
                    ],
                    textposition="auto",
                    hovertemplate="<b>%{y}</b><br>P&L: ฿%{x:+,.2f} THB<extra></extra>",
                    marker=dict(line=dict(color="#1e293b", width=1)),
                )
            )
            fig_pnl.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)", title=None),
                yaxis=dict(showgrid=False, title=None),
                height=340,
            )
            st.plotly_chart(fig_pnl, use_container_width=True)

# ------------------------------------------------------------------------------
# TAB 2: HOLDINGS & PERFORMANCE TABLE
# ------------------------------------------------------------------------------
with tab_holdings:
    st.markdown("### 📋 Portfolio Holdings Breakdown")
    if not holdings:
        st.info("No holdings data available.")
    else:
        # Market Filter Selector
        holding_types = ["ALL"] + sorted(list(set(h["asset_type"] for h in holdings)))
        selected_type = st.radio(
            "Filter Category",
            options=holding_types,
            horizontal=True,
            label_visibility="collapsed",
            format_func=lambda x: {
                "ALL": f"🌐 All Assets ({len(holdings)})",
                "US_STOCK": "🇺🇸 US Stocks",
                "TH_STOCK": "🇹🇭 Thai Stocks",
                "TH_MUTUAL_FUND": "🏦 Thai Funds",
            }.get(x, x),
        )

        filtered_holdings = [
            h for h in holdings
            if selected_type == "ALL" or h["asset_type"] == selected_type
        ]

        display_rows = []
        for h in filtered_holdings:
            curr_symbol = "$" if h["currency"] == "USD" else "฿"
            display_rows.append({
                "Asset": h["symbol"],
                "Asset Name": h["name"],
                "Type": {"US_STOCK": "🇺🇸 US", "TH_STOCK": "🇹🇭 Thai", "TH_MUTUAL_FUND": "🏦 Fund"}.get(h["asset_type"], h["asset_type"]),
                "Quantity": h["quantity"],
                "Avg Cost": f"{curr_symbol}{h['avg_cost_per_share']:,.4f}" if (h['avg_cost_per_share'] * 100) % 1 != 0 else f"{curr_symbol}{h['avg_cost_per_share']:,.2f}",
                "Live Price": f"{curr_symbol}{h['current_price']:,.4f}" if (h['current_price'] * 100) % 1 != 0 else f"{curr_symbol}{h['current_price']:,.2f}",
                "Value (THB)": h["market_value_thb"],
                "P&L (THB)": h["unrealized_pnl_thb"],
                "Return (%)": h["unrealized_pnl_percent"],
                "Weight (%)": h["weight_percent"],
            })

        df_display = pd.DataFrame(display_rows)

        st.dataframe(
            df_display,
            column_config={
                "Asset": st.column_config.TextColumn("Symbol", width="small"),
                "Asset Name": st.column_config.TextColumn("Company / Fund", width="medium"),
                "Type": st.column_config.TextColumn("Market", width="small"),
                "Quantity": st.column_config.NumberColumn("Shares/Units", format="%.6f"),
                "Avg Cost": st.column_config.TextColumn("Avg Cost", width="small"),
                "Live Price": st.column_config.TextColumn("Live Price", width="small"),
                "Value (THB)": st.column_config.NumberColumn("Current Value (THB)", format="฿%d"),
                "P&L (THB)": st.column_config.NumberColumn("P&L (THB)", format="฿%+d"),
                "Return (%)": st.column_config.NumberColumn("Return", format="%+.2f%%"),
                "Weight (%)": st.column_config.ProgressColumn("Allocation", format="%.1f%%", min_value=0, max_value=100),
            },
            hide_index=True,
            use_container_width=True,
        )

# ------------------------------------------------------------------------------
# TAB 3: TRANSACTION HISTORY & EDITING
# ------------------------------------------------------------------------------
with tab_history:
    st.markdown("### 📜 Recorded Transactions")
    tx_df = pd.DataFrame(transactions)

    if not tx_df.empty:
        col_table, col_del = st.columns([2.6, 1.4])

        with col_table:
            # Prepare clean display dataframe without exposing internal database ID
            display_tx = tx_df.copy()
            display_tx["total_cost"] = display_tx["quantity"] * display_tx["cost_per_share"]
            cols_to_show = ["symbol", "asset_type", "quantity", "cost_per_share", "currency", "total_cost", "purchase_date"]

            st.dataframe(
                display_tx[cols_to_show],
                column_config={
                    "symbol": st.column_config.TextColumn("Symbol", width="medium"),
                    "asset_type": st.column_config.TextColumn("Asset Type", width="small"),
                    "quantity": st.column_config.NumberColumn("Quantity", format="%.6f"),
                    "cost_per_share": st.column_config.NumberColumn("Cost / Share", format="%.6f"),
                    "currency": st.column_config.TextColumn("Currency", width="small"),
                    "total_cost": st.column_config.NumberColumn("Total Cost", format="%.2f"),
                    "purchase_date": st.column_config.TextColumn("Purchase Date", width="medium"),
                },
                hide_index=True,
                use_container_width=True,
            )

        with col_del:
            st.markdown("#### 🗑️ Remove Transaction")
            st.caption("Select a record from the list below to delete it directly:")

            # Build human-readable dropdown options without raw ID numbers
            tx_options_map = {}
            for _, row in tx_df.iterrows():
                curr_symbol = "$" if row["currency"] == "USD" else "฿"
                label = f"{row['symbol']} — {row['quantity']:g} @ {curr_symbol}{row['cost_per_share']:g} ({row['purchase_date']})"
                tx_options_map[label] = int(row["id"])

            selected_tx_label = st.selectbox(
                "Select Transaction to Delete",
                options=list(tx_options_map.keys()),
                index=len(tx_options_map) - 1,  # Defaults to most recent transaction
                help="Select the transaction you wish to delete from your portfolio.",
            )

            if st.button("🗑️ Delete Selected Record", type="secondary", use_container_width=True):
                target_del_id = tx_options_map[selected_tx_label]
                if delete_transaction(target_del_id, DEFAULT_DB_PATH):
                    clear_price_cache()
                    st.cache_data.clear()
                    st.session_state.refresh_nonce += 1
                    st.success(f"Removed: **{selected_tx_label}**")
                    st.rerun()
                else:
                    st.error("Failed to delete the selected transaction.")
    else:
        st.info("No transaction records found.")

# ------------------------------------------------------------------------------
# TAB 4: SYMBOL DIRECTORY & SEARCH
# ------------------------------------------------------------------------------
with tab_directory:
    st.markdown("### 🔍 Search & Browse Ticker Database")
    st.caption("Search across 10,000+ US Stocks, Thai Stocks (.BK), and Thai Mutual Funds.")

    col_search, col_filter = st.columns([3, 1])
    with col_search:
        search_query = st.text_input("Search by Symbol, Company Name, or Fund Name", placeholder="e.g. Apple, PTT, กสิกร, ONE-UGG")
    with col_filter:
        search_type_filter = st.selectbox(
            "Filter Category",
            options=["ALL", "US_STOCK", "TH_STOCK", "TH_MUTUAL_FUND"],
            format_func=lambda x: {
                "ALL": "All Categories",
                "US_STOCK": "🇺🇸 US Stocks",
                "TH_STOCK": "🇹🇭 Thai Stocks",
                "TH_MUTUAL_FUND": "🏦 Thai Funds",
            }[x],
            key="directory_filter",
        )

    filter_val = None if search_type_filter == "ALL" else search_type_filter
    search_results = search_symbols(query=search_query, asset_type=filter_val, limit=50, db_path=DEFAULT_DB_PATH)

    if search_results:
        df_results = pd.DataFrame(search_results)
        st.dataframe(
            df_results[["symbol", "name", "asset_type", "currency", "market"]],
            column_config={
                "symbol": st.column_config.TextColumn("Symbol / Ticker", width="medium"),
                "name": st.column_config.TextColumn("Company / Fund Full Name", width="large"),
                "asset_type": st.column_config.TextColumn("Asset Type", width="small"),
                "currency": st.column_config.TextColumn("Currency", width="small"),
                "market": st.column_config.TextColumn("Exchange / AMC", width="small"),
            },
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("No symbols found matching your search query.")
