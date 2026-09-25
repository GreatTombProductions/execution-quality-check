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

import json
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
from reporters import (  # noqa: E402
    COVERAGE, LIVE, MONTH, MONTH_LABEL, ORDER_SIZES, ORDER_TYPES_SUMMARY,
)

RAW_DIR = PROJECT_DIR / "data" / "raw" / MONTH
GEN_DIR = PROJECT_DIR / "data" / "generated"


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

class VenueAgg:
    def __init__(self, ric):
        self.ric = ric
        self.totals = Group()
        self.by_size = {}
        self.by_size_type = {}          # (size, type_code) -> Group
        self.symbols = {}               # sym -> {"sizes": {size: Group}, "all": Group}
        self.row_count = 0
        # data-quality counters (notional sentinel detection)
        self.mxxnn_round_boundary_rows = 0
        self.mxxnn_round_boundary_sentinel_rows = 0
        # sanity-filter counters (weighted stats only; sums unaffected)
        self.stat_suspect_rows = 0
        self.stat_suspect_weight_share_num = 0.0
        self.weight_total = 0.0

    def add(self, row, stats_ok=True):
        self.row_count += 1
        size = row["size"]
        tcode = row["type_code"]
        sym = row["symbol"]
        if not stats_ok:
            self.stat_suspect_rows += 1
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


def run_detailed(rep):
    ric = rep["ric"]
    zip_path = RAW_DIR / ric / "detailed.zip"
    print(f"[build] detailed {ric}: streaming {zip_path.name}")
    agg = VenueAgg(ric)
    efq_points = rep.get("efq_percent_points", False)
    zf, member = open_detailed_stream(zip_path)
    try:
        import io
        for row in iter_detailed(io.TextIOWrapper(member, encoding="utf-8", errors="replace")):
            if efq_points and row.get("AvgEFQPct") is not None:
                # Normalize to the OES-spec fraction convention (31.4 -> 0.314).
                row["AvgEFQPct"] = row["AvgEFQPct"] / 100.0
            agg.add(row, stats_ok=not row_stat_suspect(row))
    finally:
        zf.close()
    print(f"[build] {ric}: {agg.row_count} rows, {len(agg.symbols)} symbols, "
          f"boundary sentinel rows {agg.mxxnn_round_boundary_sentinel_rows}/{agg.mxxnn_round_boundary_rows}, "
          f"stat-suspect rows {agg.stat_suspect_rows}")
    return agg


