"""Data-contract tests: verify the generated static dataset against the shapes the
frontend depends on. Run after pipeline/build.py."""
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
GEN = PROJECT / "data" / "generated"


def load(name):
    return json.loads((GEN / name).read_text())


def test_meta_contract():
    meta = load("meta.json")
    assert meta["month"] == "202608"
    n_venues = len(meta["venues"])
    assert n_venues >= 4
    assert meta["counts"]["venues"] == n_venues
    assert len(meta["sizes"]) == 8
    assert meta["summary_stat_fields"][0] == "AvgOrderQty"
    assert meta["counts"]["symbols"] > 10000
    assert meta["counts"]["detailed_rows"] > 2000000
    recs = meta["receipts"]["files"]
    assert len(recs) == 2 * n_venues          # detailed + summary per venue
    assert all(r.get("sha256") for r in recs)


def test_summary_contract():
    meta = load("meta.json")
    n_venues = len(meta["venues"])
    d = load("summary.json")
    rows = d["rows"]
    assert len(rows) >= 144
    for r in rows:
        assert len(r) == 4 + 12 + 1          # venue, sp500, size, type + 12 stats + flags
        assert 0 <= r[0] < n_venues
        assert r[1] in ("N", "Y")
        assert isinstance(r[-1], int)


def test_symbol_shards_contract():
    a = load("symbols/A.json")
    assert "AAL" in a
    rec = a["AAL"]
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
    assert len(d["venues"]) >= 4
    assert len(d["rows"]) > 10000
    for r in d["rows"][:200]:
        assert len(r) == 6
        assert isinstance(r[0], str) and isinstance(r[1], int)


def test_coverage_contract():
    d = load("coverage.json")
    assert len(d["entries"]) >= 8
    statuses = {e["status"] for e in d["entries"]}
    assert "live_amended" in statuses
    assert d["month"] == "202608"


def test_validation_contract():
    d = load("validation.json")
    checks = {c["check"] for c in d["checks"]}
    assert "efq_normalization" in checks
    assert "data_quality_findings" in checks
    assert "family_mapping" in checks
    efq = [c for c in d["checks"] if c["check"] == "efq_normalization"][0]
    for v in efq["venues"]:
        # the raw ratio must be close to 100 (percent-points) for every venue
        assert 99.0 <= v["median"] <= 101.0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
