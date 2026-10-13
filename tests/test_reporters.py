"""Registry sanity: unique reporter codes, usable URLs, harvested roster agrees with the receipts."""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "pipeline"))
import reporters  # noqa: E402


def test_live_registry_unique_and_complete():
    rics = [r["ric"] for r in reporters.LIVE]
    assert len(rics) == len(set(rics))
    for r in reporters.LIVE:
        for k in ("name", "short", "org", "kind", "detailed_url", "summary_url", "link_site"):
            assert r.get(k), (r["ric"], k)
        assert r["efq_percent_points"] in (True, False)


def test_percent_point_reporters_are_exactly_cboe():
    pp = {r["ric"] for r in reporters.LIVE if r["efq_percent_points"]}
    assert pp == {"BATS", "BYXX", "EDGJ", "EDGK"}


def test_harvest_roster_matches_receipts():
    path = PROJECT / "data" / "raw" / reporters.MONTH / "harvest-receipts.json"
    if not path.exists():
        import pytest
        pytest.skip("harvest receipts live with the source build, not the published site")
    h = json.loads(path.read_text())
    harvested = {f["ric"] for f in h["files"] if f["ok"]}
    live_h = {r["ric"] for r in reporters.LIVE if r.get("harvested")}
    curated = {r["ric"] for r in reporters.LIVE if not r.get("harvested")}
    assert live_h | curated >= harvested
