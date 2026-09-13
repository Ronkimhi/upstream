#!/usr/bin/env python3
"""Company-pipeline postlude gate. Stdlib only, offline.

`run pipeline <TICKER>` (Sieve) writes `data/pipelines/<issuer_id>.json`: the issuer's
disclosed order backlog or remaining performance obligations (RPO), named contracts and
awards, product or drug pipeline, and project pipeline (method section 6B). Modeled on
`tools/check_impact.py`, because both stores share the same defect surface: a number typed
from a search snippet with a real, topically relevant URL attached that nobody opened.
Same rule, same excerpt bar, applied to a second store on its first day rather than after
an adversarial pass finds it missing the way it found impact's on 2026-08-30.

`validate_pipeline(root, path, obj=None)` holds the per-file checks and is imported by
`tools/validate.py` (`v_pipeline`), the same wiring `check_profile.validate_profile` and
`check_map.validate_mapping` already use, so a pipeline is checked whether the whole repo
is being validated or just this one gate is being run.

Checks:
  1. SHAPE: required top-level keys, `as_of`/`ticker` format, a non-empty `changelog`, and
     no verdict/entry-price leak anywhere in the file (method section 7: a pipeline is an
     input to a verdict, never one itself, and that vocabulary is Stocky's alone).
  2. ISSUER: `issuer_id` matches the filename and resolves to `data/companies/<issuer_id>.json`
     -- a pipeline for an issuer nobody profiled is a number with no subject.
  3. STATUS: `NONE_FOUND` carries >= 2 `searched` entries (a search that came back empty is
     a real answer and has to be written down to be one); `COMPLETE` carries >= 1 item.
  4. EXCERPT VERBATIM: an `edgar_doc` or `web_doc` item's `source_excerpt` appears, verbatim
     (whitespace/case/punctuation folded), in the stored document its `source_ref` names --
     `data/edgar/docs/<T>.json` for `edgar_doc`, `data/web/<id>.json` for `web_doc`
     (`tools/evidence_store.py`: a missing document, or a stored page that is not HTTP 200,
     fails).
  5. NUMERIC CROSS-CHECK: every number the item's `claim` asserts, and the item's own
     `value`, appears in that excerpt (`impact_score.audit_excerpt`, the same matcher
     `check_impact.py` uses for evidence items).
  6. XBRL: an `xbrl` item is exempt from checks 4 and 5 -- there is no prose document to
     quote -- and instead its `value` must equal the exact number at the
     `data/market/<T>.json` fundamentals field its `source_ref` names
     (`...#fundamentals.<field>`). A structured figure is checked against the structured
     store, not against a sentence.

Every check runs over the whole corpus on every invocation; a pipeline does not become
truer tomorrow than the excerpt it cites today, and this is not a stage with a run-day
freshness distinction the way impact's rank queue or radar's calendar sweep are.

Run: python3 tools/check_pipeline.py [--root PATH]
Exit 0 clean (or over an empty/missing store), 1 on any failure.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_store  # noqa: E402
from impact_score import audit_excerpt  # noqa: E402

ITEM_TYPES = frozenset({"BACKLOG", "RPO", "CONTRACT", "PRODUCT", "PROJECT"})
SOURCE_KINDS = frozenset({"edgar_doc", "web_doc", "xbrl"})
STATUSES = frozenset({"COMPLETE", "PARTIAL", "NONE_FOUND"})
VERDICT_WORDS = frozenset({"INVESTABLE", "WATCH", "TOO_LATE"})
ENTRY_PRICE_KEYS = frozenset({
    "entry_zone", "no_entry_above", "would_buy_zone", "would_buy_basis", "entry_price",
})
ISSUER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_TOP = ("issuer_id", "ticker", "as_of", "generated_by", "status", "items",
                "searched", "notes", "changelog")
_XBRL_REF_RE = re.compile(r"^(data/market/[A-Za-z0-9._\-/]+\.json)#fundamentals\.([A-Za-z0-9_]+)$")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def _verdict_leaks(obj, where: str = "pipeline") -> list[str]:
    """method section 7: a pipeline states no verdict and no entry price. Same recursive
    walk `check_profile._verdict_leaks` uses, because a profile and a pipeline are the same
    kind of thing (an input Stocky reads), never the verdict itself."""
    leaks = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{where}.{key}"
            kk = str(key).casefold()
            if kk == "verdict":
                leaks.append(f"{child}: verdict fields belong only in Stocky dives")
            elif kk in ENTRY_PRICE_KEYS:
                leaks.append(f"{child}: entry-price field does not belong in a pipeline "
                             f"store; a pipeline is an input to a verdict, never one itself")
            leaks.extend(_verdict_leaks(value, child))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            leaks.extend(_verdict_leaks(value, f"{where}[{i}]"))
    elif isinstance(obj, str) and obj.strip().upper() in VERDICT_WORDS:
        leaks.append(f"{where}: {obj!r} is final verdict vocabulary; a pipeline is disclosed "
                     f"backlog, not a verdict")
    return leaks


def _shape_failures(path: Path, obj: dict) -> list[str]:
    out = []
    missing = [k for k in REQUIRED_TOP if k not in obj]
    if missing:
        out.append(f"missing keys: {', '.join(missing)}")
    if not DATE_RE.match(str(obj.get("as_of"))):
        out.append(f"as_of {obj.get('as_of')!r} is not YYYY-MM-DD")
    if not TICKER_RE.match(str(obj.get("ticker"))):
        out.append(f"ticker {obj.get('ticker')!r} is not a valid ticker shape")
    cl = obj.get("changelog")
    if not isinstance(cl, list) or not cl:
        out.append("changelog must be a non-empty list")
    else:
        for i, c in enumerate(cl):
            if not isinstance(c, dict) or not {"ts", "by", "change"} <= set(c):
                out.append(f"changelog[{i}] needs ts, by, change")
    out.extend(_verdict_leaks(obj))
    return out


def _issuer_failures(data: Path, obj: dict, path: Path) -> list[str]:
    stem = path.stem
    iid = obj.get("issuer_id")
    if not isinstance(iid, str) or not ISSUER_ID_RE.fullmatch(iid):
        return [f"issuer_id {iid!r} does not match {ISSUER_ID_RE.pattern}"]
    out = []
    if iid != stem:
        out.append(f"issuer_id {iid!r} disagrees with filename {path.name}")
    if not (data / "companies" / f"{iid}.json").exists():
        out.append(f"issuer_id {iid!r} has no data/companies/{iid}.json profile -- a "
                   f"pipeline for an issuer nobody profiled is a number with no subject")
    return out


def _status_failures(obj: dict) -> list[str]:
    out = []
    status = obj.get("status")
    if status not in STATUSES:
        return [f"status {status!r} not in {sorted(STATUSES)}"]
    items = obj.get("items") if isinstance(obj.get("items"), list) else []
    searched = obj.get("searched") if isinstance(obj.get("searched"), list) else []
    for j, s in enumerate(obj.get("searched") or []):
        if not isinstance(s, dict) or not str(s.get("query_or_url") or "").strip() \
                or not str(s.get("result") or "").strip():
            out.append(f"searched[{j}] needs a non-empty query_or_url and result")
    if status == "COMPLETE" and len(items) < 1:
        out.append(f"status COMPLETE needs >= 1 item, found {len(items)}")
    elif status == "NONE_FOUND":
        if len(searched) < 2:
            out.append(f"status NONE_FOUND needs >= 2 searched entries, found "
                       f"{len(searched)} -- a search that came back empty is a real answer "
                       f"and has to be written down to be one")
        if items:
            out.append(f"status NONE_FOUND but items is non-empty ({len(items)})")
    return out


def _claim_with_value(item: dict) -> str:
    """`audit_excerpt` scans one string for numbers. `value` is a separate JSON field, not
    always restated inside `claim` prose, so it is folded in here rather than every caller
    re-deriving the same string -- "every number in claim AND value" as one scan."""
    claim = str(item.get("claim") or "")
    value = item.get("value")
    if value is None:
        return claim
    unit = str(item.get("currency") or "").strip()
    return f"{claim} ({value} {unit})".strip()


def _xbrl_failures(item: dict, root: Path) -> list[str]:
    ref = item.get("source_ref")
    m = _XBRL_REF_RE.match(str(ref)) if isinstance(ref, str) else None
    if not m:
        return [f"xbrl source_ref must be 'data/market/<T>.json#fundamentals.<field>', "
                f"got {ref!r}"]
    mpath, field = m.group(1), m.group(2)
    market = read_json(root / mpath)
    if not isinstance(market, dict):
        return [f"no market file at {mpath} to check the xbrl value against"]
    val = item.get("value")
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return [f"value must be a number for an xbrl item, got {val!r}"]
    found = (market.get("fundamentals") or {}).get(field, "<missing>")
    if isinstance(found, bool) or not isinstance(found, (int, float)):
        return [f"{mpath}#fundamentals.{field} is {found!r}, not a number -- an xbrl item's "
                f"value is that field's own number, exactly"]
    if abs(float(found) - float(val)) > 1e-6:
        return [f"value {val!r} does not equal {mpath}#fundamentals.{field} ({found!r}) -- "
                f"an xbrl item's value is the market file's own number, exactly"]
    return []


def _doc_failures(item: dict, excerpt: str, data: Path) -> list[str]:
    kind = item.get("source_kind")
    if kind == "web_doc":
        url = item.get("source_url")
        res = evidence_store.verify(data, {"url": url, "source_excerpt": excerpt})
        state = res["state"]
        if state != "MATCH":
            reason = {
                "UNFETCHED": "no stored fetch for this url in data/web/ -- queue a web_doc "
                             "request and cite from the stored text (pull-data skill)",
                "NO_URL": "source_url is missing or not http(s)",
                "MISMATCH": "the stored page does not contain this excerpt",
                "EMPTY": "the stored fetch has no text (JS-rendered or binary)",
                "NO_EXCERPT": "no source_excerpt to check",
            }.get(state, f"the stored fetch answered {state}, not 200")
            return [f"web store state {state} for {url!r} -- {reason}"]
        expected_ref = f"data/web/{evidence_store.web_doc_id(url)}.json"
        if item.get("source_ref") != expected_ref:
            return [f"source_ref {item.get('source_ref')!r} does not name the stored "
                    f"document for source_url ({expected_ref!r})"]
        return []
    # edgar_doc
    ref = item.get("source_ref")
    if not isinstance(ref, str) or not ref.startswith("data/edgar/docs/") \
            or not ref.endswith(".json"):
        return [f"source_ref {ref!r} must be data/edgar/docs/<T>.json for an edgar_doc item"]
    doc = read_json(data.parent / ref)
    if not isinstance(doc, dict) or not isinstance(doc.get("text"), str):
        return [f"cites a filing with no document on disk at {ref} -- a quote that cannot "
                f"be verified is not evidence (method section 1)"]
    if not evidence_store.excerpt_in_doc(excerpt, doc["text"]):
        return [f"source_excerpt does not appear verbatim in {ref}"]
    return []


def _item_failures(item, root: Path, data: Path) -> list[str]:
    """All findings for one items[] entry. An empty return means the item is SCORED."""
    if not isinstance(item, dict):
        return ["not an object"]
    out = []
    if item.get("type") not in ITEM_TYPES:
        out.append(f"type: {item.get('type')!r} not in {sorted(ITEM_TYPES)}")
    if not str(item.get("name") or "").strip():
        out.append("name is empty")
    if not str(item.get("claim") or "").strip():
        out.append("claim is empty")
    if not DATE_RE.match(str(item.get("date"))):
        out.append(f"date {item.get('date')!r} is not YYYY-MM-DD (method section 1: a "
                   f"number without both source and date does not exist)")
    kind = item.get("source_kind")
    if kind not in SOURCE_KINDS:
        out.append(f"source_kind: {kind!r} not in {sorted(SOURCE_KINDS)}")
    value = item.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        out.append(f"value must be a number or null, got {value!r}")
    if not str(item.get("source_url") or "").startswith(("http://", "https://")):
        out.append("source_url must be a fetchable http(s) URL")
    excerpt = item.get("source_excerpt")
    if not isinstance(excerpt, str) or not excerpt.strip():
        out.append("carries no source_excerpt")
    if out or kind not in SOURCE_KINDS:
        return out

    if kind == "xbrl":
        # No prose document to quote: exempt from the excerpt-verbatim and numeric-cross-
        # check bars below (checks 4 and 5). Its own value-equality check is check 6.
        return _xbrl_failures(item, root)

    doc_findings = _doc_failures(item, excerpt, data)
    if doc_findings:
        return doc_findings
    audit = audit_excerpt({"claim": _claim_with_value(item), "source_excerpt": excerpt})
    return list(audit["findings"])


def _items_failures(root: Path, data: Path, obj: dict) -> tuple[list[str], int, int]:
    """(findings, total items, scored items) for one pipeline's items[]."""
    items = obj.get("items")
    if not isinstance(items, list):
        return (["items must be a list"], 0, 0)
    findings, scored = [], 0
    for i, item in enumerate(items):
        item_findings = _item_failures(item, root, data)
        if item_findings:
            findings.extend(f"items[{i}]: {msg}" for msg in item_findings)
        else:
            scored += 1
    return findings, len(items), scored


