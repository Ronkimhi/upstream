#!/usr/bin/env python3
"""Web evidence as a fetched store, the same way EDGAR documents already are.

Why this exists. `tools/check_screen.py` verifies every earnings quote verbatim against
`data/edgar/docs/<T>.json`, a document GitHub Actions fetched and stored. Web citations
had no equivalent: an evidence item carried a URL nobody had opened and a `source_excerpt`
nothing could check. Adversarial verifiers that did open the URLs found roughly 85% of the
first 36 impact appraisals citing a source that does not contain the claim (method §1,
tasks/lessons.md "An agent that cites without fetching fabricates at scale"). And in the
cloud routine venue WebFetch is egress-blocked, so every web-evidence stage stood down with
`handed-to: local`, five fires in a row on one command.

One store fixes both. A session queues `{"kind": "web_doc", "url": ...}` in
data/requests.json; the fetch workflow stores the page's text under
`data/web/<web_doc_id(url)>.json`; the session cites from the stored text; the gates below
verify the excerpt against it with the same `normalize` that check_screen uses, so
"verbatim" has one definition in the repo.

Limits, stated up front rather than discovered: a paywalled page stores its HTTP status
(403 is a fact about the source, and the citation must move); a JS-rendered page stores
whatever the server sent; text is capped at MAX_WEB_CHARS; URLs are canonicalised by
stripping the fragment and tracking parameters so two spellings of one page share one file.
"""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_WEB_CHARS = 200_000
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src")


def canonical_url(url: str) -> str:
    """Lower-cased scheme and host, no fragment, no tracking parameters, no trailing slash
    on a path (the root path stays "/"). Query order is normalised."""
    if not isinstance(url, str):
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if parts.port and not ((scheme == "https" and parts.port == 443) or
                           (scheme == "http" and parts.port == 80)):
        host = f"{host}:{parts.port}"
    path = re.sub(r"/+$", "", parts.path) or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith(TRACKING_PARAMS)]
    query.sort()
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def web_doc_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def web_doc_path(data: Path, url: str) -> Path:
    return Path(data) / "web" / f"{web_doc_id(url)}.json"


def load_web_doc(data: Path, url: str) -> dict | None:
    path = web_doc_path(data, url)
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def normalize(s: str) -> str:
    """check_screen.normalize when importable, so verbatim has one definition; the same
    folding inline otherwise (the fetch job runs without tools/ on its path)."""
    try:
        import check_screen  # noqa: WPS433
        return check_screen.normalize(s)
    except Exception:  # noqa: BLE001
        import unicodedata
        if not isinstance(s, str):
            return ""
        s = unicodedata.normalize("NFKC", s)
        out = []
        for ch in s:
            cat = unicodedata.category(ch)
            if cat == "Pd":
                out.append("-")
            elif cat == "Zs":
                out.append(" ")
            elif ch in "‘’‚‛":
                out.append("'")
            elif ch in "“”„‟":
                out.append('"')
            else:
                out.append(ch)
        return re.sub(r"\s+", " ", "".join(out)).strip().casefold()


def excerpt_in_doc(excerpt: str, text: str) -> bool:
    ex = normalize(excerpt)
    return bool(ex) and ex in normalize(text)


def verify(data: Path, item: dict) -> dict:
    """One evidence item against the store.

    state: MATCH (excerpt found in the stored text), MISMATCH (stored text does not contain
    it), HTTP_<code> (the fetch answered but not 200; the citation must move),
    EMPTY (200 with no text, a JS-rendered or binary page), NO_EXCERPT (nothing to check),
    UNFETCHED (no stored document for this URL), NO_URL.
    """
    url = item.get("url") or item.get("source_url") if isinstance(item, dict) else None
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return {"state": "NO_URL", "doc": None}
    doc = load_web_doc(data, url)
    if doc is None:
        return {"state": "UNFETCHED", "doc": None}
    status = doc.get("http_status")
    if status != 200:
        return {"state": f"HTTP_{status}", "doc": doc}
    excerpt = item.get("source_excerpt")
    if not isinstance(excerpt, str) or not excerpt.strip():
        return {"state": "NO_EXCERPT", "doc": doc}
    text = doc.get("text") or ""
    if not text.strip():
        return {"state": "EMPTY", "doc": doc}
    return {"state": "MATCH" if excerpt_in_doc(excerpt, text) else "MISMATCH", "doc": doc}


def corpus_web_findings(data: Path, items, where_of) -> tuple[list[str], dict]:
    """Run verify() over an iterable of (where, item). Returns (failures, counts).

    MISMATCH, HTTP_* and EMPTY fail: each is a stored fact that the cited source does not
    say what the claim says. UNFETCHED and NO_EXCERPT are counted, never failed here; the
    caller decides whether its stage is past the date when an unfetched web citation
    stops being acceptable.
    """
    failures, counts = [], {}
    for where, item in items:
        res = verify(data, item)
        state = res["state"]
        counts[state] = counts.get(state, 0) + 1
        if state == "MISMATCH":
            failures.append(f"{where}: source_excerpt is not in the stored text of "
                            f"{web_doc_path(data, item.get('url') or item.get('source_url')).name}"
                            " (data/web); a span the source does not contain is not evidence")
        elif state.startswith("HTTP_"):
            failures.append(f"{where}: the stored fetch of this url answered {state[5:]}, not "
                            "200; the source cannot be read, so the citation must move")
        elif state == "EMPTY":
            failures.append(f"{where}: the stored fetch of this url has no text (JS-rendered "
                            "or binary); cite a page whose text can be verified")
    return failures, counts
