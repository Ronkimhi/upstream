#!/usr/bin/env python3
"""The one rule for where a ticker's market and EDGAR files live.

`tools/fetch/fetch.py` writes `data/market/<safe_name(ticker)>.json`, mapping `.` and `/`
to `-` (so `1072.HK` is `1072-HK.json`). Until 2026-09-01 every reader re-implemented that
rule or skipped it: check_impact looked up `data/market/<ticker>.json` verbatim, so a
dotted ticker was "missing" even when its file was on disk, and a coverage census counted
81 of 142 misses that were this artifact and not a gap at all.

This module is the reader-side definition. fetch.py keeps its own `safe_name` because the
fetcher runs in GitHub Actions with a different sys.path; `tools/tests/test_market_paths.py`
asserts the two agree on every ticker shape in the repo, so the rule can only drift loudly.
"""
from pathlib import Path


def safe_name(ticker) -> str:
    return str(ticker).replace(".", "-").replace("/", "-")


def market_path(data: Path, ticker) -> Path:
    """`data/market/<T>.json` for a ticker as written on disk."""
    return Path(data) / "market" / f"{safe_name(ticker)}.json"


def edgar_doc_path(data: Path, ticker) -> Path:
    return Path(data) / "edgar" / "docs" / f"{safe_name(ticker)}.json"