def validate_pipeline(root: Path, path: Path, obj=None) -> list[str]:
    """Return schema, source-discipline and reference failures for one pipeline.

    Imported by `tools/validate.py` (`v_pipeline`), same wiring as
    `check_profile.validate_profile` / `check_map.validate_mapping`.
    """
    pipeline = obj if isinstance(obj, dict) else read_json(path)
    if not isinstance(pipeline, dict):
        return ["pipeline must be a readable JSON object"]
    data = root / "data"
    findings = []
    findings += _shape_failures(path, pipeline)
    findings += _issuer_failures(data, pipeline, path)
    findings += _status_failures(pipeline)
    item_findings, _total, _scored = _items_failures(root, data, pipeline)
    findings += item_findings
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    data = root / "data"
    folder = data / "pipelines"

    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    paths = [p for p in paths if not p.name.startswith("_")]
    if not paths:
        state = "exists, 0 pipelines on disk" if folder.exists() else "does not exist"
        print(f"check_pipeline: NO STORE (data/pipelines/ {state}). Armed for the first "
              f"pipeline written.")
        return 0

    failures = []
    statuses = {k: 0 for k in STATUSES}
    total_items = scored_items = 0
    for path in paths:
        obj = read_json(path)
        if isinstance(obj, dict):
            statuses[obj.get("status")] = statuses.get(obj.get("status"), 0) + 1
        for finding in validate_pipeline(root, path, obj):
            failures.append(f"{path.name}: {finding}")
        if isinstance(obj, dict):
            _, total, scored = _items_failures(root, data, obj)
            total_items += total
            scored_items += scored

    print(f"check_pipeline: {len(paths)} pipeline(s) on disk, statuses {statuses}, "
          f"{scored_items}/{total_items} item(s) scored"
          + ("  <- none on disk, so the excerpt bar passed over nothing"
             if not total_items else ""))
    if failures:
        for finding in failures:
            print(f"  FAIL  {finding}")
        print(f"check_pipeline: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_pipeline: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
