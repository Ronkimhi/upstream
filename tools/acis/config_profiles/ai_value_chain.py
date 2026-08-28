"""ai_value_chain profile: Ron's hunting ground (THEME-001 / THEME-002).

Photonics, optical components, semi equipment, electronic components,
communications equipment, power/switchgear, cooling, wire/cable, and
lab/measurement instruments (SIC 3827 is COHR's own code; the class is
the point). Cap band deliberately wide ($300M to $40B): pre-consensus
suppliers span micro-caps to covered mid-caps that the repricing-lag
lane catches. Liquidity floor is dollar ADV (F15), not share count.
"""
from acis.config_profiles.base import Profile

PROFILE = Profile(
    name="ai_value_chain",
    benched=False,
    target_sic_codes=frozenset({
        3559,  # special industry machinery (semi equipment lives here)
        3585,  # refrigeration + cooling equipment (liquid cooling)
        3612,  # power distribution + specialty transformers
        3613,  # switchgear + switchboard apparatus
        3357,  # nonferrous wire (incl. fiber optic cable)
    }),
    target_sic_ranges=(
        (3660, 3669),  # communications equipment
        (3670, 3679),  # electronic components: semis (3674), PCBs, connectors
        (3821, 3829),  # lab + measurement instruments (3827: optical instruments, COHR)
    ),
    excluded_sic_ranges=(
        (2830, 2836),  # biotech/pharma
        (3841, 3845),  # medtech
    ),
    min_market_cap=300_000_000,
    max_market_cap=40_000_000_000,
    min_dollar_adv_20d=2_000_000,
    survival_debt_equity_max=1.5,
    survival_min_cash_runway_quarters=4.0,
    survival_max_dilution_pct_1yr=10.0,
    trailing_roic_min=0.12,
    trailing_fcf_positive_min_years=4,
    trailing_debt_equity_max=1.0,
    sec_keyword_groups={
        "demand_inflection": [
            "design win", "qualification", "800G", "1.6T",
            "co-packaged optics", "CPO", "optical transceiver",
            "silicon photonics", "liquid cooling", "hyperscale",
            "backlog", "remaining performance obligations",
            "capacity expansion", "record demand",
        ],
        "supply_dislocation": [
            "supply shortage", "allocation", "lead time",
            "sole source", "capacity constraint", "export restriction",
        ],
        "corporate_action": [
            "strategic alternatives", "spin-off", "divestiture",
            "share repurchase", "merger",
        ],
        "governance_change": [
            "activist", "proxy contest", "new CEO", "13D filing",
        ],
        "regulatory_policy": [
            "export control", "CHIPS Act", "tariff", "government contract",
        ],
    },
    sec_high_signal_keywords={
        "demand_inflection": ["design win", "co-packaged optics", "backlog"],
        "supply_dislocation": ["capacity constraint", "sole source", "allocation"],
        "corporate_action": ["strategic alternatives", "spin-off", "divestiture"],
        "governance_change": ["activist", "proxy contest", "new CEO"],
        "regulatory_policy": ["export control", "CHIPS Act", "tariff"],
    },
)
