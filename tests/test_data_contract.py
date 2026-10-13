"""Data-contract tests: verify the generated static dataset against the shapes the
frontend depends on. Run after pipeline/build.py."""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "pipeline"))
from build import shard_id  # noqa: E402

# Floors are per published month. A later month adds its own entry; it does not
# inherit August's counts.
FLOORS = {
    "202608": {
        "venues": 100,
        "symbols": 10000,
        "detailed_rows": 10000000,
        "summary_rows": 3000,
        "reporters": 200,
        "dirs": 70,
    },
}


def generated_root():
    """Source tree keeps data/generated/<month>/. The published tree serves data/<month>/."""
    for candidate in (PROJECT / "data" / "generated" / "202608", PROJECT / "data" / "202608"):
        if (candidate / "meta.json").exists():
            return candidate
    return PROJECT / "data" / "generated" / "202608"


def load(name):
    return json.loads((generated_root() / name).read_text())


def test_meta_contract():
    meta = load("meta.json")
    assert meta["month"] in FLOORS
    floor = FLOORS[meta["month"]]
    n_venues = len(meta["venues"])
    assert n_venues >= floor["venues"]
    assert meta["counts"]["venues"] == n_venues
    assert len({v["ric"] for v in meta["venues"]}) == n_venues     # reporter codes are unique
    assert [v["idx"] for v in meta["venues"]] == list(range(n_venues))
    assert len(meta["sizes"]) == 8
    assert meta["summary_stat_fields"][0] == "AvgOrderQty"
    assert meta["counts"]["symbols"] > floor["symbols"]
    assert meta["counts"]["detailed_rows"] > floor["detailed_rows"]
    for v in meta["venues"]:
        assert v["kind"] in ("exchange", "broker-dealer", "otc-market-maker", "ats",
                             "single-dealer-system", "exchange-market-maker", "other")
        assert v["participant"]
        assert v["nil_report"] == (v["detailed_rows"] == 0)
    recs = meta["receipts"]["files"]
    assert len(recs) == 2 * n_venues          # detailed + summary per venue
    assert all(r.get("sha256") for r in recs)


def test_summary_contract():
    meta = load("meta.json")
    n_venues = len(meta["venues"])
    d = load("summary.json")
    rows = d["rows"]
    assert len(rows) >= FLOORS[meta["month"]]["summary_rows"]
    for r in rows:
        assert len(r) == 4 + 12 + 1          # venue, sp500, size, type + 12 stats + flags
        assert 0 <= r[0] < n_venues
        assert r[1] in ("N", "Y")
        assert isinstance(r[-1], int)


def test_symbol_shards_contract():
    a = load(f"symbols/{shard_id('AAL')}.json")
    assert "AAL" in a
    rec = a["AAL"]
    assert len(rec) >= 20                       # most reporters cover a large-cap symbol
    # every symbol sits in the shard its id names
    import random
    names = sorted(a)
    for sym in random.Random(7).sample(names, min(50, len(names))):
        assert shard_id(sym) == shard_id(sym.upper())
    for ric in rec:
        sizes = rec[ric]["s"]
        assert isinstance(sizes, list) and len(sizes) >= 1
        for row in sizes:
            assert len(row) == 6
            assert isinstance(row[0], str)          # size code
            assert row[1] >= 0 and row[2] >= 0
        assert len(rec[ric]["a"]) == 5
    # every referenced size code must be a known bucket
    meta = load("meta.json")
    known = {c for c, _l in meta["sizes"]}
    for ric in rec:
        for row in rec[ric]["s"]:
            assert row[0] in known


def test_search_index_contract():
    meta = load("meta.json")
    d = load("search_index.json")
    assert len(d["venues"]) == len(meta["venues"])
    assert len(d["venues"]) >= 100
    assert len(d["rows"]) > 10000
    n = len(meta["venues"])
    for r in d["rows"][:500]:
        assert len(r) == 6
        assert isinstance(r[0], str) and isinstance(r[1], list) and r[1]
        assert all(isinstance(i, int) and 0 <= i < n for i in r[1])
        assert r[1] == sorted(set(r[1]))


def test_coverage_contract():
    d = load("coverage.json")
    meta = load("meta.json")
    assert len(d["entries"]) >= 9
    statuses = {e["status"] for e in d["entries"]}
    assert "live_amended" in statuses
    assert d["month"] == "202608"
    reps = d["reporters"]
    assert len(reps) >= FLOORS[meta["month"]]["reporters"]   # FINRA-registered reporters
    assert len({r["ric"] for r in reps}) == len(reps)
    assert sum(d["reporter_counts"].values()) == len(reps)
    live_rics = {v["ric"] for v in meta["venues"]}
    for r in reps:
        in_build = r["ric"] in live_rics or r.get("filed_as") in live_rics   # filed_as: code filed with a prefix
        assert (r["status"] == "live_amended") == in_build                  # ledger agrees with the build
        assert r["status"] in ("live_amended", "registered_only", "surface_unresolved", "legacy_only",
                               "published_legacy", "published_pdf_only")
        if r["status"] != "live_amended":
            assert r["note"]                                 # every gap says what was (not) checked
    census = d["census"]["shared_host_public_s3_com"]
    assert census[f"directories_with_{meta['month']}"] >= FLOORS[meta["month"]]["dirs"]


def test_validation_contract():
    d = load("validation.json")
    checks = {c["check"] for c in d["checks"]}
    assert "efq_normalization" in checks
    assert "data_quality_findings" in checks
    assert "family_mapping" in checks
    efq = [c for c in d["checks"] if c["check"] == "efq_normalization"][0]
    for v in efq["venues"]:
        # the raw ratio must be close to 100 (percent-points) for every normalized reporter
        if v["median"] is not None:               # files without a summary have no independent ratio
            assert 99.0 <= v["median"] <= 101.0
    scan = efq["convention_scan"]
    assert {v["convention"] for v in scan} <= {"fraction", "percent_points", "unverifiable"}
    # detailed-row detection and the independent summary ratio never disagree
    assert not [v["venue"] for v in scan if v["summary_agrees"] is False]
    for v in scan:
        if v["convention"] == "fraction" and v["summary_ratio_median"] is not None:
            assert 0.9 <= v["summary_ratio_median"] <= 1.1, v
    pp = {v["venue"] for v in scan if v["convention"] == "percent_points"}
    assert {"BATS", "BYXX", "EDGJ", "EDGK"} <= pp          # Cboe; two FIS-hosted files also write percent-points
    for v in scan:
        if v["basis"] == "row_spread_ratio":
            # row-level EFQ / (effective$ / quoted$): ~1 for fractions, ~100 for percent-points, nothing between
            m = v["row_ratio_median"]
            assert (m > 10) == (v["convention"] == "percent_points"), v
            assert m < 3 or m > 30, v
    integ = [c for c in d["checks"] if c["check"] == "file_integrity"][0]
    for v in integ["venues"]:
        assert not v["rows_with_other_ric"], v


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
