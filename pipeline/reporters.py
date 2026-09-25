"""Reporter registry — Rule 605 amended-format era.

The amended Rule 605 (compliance date 2026-08-01) requires monthly execution-quality
reports: detailed files are 55-field pipe-delimited ASCII (Rule 605 NMS Plan 34-103939,
Exhibit A); summary reports follow the SEC "Order Execution Summary (OES)" CSV schema
(github.com/sec-gov/oes). First reporting month = 2026-08; first reports due before the
end of September 2026.

This registry covers the reporters probed for the first month. `LIVE` entries have
verified amended-format files; everything else is documented coverage state.
"""

MONTH = "202608"
MONTH_LABEL = "August 2026"

# --- Live amended-format reporters (verified files fetched at build time) ------------
# efq_percent_points: Cboe writes AvgEFQPct in percent-points (31.4 = 31.4%) while the
# OES spec writes fractions (0.314). Verified exactly x100 on both detailed + summary
# surfaces for all four exchanges at build time; normalized to fractions in the pipeline.
LIVE = [
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
        "participant": "Z",
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
        "participant": "Z",
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
        "participant": "Z",
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
]

# --- Coverage: everything else the first month has (not) produced --------------------
# status values:
#   live_amended       — amended-format files verified and used in this build
#   published_legacy   — an August-2026 file is published, but in the legacy format
#   legacy_only        — no August-2026 file; legacy files through 202607 at this surface
#   registered_only    — reporter registered (RIC + Download Site URL) but no report content
#   surface_unresolved — reporting location not yet located / JS-only / dormant
COVERAGE = [
    {
        "org": "Cboe Global Markets (BZX, BYX, EDGA, EDGX)",
        "role": "Exchange group — Designated Participant Z",
        "status": "live_amended",
        "evidence": "Amended-format files for 202608 verified: 4 detailed reports (55-field pipe-delimited) + 4 OES summary CSVs.",
        "url": "https://www.cboe.com/markets/us/equities/regulation/605/bzx",
    },
    {
        "org": "MIAX Pearl Equities",
        "role": "Exchange — Designated Participant (MIAX)",
        "status": "published_legacy",
        "evidence": "August-2026 file published 2026-09-02 (heprl202608.zip, 610,277 bytes, last-modified 2026-09-02 21:00 UTC; unchanged at the 2026-09-25 re-check), but in the pre-amendment 26-field quoted format — not the amended 55-field layout. Not comparable with amended-format files; listed here, excluded from comparisons until restated.",
        "url": "https://www.miaxglobal.com/markets/us-equities/pearl-equities/rule605-reports",
    },
    {
        "org": "NYSE Group (NYSE, Arca, American, National, Texas)",
        "role": "Exchange group — Designated Participant",
        "status": "legacy_only",
        "evidence": "Re-checked 2026-09-25: public file listings show legacy-format monthly files through 202607 (N / PARCAX / A / C / M families); no 202608 links appear and the five 202608 zip paths return 404. NYSE also announced it will correct/restate legacy reports Jan 2022–Jul 2026 (short-sale order inclusion error).",
        "url": "https://www.nyse.com/trade/reports/rule-605",
    },
    {
        "org": "Nasdaq (NASDAQ, BX, PSX)",
        "role": "Exchange group — Designated Participant",
        "status": "legacy_only",
        "evidence": "Re-checked 2026-09-25: the current-report page lists monthly eqreport links, but the 202608 links (NASDAQ / PSX / NTX) resolve 404; the latest actual files are NASDAQ/PSX 202607, NTX 202607 and BX 202601 — legacy .dat format. Re-check when Nasdaq publishes its first amended monthly file.",
        "url": "https://www.nasdaqtrader.com/Trader.aspx?id=SECRule605Reportrecalc",
    },
    {
        "org": "IEX (Investors Exchange)",
        "role": "Exchange — Designated Participant V (self-filed)",
        "status": "live_amended",
        "evidence": "Amended-format files for 202608 published 2026-09-25 (IEXG_202608.zip, file last-modified 16:57 UTC): detailed IEXG202608.txt (55-field pipe-delimited, 396,950 rows) + OES summary IEXG202608.csv (35 rows) + PDF, bundled in one zip. Schema-conformant EFQ fractions verified (no x100 normalization needed); canonical size buckets. Live in this build.",
        "url": "https://www.iex.io/resources/regulation/605",
    },
    {
        "org": "MEMX (Members Exchange)",
        "role": "Exchange",
        "status": "legacy_only",
        "evidence": "Surface resolved at the 2026-09-25 re-check: the migrated JS application reads a public report-list API (memxtrading.com/api/v1/report/605); the latest listed report is July 2026 (U202607.zip). No August-2026 report listed yet — re-check the API for U202608.zip.",
        "url": "https://www.memxtrading.com/605-reports",
    },
    {
        "org": "24X National Exchange",
        "role": "Exchange",
        "status": "surface_unresolved",
        "evidence": "No public 605 report page located from this vantage as of the 2026-09-25 re-check (common paths 404; homepage carries no 605 link).",
        "url": "https://www.24exchange.com/",
    },
    {
        "org": "LTSE (Long-Term Stock Exchange)",
        "role": "Exchange",
        "status": "surface_unresolved",
        "evidence": "Latest published file is L202503 (uploaded 2025-04-30); no 2026 monthly files. Re-checked 2026-09-25.",
        "url": "https://public.s3.com/rule605/ltse/",
    },
    {
        "org": "FINRA-registered reporters (226 firms: broker-dealers, OTC market makers, ATSs, single-dealer trading systems, exchange market makers)",
        "role": "FINRA is Designated Participant for these reporters; per-firm Download Site URLs listed on FINRA's Link Site",
        "status": "registered_only",
        "evidence": "Re-checked 2026-09-25: Link Site live with 226 registered reporters (counts: 118 broker-dealers, 61 OTC market makers, 25 ATSs, 18 single-dealer trading systems, 4 exchange market makers; +4 net since the 2026-09-22 build — five new broker-dealer registrations, one OTC market maker dropped). Shared-host census re-run: all 107 reporter directories on public.s3.com were re-listed; none contained a 202608 file (three directories received legacy-file backfills during September: jany through 202605, jpms through 202606, vndm one file). Per-firm submission-history sweep: partial — the host rate-limited (HTTP 429) a bulk crawl on 2026-09-25; the firms checked (submission rows through 09-04-2026, e.g. Albert Securities 09-04) show registration submissions only, no August-report submissions. The full 226-firm sweep is deferred to the post-deadline harvest (after 2026-09-30, when the first-month filings are due; final-month mass expected before the deadline).",
        "url": "https://www.finra.org/filing-reporting/regulation-nms/sec-rule-605-reports",
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
