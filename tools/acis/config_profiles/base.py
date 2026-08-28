"""Profile dataclass shared by all universe/screen profiles."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    name: str
    benched: bool
    # SIC targeting. target_sic_codes: exact codes; target_sic_ranges: (low, high).
    target_sic_codes: frozenset = frozenset()
    target_sic_ranges: tuple = ()
    excluded_sic_ranges: tuple = ()
    # Cap band and liquidity (F15: dollar ADV, never share counts).
    min_market_cap: int = 0
    max_market_cap: int = 0
    min_dollar_adv_20d: int = 0
    # Quality thresholds. survival_* apply to chain-map-origin candidates;
    # trailing_* apply to generic screen-origin candidates only.
    survival_debt_equity_max: float = 1.5
    survival_min_cash_runway_quarters: float = 4.0
    survival_max_dilution_pct_1yr: float = 10.0
    trailing_roic_min: float = 0.12
    trailing_fcf_positive_min_years: int = 4
    trailing_debt_equity_max: float = 1.0
    # SEC full-text keyword groups (F14: profile-owned).
    sec_keyword_groups: dict = field(default_factory=dict)
    # High-signal subset used by the sec_filings scanner (rate-limit budget).
    sec_high_signal_keywords: dict = field(default_factory=dict)

    def is_target_sic(self, sic_code):
        if sic_code is None:
            return False
        sic_code = int(sic_code)
        if self.is_excluded_sic(sic_code):
            return False
        if sic_code in self.target_sic_codes:
            return True
        return any(low <= sic_code <= high for low, high in self.target_sic_ranges)

    def is_excluded_sic(self, sic_code):
        if sic_code is None:
            return False
        sic_code = int(sic_code)
        return any(low <= sic_code <= high for low, high in self.excluded_sic_ranges)
