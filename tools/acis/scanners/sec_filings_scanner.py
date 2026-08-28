"""Scanner 1: SEC filing keyword search via EDGAR full-text search."""
import logging
from datetime import datetime, timedelta
import requests

from acis.scanners.base_scanner import BaseScanner
from acis.config import (
    HIGH_SIGNAL_8K_ITEMS,
    EDGAR_USER_AGENT, EDGAR_DELAY_SECONDS,
)
from acis.config_profiles import get_profile
from acis.utils import RateLimiter

logger = logging.getLogger("acis.scanners.sec_filings")

_rate_limiter = RateLimiter(delay_seconds=EDGAR_DELAY_SECONDS)

US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "US", "us", "nyse", "nasdaq", "amex"}


class SECFilingsScanner(BaseScanner):
    scanner_name = "sec_filings"

    def _search_edgar(self, company_name, keyword, date_from, date_to, headers):
        """Search EDGAR EFTS for a single keyword + company. Returns list of hit dicts."""
        _rate_limiter.wait()
        try:
            resp = requests.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": '"{}"'.format(keyword),
                    "dateRange": "custom",
                    "startdt": date_from,
                    "enddt": date_to,
                    "forms": "10-K,10-Q,8-K",
                    "entity": company_name,
                },
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            return resp.json().get("hits", {}).get("hits", [])
        except Exception as e:
            logger.debug("EDGAR search error for %s/%s: %s", company_name, keyword, e)
            return []

    def scan_company(self, company):
        ticker_str = company["ticker"]
        exchange = company.get("exchange", "")

        if exchange and exchange.upper() not in {e.upper() for e in US_EXCHANGES}:
            return None

        company_name = company.get("company_name", ticker_str)
        date_to = datetime.now().strftime("%Y-%m-%d")
        date_from = (datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

        all_hits = []
        categories_hit = set()
        filing_types_hit = set()

        headers = {"User-Agent": EDGAR_USER_AGENT}

        # F14 (2026-08-28): keyword groups are PROFILE-owned. The old
        # hardcoded set here was commodity-flavored ("mine closure",
        # "impairment reversal") and could never flag a photonics design
        # win. High-signal subset only, to respect the rate budget.
        high_signal_keywords = get_profile().sec_high_signal_keywords

        for category, keywords in high_signal_keywords.items():
            for keyword in keywords:
                hits = self._search_edgar(company_name, keyword, date_from, date_to, headers)
                if hits:
                    categories_hit.add(category)
                    for hit in hits:
                        source = hit.get("_source", {})
                        form_type = source.get("form_type", "")
                        filing_types_hit.add(form_type)
                        highlight = hit.get("highlight", {})
                        excerpt = ""
                        if "content" in highlight:
                            excerpt = highlight["content"][0][:200] if highlight["content"] else ""

                        all_hits.append({
                            "keyword": keyword,
                            "category": category,
                            "form_type": form_type,
                            "filing_date": source.get("file_date", ""),
                            "entity_name": source.get("entity_name", ""),
                            "excerpt": excerpt,
                            "accession": source.get("file_num", ""),
                        })

        if not all_hits:
            return None

        multi_filing_types = len(filing_types_hit) > 1
        multi_categories = len(categories_hit) > 1
        has_8k_signal = any(h["form_type"] == "8-K" for h in all_hits)

        if multi_filing_types and multi_categories:
            score = 3
        elif multi_filing_types or multi_categories or has_8k_signal:
            score = 2
        else:
            score = 1

        primary_category = max(categories_hit, key=lambda c: sum(
            1 for h in all_hits if h["category"] == c
        ))

        evidence = []
        for h in all_hits[:5]:
            evidence.append({
                "source_type": "SEC Filing",
                "source_name": "{} {}".format(h["form_type"], h["filing_date"]),
                "source_date": h["filing_date"],
                "source_ref": "EDGAR {}".format(h["accession"]),
                "excerpt": h["excerpt"],
                "confidence": "VERIFIED",
            })

        return {
            "ticker": ticker_str,
            "score": score,
            "signal_type": "sec_filing_keywords",
            "summary": "{} keyword hit(s) across {} category(ies) and {} filing type(s)".format(
                len(all_hits), len(categories_hit), len(filing_types_hit)
            ),
            "evidence": evidence,
            "catalyst_type_hint": primary_category,
            "notes": None,
        }
