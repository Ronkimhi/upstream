#!/usr/bin/env python3
"""The one resolver for "which stored filing does this quote cite". Stdlib only, offline.

Why this exists. `data/edgar/docs/<T>.json` holds ONE filing per ticker, and an `edgar_doc`
request replaces it with the newest one. Every gate that verifies a filing quote read
whatever was in that file at the time, so a quote that was true of the filing it cites
started failing the moment a newer filing arrived. On 2026-11-19 that happened three times
in one day (ACM, CAT, GD/HUBB/NVT): pipelines went red in validate.py, screens failed
accession checks, the page build stopped, and each time a Sieve agent re-cited by hand.

The fix has two halves:
  * the fetcher (`tools/fetch/fetch.py:archive_outgoing_doc`) copies the outgoing document,
    byte-identical, to `data/edgar/docs/_archive/<T>/<accession>.json` before it overwrites
    the current one, append-only, never deleted; and
  * every gate asks THIS module for the filing a quote cites, current document or archive.

The rule, stated once:
  * A citation that names an accession (a screen nugget's or dive quote's `accession`
    field, or an EDGAR archive URL whose path carries one) is verified against THAT filing:
    the current document when its accession matches, otherwise its archived copies. If that
    filing is stored in neither place, the quote fails closed (NOT_STORED), exactly like a
    missing document did before. It is never silently re-pointed at another filing.
  * A citation that names no accession is verified against the current document first,
    then every archived filing for the same ticker, newest filing date first. It passes if
    any stored filing of that issuer contains it verbatim; the result names which one.
  * A ticker with nothing stored at all is NO_DOC, cited or not: fail closed, as method
    section 1 says.

Integrity. An archived copy is only trusted when it is the document it claims to be: its
own `ticker` names this ticker, its own `accession` is the one its filename says, its
`text` is a string, its `chars` equals `len(text)`, and, when the fetcher recorded one,
`text_sha256` equals the hash of `text`. A file failing any of these is reported as
TAMPERED and never used to verify anything. This catches accidents and casual edits; it
does not stop a deliberate forger who recomputes the hash, and is not meant to. The venue
rule (sessions never write `data/edgar/`) and git history are what stop that.

Verbatim has one definition in the repo: `check_screen.quote_in_text`, imported lazily.
"""
import hashlib
import json
import re
from pathlib import Path

ARCHIVE_DIRNAME = "_archive"
_ACC_DASHED = re.compile(r"^(\d{10})-(\d{2})-(\d{6})$")
_ACC_PLAIN = re.compile(r"^\d{18}$")
# https://www.sec.gov/Archives/edgar/data/<cik>/<18-digit accession>/<file>
_ACC_IN_URL = re.compile(r"/Archives/edgar/data/\d+/(\d{18})(?:/|$)", re.I)


def safe_name(ticker) -> str:
    """Same rule as market_paths.safe_name and fetch.safe_name (test_market_paths holds
    them together); repeated here so the fetcher can import this module standalone."""
    return str(ticker).replace(".", "-").replace("/", "-")


def normalize_accession(acc) -> str | None:
    """`0001104659-26-128300` for either spelling, None for anything else."""
    if not isinstance(acc, str):
        return None
    a = acc.strip()
    if _ACC_DASHED.match(a):
        return a
    if _ACC_PLAIN.match(a):
        return f"{a[:10]}-{a[10:12]}-{a[12:]}"
    return None


def accession_from_url(url) -> str | None:
    """The accession an EDGAR archive URL points into, dashed, or None."""
    if not isinstance(url, str):
        return None
    m = _ACC_IN_URL.search(url)
    return normalize_accession(m.group(1)) if m else None


def current_path(data: Path, ticker) -> Path:
    return Path(data) / "edgar" / "docs" / f"{safe_name(ticker)}.json"


def archive_dir(data: Path, ticker) -> Path:
    return Path(data) / "edgar" / "docs" / ARCHIVE_DIRNAME / safe_name(ticker)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path):
    try:
        obj = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def archive_integrity(path: Path, doc, ticker) -> list[str]:
    """Why an archived file cannot be trusted, or [] when it can."""
    if not isinstance(doc, dict):
        return ["not a readable JSON object"]
    out = []
    if safe_name(doc.get("ticker")) != safe_name(ticker):
        out.append(f"ticker {doc.get('ticker')!r} is not {ticker!r}")
    stem_acc = normalize_accession(path.stem.split("~", 1)[0])
    acc = normalize_accession(doc.get("accession"))
    if acc is None or acc != stem_acc:
        out.append(f"accession {doc.get('accession')!r} does not match filename {path.name}")
    return out + text_integrity(doc)


