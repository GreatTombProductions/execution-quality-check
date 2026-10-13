# Execution Quality Check

**August 2026 is the first published month of the amended SEC Rule 605 regime**: covered market
centers and large brokers must publish standardized, machine-readable monthly reports on
the quality of their order executions — effective and quoted spreads, price improvement, fill
behavior — for every NMS stock, in every order-size band.

The reports exist so execution quality can be **compared**. This tool does the comparison:
it downloads the published files, verifies their hashes, and presents the numbers side by side —
reporter by reporter, size band by size band, stock by stock — exactly as filed, with the
receipts and the data-quality notes attached.

**Live site:** https://greattombproductions.github.io/execution-quality-check/

## What it shows

- **Reporter comparison** — pick an order-size band, a stock universe (S&P 500 or not) and an
  order type (market / marketable limit) and read the published statistics across reporters:
  price-improvement rate, execution-at-quote rate, effective vs quoted spread, the
  effective-to-quoted ratio (EFQ), and 15-second / 1-minute realized spreads. Sort by any
  column; filter by reporter type (exchange, broker-dealer, OTC market maker, trading system)
  or find a firm by name.
- **Symbol explorer** — look up any of the 14,534 covered symbols and see covered orders,
  executed shares, price-improvement and spread statistics at each reporter that filed records
  for it, computed from the detailed per-symbol records.
- **Coverage** — who has published for August 2026 and who hasn't: 16 exchange venues and 151
  of the 223 FINRA-registered reporters are in the build (plus 3 further reporter codes found on
  registered firms' own pages); every other registered firm is listed with what was and was not
  checked (refused, unreachable, PDF-only, legacy layout, no link on the page).
- **Method & sources** — file receipts (SHA-256), column semantics, per-reporter unit checks,
  sanity filters, and the filing anomalies found during the build.

## Data

170 reporters, 17.8M detailed rows for August 2026 (the month this site publishes; later
months are added after their filing deadline, each with its own coverage ledger). Sources:

| Source | Reporters | Notes |
|---|---|---|
| Cboe CDN | BZX, BYX, EDGA, EDGX | separate detailed zip + summary CSV per exchange |
| IEX report archive | IEX | one zip: detailed .txt + summary .csv + PDF (restated 2026-09-30) |
| MIAX | MIAX Pearl | amended format replaced the legacy file on 2026-09-29 |
| MEMX report API | MEMX | one zip per month |
| Nasdaq | Nasdaq, PSX, and one report filed at the NTX path | one zip each |
| Shared Download Site host (https://public.s3.com/rule605/) | NYSE group (5), 24X, 105 broker-dealers / market makers / ATSs / trading systems | `<code>YYYYMM.zip` + `.csv` + `.pdf` per reporter code |
| Firm-hosted Download Sites (the URL each firm registered with FINRA) | 49 further reporter codes | zips, nested zips, gz and plain-text files; vendor portals; found by reading each registered page |

Properties of the first filings (documented in `data/202608/validation.json` and on the
method page):

- **Executed shares = executed at the reporter + executed away.** Routing brokers execute almost
  nothing themselves; the plan defines price-improved shares and the spread averages over
  executions generally. Weights and shares use the total (checked: implied price-improvement
  share lands inside the published S&P 500 / other bracket for all 870 reporter-size cells).
- **EFQ units are checked per reporter.** Six reporters (the four Cboe exchanges and two files
  hosted by a vendor) write the EFQ column in percent-points (31.4 = 31.4%) while the SEC schema
  writes fractions (0.314). Each reporter's unit is detected from its own rows (EFQ against the
  same row's effective ÷ quoted spreads) and cross-checked against its summary file where one
  exists, never inherited from another publisher.
- **Filing-format variety.** Four files ship data rows without a header row (the canonical
  Exhibit A column order is applied and recorded), some zips are nested, one filer writes its
  reporter code with the participant letter prepended, some write order-size codes as "0.0".
- **Nil, IOC-only and summary-less reports.** Seven reporters filed header-only (nil) reports.
  Thirteen reporters filed detailed rows for IOC order types only, so their OES summary files are
  empty by schema (the summary covers market and marketable-limit orders); six published no
  summary file at all. These appear in the symbol explorer and scale table but not in the
  comparison table.
- **Notional-field anomalies.** A subset of rows carries sentinel-like values in the notional
  column (e.g., market-order round lots at the <$250 and ≥$200k boundaries). No notional values
  are published by this tool; quantity-based statistics are unaffected. Rows that are internally
  inconsistent (effective spread > 100% of midpoint, quoted spread wider than the midpoint) are
  excluded from weighted statistics only — exclusions are counted per reporter and reported.
- **Overlap.** A broker's order that executes on an exchange can appear in both reports; do not
  sum volumes across reporters.

## How it's built

```
pipeline/fetch.py        exchange-host downloads + SHA-256 receipts + coverage probes
pipeline/harvest_s3.py   shared-host census + downloads + receipts (one directory per firm)
pipeline/harvest_firms.py firm-hosted Download Sites: link discovery, content checks, receipts
pipeline/finra_sweep.py  per-firm registered-vs-published ledger for FINRA-registered reporters
pipeline/build.py        parse detailed files (header-driven, parallel per reporter), aggregate, emit site data
pipeline/aggregate.py    parsing + accumulation core (unit-tested)
tests/                   Python tests (aggregation, data contract, shard parity, registry) + Playwright smoke
```

Rebuild (fetches if raw files are missing, then aggregates):

The published site is this repository. Rebuild commands that download the filings live with
the source checkout; the checks below run here, with no download:

```bash
python3 -m pytest tests/test_aggregate.py tests/test_data_contract.py tests/test_reporters.py tests/test_shard_parity.py -q
python3 tests/run_browser_smoke.py
```

## Notes

- All statistics are **self-published by the venues** — not audited or verified by the SEC.
- Informational only; not investment advice.
- Static site, no accounts, no tracking, no backend.
