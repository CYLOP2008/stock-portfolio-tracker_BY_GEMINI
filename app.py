"""
app.py
======
Streamlit Web Application for Multi-Asset Stock & Fund Portfolio Tracker.
Integrates Multi-User Authentication, Multi-Portfolio Management, Live Market Fetching
via yfinance/SEC API, USD/THB FX conversion, and Plotly interactive visualizations.
"""

from datetime import datetime, date
import json
import logging
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
    AuthenticationError,
    DatabaseError,
    PortfolioDB,
    ValidationError,
    add_transaction,
    authenticate_user,
    clear_all_transactions,
    create_portfolio,
    delete_portfolio,
    delete_transaction,
    get_all_transactions,
    get_portfolio_by_id,
    get_user_portfolios,
    init_db,
    register_user,
    update_portfolio,
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
db_init_error = None
try:
    init_db()
    init_ticker_cache(DEFAULT_DB_PATH)
except Exception as e:
    db_init_error = str(e)
    try:
        from database import DEFAULT_DB_URL
        init_db(DEFAULT_DB_URL)
        init_ticker_cache(DEFAULT_DB_PATH)
    except Exception:
        pass

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
    txs = json.loads(tx_json_str) if tx_json_str else []
    target_db = db_url or db_path or DEFAULT_DB_PATH
    return calculate_portfolio_summary(transactions=txs, db_path=target_db)


