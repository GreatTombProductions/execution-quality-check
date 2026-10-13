"""Reporter registry — Rule 605 amended-format era.

The amended Rule 605 (compliance date 2026-08-01) requires monthly execution-quality
reports: detailed files are 55-field pipe-delimited ASCII (Rule 605 NMS Plan 34-103939,
Exhibit A); summary reports follow the SEC "Order Execution Summary (OES)" CSV schema
(github.com/sec-gov/oes). First reporting month = 2026-08; first reports due before the
end of September 2026.

`LIVE` = the reporters whose verified amended-format files are used in the build: the exchange venues
curated below (Cboe x4, IEX, MIAX Pearl, MEMX, Nasdaq x3 — fetched from their own hosts by fetch.py)
plus every reporter harvested from the shared Download Site host (public.s3.com/rule605/, by
harvest_s3.py) or from a firm's own registered Download Site (harvest_firms.py). The harvested roster
comes from data/raw/<month>/harvest-receipts.json + firm-harvest-receipts.json and the name/type
directory data/reporter_directory.json. `COVERAGE` is the documented state of the exchange groups;
the per-firm ledger for FINRA-registered reporters is computed in build.py.
"""

import copy
import html
import json
import re
import sys
from pathlib import Path

DEFAULT_MONTH = "202608"
MONTH = DEFAULT_MONTH
MONTH_LABEL = "August 2026"

_PROJECT = Path(__file__).resolve().parent.parent
_DIRECTORY_PATH = _PROJECT / "data" / "reporter_directory.json"
_HARVEST_PATH = _PROJECT / "data" / "raw" / MONTH / "harvest-receipts.json"

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]


def month_label(month):
    if not re.fullmatch(r"20\d{4}", month or ""):
        raise SystemExit(f"month must be YYYYMM, got {month!r}")
    return f"{_MONTH_NAMES[int(month[4:]) - 1]} {month[:4]}"