def main():
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    receipts = read_json(RAW_DIR / "receipts.json")

    venue_meta = []
    for i, rep in enumerate(LIVE):
        venue_meta.append({
            "idx": i,
            "ric": rep["ric"],
            "name": rep["name"],
            "short": rep["short"],
            "org": rep["org"],
            "participant": rep["participant"],
            "kind": rep["kind"],
            "link_site": rep["link_site"],
        })

    # ---- summaries -------------------------------------------------------
    summary_rows = []
    summary_seen = {}
    for rep in LIVE:
        path = RAW_DIR / rep["ric"] / "summary.csv"
        rows = parse_summary(path.read_text(encoding="utf-8-sig"))
        vidx = [v["idx"] for v in venue_meta if v["ric"] == rep["ric"]][0]
        efq_points = rep.get("efq_percent_points", False)
        for r in rows:
            if r["ric"] != rep["ric"]:
                print(f"[build] summary {rep['ric']}: skipping row for ric={r['ric']}")
                continue
            tcode = r["type_code"]
            tmap = {code: j for j, (code, _label) in enumerate(ORDER_TYPES_SUMMARY)}
            if tcode not in tmap:
                print(f"[build] summary {rep['ric']}: unknown OrderType {tcode!r} — kept with raw code")
            if efq_points and r.get("AvgEFQPct") is not None:
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
    print(f"[build] summary rows: {len(summary_rows)} across {len(summary_seen)} venues")

    # ---- detailed passes -------------------------------------------------
    venue_aggs = {}
    for rep in LIVE:
        venue_aggs[rep["ric"]] = run_detailed(rep)

    # ---- rollup.json -----------------------------------------------------
    rollup = {}
    for rep in LIVE:
        a = venue_aggs[rep["ric"]]
        entry = {
            "cnt": int(a.totals.sums["CvrdOrderCnt"]),
            "qty": round(a.totals.sums["CvrdOrderQty"], 4),
            "cncl": round(a.totals.sums["CvrdOrderCnclQty"], 4),
            "exctd": round(a.totals.sums["CvrdOrderExctdQty"], 4),
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
                round(g.sums["CvrdOrderExctdQty"], 4),
                round(g.sums["ExctdPIOrdersQty"], 4),
            ]
        rollup[rep["ric"]] = entry
    write_json(GEN_DIR / "rollup.json", rollup)

    # ---- summary.json (compact rows: venue idx, sp500, size, type, 12 stats) ----
    write_json(GEN_DIR / "summary.json", {"rows": summary_rows})

    # ---- symbol shards + search index -------------------------------------
    size_order = {code: i for i, (code, _l) in enumerate(ORDER_SIZES)}
    shards = {}
    search_rows = []
    total_symbols = 0
    for rep in LIVE:
        a = venue_aggs[rep["ric"]]
        vidx = [v["idx"] for v in venue_meta if v["ric"] == rep["ric"]][0]
        for sym, s in a.symbols.items():
            letter = sym[0] if sym and "A" <= sym[0] <= "Z" else "other"
            shard = shards.setdefault(letter, {})
            sym_entry = shard.setdefault(sym, {})
            sizes = sorted(s["sizes"].items(), key=lambda kv: size_order.get(kv[0], 99))
            sym_entry[rep["ric"]] = {
                "s": [pack_size_row(code, g) for code, g in sizes],
                "a": pack_all_row(s["all"]),
            }
            # search index is built from the all-row: [cnt, exctd, pi, effW, efqW]
            arr = sym_entry[rep["ric"]]["a"]
            search_rows.append([sym, vidx, arr[0], arr[1], arr[3], arr[4]])
    total_symbols = len({r[0] for r in search_rows})

    sym_dir = GEN_DIR / "symbols"
    sym_dir.mkdir(parents=True, exist_ok=True)
    for letter, obj in shards.items():
        write_json(sym_dir / f"{letter}.json", obj)
    print(f"[build] symbols: {total_symbols} across {len(shards)} shards")

    # search index: [sym, venue_mask, cnt, exctd, effSprdPctW, efqPctW]
    index = {}
    for sym, vidx, cnt, exctd, effw, efqw in search_rows:
        cur = index.get(sym)
        if cur is None:
            cur = index[sym] = [0, 0.0, 0.0, [0.0, 0.0], [0.0, 0.0]]
        cur[0] |= (1 << vidx)
        cur[1] += cnt
        cur[2] += exctd
        # weighted stat accumulation (weight = executed qty)
        if effw is not None and exctd > 0:
            cur[3][0] += effw * exctd
            cur[3][1] += exctd
        if efqw is not None and exctd > 0:
            cur[4][0] += efqw * exctd
            cur[4][1] += exctd
    rows = []
    for sym, cur in sorted(index.items()):
        effw = round(cur[3][0] / cur[3][1], 6) if cur[3][1] > 0 else None
        efqw = round(cur[4][0] / cur[4][1], 6) if cur[4][1] > 0 else None
        rows.append([sym, cur[0], int(cur[1]), round(cur[2], 4), effw, efqw])
    write_json(GEN_DIR / "search_index.json", {"venues": [v["ric"] for v in venue_meta], "rows": rows})
    print(f"[build] search index rows: {len(rows)}")

    # ---- validation -------------------------------------------------------
    validation = run_validation(venue_aggs, summary_rows, venue_meta)
    write_json(GEN_DIR / "validation.json", validation)

    # ---- meta -------------------------------------------------------------
    meta = {
        "month": MONTH,
        "month_label": MONTH_LABEL,
        "built_at": now_iso(),
        "venues": venue_meta,
        "sizes": [[code, label] for code, label in ORDER_SIZES],
        "summary_types": [[code, label] for code, label in ORDER_TYPES_SUMMARY],
        "summary_stat_fields": SUMMARY_STAT_FIELDS,
        "counts": {
            "venues": len(LIVE),
            "symbols": total_symbols,
            "summary_rows": len(summary_rows),
            "detailed_rows": sum(a.row_count for a in venue_aggs.values()),
        },
        "receipts": receipts,
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
            "documented normalization: Cboe publishes AvgEFQPct in percent-points; normalized "
            "here to the spec's fraction convention (see validation.json).",
            "Notional values (CvrdOrderSize) are excluded from published aggregates — a subset "
            "of rows carries sentinel-like corrupted values (see validation.json "
            "data_quality_findings).",
        ],
    }
    write_json(GEN_DIR / "meta.json", meta)

    # ---- coverage ---------------------------------------------------------
    probes = read_json(RAW_DIR / "probes.json")
    coverage = {
        "checked_at": probes.get("checked_at"),
        "month": MONTH,
        "month_label": MONTH_LABEL,
        "entries": COVERAGE,
        "probes": probes.get("probes", []),
        "census": {
            "shared_host_public_s3_com": {
                "note": ("All 107 reporter directories on the shared Download Site host were re-listed on "
                         "2026-09-25 and searched for 202608 files; none contained one. Three directories "
                         "received legacy-file backfills during September (jany through 202605, jpms through "
                         "202606, vndm one file). Search Index (the host's own listing) is public; sampled "
                         "directories re-checked at build."),
                "directories_scanned": 107,
                "directories_with_202608": 0,
            },
        },
    }
    write_json(GEN_DIR / "coverage.json", coverage)

    print("[build] done")
    return 0