def load_sample_portfolio_data(target_pf_id: Optional[int] = None, target_user_id: Optional[int] = None):
    """Seed sample multi-asset transactions into the active portfolio."""
    samples = [
        ("AAPL", "US_STOCK", 15, 175.0, "USD", "2024-01-10"),
        ("NVDA", "US_STOCK", 5, 450.0, "USD", "2024-01-22"),
        ("PTT.BK", "TH_STOCK", 1000, 32.5, "THB", "2024-02-05"),
        ("CPALL.BK", "TH_STOCK", 500, 56.0, "THB", "2024-02-18"),
        ("SCBDV", "TH_MUTUAL_FUND", 2500, 12.4, "THB", "2024-03-01"),
        ("K-USA-A(A)", "TH_MUTUAL_FUND", 2000, 15.8, "THB", "2024-03-12"),
    ]
    pf_id = target_pf_id or (st.session_state.get("active_portfolio_id"))
    u_id = target_user_id or (st.session_state.get("authenticated_user", {}).get("id") if st.session_state.get("authenticated_user") else None)
    for sym, atype, qty, price, curr, pdate in samples:
        add_transaction(
            symbol=sym,
            asset_type=atype,
            quantity=qty,
            cost_per_share=price,
            currency=curr,
            purchase_date=pdate,
            portfolio_id=pf_id,
            user_id=u_id,
        )
    clear_price_cache()
    st.cache_data.clear()
    if "refresh_nonce" in st.session_state:
        st.session_state.refresh_nonce += 1


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
    
    /* Portfolio badge */
    .portfolio-badge {
        display: inline-block;
        padding: 0.35rem 0.85rem;
        background: linear-gradient(135deg, rgba(56, 189, 248, 0.15), rgba(129, 140, 248, 0.15));
        border: 1px solid rgba(56, 189, 248, 0.3);
        border-radius: 20px;
        color: #38bdf8;
        font-size: 0.9rem;
        font-weight: 600;
        margin-bottom: 1rem;
    }
    
    /* User Profile Card */
    .user-profile-box {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        padding: 0.75rem 1rem;
        margin-bottom: 1rem;
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


# ------------------------------------------------------------------------------
# 4. Session State Management
# ------------------------------------------------------------------------------
if "authenticated_user" not in st.session_state:
    st.session_state.authenticated_user = None

if "active_portfolio_id" not in st.session_state:
    st.session_state.active_portfolio_id = None

if "refresh_nonce" not in st.session_state:
    st.session_state.refresh_nonce = 0


# ==============================================================================
# 5. AUTHENTICATION PORTAL (FOR LOGGED-OUT USERS)
# ==============================================================================
if not st.session_state.authenticated_user:
    st.markdown('<div class="main-title" style="text-align: center;">Multi-Asset Portfolio Tracker</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-title" style="text-align: center;">Real-time tracking for US Stocks, Thai Stocks (.BK), and Thai Mutual Funds.</div>',
        unsafe_allow_html=True,
    )

    if db_init_error:
        st.warning(
            "⚠️ **Could not connect to PostgreSQL (Supabase). Operating in temporary local storage mode.**\n\n"
            "Configure `DATABASE_URL` in your Streamlit Cloud Secrets to use cloud persistence."
        )

    col_space_l, col_auth, col_space_r = st.columns([1, 1.8, 1])

    with col_auth:
        auth_tab_login, auth_tab_signup, auth_tab_demo = st.tabs(["🔑 Sign In", "✨ Create Account", "🚀 Quick Demo"])

        # TAB 1: LOGIN
        with auth_tab_login:
            st.markdown("#### Welcome Back")
            st.caption("Sign in to access your personal multi-asset portfolios.")

            login_identifier = st.text_input("Username or Email", key="login_ident")
            login_password = st.text_input("Password", type="password", key="login_pwd")

            if st.button("Sign In", type="primary", use_container_width=True, key="btn_signin"):
                if not login_identifier or not login_password:
                    st.error("Please enter both username/email and password.")
                else:
                    try:
                        user = authenticate_user(login_identifier, login_password)
                        if user:
                            st.session_state.authenticated_user = user
                            user_pfs = get_user_portfolios(user["id"])
                            if user_pfs:
                                st.session_state.active_portfolio_id = user_pfs[0]["id"]
                            st.success(f"Welcome back, **{user['username']}**!")
                            st.rerun()
                        else:
                            st.error("Invalid username/email or password. Please try again.")
                    except Exception as e:
                        st.error(f"Sign in error: {e}")

        # TAB 2: REGISTER
        with auth_tab_signup:
            st.markdown("#### Create Your Free Account")
            st.caption("Each account can create and manage multiple custom portfolios.")

            reg_username = st.text_input("Username", key="reg_uname", help="3 to 50 characters")
            reg_email = st.text_input("Email Address", key="reg_email")
            reg_password = st.text_input("Password", type="password", key="reg_pwd", help="Minimum 6 characters")
            reg_password_conf = st.text_input("Confirm Password", type="password", key="reg_pwd_conf")

            if st.button("Create Account", type="primary", use_container_width=True, key="btn_signup"):
                if not reg_username or not reg_email or not reg_password:
                    st.error("Please fill in all required fields.")
                elif reg_password != reg_password_conf:
                    st.error("Passwords do not match.")
                else:
                    try:
                        new_user = register_user(reg_username, reg_email, reg_password)
                        st.session_state.authenticated_user = new_user
                        st.session_state.active_portfolio_id = new_user.get("default_portfolio_id")
                        st.success(f"Account created successfully! Welcome, **{new_user['username']}**.")
                        st.rerun()
                    except (ValidationError, AuthenticationError) as err:
                        st.error(str(err))
                    except Exception as e:
                        st.error(f"Registration error: {e}")

        # TAB 3: DEMO GUEST LOGIN
        with auth_tab_demo:
            st.markdown("#### Instant Demo Access")
            st.caption("Try out the application immediately with a pre-configured demo account and sample holdings.")

            if st.button("🚀 Explore as Demo User", type="secondary", use_container_width=True, key="btn_demo"):
                try:
                    demo_user = authenticate_user("demo_user", "demo123456")
                    if not demo_user:
                        demo_user = register_user("demo_user", "demo@example.com", "demo123456")
                        demo_pf_id = demo_user.get("default_portfolio_id")
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
                                portfolio_id=demo_pf_id,
                                user_id=demo_user["id"],
                            )

                    st.session_state.authenticated_user = demo_user
                    user_pfs = get_user_portfolios(demo_user["id"])
                    if user_pfs:
                        st.session_state.active_portfolio_id = user_pfs[0]["id"]
                    st.success("Signed in as Demo User!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Demo login failed: {e}")

    st.stop()