def text_integrity(doc: dict) -> list[str]:
    """The checks that hold for the current document and an archived one alike."""
    text = doc.get("text")
    if not isinstance(text, str):
        return ["text is not a string"]
    out = []
    if doc.get("chars") is not None and doc.get("chars") != len(text):
        out.append(f"chars {doc.get('chars')!r} != len(text) {len(text)}")
    sha = doc.get("text_sha256")
    if sha is not None and sha != text_sha256(text):
        out.append("text_sha256 does not match text")
    return out


def stored_filings(data: Path, ticker) -> dict:
    """Everything stored for one ticker.

    {"docs": [(doc, rel_path, where)], "tampered": [(rel_path, reasons)]}, where `where` is
    "current" or "archive", current first, then archives newest filing_date first.
    """
    data = Path(data)
    docs, tampered = [], []
    cur = current_path(data, ticker)
    d = _read(cur)
    if isinstance(d, dict) and isinstance(d.get("text"), str):
        reasons = text_integrity(d)
        if reasons:
            tampered.append((_rel(cur, data), reasons))
        else:
            docs.append((d, _rel(cur, data), "current"))
    adir = archive_dir(data, ticker)
    arch = []
    if adir.is_dir():
        for p in sorted(adir.glob("*.json")):
            a = _read(p)
            reasons = archive_integrity(p, a, ticker)
            if reasons:
                tampered.append((_rel(p, data), reasons))
            else:
                arch.append((a, _rel(p, data), "archive"))
    arch.sort(key=lambda t: str(t[0].get("filing_date") or ""), reverse=True)
    return {"docs": docs + arch, "tampered": tampered}


def _rel(p: Path, data: Path) -> str:
    try:
        return str(p.relative_to(data.parent))
    except ValueError:
        return str(p)


def _quote_in_text(quote: str, text: str) -> bool:
    try:
        import check_screen  # noqa: WPS433  one definition of verbatim
        return check_screen.quote_in_text(quote, text)
    except ImportError:
        import evidence_store  # noqa: WPS433  same rule, fallback copy
        return evidence_store.excerpt_in_doc(quote, text)


def find_quote(data: Path, ticker, quote: str, accession=None, url=None, matcher=None) -> dict:
    """Resolve one filing quote.

    `accession` (either spelling) wins; else one parsed from `url`; else no accession.
    `matcher(quote, text)` defaults to check_screen.quote_in_text; check_screen passes its
    own so running it as a script does not import itself a second time.
    Returns {"state", "doc", "path", "where", "accession", "cited", "tampered"} where state is
      MATCH       the quote is verbatim in the cited filing (or, uncited, in some stored one)
      MISMATCH    the filing is stored and does not contain the quote
      NOT_STORED  an accession is cited, the ticker has stored filings, none of them it
      NO_DOC      nothing trustworthy is stored for this ticker at all (cited or not)
    `doc`/`path`/`where` name the filing that matched, or the one compared against.
    """
    cited = normalize_accession(accession) if accession else None
    if cited is None:
        cited = accession_from_url(url)
    store = stored_filings(data, ticker)
    docs = store["docs"]
    res = {"state": None, "doc": None, "path": None, "where": None,
           "accession": None, "cited": cited, "tampered": store["tampered"]}
    if not docs:
        res["state"] = "NO_DOC"
        return res
    if cited:
        docs = [t for t in docs if normalize_accession(t[0].get("accession")) == cited]
        if not docs:
            res["state"] = "NOT_STORED"
            return res
    match = matcher or _quote_in_text
    for doc, path, where in docs:
        if match(quote, doc["text"]):
            res.update(state="MATCH", doc=doc, path=path, where=where,
                       accession=normalize_accession(doc.get("accession")))
            return res
    doc, path, where = docs[0]
    res.update(state="MISMATCH", doc=doc, path=path, where=where,
               accession=normalize_accession(doc.get("accession")))
    return res


def describe(res: dict, ticker) -> str:
    """One plain clause for a non-MATCH result, for gate failure lines."""
    st = res["state"]
    tamper = ""
    if res.get("tampered"):
        tamper = (f"; ignored {len(res['tampered'])} archived file(s) failing integrity: "
                  + ", ".join(p for p, _ in res["tampered"][:3]))
    if st == "NOT_STORED":
        return (f"cites accession {res['cited']}, which is stored neither as "
                f"data/edgar/docs/{safe_name(ticker)}.json nor under "
                f"data/edgar/docs/{ARCHIVE_DIRNAME}/{safe_name(ticker)}/ (fail closed; "
                f"re-request the cited filing or cite one that is stored){tamper}")
    if st == "NO_DOC":
        return (f"no document on disk for {ticker} in data/edgar/docs/ or its archive "
                f"(fail closed, method section 1){tamper}")
    if st == "MISMATCH":
        d = res["doc"] or {}
        scope = (f"{d.get('form')} {d.get('accession')} at {res['path']}" if res["cited"]
                 else f"any stored filing for {ticker} (current or archived)")
        return f"quote does NOT appear verbatim in {scope}{tamper}"
    return st or "?"
