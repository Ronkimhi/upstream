"""
ACIS shared configuration constants (F6, 2026-08-28).

Everything sector-specific (SIC targeting, cap band, liquidity floor,
quality thresholds, SEC keyword groups) lives in acis.config_profiles.
The commodity values from the original 2026-03 manifesto moved intact
into config_profiles/deep_value_commodity.py (benched). This module
keeps only constants shared by every profile.
"""

# --- Data source endpoints (F13/F16) ---

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
STOOQ_DAILY_CSV_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
DUAL_SOURCE_DISAGREEMENT_PCT = 2.0  # > this = PRICE-DISPUTED, never silent

EXCHANGE_SUFFIXES = {
    "us": "",
    "tsx": ".TO",
    "lse": ".L",
    "asx": ".AX",
    "euronext": [".PA", ".AS", ".BR", ".LS"],
    "tse": ".T",
    "hkex": ".HK",
}

# GICS constants deleted 2026-08-28 (F13): the sector-string fallback that
# consumed them wholesale-excluded "information technology" and is gone.
# QUALITY_FILTER_THRESHOLDS deleted 2026-08-28 (F6/F15): thresholds are
# profile-owned; the 100K-share min_volume is replaced by dollar ADV.

DEFAULT_TAX_RATE = 0.21

CACHE_VALIDITY_DAYS = 7
API_DELAY_SECONDS = 0.5

FINVIZ_FILTERS = {
    "Market Cap.": "+Small (over $300mln)",
    "Country": "USA",
}


# is_target_sic / is_excluded_sic moved to Profile methods (F6):
# from acis.config_profiles import get_profile; get_profile().is_target_sic(x)

# --- Phase 2: Scanner Constants ---

# SEC_KEYWORD_GROUPS re-keyed per profile 2026-08-28 (F14): the commodity
# terms ("mine restart", "reserve upgrade") live in deep_value_commodity;
# the AI value-chain terms in ai_value_chain. Scanners read the profile.

HIGH_SIGNAL_8K_ITEMS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "2.01": "Completion of Acquisition or Disposition",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers",
    "8.01": "Other Events",
}

COMMODITY_MAP = {
    "oil_gas": {"futures": "CL=F", "name": "WTI Crude Oil"},
    "natural_gas": {"futures": "NG=F", "name": "Natural Gas"},
    "gold": {"futures": "GC=F", "name": "Gold"},
    "silver": {"futures": "SI=F", "name": "Silver"},
    "copper": {"futures": "HG=F", "name": "Copper"},
    "wheat": {"futures": "ZW=F", "name": "Wheat"},
    "corn": {"futures": "ZC=F", "name": "Corn"},
    "soybeans": {"futures": "ZS=F", "name": "Soybeans"},
    "coal": {"futures": None, "name": "Coal"},
    "dry_bulk": {"futures": None, "name": "Baltic Dry Index"},
}

SIC_TO_COMMODITY = {
    (1040, 1049): "gold",
    (1020, 1029): "copper",
    (1310, 1319): "oil_gas",
    (1200, 1299): "coal",
    (2911, 2911): "oil_gas",
    (100, 199): "wheat",
    (200, 299): "corn",
    (4400, 4499): "dry_bulk",
}

GICS_TO_COMMODITY = {
    "Gold": "gold",
    "Silver": "silver",
    "Copper": "copper",
    "Oil & Gas Exploration & Production": "oil_gas",
    "Oil & Gas Refining & Marketing": "oil_gas",
    "Integrated Oil & Gas": "oil_gas",
    "Coal & Consumable Fuels": "coal",
    "Agricultural Products": "wheat",
}

INSIDER_THRESHOLDS = {
    "min_purchase_value": 200_000,
    "large_purchase_value": 500_000,
    "cluster_window_days": 60,
    "near_low_pct": 0.20,
}

SCANNER_SCORE_THRESHOLDS = {
    "min_composite_score": 3,
    "min_signal_count": 2,
    "tier1_min_score": 8,
    "tier1_min_signals": 3,
    "tier2_min_score": 5,
    "tier2_min_signals": 2,
}

EDGAR_USER_AGENT = "ACIS Research Tool ronkkimhi@gmail.com"
EDGAR_DELAY_SECONDS = 0.5
OPENINSIDER_DELAY_SECONDS = 2.0
GEMINI_DELAY_SECONDS = 4.0

# --- Phase 3: Assessment Constants ---

ASSESSMENT_MODEL = "gemini-2.5-pro"
ASSESSMENT_MODEL_FLASH = "gemini-2.5-flash"
ASSESSMENT_MAX_RETRIES = 1
ASSESSMENT_BACKOFF_BASE = 10  # seconds, for 429 errors
ASSESSMENT_BACKOFF_MAX = 120

FILING_CACHE_DIR = ".cache/filing_agent"
FILING_CACHE_VALIDITY_DAYS = 90

CATALYST_TAXONOMY = [
    "asset_revaluation",
    "regulatory_policy",
    "demand_inflection",
    "supply_dislocation",
    "corporate_action",
    "governance_change",
]

PROBABILITY_WEIGHTS = {
    "LOW": 0.2,
    "MEDIUM": 0.45,
    "HIGH": 0.7,
    "INSUFFICIENT_DATA": 0.0,
}

PROBABILITY_RATINGS = ["LOW", "MEDIUM", "HIGH", "INSUFFICIENT_DATA"]

# MAGNITUDE_MIN_MIDPOINT deleted 2026-08-28 (F7): a magnitude floor let
# any big-enough story through regardless of downside. The EV_net floor
# in the invest playbook governs (catalyst-taxonomy.md).

CONFIDENCE_TAGS = ["VERIFIED", "INFERRED", "SPECULATIVE", "NULL"]

FILING_SECTIONS = [
    ("Item 1", "Business"),
    ("Item 1A", "Risk Factors"),
    ("Item 2", "Properties"),
    ("Item 7", "MD&A"),
    ("Item 8", "Financial Statements"),
]

RED_TEAM_ASSESSMENTS = [
    "STRONG_THESIS",
    "MODERATE_THESIS",
    "WEAK_THESIS",
    "REJECT_THESIS",
]
