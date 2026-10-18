"""Build the static-site dataset from fetched Rule 605 amended-format files.

Outputs (data/generated/):
  meta.json          — venues, dimensions, counts, receipts summary
  summary.json       — OES summary rows (the cross-venue comparison surface)
  rollup.json        — per-venue rollups from detailed files (scale + validation)
  symbols/<x>.json   — per-symbol per-venue aggregates (letter-sharded)
  search_index.json  — compact symbol index for search
  validation.json    — internal cross-checks (detailed <-> summary)
  coverage.json      — publisher status for the first amended month
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import os
import statistics
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from aggregate import (  # noqa: E402
    Group, iter_detailed, parse_summary, pack_all_row, pack_size_row,
    row_stat_suspect, SUM_FIELDS, SUMMARY_STAT_FIELDS,
)
import reporters  # noqa: E402
from reporters import (  # noqa: E402
    COVERAGE, LIVE, MONTH, MONTH_LABEL, ORDER_SIZES, ORDER_TYPES_SUMMARY, PARTICIPANT_NAMES, _nice_name,
)

RAW_DIR = PROJECT_DIR / "data" / "raw" / MONTH
GEN_DIR = PROJECT_DIR / "data" / "generated" / MONTH
BUILT_AT = None
GENERATED_AT = None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n")


def read_json(path: Path):
    return json.loads(path.read_text())


def open_detailed_stream(zip_path: Path):
    """Return a text line iterator over the detailed member of a downloaded zip."""
    zf = zipfile.ZipFile(zip_path)
    names = [n for n in zf.namelist() if n.lower().endswith((".txt", ".dat"))]
    if not names:
        raise RuntimeError(f"no .txt/.dat member in {zip_path}")
    member = zf.open(names[0])
    return zf, member


# ---------------------------------------------------------------------------
# Per-venue detailed pass
# ---------------------------------------------------------------------------

SIZE_ORDER = {code: i for i, (code, _l) in enumerate(ORDER_SIZES)}


def shard_id(sym):
    """256-way symbol shard: FNV-1a (32-bit) of the symbol's UTF-8 bytes, low byte as 2 hex digits.
    Mirrored exactly in frontend/common.js (EQ.shardId); tests/test_shard_parity.py checks all symbols."""
    h = 0x811C9DC5
    for b in sym.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return f"{h & 0xFF:02x}"
EFQ_SAMPLE_ROWS = 60000


def detect_efq_scale(zip_path):
    """Per-reporter EFQ unit detection from the filed detailed rows (never assumed from a sibling).

    EFQ (effective / quoted spread) is a fraction in the OES schema (typical 0.3-1.5), but a filer may write
    percent-points (31.4). Within every row, EFQ should track AvgEffctvSprd / ExctdOrderAvgQuotSprd (the two
    dollar spreads in the same row): the median of EFQ / (eff$ / quoted$) is ~1 for fractions and ~100 for
    percent-points, two orders of magnitude apart. Where too few rows carry both spreads, the median |EFQ|
    itself is the fallback (cut at 5). The verdict and both pieces of evidence go to validation.json."""
    zf, member = open_detailed_stream(zip_path)
    vals, ratios = [], []
    try:
        for row in iter_detailed(io.TextIOWrapper(member, encoding="utf-8", errors="replace")):
            v = row.get("AvgEFQPct")
            if v is None:
                continue
            vals.append(abs(v))
            eff, quo = row.get("AvgEffctvSprd"), row.get("ExctdOrderAvgQuotSprd")
            if eff and quo and eff > 0 and quo > 0 and v > 0:
                ratios.append(v / (eff / quo))
            if len(vals) >= EFQ_SAMPLE_ROWS:
                break
    finally:
        zf.close()
    med_abs = round(statistics.median(vals), 4) if vals else None
    med_ratio = round(statistics.median(ratios), 3) if ratios else None
    if len(ratios) >= 5:
        conv, basis = ("percent_points" if med_ratio > 10 else "fraction"), "row_spread_ratio"
    elif len(vals) >= 20:
        conv, basis = ("percent_points" if med_abs > 5 else "fraction"), "median_abs"
    else:
        conv, basis = "unverifiable", None
    return {"convention": conv, "basis": basis, "n": len(vals), "median_abs": med_abs,
            "row_ratio_n": len(ratios), "row_ratio_median": med_ratio}


class VenueAgg:
    def __init__(self, ric):
        self.ric = ric
        self.totals = Group()
        self.by_size = {}
        self.by_size_type = {}          # (size, type_code) -> Group
        self.symbols = {}               # sym -> {"sizes": {size: Group}, "all": Group}
        self.row_count = 0
        self.participants = {}          # DsgntParticipant value -> rows
        self.row_rics = {}              # RprtEntityCd value -> rows
        self.type_codes = {}            # OrderType code -> rows
        self.exec_gt_qty_rows = 0       # rows whose executed (own + away) exceeds covered qty by >0.1%
        # data-quality counters (notional sentinel detection)
        self.mxxnn_round_boundary_rows = 0
        self.mxxnn_round_boundary_sentinel_rows = 0
        # sanity-filter counters (weighted stats only; sums unaffected)
        self.stat_suspect_rows = 0
        self.stat_suspect_weight_share_num = 0.0
        self.weight_total = 0.0
        self.packed_symbols = None      # filled by finalize(): sym -> {"s": [...], "a": [...]}

    def add(self, row, stats_ok=True):
        self.row_count += 1
        size = row["size"]
        tcode = row["type_code"]
        sym = row["symbol"]
        self.participants[row["participant"]] = self.participants.get(row["participant"], 0) + 1
        self.row_rics[row["ric"]] = self.row_rics.get(row["ric"], 0) + 1
        self.type_codes[tcode] = self.type_codes.get(tcode, 0) + 1
        if not stats_ok:
            self.stat_suspect_rows += 1
        et, cq = row.get("ExctdTotalQty"), row.get("CvrdOrderQty")
        if et is not None and cq is not None and et > cq * 1.001 + 1e-6:
            self.exec_gt_qty_rows += 1
        w = row.get("_weight")
        if w and w > 0:
            self.weight_total += w
            if not stats_ok:
                self.stat_suspect_weight_share_num += w
        # Notional sentinel audit: market-order round-lot rows at boundary buckets.
        if tcode in ("MXXNN", "MXXNY") and row["lot_size_flag"] == "ROUND" and size in ("0", "200000"):
            qty = row.get("CvrdOrderQty")
            sz = row.get("CvrdOrderSize")
            if qty and qty > 0 and sz is not None:
                self.mxxnn_round_boundary_rows += 1
                ratio = sz / qty
                if ratio < 0.001 or ratio > 100000:
                    self.mxxnn_round_boundary_sentinel_rows += 1
        self.totals.add_row(row, stats_ok)
        self.by_size.setdefault(size, Group()).add_row(row, stats_ok)
        self.by_size_type.setdefault((size, tcode), Group()).add_row(row, stats_ok)
        s = self.symbols.get(sym)
        if s is None:
            s = self.symbols[sym] = {"sizes": {}, "all": Group()}
        s["sizes"].setdefault(size, Group()).add_row(row, stats_ok)
        s["all"].add_row(row, stats_ok)

    def finalize(self):
        """Pack per-symbol accumulators (keeps the cross-process payload small)."""
        packed = {}
        for sym, s in self.symbols.items():
            sizes = sorted(s["sizes"].items(), key=lambda kv: SIZE_ORDER.get(kv[0], 99))
            packed[sym] = {"s": [pack_size_row(code, g) for code, g in sizes], "a": pack_all_row(s["all"])}
        self.packed_symbols = packed
        self.symbols = None
        return self


def run_detailed(rep, raw_dir):
    ric = rep["ric"]
    zip_path = Path(raw_dir) / ric / "detailed.zip"
    agg = VenueAgg(ric)
    efq = detect_efq_scale(zip_path)
    agg.efq_detection = efq
    scale_down = efq["convention"] == "percent_points"
    zf, member = open_detailed_stream(zip_path)
    try:
        for row in iter_detailed(io.TextIOWrapper(member, encoding="utf-8", errors="replace")):
            if scale_down and row.get("AvgEFQPct") is not None:
                # Normalize to the OES-spec fraction convention (31.4 -> 0.314).
                row["AvgEFQPct"] = row["AvgEFQPct"] / 100.0
            agg.add(row, stats_ok=not row_stat_suspect(row))
    finally:
        zf.close()
    agg.finalize()
    print(f"[build] {ric}: {agg.row_count} rows, {len(agg.packed_symbols)} symbols, "
          f"efq={efq['convention']}, boundary sentinel rows "
          f"{agg.mxxnn_round_boundary_sentinel_rows}/{agg.mxxnn_round_boundary_rows}, "
          f"stat-suspect rows {agg.stat_suspect_rows}", flush=True)
    return agg


def summary_efq_evidence(rows):
    """Median of raw EFQ / (effective / quoted) over filed summary rows: ~1 = fractions, ~100 = percent-points."""
    ratios = []
    for r in rows:
        eff, quo, efq = r["AvgEffctvSprdPct"], r["AvgPctQuotSprd"], r["AvgEFQPct"]
        if eff and quo and efq:
            ratios.append(efq / (eff / quo))
    if not ratios:
        return {"n": 0, "median": None, "min": None, "max": None}
    ratios.sort()
    return {"n": len(ratios), "median": round(ratios[len(ratios) // 2], 3),
            "min": round(ratios[0], 3), "max": round(ratios[-1], 3)}


def load_restatement_ledger():
    """Optional. Written when a later fetch replaces first-filed bytes.
    Absent on a month that has not been restated — the build still runs."""
    return _read_json_if(RAW_DIR / "restatement-ledger.json") or {}


def load_receipts():
    """Merge fetch.py receipts (exchange hosts) with harvest_s3.py receipts (shared host)."""
    base = read_json(RAW_DIR / "receipts.json")
    files = list(base["files"])
    out = {"month": base.get("month", MONTH), "fetched_at": base.get("fetched_at"), "files": files}
    hp = RAW_DIR / "harvest-receipts.json"
    if hp.exists():
        h = read_json(hp)
        out["harvested_at"] = h.get("harvested_at")
        for f in h["files"]:
            row = {
                "ric": f["ric"], "kind": f["kind"], "url": f["url"], "ok": f["ok"],
                "bytes": f.get("bytes"), "sha256": f.get("sha256"),
                "last_modified": f.get("last_modified"), "fetched_at": f.get("fetched_at"),
            }
            if f.get("restated"):
                row["restated"] = True
            files.append(row)
    fp = RAW_DIR / "firm-harvest-receipts.json"
    if fp.exists():
        h = read_json(fp)
        out["firm_harvested_at"] = h.get("harvested_at")
        for f in h["files"]:
            row = {
                "ric": f["ric"], "kind": f["kind"], "url": f.get("url"), "ok": f["ok"],
                "bytes": f.get("bytes"), "sha256": f.get("sha256"),
                "last_modified": f.get("last_modified"), "fetched_at": f.get("fetched_at"),
                "note": f.get("note"),
            }
            if f.get("restated"):
                row["restated"] = True
            files.append(row)
    live = {r["ric"] for r in LIVE}
    seen, uniq = set(), []
    for f in files:                      # first source wins per (code, kind), matching the loader
        k = (f["ric"], f["kind"])
        if f["ric"] in live and k not in seen:
            seen.add(k)
            uniq.append(f)
    out["files"] = uniq
    return out


def _read_json_if(path):
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text())


def configure(month, restamp=False):
    """Bind this process at one month. August keeps the stamps already published."""
    global RAW_DIR, GEN_DIR, MONTH, MONTH_LABEL, LIVE, BUILT_AT, GENERATED_AT
    reporters.activate(month)
    MONTH = reporters.MONTH
    MONTH_LABEL = reporters.MONTH_LABEL
    LIVE = reporters.LIVE
    RAW_DIR = PROJECT_DIR / "data" / "raw" / MONTH
    GEN_DIR = PROJECT_DIR / "data" / "generated" / MONTH
    if MONTH != "202608":
        missing = [n for n in ("receipts.json", "harvest-receipts.json", "probes.json")
                   if not (RAW_DIR / n).exists()]
        if missing:
            raise SystemExit(
                f"refusing to build {MONTH}: missing {missing}. "
                "A partial month is not a build, and August's files are not a substitute."
            )
    legacy = PROJECT_DIR / "data" / "generated"
    prior_meta = _read_json_if(GEN_DIR / "meta.json")
    if prior_meta is None and MONTH == "202608":
        prior_meta = _read_json_if(legacy / "meta.json")
    if prior_meta and prior_meta.get("month") != MONTH:
        prior_meta = None
    prior_val = _read_json_if(GEN_DIR / "validation.json")
    if prior_val is None and MONTH == "202608":
        prior_val = _read_json_if(legacy / "validation.json")
    BUILT_AT = prior_meta["built_at"] if prior_meta and not restamp else now_iso()
    GENERATED_AT = prior_val["generated_at"] if prior_val and not restamp else now_iso()


def month_word():
    """The word already baked into the August coverage notes. Later months use their own name."""
    return "August" if MONTH == "202608" else MONTH_LABEL.split(" ")[0]


def parse_args(argv):
    p = argparse.ArgumentParser(description="Build one Rule 605 month")
    p.add_argument("--month", default="202608")
    p.add_argument("--publish", action="store_true",
                   help="Mark this month published in months.json. August is always published. "
                        "Any other month also needs receipts, probes, harvest and a FINRA sweep.")
    p.add_argument("--restamp", action="store_true", help="Write a new built_at instead of reusing the published stamp")
    return p.parse_args(argv)


def write_months_index(publish):
    """Published months only drive the picker. A month absent from a later ledger is not filled in."""
    path = PROJECT_DIR / "data" / "generated" / "months.json"
    prior = _read_json_if(path) or {"months": []}
    already = next((m for m in prior.get("months", []) if m.get("id") == MONTH), None)
    make_public = MONTH == "202608" or publish or bool(already and already.get("published"))
    if make_public and MONTH != "202608":
        missing = [n for n in ("receipts.json", "harvest-receipts.json", "probes.json", "finra-sweep.json")
                   if not (RAW_DIR / n).exists()]
        if missing:
            raise SystemExit(f"refusing to publish {MONTH}: missing {missing}")
    months = [m for m in prior.get("months", []) if m.get("id") != MONTH]
    months.append({"id": MONTH, "label": MONTH_LABEL, "published": bool(make_public)})
    months.sort(key=lambda m: m["id"])
    published_ids = [m["id"] for m in months if m["published"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "default": published_ids[-1] if published_ids else None,
        "months": months,
    }, indent=2) + "\n")


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    configure(args.month, restamp=args.restamp)
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    receipts = load_receipts()

    # ---- detailed passes (parallel, largest first) -------------------------
    order = sorted(LIVE, key=lambda r: -(RAW_DIR / r["ric"] / "detailed.zip").stat().st_size)
    venue_aggs = {}
    workers = max(2, min(12, (os.cpu_count() or 4) - 2))
    print(f"[build] {len(LIVE)} reporters, {workers} workers")
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_detailed, rep, str(RAW_DIR)): rep["ric"] for rep in order}
        for fut in cf.as_completed(futs):
            venue_aggs[futs[fut]] = fut.result()

    # ---- summaries -------------------------------------------------------
    summary_rows = []
    summary_seen = {}
    summary_parsed = {}
    summary_evidence = {}               # raw (pre-normalization) EFQ ratio evidence per reporter
    venue_meta = []
    for i, rep in enumerate(LIVE):
        agg = venue_aggs[rep["ric"]]
        srows = parse_summary((RAW_DIR / rep["ric"] / "summary.csv").read_text(encoding="utf-8-sig"))
        summary_parsed[rep["ric"]] = srows
        ev = summary_evidence[rep["ric"]] = summary_efq_evidence(srows)
        det = agg.efq_detection
        if det["convention"] == "unverifiable" and ev["median"] is not None:
            # Tiny detailed file: fall back to the filed summary ratio (independent evidence).
            det["convention"] = "percent_points" if ev["median"] > 10 else "fraction"
            det["source"] = "summary_ratio"
            if det["convention"] == "percent_points":
                raise SystemExit(f"{rep['ric']}: percent-points EFQ detected from summary only; "
                                 "detailed rows were aggregated unscaled — rerun with a detailed-side fix")
        # participant as filed (detailed rows first, then summary rows); registry value is a fallback
        counts = dict(agg.participants)
        if not counts:
            for r in srows:
                counts[r["participant"]] = counts.get(r["participant"], 0) + 1
        participant = max(counts, key=counts.get) if counts else rep.get("participant", "")
        venue_meta.append({
            "idx": i,
            "ric": rep["ric"],
            "name": rep["name"],
            "short": rep["short"],
            "org": rep["org"],
            "participant": participant,
            "participant_name": PARTICIPANT_NAMES.get(participant, participant),
            "kind": rep["kind"],
            "link_site": rep["link_site"],
            "detailed_rows": agg.row_count,
            "nil_report": agg.row_count == 0,
            "summary_rows": len(srows),
        })
    ric_to_vidx = {v["ric"]: v["idx"] for v in venue_meta}

    for rep in LIVE:
        vidx = ric_to_vidx[rep["ric"]]
        agg = venue_aggs[rep["ric"]]
        scale_down = agg.efq_detection["convention"] == "percent_points"
        for r in summary_parsed[rep["ric"]]:
            if r["ric"] != rep["ric"]:
                print(f"[build] summary {rep['ric']}: skipping row for ric={r['ric']}")
                continue
            tcode = r["type_code"]
            if tcode not in {code for code, _l in ORDER_TYPES_SUMMARY}:
                print(f"[build] summary {rep['ric']}: unknown OrderType {tcode!r} — kept with raw code")
            if scale_down and r.get("AvgEFQPct") is not None:
                r["AvgEFQPct"] = r["AvgEFQPct"] / 100.0
            # sanity flags (published values are kept; flags annotate): bit1 eff>10%,
            # bit2 quoted>10%, bit4 |EFQ|>3, bit8 core stat missing
            flags = 0
            if r["AvgEffctvSprdPct"] is not None and abs(r["AvgEffctvSprdPct"]) > 0.10:
                flags |= 1
            if r["AvgPctQuotSprd"] is not None and abs(r["AvgPctQuotSprd"]) > 0.10:
                flags |= 2
            if r["AvgEFQPct"] is not None and abs(r["AvgEFQPct"]) > 3:
                flags |= 4
            if r["AvgEffctvSprdPct"] is None or r["AvgEFQPct"] is None:
                flags |= 8
            vals = [r[f] for f in SUMMARY_STAT_FIELDS]
            summary_rows.append([vidx, r["sp500"], r["size"], tcode] + vals + [flags])
            summary_seen[rep["ric"]] = summary_seen.get(rep["ric"], 0) + 1
    summary_rows.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    print(f"[build] summary rows: {len(summary_rows)} across {len(summary_seen)} reporters")

    # ---- rollup.json -----------------------------------------------------
    rollup = {}
    for rep in LIVE:
        a = venue_aggs[rep["ric"]]
        entry = {
            "cnt": int(a.totals.sums["CvrdOrderCnt"]),
            "qty": round(a.totals.sums["CvrdOrderQty"], 4),
            "cncl": round(a.totals.sums["CvrdOrderCnclQty"], 4),
            "exctd": round(a.totals.sums["ExctdTotalQty"], 4),
            "own": round(a.totals.sums["CvrdOrderExctdQty"], 4),
            "away": round(a.totals.sums["CvrdOrderExctdAwayQty"], 4),
            "pi": round(a.totals.sums["ExctdPIOrdersQty"], 4),
            "filled": int(a.totals.sums["FilledOrderCnt"]),
            "by_size": {},
        }
        for size, _label in ORDER_SIZES:
            g = a.by_size.get(size)
            if g is None:
                continue
            entry["by_size"][size] = [
                int(g.sums["CvrdOrderCnt"]),
                round(g.sums["ExctdTotalQty"], 4),
                round(g.sums["ExctdPIOrdersQty"], 4),
            ]
        rollup[rep["ric"]] = entry
    write_json(GEN_DIR / "rollup.json", rollup)

    # ---- summary.json (compact rows: venue idx, sp500, size, type, 12 stats) ----
    write_json(GEN_DIR / "summary.json", {"rows": summary_rows})

    # ---- symbol shards + search index -------------------------------------
    shards = {}
    index = {}
    for rep in LIVE:
        a = venue_aggs[rep["ric"]]
        vidx = ric_to_vidx[rep["ric"]]
        for sym, packed in a.packed_symbols.items():
            shards.setdefault(shard_id(sym), {}).setdefault(sym, {})[rep["ric"]] = packed
            arr = packed["a"]                # [cnt, exctd, pi, effW, efqW]
            cur = index.get(sym)
            if cur is None:
                cur = index[sym] = [[], 0, 0.0, [0.0, 0.0], [0.0, 0.0]]
            cur[0].append(vidx)
            cur[1] += arr[0]
            cur[2] += arr[1]
            # weighted stat accumulation (weight = executed qty)
            if arr[3] is not None and arr[1] > 0:
                cur[3][0] += arr[3] * arr[1]
                cur[3][1] += arr[1]
            if arr[4] is not None and arr[1] > 0:
                cur[4][0] += arr[4] * arr[1]
                cur[4][1] += arr[1]
    total_symbols = len(index)

    sym_dir = GEN_DIR / "symbols"
    sym_dir.mkdir(parents=True, exist_ok=True)
    for old in sym_dir.glob("*.json"):
        old.unlink()
    for sid, obj in shards.items():
        write_json(sym_dir / f"{sid}.json", obj)
    print(f"[build] symbols: {total_symbols} across {len(shards)} shards")

    # search index row: [sym, [venue idx...], cnt, exctd, effSprdPctW, efqPctW]
    rows = []
    for sym, cur in sorted(index.items()):
        effw = round(cur[3][0] / cur[3][1], 6) if cur[3][1] > 0 else None
        efqw = round(cur[4][0] / cur[4][1], 6) if cur[4][1] > 0 else None
        rows.append([sym, sorted(cur[0]), int(cur[1]), round(cur[2], 4), effw, efqw])
    write_json(GEN_DIR / "search_index.json", {"venues": [v["ric"] for v in venue_meta], "rows": rows})
    print(f"[build] search index rows: {len(rows)}")

    # ---- validation -------------------------------------------------------
    validation = run_validation(venue_aggs, summary_rows, venue_meta, summary_evidence)
    write_json(GEN_DIR / "validation.json", validation)

    # ---- meta -------------------------------------------------------------
    ledger = load_restatement_ledger()
    meta = {
        "month": MONTH,
        "month_label": MONTH_LABEL,
        "built_at": BUILT_AT,
        "venues": venue_meta,
        "sizes": [[code, label] for code, label in ORDER_SIZES],
        "summary_types": [[code, label] for code, label in ORDER_TYPES_SUMMARY],
        "summary_stat_fields": SUMMARY_STAT_FIELDS,
        "counts": {
            "venues": len(LIVE),
            "symbols": total_symbols,
            "summary_rows": len(summary_rows),
            "detailed_rows": sum(a.row_count for a in venue_aggs.values()),
            "nil_reports": sum(1 for v in venue_meta if v["nil_report"]),
            "reporters_with_summary_rows": sum(1 for v in venue_meta if v["summary_rows"]),
        },
        "receipts": receipts,
        "files_as_of": ledger.get("files_as_of"),
        "restated_rics": sorted({e["ric"] for e in ledger.get("replaced", [])}),
        "withdrawn": ledger.get("withdrawn_reporters", []),
        "summary_flag_bits": {
            "1": "effective spread > 10% (as filed)",
            "2": "quoted spread > 10% (as filed)",
            "4": "|EFQ| > 300% (as filed)",
            "8": "core statistic missing",
        },
        "notes": [
            "Percentage statistics from detailed reports are share-weighted averages of the "
            "reported per-symbol averages (weight = executed quantity).",
            "Summary statistics are venue-published aggregates (OES schema), used with one "
            "documented normalization: reporters that file AvgEFQPct in percent-points (the four "
            "Cboe exchanges) are normalized to the spec's fraction convention. The convention is "
            "detected per reporter from the filed rows, never assumed (see validation.json).",
            "Notional values (CvrdOrderSize) are excluded from published aggregates — a subset "
            "of rows carries sentinel-like corrupted values (see validation.json "
            "data_quality_findings).",
        ],
    }
    write_json(GEN_DIR / "meta.json", meta)

    # ---- coverage ---------------------------------------------------------
    write_json(GEN_DIR / "coverage.json", build_coverage(venue_meta, venue_aggs))

    write_months_index(args.publish)
    print("[build] done")
    return 0


def _exchange_entries_from_files(venue_meta):
    """Exchange-group ledger for a month that is not August.

    August's COVERAGE prose is an August artifact. Later months get one row per
    curated exchange that actually has files in this build, plus one row per
    curated exchange that does not. No sentence is copied forward.
    """
    by_org = {}
    for rep in LIVE:
        if rep.get("harvested"):
            continue
        by_org.setdefault(rep["org"], []).append(rep)
    live = {v["ric"]: v for v in venue_meta}
    entries = []
    for org, reps in by_org.items():
        present = [r for r in reps if r["ric"] in live]
        missing = [r["ric"] for r in reps if r["ric"] not in live]
        if present and not missing:
            status = "live_amended"
            evidence = (
                f"{len(present)} amended-format {MONTH_LABEL} file group(s) for {org} "
                f"({', '.join(r['ric'] for r in present)}) are in this build. "
                "Units and layout were detected from these files, not inherited from August."
            )
        elif present:
            status = "live_amended"
            evidence = (
                f"In this build for {MONTH_LABEL}: {', '.join(r['ric'] for r in present)}. "
                f"No file for: {', '.join(missing)}."
            )
        else:
            status = "registered_only"
            evidence = f"No {MONTH_LABEL} file for {org} ({', '.join(missing)}) was in this month's inputs."
        entries.append({
            "org": org,
            "role": "Exchange",
            "status": status,
            "evidence": evidence,
            "url": reps[0].get("link_site"),
        })
    return entries


def build_coverage(venue_meta, venue_aggs):
    """Coverage ledger: exchange-group entries (reporters.COVERAGE), the per-reporter FINRA
    ledger (registered vs published), probe log, and the shared-host census."""
    probes = read_json(RAW_DIR / "probes.json")
    directory = read_json(PROJECT_DIR / "data" / "reporter_directory.json")
    live_by_ric = {v["ric"]: v for v in venue_meta}
    ledger = load_restatement_ledger()
    restated = {e["ric"] for e in ledger.get("replaced", [])}
    withdrawn_note = {e["ric"]: e.get("note", "") for e in ledger.get("withdrawn_reporters", [])}
    harvest = read_json(RAW_DIR / "harvest-receipts.json") if (RAW_DIR / "harvest-receipts.json").exists() else None
    sweep_path = RAW_DIR / "finra-sweep.json"
    sweep = read_json(sweep_path) if sweep_path.exists() else {}
    hfiles = {}
    if harvest:
        for f in harvest["files"]:
            if f["kind"] == "detailed":
                hfiles[f["ric"]] = f
    firm_path = RAW_DIR / "firm-harvest-receipts.json"
    firm = read_json(firm_path) if firm_path.exists() else None
    no_summary = set()
    if firm:
        for f in firm["files"]:
            if f["kind"] == "detailed":
                hfiles.setdefault(f["ric"], f)
            elif "no summary file located" in (f.get("note") or ""):
                no_summary.add(f["ric"])

    reporters = []
    for ric, d in sorted(directory.items(), key=lambda kv: (kv[1].get("type", ""), kv[1]["name"].lower())):
        if not d.get("finra_url"):
            continue                      # exchange venues are in the group entries
        sw = sweep.get(d["slug"], {})
        entry = {
            "ric": ric, "name": _nice_name(d["name"]), "type": d["type"], "finra_url": d["finra_url"],
            "report_url": sw.get("report_url"),
            "latest_submission": (sw.get("submissions") or [{}])[0].get("date") if sw.get("submissions") else None,
        }
        v = live_by_ric.get(ric) or live_by_ric.get("T" + ric)
        if v:
            h = hfiles.get(v["ric"], {})
            entry["status"] = "live_amended"
            if v["ric"] != ric:
                entry["filed_as"] = v["ric"]
            entry["nil_report"] = v["nil_report"]
            entry["detailed_rows"] = v["detailed_rows"]
            entry["file_published"] = h.get("last_modified")
            codes = set(venue_aggs[v["ric"]].type_codes)
            ioc_only = bool(codes) and all(len(c) == 5 and c[3] == "Y" for c in codes)
            if v["nil_report"]:
                entry["note"] = f"Nil report: the {month_word()} file carries a header and no covered-order rows."
            elif not v["summary_rows"]:
                if ioc_only:
                    entry["note"] = ("Detailed file carries IOC order types only; the OES summary is empty by schema "
                                     "(summary covers market and marketable-limit orders).")
                elif v["ric"] in no_summary:
                    entry["note"] = ("No OES summary file was published alongside the detailed file; the detailed file is "
                                     "used, so this reporter appears in the symbol explorer and scale table but not in "
                                     "the comparison table.")
                else:
                    entry["note"] = "OES summary file is empty although the detailed file carries non-IOC order types."
            else:
                entry["note"] = ""
            hf = hfiles.get(v["ric"], {})
            if hf.get("page"):
                extra = f"Read from the firm's own Download Site ({hf['page']})"
                if hf.get("note"):
                    extra += f"; {hf['note']}"
                if v["ric"] != ric:
                    extra += f"; the file's own reporter code is {v['ric']}, not the registered {ric}"
                entry["note"] = (entry["note"] + " " if entry["note"] else "") + extra + "."
            if v["ric"] in restated or ric in restated:
                when = ledger.get("files_as_of") or "a later check"
                extra = (f"Restated after the first harvest. This page shows the file fetched on {when}, "
                         "not the first-filed copy, which is kept with the source build.")
                entry["note"] = (entry["note"] + " " if entry["note"] else "") + extra
        else:
            entry["status"] = sw.get("status") or "registered_only"
            entry["note"] = sw.get("note", "")
            if ric in withdrawn_note:
                entry["status"] = "registered_only"
                entry["note"] = withdrawn_note[ric]
            elif MONTH != "202608" and not entry["note"]:
                entry["note"] = (
                    f"No {MONTH_LABEL} file is in this month's harvest. "
                    "That is not evidence the reporter failed to publish."
                )
        reporters.append(entry)

    counts = {}
    for r in reporters:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    if MONTH == "202608":
        entries = list(COVERAGE)
    else:
        entries = _exchange_entries_from_files(venue_meta)
    n_firms = len(reporters)
    live_firms = counts.get("live_amended", 0)
    others = {k: v for k, v in counts.items() if k != "live_amended"}
    entries.append({
        "org": f"FINRA-registered reporters ({n_firms} firms)",
        "role": "FINRA is the designated participant; each firm registers a reporter code and a Download Site URL",
        "status": "live_amended" if live_firms else "registered_only",
        "evidence": (f"{live_firms} of {n_firms} registered reporters have an amended-format {month_word()} file on the "
                     f"shared Download Site host and are in this build (including nil reports). The other "
                     f"{n_firms - live_firms} are listed below with what was and was not checked: "
                     + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(others.items()))
                     + ". A registered firm with no file located is not evidence it failed to publish: "
                       "the sweep can only see the shared host and Download Site URLs it could reach."),
        "url": "https://www.finra.org/filing-reporting/regulation-nms/sec-rule-605-reports",
    })
    n_dirs = len(harvest["census"]) if harvest else 0
    n_dirs_aug = sum(1 for c in harvest["census"].values() if c.get(f"files_{MONTH}")) if harvest else 0
    return {
        "checked_at": probes.get("checked_at"),
        "harvested_at": harvest.get("harvested_at") if harvest else None,
        "month": MONTH,
        "month_label": MONTH_LABEL,
        "entries": entries,
        "reporters": reporters,
        "reporter_counts": counts,
        "probes": probes.get("probes", []),
        "census": {
            "shared_host_public_s3_com": {
                "directories_scanned": n_dirs,
                f"directories_with_{MONTH}": n_dirs_aug,
                f"reporters_with_{MONTH}_files": len([1 for f in (harvest["files"] if harvest else []) if f["kind"] == "detailed"]),
            },
            "firm_hosted": {
                "pages_read": len(firm["pages"]) if firm else 0,
                "reporters_with_files": len({f["ric"] for f in firm["files"]}) if firm else 0,
            },
        },
    }


# ---------------------------------------------------------------------------
# Validation: cross-check detailed aggregations against published summaries
# ---------------------------------------------------------------------------

def run_validation(venue_aggs, summary_rows, venue_meta, summary_evidence):
    """Cross-checks between detailed-file aggregations and the published summaries.

    The summaries carry only averages/ratios (no counts), so the cross-check is:
    implied averages computed from detailed quantity sums vs the corresponding
    published summary values. Mismatches are recorded, not resolved.
    """
    checks = []
    ric_to_vidx = {v["ric"]: v["idx"] for v in venue_meta}

    sum_lookup = {}
    for r in summary_rows:
        vidx, sp500, size, tcode = r[0], r[1], r[2], r[3]
        sum_lookup[(vidx, sp500, size, tcode)] = dict(zip(SUMMARY_STAT_FIELDS, r[4:]))

    for rep in LIVE:
        agg = venue_aggs[rep["ric"]]
        vidx = ric_to_vidx[rep["ric"]]
        results = []
        for tcode, label, codes in (
            ("M", "MXXNN", {"MXXNN"}),
            ("ML", "LYNNN", {"LYNNN"}),
            ("ML", "LYNNN+LYNYN", {"LYNNN", "LYNYN"}),
        ):
            rows = []
            for size, _lbl in ORDER_SIZES:
                tot = Group()
                for (sz, tc2), g in agg.by_size_type.items():
                    if sz != size or tc2 not in codes:
                        continue
                    for f in SUM_FIELDS:
                        tot.sums[f] += g.sums[f]
                cnt = tot.sums["CvrdOrderCnt"]
                if cnt <= 0:
                    continue
                implied_size = tot.sums["CvrdOrderSize"] / cnt
                implied_qty = tot.sums["CvrdOrderQty"] / cnt
                exctd = tot.sums["ExctdTotalQty"]
                pi_share = (tot.sums["ExctdPIOrdersQty"] / exctd) if exctd > 0 else None
                srow = sum_lookup.get((vidx, "N", size, tcode))
                yrow = sum_lookup.get((vidx, "Y", size, tcode))
                rows.append({
                    "size": size,
                    "implied_avg_order_size": round(implied_size, 4),
                    "implied_avg_order_qty": round(implied_qty, 4),
                    "pi_share_of_executed_shares": None if pi_share is None else round(pi_share, 6),
                    "summary_avg_order_size": None if not srow else srow["AvgOrderSize"],
                    "summary_avg_order_qty": None if not srow else srow["AvgOrderQty"],
                    "summary_pct_exctd_pi": None if not srow else srow["PctExctdPI"],
                    "summary_pct_exctd_pi_sp500": None if not yrow else yrow["PctExctdPI"],
                })
            # aggregate across sub-$200k sizes for the 199999 question
            tot = Group()
            for (sz, tc2), g in agg.by_size_type.items():
                if sz != "200000" and tc2 in codes:
                    for f in SUM_FIELDS:
                        tot.sums[f] += g.sums[f]
            cnt = tot.sums["CvrdOrderCnt"]
            sub200 = None
            if cnt > 0:
                sub200 = {
                    "implied_avg_order_size": round(tot.sums["CvrdOrderSize"] / cnt, 4),
                    "implied_avg_order_qty": round(tot.sums["CvrdOrderQty"] / cnt, 4),
                }
            srow999 = sum_lookup.get((vidx, "N", "199999", tcode))
            results.append({
                "summary_type": tcode, "detailed_candidates": label,
                "by_size": rows,
                "detailed_sub_200k_totals": sub200,
                "summary_199999": None if not srow999 else {
                    k: srow999[k] for k in ("AvgOrderQty", "AvgOrderSize", "PctExctdPI", "AvgEffctvSprdPct")
                },
            })
        checks.append({"check": "family_mapping", "venue": rep["ric"], "results": results})

    # (2) EFQ unit evidence, per reporter (never inherited from a sibling producer).
    # Detection runs on the filed detailed rows; the summary ratio (raw EFQ / (effective/quoted))
    # is independent confirmation where summary rows exist: ~1 = fractions, ~100 = percent-points.
    efq_checks, scan = [], []
    for rep in LIVE:
        agg = venue_aggs[rep["ric"]]
        det = agg.efq_detection
        ev = summary_evidence[rep["ric"]]
        agree = None
        if ev["median"] is not None and det["convention"] != "unverifiable":
            agree = (ev["median"] > 10) == (det["convention"] == "percent_points")
        scan.append({"venue": rep["ric"], "convention": det["convention"], "source": det.get("source", "detailed_rows"),
                     "basis": det.get("basis"), "row_ratio_median": det.get("row_ratio_median"),
                     "row_ratio_n": det.get("row_ratio_n"),
                     "detailed_median_abs": det["median_abs"], "detailed_n": det["n"],
                     "summary_n": ev["n"], "summary_ratio_median": ev["median"],
                     "summary_agrees": agree})
        if det["convention"] == "percent_points":
            efq_checks.append({"venue": rep["ric"], **ev})
    checks.append({
        "check": "efq_normalization",
        "note": "raw EFQ / (effective/quoted) ~= 100 => percent-points; normalized by /100 to spec fractions",
        "venues": efq_checks,
        "convention_scan": scan,
    })

    # (2b) per-file integrity: identifiers as filed vs. the registry
    integrity = []
    for rep in LIVE:
        agg = venue_aggs[rep["ric"]]
        other_rics = {k: n for k, n in agg.row_rics.items() if k != rep["ric"]}
        integrity.append({
            "venue": rep["ric"], "rows": agg.row_count,
            "participants": agg.participants, "order_types": agg.type_codes,
            "rows_with_other_ric": other_rics,
            "rows_executed_exceeds_covered_qty": agg.exec_gt_qty_rows,
        })
    checks.append({"check": "file_integrity", "venues": integrity})



    # (3) data-quality findings from the detailed passes
    dq = []
    for rep in LIVE:
        a = venue_aggs[rep["ric"]]
        if a.mxxnn_round_boundary_rows:
            share = a.mxxnn_round_boundary_sentinel_rows / a.mxxnn_round_boundary_rows
            dq.append({
                "finding": "CvrdOrderSize sentinel rows (market-order round lots, <$250 and >=$200k buckets)",
                "venue": rep["ric"],
                "rows_checked": a.mxxnn_round_boundary_rows,
                "rows_with_sentinel_ratios": a.mxxnn_round_boundary_sentinel_rows,
                "share": round(share, 4),
                "observed_ratio_examples": {"low": 0.0001, "high": 999999.99},
            })
    checks.append({
        "check": "data_quality_findings",
        "note": ("CvrdOrderSize anomalous on some rows (qty x $0.0001 / qty x $999,999.99 pattern) "
                 "in market-order round-lot rows at boundary buckets for some venues; excluded from "
                 "published aggregates. Additionally, some rows are internally inconsistent (effective "
                 "spreads > 100% of midpoint, quoted spreads wider than midpoint); those rows are "
                 "excluded from weighted statistics only (quantity sums unchanged). All other row "
                 "classes reconcile size/qty against reported midpoint within ~+/-10%."),
        "findings": dq,
        "stat_suspect_exclusions": [
            {
                "venue": rep["ric"],
                "rows": venue_aggs[rep["ric"]].stat_suspect_rows,
                "share_of_rows": round(venue_aggs[rep["ric"]].stat_suspect_rows / max(venue_aggs[rep["ric"]].row_count, 1), 5),
                "share_of_executed_volume": round(
                    venue_aggs[rep["ric"]].stat_suspect_weight_share_num / max(venue_aggs[rep["ric"]].weight_total, 1e-9), 6),
            }
            for rep in LIVE
        ],
    })

    return {"generated_at": GENERATED_AT, "checks": checks}


if __name__ == "__main__":
    raise SystemExit(main())