def month_from_argv(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--month" not in argv:
        return DEFAULT_MONTH
    i = argv.index("--month")
    if i + 1 >= len(argv) or not re.fullmatch(r"20\d{4}", argv[i + 1]):
        raise SystemExit("--month expects YYYYMM, e.g. 202608")
    return argv[i + 1]


def positionals(argv=None):
    """Arguments that are not `--month YYYYMM`. Other flags stay, so a crawl path
    passed beside `--month` is still the positional and `--month` is not a path."""
    argv = list(sys.argv[1:] if argv is None else argv)
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == "--month":
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def month_mention_re(month):
    """Body pattern for 'this reporting month'. August's pattern is the one the
    first sweep used, built the same way rather than special-cased."""
    month_label(month)
    year, mm = month[:4], month[4:]
    full = _MONTH_NAMES[int(mm) - 1].lower()
    abbr = full[:3]
    return re.compile(rf"{month}|{year}[-_/]{mm}|{full}[\s_-]*{year}|{abbr}[\s_-]*{year}", re.I)


def _memx_url(month):
    """MEMX report ids are per month. August's id is 40. Later months come from
    data/raw/<month>/memx-report-id.txt written by the probe — never guessed."""
    if month == "202608":
        return "https://www.memxtrading.com/api/v1/report/605/40"
    side = _PROJECT / "data" / "raw" / month / "memx-report-id.txt"
    if not side.exists():
        return None
    rid = side.read_text().strip()
    if not rid.isdigit():
        raise SystemExit(f"memx report id {rid!r} is not numeric")
    return f"https://www.memxtrading.com/api/v1/report/605/{rid}"

# Designated Participant code -> label (from the files' DsgntParticipant column + the OES PDFs).
PARTICIPANT_NAMES = {
    "A": "NYSE American", "B": "Nasdaq BX", "C": "NYSE National", "G": "24X", "H": "MIAX Pearl",
    "J": "Cboe EDGA", "K": "Cboe EDGX", "M": "NYSE Texas", "N": "NYSE", "P": "NYSE Arca",
    "Q": "Nasdaq", "T": "FINRA", "U": "MEMX", "V": "IEX", "X": "Nasdaq PSX", "Y": "Cboe BYX",
    "Z": "Cboe BZX",
}

# --- Live amended-format reporters (verified files fetched at build time) ------------
# efq_percent_points: Cboe writes AvgEFQPct in percent-points (31.4 = 31.4%) while the
# OES spec writes fractions (0.314). Verified exactly x100 on both detailed + summary
# surfaces for all four exchanges at build time; normalized to fractions in the pipeline.
_EXCHANGES_AUGUST = [
    {
        "ric": "BATS",
        "name": "Cboe BZX Exchange",
        "short": "Cboe BZX",
        "org": "Cboe Global Markets",
        "participant": "Z",
        "participant_name": "Cboe",
        "kind": "exchange",
        "efq_percent_points": True,
        "detailed_url": "https://cdn.cboe.com/resources/us/equities/regulation/605/bzx/BATS202608.zip",
        "summary_url": "https://cdn.cboe.com/resources/us/equities/regulation/605/bzx/summary/BATS202608.csv",
        "link_site": "https://www.cboe.com/markets/us/equities/regulation/605/bzx",
    },
    {
        "ric": "BYXX",
        "name": "Cboe BYX Exchange",
        "short": "Cboe BYX",
        "org": "Cboe Global Markets",
        "participant": "Y",
        "participant_name": "Cboe",
        "kind": "exchange",
        "efq_percent_points": True,
        "detailed_url": "https://cdn.cboe.com/resources/us/equities/regulation/605/byx/BYXX202608.zip",
        "summary_url": "https://cdn.cboe.com/resources/us/equities/regulation/605/byx/summary/BYXX202608.csv",
        "link_site": "https://www.cboe.com/markets/us/equities/regulation/605/byx",
    },
    {
        "ric": "EDGJ",
        "name": "Cboe EDGA Exchange",
        "short": "Cboe EDGA",
        "org": "Cboe Global Markets",
        "participant": "J",
        "participant_name": "Cboe",
        "kind": "exchange",
        "efq_percent_points": True,
        "detailed_url": "https://cdn.cboe.com/resources/us/equities/regulation/605/edga/EDGJ202608.zip",
        "summary_url": "https://cdn.cboe.com/resources/us/equities/regulation/605/edga/summary/EDGJ202608.csv",
        "link_site": "https://www.cboe.com/markets/us/equities/regulation/605/edga",
    },
    {
        "ric": "EDGK",
        "name": "Cboe EDGX Exchange",
        "short": "Cboe EDGX",
        "org": "Cboe Global Markets",
        "participant": "K",
        "participant_name": "Cboe",
        "kind": "exchange",
        "efq_percent_points": True,
        "detailed_url": "https://cdn.cboe.com/resources/us/equities/regulation/605/edgx/EDGK202608.zip",
        "summary_url": "https://cdn.cboe.com/resources/us/equities/regulation/605/edgx/summary/EDGK202608.csv",
        "link_site": "https://www.cboe.com/markets/us/equities/regulation/605/edgx",
    },
    {
        # Published 2026-09-25 (file last-modified 16:57 UTC; first filing, no restatement).
        # IEX bundles the detailed 55-field .txt + OES summary .csv + PDF in ONE zip — the
        # summary member is extracted from the detailed download at fetch time
        # (summary_from_detailed_zip). IEX's EFQ values are already schema-conformant
        # fractions (verified: EFQ == effective/quoted ratio on the summary rows); no x100.
        "ric": "IEXG",
        "name": "IEX (Investors Exchange)",
        "short": "IEX",
        "org": "IEX Group",
        "participant": "V",
        "participant_name": "IEX",
        "kind": "exchange",
        "efq_percent_points": False,
        "summary_from_detailed_zip": True,
        "detailed_url": "https://storage.googleapis.com/iex/regulation/605/reports/IEXG_202608.zip",
        "summary_url": "https://storage.googleapis.com/iex/regulation/605/reports/IEXG_202608.zip",
        "link_site": "https://www.iex.io/resources/regulation/605",
    },
    # --- Post-deadline additions (2026-10-03 harvest): other exchange venues ------------------
    # All five bundle detailed .txt + OES summary .csv (+ PDF) in ONE zip, like IEX. EFQ
    # convention is verified per reporter at build time (build.py asserts the fraction or
    # percent-points convention from the filed summary rows; none of these use percent-points).
    {
        # Restated 2026-09-29 in the amended 55-field format (the 2026-09-02 file was legacy 26-field).
        "ric": "EPRL", "name": "MIAX Pearl Equities", "short": "MIAX Pearl", "org": "MIAX",
        "participant": "H", "kind": "exchange", "efq_percent_points": False,
        "summary_from_detailed_zip": True,
        "detailed_url": "https://www.miaxglobal.com/sites/default/files/rule_report-files/heprl202608.zip",
        "summary_url": "https://www.miaxglobal.com/sites/default/files/rule_report-files/heprl202608.zip",
        "link_site": "https://www.miaxglobal.com/markets/us-equities/pearl-equities/rule605-reports",
    },
    {
        # The Rule 605 list API gives the report id; download route is /api/v1/report/605/<id>.
        "ric": "U", "name": "MEMX", "short": "MEMX", "org": "MEMX",
        "participant": "U", "kind": "exchange", "efq_percent_points": False,
        "summary_from_detailed_zip": True,
        "api_generated": True,
        "detailed_url": "https://www.memxtrading.com/api/v1/report/605/40",
        "summary_url": "https://www.memxtrading.com/api/v1/report/605/40",
        "link_site": "https://www.memxtrading.com/605-reports",
    },
    {
        "ric": "Q", "name": "Nasdaq Stock Market", "short": "Nasdaq", "org": "Nasdaq",
        "participant": "Q", "kind": "exchange", "efq_percent_points": False,
        "summary_from_detailed_zip": True,
        "detailed_url": "https://www.nasdaqtrader.com/content/MarketRegulation/NASDAQ/eqreport/eqreport_202608.zip",
        "summary_url": "https://www.nasdaqtrader.com/content/MarketRegulation/NASDAQ/eqreport/eqreport_202608.zip",
        "link_site": "https://www.nasdaqtrader.com/Trader.aspx?id=SECRule605Reportrecalc",
    },
    {
        "ric": "X", "name": "Nasdaq PSX", "short": "Nasdaq PSX", "org": "Nasdaq",
        "participant": "X", "kind": "exchange", "efq_percent_points": False,
        "summary_from_detailed_zip": True,
        "detailed_url": "https://www.nasdaqtrader.com/content/MarketRegulation/PSX/eqreport/psxeqreport_202608.zip",
        "summary_url": "https://www.nasdaqtrader.com/content/MarketRegulation/PSX/eqreport/psxeqreport_202608.zip",
        "link_site": "https://www.nasdaqtrader.com/Trader.aspx?id=SECRule605Reportrecalc",
    },
    {
        # Filed at Nasdaq's "ntxeqreport" path; the file's own identifiers are RIC B / participant B
        # (the OES PDF names the designated participant "BSE", i.e. the BX lineage). Labeled by the
        # filed RIC rather than guessing a venue brand.
        "ric": "B", "name": "Nasdaq BX (RIC B, filed at the NTX report path)", "short": "Nasdaq BX (B)",
        "org": "Nasdaq", "participant": "B", "kind": "exchange", "efq_percent_points": False,
        "summary_from_detailed_zip": True,
        "detailed_url": "https://www.nasdaqtrader.com/content/MarketRegulation/NTX/eqreport/ntxeqreport_202608.zip",
        "summary_url": "https://www.nasdaqtrader.com/content/MarketRegulation/NTX/eqreport/ntxeqreport_202608.zip",
        "link_site": "https://www.nasdaqtrader.com/Trader.aspx?id=SECRule605Reportrecalc",
    },
]

_UPPER_TOKENS = {"LLC", "LP", "LLP", "INC", "NA", "USA", "US", "ATS", "BD", "BNP", "UBS", "II", "III",
                 "BMO", "RBC", "TD", "HRT", "JP", "XTX", "GTS", "IBKR", "ETF", "TMX", "BTIG", "MKT", "NFS", "LPL",
                 "SG", "BOFA", "FINRA", "LTD", "PLC", "TP", "GS", "MS", "DA", "AG", "BTC", "FX", "ICBC", "RJ"}
_LEGAL_SUFFIX = None


def _nice_name(name):
    """FINRA lists reporter names in capitals; title-case them but keep legal/abbreviation tokens."""
    if not name.isupper():
        return name
    out = []
    for i, w in enumerate(name.split()):
        core = w.strip(",.()/&").upper().replace(".", "")
        if i and core in ("AND", "OF", "THE", "FOR"):
            out.append(w.lower())
        elif core in _UPPER_TOKENS or (len(core) <= 3 and "." in w):
            out.append(w.upper())
        else:
            out.append("-".join(p.capitalize() for p in w.split("-")))
    return " ".join(out)


def _short_name(name):
    import re
    n = re.sub(r"[,\s]+(L\.?L\.?C\.?|L\.?P\.?|L\.?L\.?P\.?|INC\.?|CORP\.?|CO\.?|LTD\.?|N\.?A\.?)$", "", name, flags=re.I).strip(" ,.")
    n = re.sub(r"[,\s]+(L\.?L\.?C\.?|INC\.?)$", "", n, flags=re.I).strip(" ,.")
    return n[:40]


def _load_harvested():
    """Reporters harvested from the shared Download Site host (harvest_s3.py) and from firm-hosted
    Download Sites (harvest_firms.py); the shared host wins when a code appears in both."""
    directory = json.loads(_DIRECTORY_PATH.read_text()) if _DIRECTORY_PATH.exists() else {}
    curated = {r["ric"] for r in LIVE}
    by_ric = {}
    pages = {}
    firm_path = _HARVEST_PATH.with_name("firm-harvest-receipts.json")
    sources = []
    if _HARVEST_PATH.exists():
        sources.append(("shared", json.loads(_HARVEST_PATH.read_text())))
    if firm_path.exists():
        fr = json.loads(firm_path.read_text())
        for pg in fr.get("pages", []):
            pages[pg["slug"]] = pg
        sources.append(("firm", fr))
    for origin, receipts in sources:
        for f in receipts["files"]:
            if f.get("ok"):
                by_ric.setdefault(f["ric"], {}).setdefault(f["kind"], {**f, "origin": origin})
    out = []
    for ric in sorted(by_ric):
        if ric in curated:
            continue
        files = by_ric[ric]
        if not (files.get("detailed") and files.get("summary")):
            continue
        # A few filers write the reporter code with the participant letter prepended (TRAJA for the
        # registered code RAJA): resolve the registered firm by that alias, keep the code as filed.
        d = directory.get(ric) or (directory.get(ric[1:]) if ric.startswith("T") else None) or {}
        det = files["detailed"]
        page = pages.get(det.get("slug"), {})
        if d:
            name = d["name"]
        elif page and not page.get("also_registered_here"):
            name = f"{_nice_name(html.unescape(page.get('name', ric)))} ({ric})"
        else:
            # one page serves several registered firms and this code is not one of them: say so, don't guess
            host = (det.get("page") or "").split("/")[2] if det.get("page") else "its Download Site"
            name = f"Reporter code {ric} (published on {host})"
        typ = d.get("type") or "Unclassified"
        kind = {"Exchange": "exchange", "Broker-Dealer": "broker-dealer",
                "OTC Market Maker": "otc-market-maker", "Alternative Trading System": "ats",
                "Single Dealer Trading System": "single-dealer-system",
                "Exchange Market Maker": "exchange-market-maker"}.get(typ, "other")
        pretty = name if typ == "Exchange" else _nice_name(name)
        link = d.get("link_site") or (det.get("page") if det["origin"] == "firm" else None) or d.get("finra_url") \
            or f"https://public.s3.com/rule605/{det.get('dir')}/"
        out.append({
            "ric": ric,
            "name": pretty,
            "short": _short_name(pretty),
            "org": name,
            "participant": "T",
            "kind": kind,
            "efq_percent_points": False,
            "harvested": True,
            "origin": det["origin"],
            "detailed_url": det["url"],
            "summary_url": files["summary"].get("url") or det["url"],
            "link_site": link,
        })
    # Exchange venues first (by name), then everyone else by name.
    out.sort(key=lambda r: (r["kind"] != "exchange", r["name"].lower()))
    # One firm can file several reporter codes (e.g. a broker-dealer and its OTC market-maker desk):
    # disambiguate the short label with the code wherever names collide.
    seen = {}
    for r in out:
        seen[r["short"]] = seen.get(r["short"], 0) + 1
    for r in out:
        if seen[r["short"]] > 1:
            r["short"] = f"{r['short']} ({r['ric']})"
    return out


def _exchanges_for(month):
    """August returns the literal registry (URLs and EFQ flags unchanged).
    Any other month rewrites the month token in those URLs and drops the
    August EFQ flag — units are re-detected per file. MEMX's report id is not
    in the URL pattern; a missing id file omits MEMX rather than reusing 40."""
    if month == "202608":
        return copy.deepcopy(_EXCHANGES_AUGUST)
    out = []
    for rep in _EXCHANGES_AUGUST:
        row = copy.deepcopy(rep)
        row["efq_percent_points"] = False
        if row["ric"] == "U":
            url = _memx_url(month)
            if url is None:
                continue
            row["detailed_url"] = url
            row["summary_url"] = url
        else:
            row["detailed_url"] = row["detailed_url"].replace("202608", month)
            row["summary_url"] = row["summary_url"].replace("202608", month)
        out.append(row)
    return out


def activate(month=None):
    """Point the registry at one reporting month. Harvested reporters come
    only from that month's receipts — August's roster is not carried forward."""
    global MONTH, MONTH_LABEL, LIVE, _HARVEST_PATH
    month = month or DEFAULT_MONTH
    month_label(month)  # validates
    MONTH = month
    MONTH_LABEL = month_label(month)
    _HARVEST_PATH = _PROJECT / "data" / "raw" / MONTH / "harvest-receipts.json"
    curated = _exchanges_for(month)
    LIVE = curated
    LIVE = curated + _load_harvested()
    return LIVE


LIVE = []
activate(DEFAULT_MONTH)

# --- Coverage: everything else the first month has (not) produced --------------------
# status values:
#   live_amended       — amended-format files verified and used in this build
#   published_legacy   — an August-2026 file is published, but in the legacy format
#   legacy_only        — no August-2026 file; legacy files only at this surface
#   registered_only    — reporter registered (RIC + Download Site URL) but no August file located
#   surface_unresolved — Download Site not retrieved / not machine-probeable / unreachable
COVERAGE = [
    {
        "org": "Cboe Global Markets (BZX, BYX, EDGA, EDGX)",
        "role": "Exchange group — four reporters",
        "status": "live_amended",
        "evidence": "Amended-format files for 202608: 4 detailed reports (55-field pipe-delimited) + 4 OES summary CSVs, published 2026-09-14 and byte-identical at the 2026-10-04 re-fetch. Cboe files EFQ in percent-points (normalized here).",
        "url": "https://www.cboe.com/markets/us/equities/regulation/605/bzx",
    },
    {
        "org": "IEX (Investors Exchange)",
        "role": "Exchange — self-filed",
        "status": "live_amended",
        "evidence": "Amended-format zip (detailed .txt + OES summary .csv + PDF). First published 2026-09-25; restated 2026-09-30 16:39 UTC (different size and hash). This build uses the restated file (the first file was 28,354,983 bytes).",
        "url": "https://www.iex.io/resources/regulation/605",
    },
    {
        "org": "MIAX Pearl Equities",
        "role": "Exchange",
        "status": "live_amended",
        "evidence": "The 2026-09-02 August file was legacy 26-field format; it was replaced 2026-09-29 20:47 UTC by an amended 55-field file (detailed .txt + OES summary .csv + two PDFs). This build uses the amended file. The same host already carries a heprl202609 file (posted 2026-10-02) for the next month; it is outside this tool's scope.",
        "url": "https://www.miaxglobal.com/markets/us-equities/pearl-equities/rule605-reports",
    },
    {
        "org": "Nasdaq (Nasdaq, PSX, and a third report filed at the NTX path)",
        "role": "Exchange group — three reporters",
        "status": "live_amended",
        "evidence": "Amended-format zips (detailed .txt + OES summary .csv + PDF) published 2026-09-30 for RIC Q (Nasdaq), X (PSX) and B (filed at the ntxeqreport path; the file's own reporter code is B and its OES PDF names the designated participant BSE — labeled by the filed code, not by a venue brand). The BX report path for 202608 answers with Nasdaq's not-found redirect: no separate BX August file.",
        "url": "https://www.nasdaqtrader.com/Trader.aspx?id=SECRule605Reportrecalc",
    },
    {
        "org": "NYSE Group (NYSE, Arca, American, National, Texas)",
        "role": "Exchange group — five reporters",
        "status": "live_amended",
        "evidence": "Amended-format zips posted to the shared Download Site host on 2026-10-01 (12:50–12:58 UTC; after the end-of-September due date). Reporter codes XNYS, ARCX, XASE, XCIS, XCHI. The earlier guessed NYSE-site paths (N/PARCAX/A/C/M 202608 zips) still 404 — the files live on the shared host, which NYSE's own reports page links.",
        "url": "https://www.nyse.com/trade/reports/rule-605",
    },
    {
        "org": "MEMX (Members Exchange)",
        "role": "Exchange",
        "status": "live_amended",
        "evidence": "August 2026 report listed on MEMX's report API (id 40); the zip's member timestamps read 2026-09-17 (the download's Last-Modified header is the API response time, so no publication time is claimed). Detailed .txt + OES summary .csv + PDF.",
        "url": "https://www.memxtrading.com/605-reports",
    },
    {
        "org": "24X National Exchange",
        "role": "Exchange",
        "status": "live_amended",
        "evidence": "Amended-format zip on the shared Download Site host (directory 24eq, reporter code TFEX; designated participant 24X), published 2026-09-30. No 605 page was located on the 24X website itself.",
        "url": "https://public.s3.com/rule605/24eq/",
    },
    {
        "org": "LTSE (Long-Term Stock Exchange)",
        "role": "Exchange",
        "status": "legacy_only",
        "evidence": "Shared-host directory ltse re-listed 2026-10-03: latest file is L202503 (legacy format); no 2026 files, including August. Whether LTSE had covered orders to report is not something the listing shows.",
        "url": "https://public.s3.com/rule605/ltse/",
    },
]

# OrderSize code -> label (Rule 605 NMS Plan Exhibit A + OES spec)
ORDER_SIZES = [
    ("0", "Less than $250"),
    ("250", "$250 – <$1,000"),
    ("1000", "$1,000 – <$5,000"),
    ("5000", "$5,000 – <$10,000"),
    ("10000", "$10,000 – <$20,000"),
    ("20000", "$20,000 – <$50,000"),
    ("50000", "$50,000 – <$200,000"),
    ("200000", "$200,000 or more"),
]

# Detailed-file OrderType code -> label (Exhibit A)
ORDER_TYPES_DETAILED = {
    "MXXNN": "Market orders",
    "LYNNN": "Marketable limit orders",
    "LYNYN": "Marketable IOC orders",
    "LNYNN": "Midpoint-or-better limit orders",
    "LNYYN": "Midpoint-or-better limit IOC orders",
    "LNNNN": "Executable non-marketable limit orders",
    "LNNYN": "Executable non-marketable IOC orders",
    "MXXNY": "Executable stop market orders",
    "LYNNY": "Executable stop marketable limit orders",
    "LNNNY": "Executable stop non-marketable limit orders",
}

# Summary CSV OrderType codes (OES spec: summary is limited to this subset)
ORDER_TYPES_SUMMARY = [
    ("M", "Market orders"),
    ("ML", "Marketable limit orders"),
]
