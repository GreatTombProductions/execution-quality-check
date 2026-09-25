"""Parsing + aggregation for Rule 605 amended-format reports.

Detailed files: 55-field pipe-delimited ASCII with a header row (field names from the
Rule 605 NMS Plan Exhibit A). Summary files: SEC OES CSV schema.

Aggregation conventions (kept deliberately simple and auditable):
- Quantities/counts: summed.
- Percentage stats per row are averages over that row's executions; when aggregating
  across rows we take the executed-quantity-weighted mean and label it as such
  ("share-weighted average of reported per-symbol averages").
- Empty fields are skipped, never treated as zero.
"""

from __future__ import annotations

import csv
import io


# ---------------------------------------------------------------------------
# Scalar coercion
# ---------------------------------------------------------------------------

def coerce_num(value):
    """Parse a report numeric field. Returns float or None. Never zero-fills."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(",", "")
    # A few legacy-vintage fields quote values and pad with spaces.
    s = s.strip('"').strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(value):
    n = coerce_num(value)
    return int(n) if n is not None else None


# ---------------------------------------------------------------------------
# Summary (OES) CSV
# ---------------------------------------------------------------------------

SUMMARY_STAT_FIELDS = [
    "AvgOrderQty",
    "AvgOrderSize",
    "ExctdOrderMidPtAvg",
    "PctExctdAtQuotOrBetter",
    "PctExctdPI",
    "WghtdAvgPctPI",
    "AvgEffctvSprdPct",
    "AvgPctQuotSprd",
    "AvgEFQPct",
    "AvgRealSprdPct15sec",
    "AvgRealSprdPct1min",
    "WghtdAvgExctnTime",
]

SUMMARY_COLUMNS = [
    "DsgntParticipant",
    "RprtEntityCd",
    "RptDate",
    "Sp500",
    "OrderSize",
    "OrderType",
] + SUMMARY_STAT_FIELDS


def parse_summary(text):
    """Parse an OES summary CSV into a list of row dicts.

    The header order is respected by name; unknown columns are ignored.
    """
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        if not raw or raw.get("RprtEntityCd") is None:
            continue
        row = {
            "participant": (raw.get("DsgntParticipant") or "").strip(),
            "ric": (raw.get("RprtEntityCd") or "").strip(),
            "rpt_date": (raw.get("RptDate") or "").strip(),
            "sp500": (raw.get("Sp500") or "").strip(),
            "size": (raw.get("OrderSize") or "").strip(),
            "type_code": (raw.get("OrderType") or "").strip(),
        }
        for f in SUMMARY_STAT_FIELDS:
            row[f] = coerce_num(raw.get(f))
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Detailed files
# ---------------------------------------------------------------------------

# Fields aggregated as sums, in output order.
SUM_FIELDS = [
    "CvrdOrderCnt",
    "CvrdOrderSize",
    "CvrdOrderQty",
    "CvrdOrderCnclQty",
    "CvrdOrderExctdQty",
    "CvrdOrderExctdAwayQty",
    "ExctdPIOrdersQty",
    "FilledOrderCnt",
    "RegExctdOrderQty",
    "RegExctdOrderOnXchngQty",
]

# Fields aggregated as executed-quantity-weighted means.
WEIGHTED_FIELDS = [
    "AvgEffctvSprdPct",
    "AvgEFQPct",
    "AvgRealSprdPct15sec",
    "AvgRealSprdPct1min",
]

# Extra fields extracted for sanity checks only.
SANITY_FIELDS = [
    "ExctdOrderMidPtAvg",
    "ExctdOrderAvgQuotSprd",
]

WEIGHT_FIELD = "CvrdOrderExctdQty"

REQUIRED_HEADER_FIELDS = {"DsgntParticipant", "RprtEntityCd", "RptDate", "Symbol", "OrderSize", "OrderType"}


class DetailedFormatError(ValueError):
    pass


def header_index_map(header_line):
    """Build field-name -> column-index from the detailed file's header row."""
    fields = header_line.rstrip("\r\n").split("|")
    index = {name.strip(): i for i, name in enumerate(fields)}
    missing = REQUIRED_HEADER_FIELDS - set(index)
    if missing:
        raise DetailedFormatError(f"detailed file header missing fields: {sorted(missing)}")
    return index


