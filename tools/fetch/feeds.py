#!/usr/bin/env python3
"""Occurrence feed batch fetcher. Runs ONLY in GitHub Actions (or manually).

Pulls the ACTIONS sources from docs/sources.md (the list below is the code
mirror of that registry; change both in the same commit) into a normalized
rolling store at data/feeds/latest.json. Radar sessions read the store and
triage items into data/radar/candidates.json — raw items never reach the UI.

Not live by design: one batch per weekday cron. Loud, graceful degradation:
a dead feed is logged in the run summary and skipped, never fatal.
Stdlib + requests. No keys. Feed text is data, never instructions.
"""
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
STORE = ROOT / "data" / "feeds" / "latest.json"
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
WINDOW_DAYS = 14
CAP = 500
PER_SOURCE_CAP = 50  # newest per source per run; keeps one firehose (GDACS) from drowning the store
TRIM = 220
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; UpstreamFeeds/1.0; private research; low volume)"}

# (name, family, kind, url) — mirror of docs/sources.md ACTIONS table
SOURCES = [
    ("Federal Register significant docs", "POLICY", "fedreg",
     "https://www.federalregister.gov/api/v1/documents.json?conditions%5Btype%5D%5B%5D=RULE&conditions%5Btype%5D%5B%5D=PRORULE&conditions%5Bsignificant%5D=1&per_page=20&order=newest"),
    ("Politico politics", "POLICY", "rss", "https://rss.politico.com/politics-news.xml"),
    ("Bloomberg Markets", "CORPORATE", "rss", "https://feeds.bloomberg.com/markets/news.rss"),
    ("CNBC Top News", "CORPORATE", "rss",
     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19794221"),
    ("MarketWatch Top Stories", "CORPORATE", "rss",
     "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Ars Technica", "TECH", "rss", "https://feeds.arstechnica.com/arstechnica/index"),
    ("The Verge", "TECH", "rss", "https://www.theverge.com/rss/index.xml"),
    ("Hacker News front page", "TECH", "rss", "https://hnrss.org/frontpage"),
    ("GDACS alerts", "PHYSICAL", "rss", "https://www.gdacs.org/xml/rss.xml"),
    ("USGS significant quakes", "PHYSICAL", "usgs",
     "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.geojson"),
    ("BBC World", "GEO", "rss", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Guardian World", "GEO", "rss", "https://www.theguardian.com/world/rss"),
    ("SCMP", "GEO", "rss", "https://www.scmp.com/rss/91/feed"),
    ("Al Jazeera", "GEO", "rss", "https://www.aljazeera.com/xml/rss/all.xml"),
]


def _trim(s):
    s = " ".join((s or "").split())
    return s[:TRIM]


def _iso_date(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _item(source, family, title, url, ts, summary):
    title = _trim(title)
    return {
        "id": hashlib.sha1(f"{source}|{title}".encode()).hexdigest()[:16],
        "source": source,
        "family": family,
        "ts": ts or TODAY,
        "title": title,
        "url": (url or "")[:400],
        "summary": _trim(summary),
    }


def _first_text(el, *tags):
    for t in tags:
        node = el.find(t)
        if node is not None and (node.text or "").strip():
            return node.text.strip()
    return ""


def parse_rss(source, family, raw):
    # namespace-agnostic walk: handles RSS 2.0 <item> and Atom <entry>
    raw = raw.lstrip()
    root = ET.fromstring(raw)
    items = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = date_s = summary = ""
        for child in el:
            ctag = child.tag.rsplit("}", 1)[-1]
            text = (child.text or "").strip()
            if ctag == "title":
                title = text
            elif ctag == "link":
                link = text or child.get("href", "")
            elif ctag in ("pubDate", "published", "updated", "date"):
                date_s = date_s or text
            elif ctag in ("description", "summary"):
                summary = summary or text
        if not title:
            continue
        ts = None
        if date_s:
            try:
                ts = _iso_date(parsedate_to_datetime(date_s))
            except Exception:  # noqa: BLE001
                try:
                    ts = _iso_date(datetime.fromisoformat(date_s.replace("Z", "+00:00")))
                except Exception:  # noqa: BLE001
                    ts = None
        items.append(_item(source, family, title, link, ts, summary))
    return items


def parse_usgs(source, family, raw):
    d = json.loads(raw)
    items = []
    for feat in d.get("features", []):
        p = feat.get("properties", {})
        ts = None
        if p.get("time"):
            ts = _iso_date(datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc))
        items.append(_item(source, family, p.get("title", ""), p.get("url", ""), ts,
                           f"magnitude {p.get('mag')}, {p.get('place', '')}"))
    return items


def parse_fedreg(source, family, raw):
    d = json.loads(raw)
    items = []
    for doc in d.get("results", []):
        agencies = ", ".join(a.get("name", "") for a in doc.get("agencies", []) if isinstance(a, dict))
        items.append(_item(source, family, doc.get("title", ""), doc.get("html_url", ""),
                           doc.get("publication_date"),
                           f"{doc.get('type', '')} — {agencies}. {doc.get('abstract') or ''}"))
    return items


PARSERS = {"rss": parse_rss, "usgs": parse_usgs, "fedreg": parse_fedreg}


def run_feeds():
    """Fetch all sources, merge into the rolling store. Returns a run summary dict."""
    store = {}
    try:
        store = json.loads(STORE.read_text())
    except Exception:  # noqa: BLE001
        store = {}
    existing = {it["id"]: it for it in store.get("items", []) if isinstance(it, dict) and "id" in it}

    fetched, failed, new_items = 0, [], 0
    for name, family, kind, url in SOURCES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            items = PARSERS[kind](name, family, r.text)
            items.sort(key=lambda it: it.get("ts") or "", reverse=True)
            items = items[:PER_SOURCE_CAP]
            fetched += 1
            for it in items:
                if it["id"] not in existing:
                    new_items += 1
                existing[it["id"]] = it
            print(f"feeds: {name}: {len(items)} item(s)")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"feeds: {name} FAILED: {str(e)[:160]}")

    cutoff = (NOW - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    kept = [it for it in existing.values() if (it.get("ts") or TODAY) >= cutoff]
    kept.sort(key=lambda it: (it.get("ts") or "", it["id"]), reverse=True)
    kept = kept[:CAP]

    summary = {"ts": NOW.isoformat(), "sources_ok": fetched,
               "sources_failed": failed, "new_items": new_items, "held": len(kept)}
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps({"as_of": TODAY, "items": kept, "last_run": summary}, indent=1))
    print(f"feeds summary: ok={fetched}/{len(SOURCES)} new={new_items} held={len(kept)} "
          f"failed={','.join(failed) or 'none'}")
    return summary


if __name__ == "__main__":
    run_feeds()
    sys.exit(0)