else:
    # ==============================================================================
    # 6. AUTHENTICATED USER DASHBOARD
    # ==============================================================================
    current_user = st.session_state.authenticated_user
    user_id = current_user["id"]

    # Retrieve all user portfolios
    user_portfolios = get_user_portfolios(user_id)
    if not user_portfolios:
        default_pf = create_portfolio(user_id, "Main Portfolio", "Default portfolio")
        user_portfolios = [default_pf]
        st.session_state.active_portfolio_id = default_pf["id"]

    # Ensure valid active portfolio ID
    valid_pf_ids = [p["id"] for p in user_portfolios]
    if st.session_state.active_portfolio_id not in valid_pf_ids:
        st.session_state.active_portfolio_id = valid_pf_ids[0]

    active_pf_id = st.session_state.active_portfolio_id
    active_pf = next((p for p in user_portfolios if p["id"] == active_pf_id), user_portfolios[0])

    # ==============================================================================
    # SIDEBAR: USER PROFILE, PORTFOLIO SELECTOR, AND TRANSACTION FORM
    # ==============================================================================
    with st.sidebar:
        # User Profile Card
        st.markdown(
            f"""
            <div class="user-profile-box">
                <div style="font-size: 1.1rem; font-weight: 700; color: #f1f5f9;">👤 {current_user['username']}</div>
                <div style="font-size: 0.8rem; color: #94a3b8;">{current_user['email']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("🚪 Sign Out", use_container_width=True, type="secondary"):
            st.session_state.authenticated_user = None
            st.session_state.active_portfolio_id = None
            st.rerun()

        st.markdown("---")

        # Portfolio Switcher & Management
        st.markdown("### 📂 Active Portfolio")
        pf_options_map = {f"📁 {p['name']}": p["id"] for p in user_portfolios}
        selected_pf_label = st.selectbox(
            "Select Portfolio",
            options=list(pf_options_map.keys()),
            index=list(pf_options_map.values()).index(active_pf_id),
            label_visibility="collapsed",
        )
        if pf_options_map[selected_pf_label] != active_pf_id:
            st.session_state.active_portfolio_id = pf_options_map[selected_pf_label]
            st.cache_data.clear()
            st.session_state.refresh_nonce += 1
            st.rerun()

        # Portfolio Tools Expander (Create, Rename, Delete)
        with st.expander("⚙️ Manage Portfolios", expanded=False):
            st.markdown("##### ➕ Create New Portfolio")
            new_pf_name = st.text_input("Portfolio Name", key="new_pf_name_input", placeholder="e.g. Retirement Fund")
            new_pf_desc = st.text_input("Description (Optional)", key="new_pf_desc_input", placeholder="e.g. Long-term dividend assets")
            if st.button("Create Portfolio", type="primary", use_container_width=True):
                if new_pf_name.strip():
                    try:
                        created_pf = create_portfolio(user_id, new_pf_name, new_pf_desc)
                        st.session_state.active_portfolio_id = created_pf["id"]
                        st.success(f"Portfolio **{new_pf_name}** created!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error creating portfolio: {e}")
                else:
                    st.warning("Please enter a portfolio name.")

            st.markdown("---")
            st.markdown("##### ✏️ Edit Current Portfolio")
            edit_pf_name = st.text_input("Rename Portfolio", value=active_pf["name"], key="edit_pf_name")
            edit_pf_desc = st.text_input("Description", value=active_pf.get("description", ""), key="edit_pf_desc")
            if st.button("Save Changes", use_container_width=True):
                if edit_pf_name.strip():
                    try:
                        update_portfolio(active_pf_id, user_id, name=edit_pf_name, description=edit_pf_desc)
                        st.success("Portfolio updated!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Update failed: {e}")

            if len(user_portfolios) > 1:
                st.markdown("---")
                if st.button(f"🗑️ Delete '{active_pf['name']}'", type="secondary", use_container_width=True):
                    try:
                        delete_portfolio(active_pf_id, user_id)
                        remaining = get_user_portfolios(user_id)
                        st.session_state.active_portfolio_id = remaining[0]["id"]
                        st.warning(f"Deleted portfolio.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Delete failed: {e}")

        st.markdown("---")

        # Add Transaction Form
        st.markdown(f'<div class="sidebar-header">➕ Add Transaction</div>', unsafe_allow_html=True)
        st.caption(f"Adding transaction to **{active_pf['name']}**")

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

            options_list = [f"{s['symbol']} - {s['name']}" for s in registered_symbols]
            options_map = {f"{s['symbol']} - {s['name']}": s for s in registered_symbols}

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

                st.markdown(
                    f"""
                    <div class="asset-info-card">
                        <b>Selected:</b> {selected_symbol} ({selected_name})<br/>
                        <b>Type:</b> {selected_asset_type} | <b>Currency:</b> {selected_currency}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        with st.form("add_transaction_form", clear_on_submit=True):
            if entry_mode == "search":
                manual_symbol = st.text_input("Ticker Symbol", value=selected_symbol, help="Auto-populated from search.")
                target_asset_type = selected_asset_type
                target_currency = selected_currency
            else:
                manual_symbol = st.text_input("Ticker Symbol", placeholder="e.g. MSFT, BDMS.BK, SCBDV", help="Enter symbol.")
                target_asset_type = st.selectbox(
                    "Asset Type",
                    options=["US_STOCK", "TH_STOCK", "TH_MUTUAL_FUND"],
                    format_func=lambda x: {
                        "US_STOCK": "🇺🇸 US Stock / ETF",
                        "TH_STOCK": "🇹🇭 Thai Stock (.BK)",
                        "TH_MUTUAL_FUND": "🏦 Thai Mutual Fund",
                    }[x],
                )
                target_currency = "USD" if target_asset_type == "US_STOCK" else "THB"
                st.caption(f"Currency automatically set to **{target_currency}**")

            col_q, col_p = st.columns(2)
            with col_q:
                quantity = st.number_input("Quantity / Units", min_value=0.00000001, value=1.0, step=1.0, format="%.8f")
            with col_p:
                curr_label = "Cost/Share (USD)" if target_currency == "USD" else "Cost/Unit (THB)"
                cost_per_share = st.number_input(curr_label, min_value=0.000001, value=100.0, step=1.0, format="%.6f")

            purchase_date = st.date_input("Purchase Date", value=datetime.now().date(), max_value=datetime.now().date())
            submitted = st.form_submit_button("➕ Save Transaction", type="primary", use_container_width=True)

            if submitted:
                target_symbol = manual_symbol.strip().upper()
                if not target_symbol:
                    st.error("Please enter a valid ticker symbol.")
                else:
                    with st.spinner(f"Validating ticker '{target_symbol}'..."):
                        val_res = validate_symbol_detailed(
                            symbol=target_symbol,
                            asset_type=target_asset_type,
                            sec_api_key=SEC_API_KEY,
                        )

                    if not val_res["valid"]:
                        st.error(f"❌ Invalid symbol '{target_symbol}': {val_res.get('message', 'Market data not found.')}")
                    else:
                        try:
                            new_id = add_transaction(
                                symbol=target_symbol,
                                asset_type=target_asset_type,
                                quantity=quantity,
                                cost_per_share=cost_per_share,
                                currency=target_currency,
                                purchase_date=purchase_date.strftime("%Y-%m-%d"),
                                portfolio_id=active_pf_id,
                                user_id=user_id,
                            )
                            clear_price_cache()
                            st.cache_data.clear()
                            st.session_state.refresh_nonce += 1
                            st.success(f"Added transaction #{new_id} for **{target_symbol}** to **{active_pf['name']}**!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error saving transaction: {e}")

        st.markdown("---")

        # Live FX Rate Card
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
            if st.button(f"📥 Load Sample Data to '{active_pf['name']}'", use_container_width=True):
                load_sample_portfolio_data(active_pf_id, user_id)
                st.success(f"Sample portfolio data loaded into **{active_pf['name']}**!")
                st.rerun()

            if st.button("🔄 Sync Symbol Database (SEC/NASDAQ)", use_container_width=True):
                with st.spinner("Updating ticker database from SEC/NASDAQ..."):
                    cnt = update_ticker_cache(db_path=DEFAULT_DB_PATH, force=True)
                    st.success(f"Symbol registry updated ({cnt:,} symbols cached)!")
                    st.rerun()

            if st.button(f"🗑️ Clear '{active_pf['name']}' Transactions", use_container_width=True, type="secondary"):
                clear_all_transactions(portfolio_id=active_pf_id, user_id=user_id)
                clear_price_cache()
                st.cache_data.clear()
                st.session_state.refresh_nonce += 1
                st.warning(f"All transactions in **{active_pf['name']}** deleted.")
                st.rerun()


    # ==============================================================================
    # MAIN DASHBOARD CONTENT (SCOPED TO ACTIVE PORTFOLIO)
    # ==============================================================================

    # Title and Header
    st.markdown('<div class="main-title">Multi-Asset Portfolio Tracker</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="portfolio-badge">📂 Active Portfolio: <b>{active_pf["name"]}</b>'
        f'{" — " + active_pf["description"] if active_pf.get("description") else ""}</div>',
        unsafe_allow_html=True,
    )

    if db_init_error:
        st.warning(
            "⚠️ **Could not connect to PostgreSQL (Supabase). Currently using temporary local fallback storage.**\n\n"
            "**To connect your Supabase database in Streamlit Community Cloud:**\n"
            "1. Go to your **Streamlit App Dashboard** -> Click **Manage app** (bottom-right) -> **App Settings** -> **Secrets**.\n"
            "2. Add your **Supabase Connection Pooler URI** (IPv4):\n"
            "   ```toml\n"
            "   DATABASE_URL = \"postgresql://postgres.[YOUR-PROJECT-REF]:[YOUR-PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres\"\n"
            "   ```\n"
            "3. *Tip: Use port `6543` and ensure special characters in your password (e.g. `@`, `#`, `%`) are URL-encoded.*"
        )

    # Fetch transactions scoped to active portfolio
    transactions = get_all_transactions(portfolio_id=active_pf_id, as_dataframe=False)

    if not transactions:
        st.info(
            f"👋 **Portfolio '{active_pf['name']}' is currently empty.**\n\n"
            "**To get started:**\n"
            "- ➕ **Add Transactions**: Use the transaction form in the sidebar on the left.\n"
            f"- 📥 **Sample Portfolio**: Open **'Database Tools & Seed Data'** in the sidebar and click **'Load Sample Data to \\'{active_pf['name']}\\'** to explore with pre-loaded demo holdings."
        )
        st.stop()

    # Perform cached portfolio calculations
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
            delta=f"{total_return_pct:+.2f}% Overall",
            help="Total current market value of all assets converted into THB.",
        )

    with kpi2:
        st.metric(
            label="Total Invested Capital",
            value=f"฿{total_cost_thb:,.2f}",
            help="Total cost basis of all purchased assets converted into THB.",
        )

    with kpi3:
        pnl_prefix = "+" if total_pnl_thb >= 0 else ""
        st.metric(
            label="Total Unrealized P&L",
            value=f"{pnl_prefix}฿{total_pnl_thb:,.2f}",
            delta=f"{total_return_pct:+.2f}%",
            help="Total profit or loss in Thai Baht.",
        )

    with kpi4:
        st.metric(
            label="Active Holdings",
            value=f"{len(holdings)} Assets",
            help="Number of distinct assets currently held.",
        )

    with kpi5:
        st.metric(
            label="USD / THB Rate",
            value=f"฿{usd_rate:,.2f}",
            help="Live USD to THB exchange rate used for conversions.",
        )

    st.markdown("---")

    # ==============================================================================
    # DASHBOARD TABS
    # ==============================================================================
    tab_charts, tab_holdings, tab_history = st.tabs([
        "📊 Overview & Allocation",
        "📋 Holdings Breakdown",
        "📜 Transaction History",
    ])

    # ------------------------------------------------------------------------------
    # TAB 1: VISUALIZATIONS & ALLOCATION BREAKDOWN
    # ------------------------------------------------------------------------------
    with tab_charts:
        holdings_df = pd.DataFrame(holdings)

        if not holdings_df.empty:
            us_val = holdings_df[holdings_df["asset_type"] == "US_STOCK"]["market_value_thb"].sum() if "asset_type" in holdings_df.columns else 0.0
            th_stock_val = holdings_df[holdings_df["asset_type"] == "TH_STOCK"]["market_value_thb"].sum() if "asset_type" in holdings_df.columns else 0.0
            th_fund_val = holdings_df[holdings_df["asset_type"] == "TH_MUTUAL_FUND"]["market_value_thb"].sum() if "asset_type" in holdings_df.columns else 0.0

            c1, c2, c3 = st.columns(3)
            with c1:
                us_pct = (us_val / total_val_thb * 100) if total_val_thb > 0 else 0
                st.metric("🇺🇸 US Stocks & ETFs", f"฿{us_val:,.2f}", f"{us_pct:.1f}% of Portfolio")
            with c2:
                th_pct = (th_stock_val / total_val_thb * 100) if total_val_thb > 0 else 0
                st.metric("🇹🇭 Thai Stocks", f"฿{th_stock_val:,.2f}", f"{th_pct:.1f}% of Portfolio")
            with c3:
                fund_pct = (th_fund_val / total_val_thb * 100) if total_val_thb > 0 else 0
                st.metric("🏦 Thai Mutual Funds", f"฿{th_fund_val:,.2f}", f"{fund_pct:.1f}% of Portfolio")

            st.markdown("<br/>", unsafe_allow_html=True)
            col_pie, col_bar = st.columns([1.1, 1.2])

            with col_pie:
                st.markdown("#### 🎯 Asset Allocation")
                pie_fig = px.pie(
                    holdings_df,
                    values="market_value_thb",
                    names="symbol",
                    hole=0.55,
                    color_discrete_sequence=px.colors.qualitative.Prism,
                )
                pie_fig.update_traces(
                    textposition="inside",
                    textinfo="percent+label",
                    hovertemplate="<b>%{label}</b><br>Value: ฿%{value:,.2f}<br>Weight: %{percent:.1%}<extra></extra>",
                )
                pie_fig.update_layout(
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
                    margin=dict(t=20, b=30, l=10, r=10),
                    height=340,
                    annotations=[
                        dict(
                            text=f"Total<br><b>฿{total_val_thb/1000:,.1f}k</b>",
                            x=0.5,
                            y=0.5,
                            font_size=15,
                            showarrow=False,
                        )
                    ],
                )
                st.plotly_chart(pie_fig, use_container_width=True)

            with col_bar:
                st.markdown("#### 📈 Unrealized Gain / Loss by Asset")
                sorted_holdings = holdings_df.sort_values(by="unrealized_pnl_thb", ascending=True)
                bar_colors = ["#22c55e" if x >= 0 else "#ef4444" for x in sorted_holdings["unrealized_pnl_thb"]]

                bar_fig = go.Figure(
                    go.Bar(
                        x=sorted_holdings["unrealized_pnl_thb"],
                        y=sorted_holdings["symbol"],
                        orientation="h",
                        marker=dict(color=bar_colors, cornerradius=4),
                        text=[f"{val:+,.0f} ฿ ({pct:+.1f}%)" for val, pct in zip(sorted_holdings["unrealized_pnl_thb"], sorted_holdings["unrealized_pnl_percent"])],
                        textposition="auto",
                        hovertemplate="<b>%{y}</b><br>P&L: ฿%{x:,.2f}<extra></extra>",
                    )
                )
                bar_fig.update_layout(
                    xaxis_title="Unrealized P&L (THB)",
                    yaxis_title="",
                    margin=dict(t=20, b=20, l=10, r=10),
                    height=340,
                )
                st.plotly_chart(bar_fig, use_container_width=True)


    # ------------------------------------------------------------------------------
    # TAB 2: HOLDINGS BREAKDOWN TABLE
    # ------------------------------------------------------------------------------
    with tab_holdings:
        st.markdown(f"### 📋 Holdings Breakdown — {active_pf['name']}")

        if holdings:
            display_holdings = []
            for h in holdings:
                curr_sym = "$" if h["currency"] == "USD" else "฿"
                display_holdings.append({
                    "Symbol": h["symbol"],
                    "Asset Type": h["asset_type"],
                    "Units": h["total_quantity"],
                    "Avg Cost": f"{curr_sym}{h['avg_cost_per_share']:,.2f}",
                    "Current Price": f"{curr_sym}{h['current_price']:,.2f}",
                    "Cost Basis (THB)": h["total_cost_thb"],
                    "Market Value (THB)": h["market_value_thb"],
                    "P&L (THB)": h["unrealized_pnl_thb"],
                    "Return %": h["unrealized_pnl_percent"],
                    "Weight %": h["weight_percent"],
                })

            df_display = pd.DataFrame(display_holdings)

            filter_col, _ = st.columns([2, 3])
            with filter_col:
                market_filter = st.segmented_control(
                    "Filter by Market",
                    options=["All", "US Stocks", "Thai Stocks", "Thai Funds"],
                    default="All",
                    label_visibility="collapsed",
                )

            if market_filter == "US Stocks":
                df_display = df_display[df_display["Asset Type"] == "US_STOCK"]
            elif market_filter == "Thai Stocks":
                df_display = df_display[df_display["Asset Type"] == "TH_STOCK"]
            elif market_filter == "Thai Funds":
                df_display = df_display[df_display["Asset Type"] == "TH_MUTUAL_FUND"]

            st.dataframe(
                df_display,
                column_config={
                    "Symbol": st.column_config.TextColumn("Symbol", width="medium"),
                    "Asset Type": st.column_config.TextColumn("Type", width="small"),
                    "Units": st.column_config.NumberColumn("Quantity", format="%.6f"),
                    "Avg Cost": st.column_config.TextColumn("Avg Cost", width="small"),
                    "Current Price": st.column_config.TextColumn("Price", width="small"),
                    "Cost Basis (THB)": st.column_config.NumberColumn("Cost (THB)", format="฿%.2f"),
                    "Market Value (THB)": st.column_config.NumberColumn("Value (THB)", format="฿%.2f"),
                    "P&L (THB)": st.column_config.NumberColumn("P&L (THB)", format="฿%.2f"),
                    "Return %": st.column_config.NumberColumn("Return %", format="%+.2f%%"),
                    "Weight %": st.column_config.NumberColumn("Weight", format="%.1f%%"),
                },
                hide_index=True,
                use_container_width=True,
            )


    # ------------------------------------------------------------------------------
    # TAB 3: TRANSACTION HISTORY & EDITING
    # ------------------------------------------------------------------------------
    with tab_history:
        st.markdown(f"### 📜 Recorded Transactions — {active_pf['name']}")
        tx_df = pd.DataFrame(transactions)

        if not tx_df.empty:
            col_table, col_del = st.columns([2.6, 1.4])

            with col_table:
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

                tx_options_map = {}
                for _, row in tx_df.iterrows():
                    curr_symbol = "$" if row["currency"] == "USD" else "฿"
                    label = f"{row['symbol']} — {row['quantity']:g} @ {curr_symbol}{row['cost_per_share']:g} ({row['purchase_date']})"
                    tx_options_map[label] = int(row["id"])

                selected_tx_label = st.selectbox(
                    "Select Transaction to Delete",
                    options=list(tx_options_map.keys()),
                    index=len(tx_options_map) - 1,
                    help="Select the transaction you wish to delete.",
                )

                if st.button("🗑️ Delete Selected Record", type="secondary", use_container_width=True):
                    target_del_id = tx_options_map[selected_tx_label]
                    if delete_transaction(target_del_id, portfolio_id=active_pf_id, user_id=user_id):
                        clear_price_cache()
                        st.cache_data.clear()
                        st.session_state.refresh_nonce += 1
                        st.success(f"Removed: **{selected_tx_label}**")
                        st.rerun()
