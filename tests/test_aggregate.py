"""Unit tests for the Rule 605 aggregation core."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import aggregate as agg  # noqa: E402


def test_coerce_num():
    assert agg.coerce_num("") is None
    assert agg.coerce_num(None) is None
    assert agg.coerce_num("1,234.5") == 1234.5
    assert agg.coerce_num('"10,200"') == 10200.0
    assert agg.coerce_num("  3.5  ") == 3.5
    assert agg.coerce_num("n/a") is None
    assert agg.coerce_num("0") == 0.0


def test_parse_summary():
    text = (
        "DsgntParticipant,RprtEntityCd,RptDate,Sp500,OrderSize,OrderType,AvgOrderQty,"
        "AvgOrderSize,ExctdOrderMidPtAvg,PctExctdAtQuotOrBetter,PctExctdPI,WghtdAvgPctPI,"
        "AvgEffctvSprdPct,AvgPctQuotSprd,AvgEFQPct,AvgRealSprdPct15sec,AvgRealSprdPct1min,"
        "WghtdAvgExctnTime\n"
        "Z,BATS,202608,N,1000,M,115.15,2338.74,21.43,0.6988,0.5575,0.0028,0.0120,0.0146,"
        "82.4064,0.0120,0.0118,2448.79\n"
    )
    rows = agg.parse_summary(text)
    assert len(rows) == 1
    r = rows[0]
    assert r["ric"] == "BATS" and r["size"] == "1000" and r["type_code"] == "M"
    assert r["AvgOrderQty"] == 115.15
    assert r["AvgEFQPct"] == 82.4064


HEADER = "|".join([
    "DsgntParticipant", "RprtEntityCd", "RptDate", "Symbol", "LotSizeFlag", "OrderSize",
    "OrderType", "CvrdOrderCnt", "CvrdOrderSize", "CvrdOrderQty", "CvrdOrderCnclQty",
    "CvrdOrderExctdQty", "CvrdOrderExctdAwayQty", "CvrdOrderQtyto100microsec",
    "CvrdOrderQtyto1millisec", "CvrdOrderQtyto10millisec", "CvrdOrderQtyto1sec",
    "CvrdOrderQtyto10sec", "CvrdOrderQtyto30sec", "CvrdOrderQtyto5min", "CvrdOrderQtyOthr",
    "AvgRealSprd50millisec", "AvgRealSprdPct50millisec", "AvgRealSprd1sec", "AvgRealSprdPct1sec",
    "AvgRealSprd15sec", "AvgRealSprdPct15sec", "AvgRealSprd1min", "AvgRealSprdPct1min",
    "AvgRealSprd5min", "AvgRealSprdPct5min", "ExctdOrderMidPtAvg", "ExctdOrderAvgQuotSprd",
    "AvgEffctvSprd", "AvgEffctvSprdPct", "AvgEFQPct", "ExctdPIOrdersQty",
    "ExctdPIOrdWghtdAvgAmtPerShare", "ExctdPIOrdWghtdAvgTime", "ExctdOrderAtQuotQty",
    "ExctdAtQuotOrdWghtdAvgTime", "ExctdOrderOutQuotQty", "ExctdOutQuotOrdWghtdAvgAmt",
    "ExctdOutQuotOrdWghtdAvgTime", "ExctdPIOrderRltvBestPrQty", "ExctdPIOrderRltvBestPrWghtdAvg",
    "ExctdOrderAtBestPrQty", "ExctdOrderOutBestPrQty", "ExctdOrderOutBestPrWghtdAvgAmt",
    "ExctdOrderSizeBnchmrkQty", "ExctdSIOutsizedQty", "FilledOrderCnt", "RegExctdOrderQty",
    "RegExctdOrderOnXchngQty", "FilledOrderWghtdAvgTime",
])


def make_row(symbol="AAL", size="1000", tcode="MXXNN", cnt=100, qty=10000, exctd=9000,
             pi=4500, eff="0.0012", efq="85.5", mid="15.00", quo="0.02", away=0):
    fields = [""] * 55
    fields[0], fields[1], fields[2], fields[3] = "Z", "BATS", "202608", symbol
    fields[4], fields[5], fields[6] = "ROUND", size, tcode
    fields[7], fields[8], fields[9] = str(cnt), "150000.0", str(qty)
    fields[10], fields[11], fields[12] = "1000", str(exctd), str(away)
    fields[31], fields[32], fields[33] = mid, quo, eff
    fields[34], fields[35] = eff, efq
    fields[36] = str(pi)
    return "|".join(fields)


def test_iter_detailed():
    text = HEADER + "\n" + make_row() + "\n" + make_row(symbol="SPY", size="250") + "\n"
    rows = list(agg.iter_detailed(io.StringIO(text)))
    assert len(rows) == 2
    r = rows[0]
    assert r["symbol"] == "AAL" and r["size"] == "1000" and r["type_code"] == "MXXNN"
    assert r["CvrdOrderCnt"] == 100 and r["CvrdOrderExctdQty"] == 9000
    assert r["AvgEffctvSprdPct"] == 0.0012
    assert r["_weight"] == 9000


def test_group_weighting_and_skip():
    g = agg.Group()
    rows = list(agg.iter_detailed(io.StringIO(
        HEADER + "\n" + make_row(eff="0.0010", efq="80") + "\n" + make_row(eff="0.0030", efq="90") + "\n"
    )))
    for r in rows:
        g.add_row(r)
    assert abs(g.weighted_mean("AvgEffctvSprdPct") - 0.0020) < 1e-12
    # skip the second row's stats: mean becomes the first row's value
    g2 = agg.Group()
    g2.add_row(rows[0])
    g2.add_row(rows[1], stats_ok=False)
    assert abs(g2.weighted_mean("AvgEffctvSprdPct") - 0.0010) < 1e-12
    # sums are unaffected by the skip
    assert g2.sums["CvrdOrderCnt"] == 200


def test_routing_broker_executed_away_is_the_weight_and_denominator():
    """A routing broker executes ~nothing itself; its executions (and price improvement) are away.
    Rule 605 averages cover executions of covered orders generally, so weights/shares use own + away."""
    rows = list(agg.iter_detailed(io.StringIO(
        HEADER + "\n"
        + make_row(exctd=0, away=8000, pi=6000, eff="0.0010") + "\n"
        + make_row(exctd=1, away=2000, pi=1500, eff="0.0050") + "\n"
    )))
    assert rows[0]["ExctdTotalQty"] == 8000 and rows[0]["_weight"] == 8000
    assert rows[1]["ExctdTotalQty"] == 2001
    g = agg.Group()
    for r in rows:
        g.add_row(r)
    assert g.sums["ExctdTotalQty"] == 10001
    assert g.sums["CvrdOrderExctdQty"] == 1                      # own executions alone would be 1 share
    assert abs(agg.pi_share(g) - 7500 / 10001) < 1e-9           # never > 1, unlike PI / own executed
    # weighted mean uses the total: (0.0010*8000 + 0.0050*2001) / 10001
    assert abs(g.weighted_mean("AvgEffctvSprdPct") - (0.0010 * 8000 + 0.0050 * 2001) / 10001) < 1e-12
    a = agg.pack_all_row(g)
    assert a[1] == 10001                                         # packed "executed" slot is the total


def test_executed_total_missing_when_both_fields_empty():
    fields = make_row().split("|")
    fields[11] = ""; fields[12] = ""
    r = list(agg.iter_detailed(io.StringIO(HEADER + "\n" + "|".join(fields) + "\n")))[0]
    assert r["ExctdTotalQty"] is None and r["_weight"] is None


def test_row_stat_suspect():
    ok = {"AvgEffctvSprdPct": 0.01, "AvgEFQPct": 1.2, "ExctdOrderAvgQuotSprd": 0.02, "ExctdOrderMidPtAvg": 15.0}
    assert not agg.row_stat_suspect(ok)
    assert agg.row_stat_suspect({**ok, "AvgEffctvSprdPct": 1.98})
    assert agg.row_stat_suspect({**ok, "AvgEFQPct": 12.0})
    assert agg.row_stat_suspect({**ok, "ExctdOrderAvgQuotSprd": 3009.0, "ExctdOrderMidPtAvg": 1518.0})
    # empty stats never trigger
    assert not agg.row_stat_suspect({"AvgEffctvSprdPct": None, "AvgEFQPct": None})


def test_pack_shapes():
    g = agg.Group()
    rows = list(agg.iter_detailed(io.StringIO(HEADER + "\n" + make_row() + "\n")))
    for r in rows:
        g.add_row(r)
    s = agg.pack_size_row("1000", g)
    assert len(s) == 6 and s[0] == "1000" and s[1] == 100
    a = agg.pack_all_row(g)
    assert len(a) == 5 and a[0] == 100 and a[1] == 9000


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
