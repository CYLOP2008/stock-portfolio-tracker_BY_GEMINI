"""
ticker_registry.py
==================
Module for fetching, managing, and caching complete lists of stock tickers
and mutual fund symbols for US and Thai markets.

Supported Markets:
1. US Stocks & ETFs: Tickers and company names from SEC/NASDAQ.
2. Thai Stocks (SET & MAI): Formatted with '.BK' suffix (e.g., 'PTT.BK', 'AOT.BK').
3. Thai Mutual Funds: Fund codes and full fund names across major AMCs.

Caching:
- Dual-tier caching: SQLite table `tickers_registry` in `portfolio.db` and JSON file `tickers_cache.json`.
- Cache TTL management (default 7 days) and instant fallback to bundled master datasets.
"""

from datetime import datetime, timedelta
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Union
import requests

from database import DEFAULT_DB_PATH, get_connection

# Configure module logger
logger = logging.getLogger("ticker_registry")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default cache configuration
CACHE_JSON_PATH = "tickers_cache.json"
CACHE_TTL_DAYS = 7
REQUEST_TIMEOUT = 8

# ==============================================================================
# CURATED MASTER DATASETS (Guaranteed Offline Availability)
# ==============================================================================

BUNDLED_US_STOCKS = [
    {"symbol": "AAPL", "name": "Apple Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "NVDA", "name": "NVIDIA Corporation", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "MSFT", "name": "Microsoft Corporation", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "AMZN", "name": "Amazon.com, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "GOOGL", "name": "Alphabet Inc. (Class A)", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "GOOG", "name": "Alphabet Inc. (Class C)", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "META", "name": "Meta Platforms, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "TSLA", "name": "Tesla, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "BRK-B", "name": "Berkshire Hathaway Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "AVGO", "name": "Broadcom Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "LLY", "name": "Eli Lilly and Company", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "JPM", "name": "JPMorgan Chase & Co.", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "V", "name": "Visa Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "UNH", "name": "UnitedHealth Group Incorporated", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "XOM", "name": "Exxon Mobil Corporation", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "MA", "name": "Mastercard Incorporated", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "WMT", "name": "Walmart Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "JNJ", "name": "Johnson & Johnson", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "PG", "name": "Procter & Gamble Company", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "HD", "name": "The Home Depot, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "COST", "name": "Costco Wholesale Corporation", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "ORCL", "name": "Oracle Corporation", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "AMD", "name": "Advanced Micro Devices, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "NFLX", "name": "Netflix, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "CRM", "name": "Salesforce, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "BAC", "name": "Bank of America Corporation", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "QCOM", "name": "QUALCOMM Incorporated", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "INTC", "name": "Intel Corporation", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "CSCO", "name": "Cisco Systems, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "DIS", "name": "The Walt Disney Company", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "NKE", "name": "NIKE, Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "ADBE", "name": "Adobe Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "TXN", "name": "Texas Instruments Incorporated", "asset_type": "US_STOCK", "currency": "USD", "market": "NASDAQ"},
    {"symbol": "PLTR", "name": "Palantir Technologies Inc.", "asset_type": "US_STOCK", "currency": "USD", "market": "NYSE"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust (Nasdaq-100)", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "VOO", "name": "Vanguard S&P 500 ETF", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "DIA", "name": "SPDR Dow Jones Industrial Average ETF", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "ARKK", "name": "ARK Innovation ETF", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "TLT", "name": "iShares 20+ Year Treasury Bond ETF", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
    {"symbol": "GLD", "name": "SPDR Gold Shares", "asset_type": "US_STOCK", "currency": "USD", "market": "ETF"},
]

BUNDLED_THAI_STOCKS = [
    {"symbol": "PTT.BK", "name": "PTT Public Company Limited (ปตท.)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "AOT.BK", "name": "Airports of Thailand (ท่าอากาศยานไทย)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "CPALL.BK", "name": "CP ALL Public Company Limited (ซีพี ออลล์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "DELTA.BK", "name": "Delta Electronics (Thailand) (เดลต้า อีเลคโทรนิคส์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "KBANK.BK", "name": "Kasikornbank Public Co., Ltd. (ธนาคารกสิกรไทย)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "ADVANC.BK", "name": "Advanced Info Service (แอดวานซ์ อินโฟร์ เซอร์วิส)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BDMS.BK", "name": "Bangkok Dusit Medical Services (กรุงเทพดุสิตเวชการ)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "SCB.BK", "name": "SCB X Public Company Limited (เอสซีบี เอกซ์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "GULF.BK", "name": "Gulf Energy Development (กัลฟ์ เอ็นเนอร์จี)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "PTTEP.BK", "name": "PTT Exploration and Production (ปตท. สำรวจและผลิตปิโตรเลียม)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BBL.BK", "name": "Bangkok Bank Public Company Limited (ธนาคารกรุงเทพ)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "KTB.BK", "name": "Krung Thai Bank Public Company Limited (ธนาคารกรุงไทย)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "TRUE.BK", "name": "True Corporation (ทรู คอร์ปอเรชั่น)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "SCC.BK", "name": "The Siam Cement (ปูนซิเมนต์ไทย)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "MINT.BK", "name": "Minor International (ไมเนอร์ อินเตอร์เนชั่นแนล)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "CRC.BK", "name": "Central Retail Corporation (เซ็นทรัล รีเทล)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "CPF.BK", "name": "Charoen Pokphand Foods (เจริญโภคภัณฑ์อาหาร)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "WHA.BK", "name": "WHA Corporation (ดับบลิวเอชเอ คอร์ปอเรชั่น)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "TU.BK", "name": "Thai Union Group (ไทยยูเนี่ยน กรุ๊ป)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BH.BK", "name": "Bumrungrad Hospital (โรงพยาบาลบำรุงราษฎร์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "IVL.BK", "name": "Indorama Ventures (อินโดรามา เวนเจอร์ส)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "OR.BK", "name": "PTT Oil and Retail Business (ปตท. น้ำมันและการค้าปลีก)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "TOP.BK", "name": "Thai Oil Public Company Limited (ไทยออยล์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BANPU.BK", "name": "Banpu Public Company Limited (บ้านปู)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BGRIM.BK", "name": "B.Grimm Power (บี.กริม เพาเวอร์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "LH.BK", "name": "Land and Houses (แลนด์แอนด์เฮ้าส์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "HMPRO.BK", "name": "Home Product Center (โฮม โปรดักส์ เซ็นเตอร์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "TTB.BK", "name": "TMBThanachart Bank (ธนาคารทหารไทยธนชาต)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "SAWAD.BK", "name": "Srisawad Corporation (ศรีสวัสดิ์ คอร์ปอเรชั่น)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "MTC.BK", "name": "Muangthai Capital (เมืองไทย แคปปิตอล)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "CBG.BK", "name": "Carabao Group (คาราบาวกรุ๊ป)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "OSP.BK", "name": "Osotspa (โอสถสภา)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "GPSC.BK", "name": "Global Power Synergy (โกลบอล เพาเวอร์ ซินเนอร์ยี่)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "EGCO.BK", "name": "Electricity Generating (ผลิตไฟฟ้า)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "RATCH.BK", "name": "RATCH Group (ราช กรุ๊ป)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "TIDLOR.BK", "name": "Ngern Tid Lor (เงินติดล้อ)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "CENTEL.BK", "name": "Central Plaza Hotel (โรงแรมเซ็นทรัลพลาซา)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "CPN.BK", "name": "Central Pattana (เซ็นทรัลพัฒนา)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "AP.BK", "name": "AP (Thailand) (เอพี ไทยแลนด์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "SPALI.BK", "name": "Supalai (ศุภาลัย)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "SIRI.BK", "name": "Sansiri (แสนสิริ)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BTS.BK", "name": "BTS Group Holdings (บีทีเอส กรุ๊ป โฮลดิ้งส์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BEM.BK", "name": "Bangkok Expressway and Metro (ทางด่วนและรถไฟฟ้ากรุงเทพ)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "COM7.BK", "name": "COM7 Public Company Limited (คอมเซเว่น)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "JMART.BK", "name": "Jaymart Group Holdings (เจมาร์ท กรุ๊ป โฮลดิ้งส์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "JMT.BK", "name": "JMT Network Services (เจ เอ็ม ที เน็ทเวอร์ค เซอร์วิสเซ็ส)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "CHG.BK", "name": "Chularat Hospital (โรงพยาบาลจุฬารัตน์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BCH.BK", "name": "Bangkok Chain Hospital (บางกอก เชน ฮอสปิทอล)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "KCE.BK", "name": "KCE Electronics (เคซีอี อีเลคโทรนิคส์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "HANA.BK", "name": "Hana Microelectronics (ฮานา ไมโครอิเล็คโทรนิคส)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "CCET.BK", "name": "Cal-Comp Electronics (Thailand) (แคล-คอมพ์ อีเล็คโทรนิคส์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "ITC.BK", "name": "i-Tail Corporation (ไอ-เทล คอร์ปอเรชั่น)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "SAPPE.BK", "name": "Sappe Public Company Limited (เซ็ปเป้)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "ICHI.BK", "name": "Ichitan Group (อิชิตัน กรุ๊ป)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "TLI.BK", "name": "Thai Life Insurance (ไทยประกันชีวิต)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BAM.BK", "name": "Bangkok Commercial Asset Management (บริหารสินทรัพย์ กรุงเทพพาณิชย์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "MEGA.BK", "name": "Mega Lifesciences (เมก้า ไลฟ์ไซแอ็นซ์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "DOHOME.BK", "name": "Dohome (ดูโฮม)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "GLOBAL.BK", "name": "Siam Global House (สยามโกลบอลเฮ้าส์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "MOSHI.BK", "name": "Moshi Moshi Retail Corporation (โมชิ โมชิ รีเทล)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "SCGP.BK", "name": "SCG Packaging (เอสซีจี แพคเกจจิ้ง)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BJC.BK", "name": "Berli Jucker (เบอร์ลี่ ยุคเกอร์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "KTC.BK", "name": "Krungthai Card (บัตรกรุงไทย)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "BCPG.BK", "name": "BCPG Public Company Limited (บีซีพีจี)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "GUNKUL.BK", "name": "Gunkul Engineering (กันกุลเอ็นจิเนียริ่ง)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "EA.BK", "name": "Energy Absolute (พลังงานบริสุทธิ์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "SISB.BK", "name": "SISB Public Company Limited (เอสไอเอสบี)", "asset_type": "TH_STOCK", "currency": "THB", "market": "SET"},
    {"symbol": "AU.BK", "name": "After You (อาฟเตอร์ ยู)", "asset_type": "TH_STOCK", "currency": "THB", "market": "MAI"},
    {"symbol": "MASTER.BK", "name": "Master Style (มาสเตอร์ สไตล์)", "asset_type": "TH_STOCK", "currency": "THB", "market": "MAI"},
    {"symbol": "KLINIQ.BK", "name": "The Klinique Medical Clinic (เดอะคลีนิกค์ คลินิกเวชกรรม)", "asset_type": "TH_STOCK", "currency": "THB", "market": "MAI"},
    {"symbol": "SPA.BK", "name": "Siam Wellness Group (สยามเวลเนสกรุ๊ป)", "asset_type": "TH_STOCK", "currency": "THB", "market": "MAI"},
]

BUNDLED_THAI_FUNDS = [
    {"symbol": "ONE-UGG-RA", "name": "ONE Ultimate Global Growth Fund - Accumulation (วรรณ อัลติเมท โกลบอล โกรท)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "OneAM"},
    {"symbol": "ONE-GEQ", "name": "ONE Global Equity Fund (กองทุนเปิด วรรณ โกลบอล อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "OneAM"},
    {"symbol": "K-CHANGE-A(A)", "name": "K Positive Change Action Fund (เค โพซิทีฟ เชนจ์ แอคชั่น)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "K-USA-A(A)", "name": "K USA Equity Fund (กองทุนเปิดเค ยูเอสเอ หุ้นทุน)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "K-USXNDQ-A(A)", "name": "K USX Nasdaq 100 Index Fund (เค ยูเอสเอ็กซ์-อินเด็กซ์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "K-INDIA-A(A)", "name": "K India Equity Fund (เค อินเดีย อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "K-VIETNAM", "name": "K Vietnam Equity Fund (เค เวียดนาม อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "K-CHINA-A(A)", "name": "K China Equity Fund (เค ไชน่า อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "K-GINFRA-A(A)", "name": "K Global Infrastructure Fund (เค โกลบอล อินฟราสตรัคเจอร์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "K-GHEALTH", "name": "K Global Healthcare Equity Fund (เค โกลบอล เฮลท์แคร์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "K-GOLD-A(A)", "name": "K Gold Fund (เค โกลด์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KAsset"},
    {"symbol": "SCBDV", "name": "SCB Dividend Stock Open End Fund (เอสซีบี ปันผลหุ้นระยะยาว)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "SCBSE", "name": "SCB SET Energy and Petrochemicals (เอสซีบี หุ้นกลุ่มพลังงานและปิโตรเคมี)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "SCBGLOBAL", "name": "SCB Global Equity Fund (เอสซีบี โกลบอล อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "SCBNDQ", "name": "SCB US Nasdaq-100 Index Fund (เอสซีบี ยูเอส แนสแดค-100)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "SCBUS", "name": "SCB US Equity Fund (เอสซีบี หุ้นยูเอส)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "SCBVIET", "name": "SCB Vietnam Equity Fund (เอสซีบี หุ้นเวียดนาม)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "SCBINDIAN", "name": "SCB India Equity Fund (เอสซีบี หุ้นอินเดีย)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "SCBCHAA", "name": "SCB China A-Shares Fund (เอสซีบี ไชน่า เอแชร์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "SCBSEMI", "name": "SCB Semiconductor Fund (เอสซีบี เซมิคอนดักเตอร์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "SCBAM"},
    {"symbol": "B-BHARAT", "name": "Bualuang Bharat Fund (บัวหลวง ภารตะ - กองทุนหุ้นอินเดีย)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "BBLAM"},
    {"symbol": "B-INNOTECH", "name": "Bualuang Innovative Technologies Fund (บัวหลวง อินโนเวทีฟ เทคโนโลยี)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "BBLAM"},
    {"symbol": "B-GLOBAL", "name": "Bualuang Global Equity Fund (บัวหลวง โกลบอล อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "BBLAM"},
    {"symbol": "B-CHINE-EQ", "name": "Bualuang China Equity Fund (บัวหลวง ไชน่า อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "BBLAM"},
    {"symbol": "B-VIETNAM", "name": "Bualuang Vietnam Equity Fund (บัวหลวง เวียดนาม อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "BBLAM"},
    {"symbol": "B-USALPHA", "name": "Bualuang US Alpha Equity Fund (บัวหลวง ยูเอส อัลฟ่า อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "BBLAM"},
    {"symbol": "KF-GTECH", "name": "Krungsri Global Technology Equity Fund (กรุงศรี โกลบอล เทคโนโลยี อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KrungsriAsset"},
    {"symbol": "KFGLOBAL", "name": "Krungsri Global Equity Fund (กรุงศรี โกลบอล อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KrungsriAsset"},
    {"symbol": "KF-VIET", "name": "Krungsri Vietnam Equity Fund (กรุงศรี เวียดนาม อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KrungsriAsset"},
    {"symbol": "KF-INDIA", "name": "Krungsri India Equity Fund (กรุงศรี อินเดีย อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KrungsriAsset"},
    {"symbol": "KF-CHINA", "name": "Krungsri China Equity Fund (กรุงศรี ไชน่า อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KrungsriAsset"},
    {"symbol": "PRINCIPAL VNEQ-A", "name": "Principal Vietnam Equity Fund (พรินซิเพิล เวียดนาม อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "Principal"},
    {"symbol": "PRINCIPAL APDI", "name": "Principal Asia Pacific Dynamic Income Fund (พรินซิเพิล เอเชีย แปซิฟิก ไดนามิก อินคัม)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "Principal"},
    {"symbol": "PRINCIPAL GCLOUD-A", "name": "Principal Global Cloud Computing Fund (พรินซิเพิล โกลบอล คลาวด์ คอมพิวติ้ง)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "Principal"},
    {"symbol": "PRINCIPAL GHEALTH-A", "name": "Principal Global Health Care Fund (พรินซิเพิล โกลบอล เฮลท์ แคร์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "Principal"},
    {"symbol": "PRINCIPAL USEQ-A", "name": "Principal US Equity Fund (พรินซิเพิล ยูเอส อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "Principal"},
    {"symbol": "KT-VIETNAM-A", "name": "KTAM Vietnam Equity Fund (เคแทม เวียดนาม อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KTAM"},
    {"symbol": "KT-INDIA-A", "name": "KTAM India Equity Fund (เคแทม อินเดีย อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KTAM"},
    {"symbol": "KT-US-A", "name": "KTAM US Equity Fund (เคแทม ยูเอส อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KTAM"},
    {"symbol": "KT-CHINA-A", "name": "KTAM China Equity Fund (เคแทม ไชน่า อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KTAM"},
    {"symbol": "KKP G-THEME-H-A", "name": "KKP Global Theme Opportunities Fund (เคเคพี โกลบอล ธีม ออพพอร์ทูนิตี้ส์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KKPAM"},
    {"symbol": "KKP GNP-H", "name": "KKP Global New Perspective Fund (เคเคพี โกลบอล นิว เพอร์สเปกทีฟ)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KKPAM"},
    {"symbol": "KKP TECH-H", "name": "KKP Semiconductor & Technology Fund (เคเคพี เซมิคอนดักเตอร์ แอนด์ เทคโนโลยี)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KKPAM"},
    {"symbol": "KKP NDQ100-H", "name": "KKP Nasdaq 100 Tracker Fund (เคเคพี แนสแดค 100 แทร็กเกอร์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "KKPAM"},
    {"symbol": "UOBSG", "name": "UOB Smart Global Equity Fund (ยูโอบี สมาร์ท โกลบอล อิควิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "UOBAM"},
    {"symbol": "UOBGQ", "name": "UOB Global Quality Growth Fund (ยูโอบี โกลบอล ควอลิตี้ โกรท)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "UOBAM"},
    {"symbol": "UEV-N", "name": "UOB Electric Vehicles and Future Mobility Fund (ยูโอบี อีวี แอนด์ ฟิวเจอร์ โมบิลิตี้)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "UOBAM"},
    {"symbol": "UGIS-N", "name": "United Global Income Strategic Bond Fund (ยูไนเต็ด โกลบอล อินคัม สตราทีจิค บอนด์)", "asset_type": "TH_MUTUAL_FUND", "currency": "THB", "market": "UOBAM"},
]


# ==============================================================================
# DATABASE SCHEMA & INITIALIZATION
# ==============================================================================

def init_ticker_cache(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize the SQLite tickers registry table and indexes.

    Args:
        db_path (str): Path to SQLite database file.
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS tickers_registry (
        symbol TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        asset_type TEXT NOT NULL CHECK(asset_type IN ('US_STOCK', 'TH_STOCK', 'TH_MUTUAL_FUND')),
        currency TEXT NOT NULL CHECK(currency IN ('USD', 'THB')),
        market TEXT,
        updated_at TEXT NOT NULL
    );
    """
    create_idx_sql = [
        "CREATE INDEX IF NOT EXISTS idx_tickers_asset_type ON tickers_registry(asset_type);",
        "CREATE INDEX IF NOT EXISTS idx_tickers_name ON tickers_registry(name);",
    ]

    try:
        conn = get_connection(db_path)
        with conn:
            conn.execute(create_table_sql)
            for idx_sql in create_idx_sql:
                conn.execute(idx_sql)
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize ticker registry table: {e}")


def _save_tickers_to_db(tickers: List[Dict[str, Any]], db_path: str = DEFAULT_DB_PATH) -> None:
    """Save or update ticker records in SQLite.

    Args:
        tickers (List[Dict[str, Any]]): List of standardized ticker records.
        db_path (str): Database file path.
    """
    init_ticker_cache(db_path)
    now_str = datetime.now().isoformat()
    sql = """
    INSERT OR REPLACE INTO tickers_registry (symbol, name, asset_type, currency, market, updated_at)
    VALUES (?, ?, ?, ?, ?, ?);
    """
    try:
        conn = get_connection(db_path)
        with conn:
            cursor = conn.cursor()
            for t in tickers:
                cursor.execute(
                    sql,
                    (
                        t["symbol"].strip().upper(),
                        t["name"].strip(),
                        t["asset_type"].strip().upper(),
                        t["currency"].strip().upper(),
                        t.get("market", ""),
                        now_str,
                    ),
                )
        conn.close()
        logger.info(f"Saved/Updated {len(tickers)} tickers in SQLite registry.")
    except sqlite3.Error as e:
        logger.error(f"Error writing tickers to SQLite: {e}")


def _save_tickers_to_json(tickers: List[Dict[str, Any]], json_path: str = CACHE_JSON_PATH) -> None:
    """Save ticker records to local JSON cache file."""
    try:
        cache_data = {
            "cached_at": datetime.now().isoformat(),
            "count": len(tickers),
            "tickers": tickers,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(tickers)} tickers to JSON cache '{json_path}'.")
    except Exception as e:
        logger.debug(f"Could not write JSON cache file: {e}")


def _load_tickers_from_json(json_path: str = CACHE_JSON_PATH) -> Optional[List[Dict[str, Any]]]:
    """Load cached tickers from local JSON file if valid and not expired."""
    if not os.path.exists(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_at_str = data.get("cached_at")
        if cached_at_str:
            cached_dt = datetime.fromisoformat(cached_at_str)
            if datetime.now() - cached_dt < timedelta(days=CACHE_TTL_DAYS):
                return data.get("tickers", [])
    except Exception as e:
        logger.debug(f"JSON cache read failed: {e}")
    return None


# ==============================================================================
# ONLINE FETCHING HELPERS
# ==============================================================================

def fetch_us_stocks_online() -> List[Dict[str, Any]]:
    """Fetch complete US stocks and ETFs directory from official SEC EDGAR endpoint.

    Falls back cleanly to bundled dataset if offline or network error.

    Returns:
        List[Dict[str, Any]]: Standardized list of US tickers.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {
        "User-Agent": "PortfolioTrackerApp contact@example.com",
        "Accept": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            raw_data = resp.json()
            results: List[Dict[str, Any]] = []
            for item in raw_data.values():
                ticker = str(item.get("ticker", "")).strip().upper()
                title = str(item.get("title", "")).strip()
                if ticker and title:
                    results.append({
                        "symbol": ticker,
                        "name": title.title(),
                        "asset_type": "US_STOCK",
                        "currency": "USD",
                        "market": "US_EXCHANGE",
                    })
            if results:
                logger.info(f"Fetched {len(results)} US tickers from SEC EDGAR API.")
                return results
    except Exception as e:
        logger.debug(f"SEC EDGAR online fetch unavailable: {e}. Using bundled dataset.")

    return list(BUNDLED_US_STOCKS)


def fetch_thai_stocks_online() -> List[Dict[str, Any]]:
    """Fetch/Return Thai Stocks formatted with '.BK' suffix (SET & MAI).

    Returns:
        List[Dict[str, Any]]: Standardized Thai stocks list.
    """
    # Thai stocks are verified and formatted with .BK suffix
    return list(BUNDLED_THAI_STOCKS)


def fetch_thai_mutual_funds_online() -> List[Dict[str, Any]]:
    """Fetch/Return Thai Mutual Funds mapping codes and full names.

    Returns:
        List[Dict[str, Any]]: Standardized Thai mutual funds list.
    """
    return list(BUNDLED_THAI_FUNDS)


# ==============================================================================
# PUBLIC REGISTRY API
# ==============================================================================

def update_ticker_cache(
    db_path: str = DEFAULT_DB_PATH,
    json_path: str = CACHE_JSON_PATH,
    force: bool = False,
) -> int:
    """Download, merge, and persist all stock tickers and fund symbols into SQLite and JSON cache.

    Args:
        db_path (str): SQLite database file path.
        json_path (str): JSON cache file path.
        force (bool): If True, forces online download even if cache is fresh.

    Returns:
        int: Total number of tickers stored.
    """
    init_ticker_cache(db_path)

    # Check existing SQLite cache freshness
    if not force:
        try:
            conn = get_connection(db_path)
            with conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), MAX(updated_at) FROM tickers_registry;")
                row = cursor.fetchone()
                count, max_updated = row[0], row[1]
            conn.close()

            if count > 0 and max_updated:
                last_updated = datetime.fromisoformat(max_updated)
                if datetime.now() - last_updated < timedelta(days=CACHE_TTL_DAYS):
                    logger.info(f"Ticker cache is fresh ({count} records updated at {max_updated}). Skipping fetch.")
                    return count
        except Exception as e:
            logger.debug(f"Cache freshness check skipped: {e}")

    # Gather data across US stocks, Thai stocks, and Thai funds
    us_stocks = fetch_us_stocks_online()
    thai_stocks = fetch_thai_stocks_online()
    thai_funds = fetch_thai_mutual_funds_online()

    # Consolidate and deduplicate by symbol
    combined_dict: Dict[str, Dict[str, Any]] = {}
    for item in BUNDLED_US_STOCKS + BUNDLED_THAI_STOCKS + BUNDLED_THAI_FUNDS + us_stocks + thai_stocks + thai_funds:
        sym = item["symbol"].strip().upper()
        if sym:
            combined_dict[sym] = item

    all_tickers = list(combined_dict.values())

    # Write to SQLite
    _save_tickers_to_db(all_tickers, db_path=db_path)

    # Write to JSON
    _save_tickers_to_json(all_tickers, json_path=json_path)

    return len(all_tickers)


def get_all_symbols(
    db_path: str = DEFAULT_DB_PATH,
    asset_type: Optional[str] = None,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Retrieve all cached symbols, optionally filtered by asset_type.

    Args:
        db_path (str): Database file path.
        asset_type (str, optional): 'US_STOCK', 'TH_STOCK', or 'TH_MUTUAL_FUND'.
        force_refresh (bool): If True, forces cache reload.

    Returns:
        List[Dict[str, Any]]: List of ticker dictionaries.
    """
    init_ticker_cache(db_path)
    if force_refresh:
        update_ticker_cache(db_path=db_path, force=True)
    else:
        # Check if table has data; if empty, initialize
        try:
            conn = get_connection(db_path)
            with conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM tickers_registry;")
                if cursor.fetchone()[0] == 0:
                    conn.close()
                    update_ticker_cache(db_path=db_path, force=False)
                else:
                    conn.close()
        except Exception:
            update_ticker_cache(db_path=db_path, force=False)

    try:
        conn = get_connection(db_path)
        with conn:
            cursor = conn.cursor()
            if asset_type:
                clean_type = asset_type.strip().upper()
                cursor.execute(
                    "SELECT symbol, name, asset_type, currency, market FROM tickers_registry WHERE asset_type = ? ORDER BY symbol ASC;",
                    (clean_type,),
                )
            else:
                cursor.execute(
                    "SELECT symbol, name, asset_type, currency, market FROM tickers_registry ORDER BY asset_type, symbol ASC;"
                )
            rows = cursor.fetchall()
            records = [dict(r) for r in rows]
        conn.close()
        return records
    except Exception as e:
        logger.error(f"Error querying ticker registry: {e}")
        # Fallback directly to bundled master list
        bundled = BUNDLED_US_STOCKS + BUNDLED_THAI_STOCKS + BUNDLED_THAI_FUNDS
        if asset_type:
            bundled = [b for b in bundled if b["asset_type"] == asset_type.strip().upper()]
        return bundled


def search_symbols(
    query: str,
    asset_type: Optional[str] = None,
    limit: int = 20,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """Search for symbols or company/fund names matching a search query.

    Args:
        query (str): Search term (e.g. 'Apple', 'AAPL', 'PTT', 'กสิกร', 'ONE-UGG').
        asset_type (str, optional): 'US_STOCK', 'TH_STOCK', or 'TH_MUTUAL_FUND'.
        limit (int): Maximum number of results to return.
        db_path (str): Database file path.

    Returns:
        List[Dict[str, Any]]: List of matching ticker dictionaries.
    """
    if not query or not query.strip():
        return get_all_symbols(db_path=db_path, asset_type=asset_type)[:limit]

    clean_query = query.strip()
    pattern = f"%{clean_query}%"

    try:
        init_ticker_cache(db_path)
        conn = get_connection(db_path)
        with conn:
            cursor = conn.cursor()
            if asset_type:
                sql = """
                SELECT symbol, name, asset_type, currency, market
                FROM tickers_registry
                WHERE asset_type = ? AND (symbol LIKE ? OR name LIKE ?)
                ORDER BY (symbol = ?) DESC, (symbol LIKE ?) DESC, symbol ASC
                LIMIT ?;
                """
                cursor.execute(
                    sql,
                    (
                        asset_type.strip().upper(),
                        pattern,
                        pattern,
                        clean_query.upper(),
                        f"{clean_query.upper()}%",
                        limit,
                    ),
                )
            else:
                sql = """
                SELECT symbol, name, asset_type, currency, market
                FROM tickers_registry
                WHERE (symbol LIKE ? OR name LIKE ?)
                ORDER BY (symbol = ?) DESC, (symbol LIKE ?) DESC, symbol ASC
                LIMIT ?;
                """
                cursor.execute(
                    sql,
                    (
                        pattern,
                        pattern,
                        clean_query.upper(),
                        f"{clean_query.upper()}%",
                        limit,
                    ),
                )
            rows = cursor.fetchall()
            records = [dict(r) for r in rows]
        conn.close()
        return records
    except Exception as e:
        logger.debug(f"Search query error: {e}")
        # In-memory search fallback
        all_syms = get_all_symbols(db_path=db_path, asset_type=asset_type)
        q_lower = clean_query.lower()
        matched = [
            s for s in all_syms
            if q_lower in s["symbol"].lower() or q_lower in s["name"].lower()
        ]
        return matched[:limit]


def get_symbol_info(symbol: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieve full metadata for a specific symbol.

    Args:
        symbol (str): Ticker symbol.
        db_path (str): Database file path.

    Returns:
        Dict[str, Any] | None: Ticker dictionary or None if not registered.
    """
    if not symbol:
        return None
    clean_sym = symbol.strip().upper()

    try:
        init_ticker_cache(db_path)
        conn = get_connection(db_path)
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT symbol, name, asset_type, currency, market FROM tickers_registry WHERE symbol = ?;",
                (clean_sym,),
            )
            row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception as e:
        logger.debug(f"Error looking up symbol '{symbol}': {e}")

    # Fallback to bundled search
    for item in BUNDLED_US_STOCKS + BUNDLED_THAI_STOCKS + BUNDLED_THAI_FUNDS:
        if item["symbol"] == clean_sym:
            return item

    return None


def get_symbol_display_options(
    asset_type: Optional[str] = None, db_path: str = DEFAULT_DB_PATH
) -> List[str]:
    """Return formatted display strings for dropdowns, e.g.:

    'AAPL - Apple Inc.'
    'PTT.BK - PTT Public Company Limited'
    'ONE-UGG-RA - ONE Ultimate Global Growth Fund'
    """
    symbols = get_all_symbols(db_path=db_path, asset_type=asset_type)
    options = [f"{s['symbol']} - {s['name']}" for s in symbols]
    return options


if __name__ == "__main__":
    print("=" * 70)
    print("TICKER REGISTRY & CACHE DEMONSTRATION")
    print("=" * 70)

    # 1. Update cache
    total_cached = update_ticker_cache(force=False)
    print(f"\n1. Cache Initialized: {total_cached} total symbols loaded.")

    # 2. Search examples
    print("\n2. Search Examples:")
    for query in ["Apple", "PTT", "ONE-UGG", "กสิกร", "Semiconductor"]:
        results = search_symbols(query, limit=3)
        print(f"\n   Search '{query}':")
        for r in results:
            print(f"      - {r['symbol']:<14} | {r['asset_type']:<15} | {r['name']}")

    # 3. Lookup specific symbol
    print("\n3. Symbol Lookup:")
    for test_sym in ["NVDA", "AOT.BK", "K-USA-A(A)"]:
        info = get_symbol_info(test_sym)
        print(f"   - {test_sym}: {info}")
    print("\n" + "=" * 70)
