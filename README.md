# Execution Quality Check

**August 2026 is the first month of the amended SEC Rule 605 regime**: every covered market
center and large broker-dealer must publish standardized, machine-readable monthly reports on
the quality of their order executions — effective and quoted spreads, price improvement, fill
behavior — for every NMS stock, in every order-size band.

The reports exist so execution quality can be **compared**. This tool does the comparison:
it downloads the published files, verifies their hashes, and presents the numbers side by side —
venue by venue, size band by size band, stock by stock — exactly as filed, with the receipts
and the data-quality notes attached.

**Live site:** https://greattombproductions.github.io/execution-quality-check/

## What it shows

- **Venue comparison** — pick an order-size band, a stock universe (S&P 500 or not) and an
  order type (market / marketable limit) and read the published statistics across venues:
  price-improvement rate, execution-at-quote rate, effective vs quoted spread, the
  effective-to-quoted ratio (EFQ), and 15-second / 1-minute realized spreads.
- **Symbol explorer** — look up any of the 13,299 covered symbols and see covered orders,
  executed shares, price-improvement and spread statistics at each publishing venue, computed
  from the venues' detailed per-symbol records.
- **Coverage** — who has published for August 2026 and who hasn't (yet), with probe evidence.
  The deadline is end-of-September 2026; publishers come online through that window.
- **Method & sources** — file receipts (SHA-256), column semantics, normalizations, sanity
  filters, and the filing anomalies found during the build.

## Data

Sources for the build in `data/raw/202608/` (downloaded via `pipeline/fetch.py`):

| Venue | Detailed (55-field pipe-delimited) | Summary (OES CSV) |
|---|---|---|
| Cboe BZX | `BATS202608.zip` | `summary/BATS202608.csv` |
| Cboe BYX | `BYXX202608.zip` | `summary/BYXX202608.csv` |
| Cboe EDGA | `EDGJ202608.zip` | `summary/EDGJ202608.csv` |
| Cboe EDGX | `EDGK202608.zip` | `summary/EDGK202608.csv` |
| IEX | `IEXG_202608.zip` (bundles `IEXG202608.txt` + `IEXG202608.csv` + PDF) | in-zip `IEXG202608.csv` |

Two properties of the first filings (documented in `data/generated/validation.json` and on the
method page):

- **EFQ units.** Cboe writes the EFQ column in percent-points (31.4 = 31.4%) while the SEC schema
  writes fractions (0.314). Verified as an exact ×100 on every published row at build time;
  normalized to fractions here. IEX’s files already use the fraction convention (verified as
  EFQ ≈ effective/quoted ratio on every published row) and are used as filed.
- **Notional-field anomalies.** A subset of rows carries sentinel-like values in the notional
  column (e.g., market-order round lots at the <$250 and ≥$200k boundaries: qty × $0.0001 /
  qty × $999,999.99). No notional values are published by this tool; quantity-based statistics
  are unaffected. Rows that are internally inconsistent (effective spread > 100% of midpoint,
  quoted spread wider than the midpoint) are excluded from weighted statistics only — exclusions
  are counted per venue and reported.

## How it's built

```
pipeline/fetch.py      download + SHA-256 receipts + coverage probes
pipeline/build.py      parse detailed files (header-driven), aggregate, emit site data
pipeline/aggregate.py  parsing + accumulation core (unit-tested)
tests/                 12 Python tests + Playwright browser smoke over the staged site
```

Rebuild (fetches if raw files are missing, then aggregates):

```bash
python3 pipeline/fetch.py          # only needed when data/raw/<month> is absent
python3 pipeline/build.py
python3 -m pytest tests/test_aggregate.py tests/test_data_contract.py -q
python3 tests/run_browser_smoke.py
```

`deploy.sh` runs all of the above and publishes `frontend/` + `data/generated/` to GitHub Pages.

## Notes

- All statistics are **self-published by the venues** — not audited or verified by the SEC.
- Informational only; not investment advice.
- Static site, no accounts, no tracking, no backend.