# ---------------------------------------------------------------------------
# Validation: cross-check detailed aggregations against published summaries
# ---------------------------------------------------------------------------

def run_validation(venue_aggs, summary_rows, venue_meta):
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
                exctd = tot.sums["CvrdOrderExctdQty"]
                pi_share = (tot.sums["ExctdPIOrdersQty"] / exctd) if exctd > 0 else None
                srow = sum_lookup.get((vidx, "N", size, tcode))
                rows.append({
                    "size": size,
                    "implied_avg_order_size": round(implied_size, 4),
                    "implied_avg_order_qty": round(implied_qty, 4),
                    "pi_share_of_executed_shares": None if pi_share is None else round(pi_share, 6),
                    "summary_avg_order_size": None if not srow else srow["AvgOrderSize"],
                    "summary_avg_order_qty": None if not srow else srow["AvgOrderQty"],
                    "summary_pct_exctd_pi": None if not srow else srow["PctExctdPI"],
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

    # (2) EFQ normalization evidence: raw values are percent-points (ratio ~= 100).
    efq_checks = []
    for rep in LIVE:
        if not rep.get("efq_percent_points"):
            continue
        ratios = []
        for r in parse_summary((RAW_DIR / rep["ric"] / "summary.csv").read_text(encoding="utf-8-sig")):
            eff, quo, efq = r["AvgEffctvSprdPct"], r["AvgPctQuotSprd"], r["AvgEFQPct"]
            if eff and quo and efq:
                ratios.append(efq / (eff / quo))
        ratios.sort()
        efq_checks.append({
            "venue": rep["ric"], "n": len(ratios),
            "median": round(ratios[len(ratios) // 2], 3),
            "min": round(ratios[0], 3), "max": round(ratios[-1], 3),
        })
    checks.append({
        "check": "efq_normalization",
        "note": "raw EFQ / (effective/quoted) ~= 100 => percent-points; normalized by /100 to spec fractions",
        "venues": efq_checks,
    })

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

    return {"generated_at": now_iso(), "checks": checks}


if __name__ == "__main__":
    raise SystemExit(main())