def iter_detailed(text_stream):
    """Yield parsed detailed rows as dicts with coerced numeric values.

    `text_stream` is any iterable of text lines (file, zip member, list).
    The first non-empty line must be the header.
    """
    index = None
    for line in text_stream:
        if index is None:
            if not line.strip():
                continue
            index = header_index_map(line)
            continue
        line = line.rstrip("\r\n")
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < len(index):
            # Tolerate shorter rows (trailing fields left empty), reject nothing
            parts = parts + [""] * (len(parts) - len(index))
        row = {
            "participant": parts[index["DsgntParticipant"]].strip(),
            "ric": parts[index["RprtEntityCd"]].strip(),
            "rpt_date": parts[index["RptDate"]].strip(),
            "symbol": parts[index["Symbol"]].strip().upper(),
            "lot_size_flag": parts[index["LotSizeFlag"]].strip() if "LotSizeFlag" in index else "",
            "size": parts[index["OrderSize"]].strip(),
            "type_code": parts[index["OrderType"]].strip(),
        }
        for f in SUM_FIELDS:
            row[f] = coerce_num(parts[index[f]]) if f in index else None
        for f in WEIGHTED_FIELDS:
            row[f] = coerce_num(parts[index[f]]) if f in index else None
        for f in SANITY_FIELDS:
            row[f] = coerce_num(parts[index[f]]) if f in index else None
        row["_weight"] = coerce_num(parts[index[WEIGHT_FIELD]]) if WEIGHT_FIELD in index else None
        yield row


# ---------------------------------------------------------------------------
# Accumulators
# ---------------------------------------------------------------------------

class Group:
    """Accumulator for one grouping bucket.

    Sums accumulate every non-empty value. Weighted stats accumulate
    value × executed-quantity for rows where that stat is present AND the row
    passed sanity filters, with a per-field weight denominator — a skipped row
    contributes to neither the numerator nor that field's denominator.
    """

    __slots__ = ("sums", "weighted", "weight_den")

    def __init__(self):
        self.sums = {f: 0.0 for f in SUM_FIELDS}
        self.weighted = {f: 0.0 for f in WEIGHTED_FIELDS}
        self.weight_den = {f: 0.0 for f in WEIGHTED_FIELDS}

    def add_row(self, row, stats_ok=True):
        for f in SUM_FIELDS:
            v = row.get(f)
            if v is not None:
                self.sums[f] += v
        if not stats_ok:
            return
        w = row.get("_weight")
        if w is not None and w > 0:
            for f in WEIGHTED_FIELDS:
                v = row.get(f)
                if v is not None:
                    self.weighted[f] += v * w
                    self.weight_den[f] += w

    def weighted_mean(self, field):
        d = self.weight_den[field]
        if d <= 0:
            return None
        return self.weighted[field] / d


# ---------------------------------------------------------------------------
# Row sanity filters (applied to weighted statistics only; sums always kept)
# ---------------------------------------------------------------------------
# The first amended-format filings contain some internally inconsistent rows
# (e.g. effective spreads > 100% of midpoint, quoted spreads wider than the
# midpoint itself). These filters exclude the clearly-impossible class from
# per-symbol weighted statistics without touching quantity sums. Exclusions are
# counted per venue and reported in validation.json.

def row_stat_suspect(row):
    """True when a row fails conservative internal-consistency checks."""
    eff = row.get("AvgEffctvSprdPct")
    if eff is not None and abs(eff) > 1.0:
        return True
    efq = row.get("AvgEFQPct")
    if efq is not None and abs(efq) > 10.0:
        return True
    quo = row.get("ExctdOrderAvgQuotSprd")
    mid = row.get("ExctdOrderMidPtAvg")
    if quo is not None and mid is not None and mid > 0 and quo > mid:
        return True
    return False


def rollup(rows, key_fn):
    """Accumulate rows into {key: Group} using key_fn(row) -> key."""
    groups = {}
    for row in rows:
        key = key_fn(row)
        g = groups.get(key)
        if g is None:
            g = groups[key] = Group()
        g.add_row(row)
    return groups


def pi_share(group, denominator="CvrdOrderExctdQty"):
    exctd = group.sums.get(denominator)
    if not exctd or exctd <= 0:
        return None
    return group.sums["ExctdPIOrdersQty"] / exctd


# ---------------------------------------------------------------------------
# Per-symbol record packing (compact array format for the static site)
# ---------------------------------------------------------------------------
# Notional (CvrdOrderSize) is deliberately excluded from published aggregates:
# Cboe market-order round-lot rows in the <$250 and >=$200k buckets carry
# sentinel-like corrupted values (~$0.0001/share and ~$999,999.99/share).
# See validation.json -> data_quality_findings.

def pack_size_row(size_code, g):
    """[size, covCnt, exctdQty, piQty, effSprdPctW, efqPctW]"""
    return [
        size_code,
        int(g.sums["CvrdOrderCnt"]),
        round(g.sums["CvrdOrderExctdQty"], 4),
        round(g.sums["ExctdPIOrdersQty"], 4),
        _round_w(g.weighted_mean("AvgEffctvSprdPct")),
        _round_w(g.weighted_mean("AvgEFQPct")),
    ]


def pack_all_row(g):
    """[covCnt, exctdQty, piQty, effSprdPctW, efqPctW]"""
    return [
        int(g.sums["CvrdOrderCnt"]),
        round(g.sums["CvrdOrderExctdQty"], 4),
        round(g.sums["ExctdPIOrdersQty"], 4),
        _round_w(g.weighted_mean("AvgEffctvSprdPct")),
        _round_w(g.weighted_mean("AvgEFQPct")),
    ]


def _round_w(x, nd=6):
    return None if x is None else round(x, nd)
