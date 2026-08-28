"""Abstract base class for all ACIS signal scanners.

F9 (2026-08-28): every scan_all reports a Rule-21 health line, examined
next to found next to errored, at INFO. Errors were previously swallowed
at DEBUG, so a scanner whose API had died for weeks read as "no signals"
instead of "no data". A run whose errors equal its examined count is a
FAILED run and logs at ERROR.
"""
from abc import ABC, abstractmethod
from datetime import datetime
import logging


class BaseScanner(ABC):
    scanner_name = "base"

    def __init__(self, lookback_days=180):
        self.lookback_days = lookback_days

    @abstractmethod
    def scan_company(self, company):
        """Scan a single company. Return a signal dict with score > 0, or None."""
        pass

    def scan_all(self, companies):
        """Run scanner against all companies. Returns standardized output dict
        including a `health` block (examined/signals/errors)."""
        logger = logging.getLogger("acis.scanners." + self.scanner_name)
        signals = []
        examined = 0
        errors = 0
        for company in companies:
            examined += 1
            try:
                result = self.scan_company(company)
                if result and result.get("score", 0) > 0:
                    signals.append(result)
            except Exception as e:
                errors += 1
                logger.warning("scanner %s error on %s: %s",
                               self.scanner_name, company.get("ticker", "?"), e)

        health = {"examined": examined, "signals": len(signals), "errors": errors}
        logger.info("scanner %s health: %d examined, %d signals, %d errors",
                    self.scanner_name, examined, len(signals), errors)
        if examined > 0 and errors == examined:
            logger.error(
                "scanner %s: EVERY company errored (%d/%d). This is a FAILED "
                "run, not an empty result. Do not treat its absence of "
                "signals as information.", self.scanner_name, errors, examined)

        return {
            "scanner_name": self.scanner_name,
            "scan_date": datetime.now().isoformat(),
            "lookback_days": self.lookback_days,
            "signals": signals,
            "health": health,
        }
