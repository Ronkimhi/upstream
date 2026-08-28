"""deep_value_commodity profile: the original 2026-03 ACIS configuration,
moved intact from acis.config (F6/F14) and BENCHED. Runnable by name
(ACIS_PROFILE=deep_value_commodity), never scheduled.

One deliberate change from the original: the liquidity floor is dollar
ADV (F15) instead of the deleted 100K-share min_volume; $2M/day is the
nearest equivalent for the $500M+ cap floor and keeps the two profiles
comparable. Everything else carries the original values, including the
12% ROIC minimum (the signal-log header's "ROIC >= 10%" was doc-code
drift, annotated 2026-08-28).
"""
from acis.config_profiles.base import Profile

PROFILE = Profile(
    name="deep_value_commodity",
    benched=True,
    target_sic_ranges=(
        (1300, 1389),  # energy oil + gas
        (1200, 1299),  # coal
        (1000, 1099),  # metal mining (incl. gold 1040-1049)
        (1400, 1499),  # mining other
        (3720, 3769),  # defense aerospace
        (3480, 3489),  # defense ordnance
        (3812, 3812),  # defense electronics
        (4400, 4499),  # shipping
        (4800, 4899),  # telecom
        (4900, 4949),  # utilities electric
        (4950, 4959),  # utilities water (incl. waste 4953)
        (100, 999),    # agriculture
        (2000, 2099),  # food processing
        (2800, 2899),  # industrial chemicals
        (3400, 3599),  # industrial manufacturing
        (2911, 2911),  # petroleum refining
    ),
    excluded_sic_ranges=(
        (2830, 2836),  # biotech/pharma
        (3841, 3845),  # medtech
    ),
    min_market_cap=500_000_000,
    max_market_cap=15_000_000_000,
    min_dollar_adv_20d=2_000_000,
    survival_debt_equity_max=1.5,
    survival_min_cash_runway_quarters=4.0,
    survival_max_dilution_pct_1yr=10.0,
    trailing_roic_min=0.12,
    trailing_fcf_positive_min_years=4,
    trailing_debt_equity_max=1.0,
    sec_keyword_groups={
        "asset_revaluation": [
            "restart", "recommission", "rebuild", "remediation",
            "impairment reversal", "asset write-up", "reserve upgrade",
            "mine restart", "facility reopen", "production restart",
            "idle capacity", "mothballed", "dormant asset",
        ],
        "regulatory_policy": [
            "regulatory approval", "permit granted", "license renewal",
            "government contract", "policy change", "tariff",
            "anti-dumping", "trade remedy", "rate case",
            "privatization", "deregulation", "IPO",
        ],
        "demand_inflection": [
            "record demand", "capacity expansion", "backlog increase",
            "order book", "new market", "demand exceeds supply",
            "secular growth", "structural shift", "emerging application",
        ],
        "supply_dislocation": [
            "supply shortage", "force majeure", "production disruption",
            "competitor closure", "mine closure", "refinery shutdown",
            "sanctions impact", "export restriction", "supply chain constraint",
        ],
        "corporate_action": [
            "strategic alternatives", "portfolio review",
            "spin-off", "spinoff", "divestiture", "asset sale",
            "share repurchase", "buyback", "special dividend",
            "merger", "acquisition target", "going private",
        ],
        "governance_change": [
            "board refreshment", "new CEO", "management transition",
            "activist", "proxy contest", "shareholder proposal",
            "13D filing", "board seat", "strategic review committee",
        ],
    },
    sec_high_signal_keywords={
        "asset_revaluation": ["restart", "mothballed", "impairment reversal"],
        "regulatory_policy": ["regulatory approval", "tariff", "government contract"],
        "demand_inflection": ["record demand", "capacity expansion", "backlog increase"],
        "supply_dislocation": ["supply shortage", "force majeure", "mine closure"],
        "corporate_action": ["strategic alternatives", "spin-off", "divestiture"],
        "governance_change": ["activist", "proxy contest", "new CEO"],
    },
)
