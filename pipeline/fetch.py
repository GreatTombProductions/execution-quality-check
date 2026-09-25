"""Fetch Rule 605 amended-format files + run coverage probes.

Writes raw captures under data/raw/<month>/<ric>/ and a receipts + probes record.
Network fetching uses curl (repo convention: multi-transport, hard deadlines).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from reporters import LIVE, MONTH  # noqa: E402

RAW_DIR = PROJECT_DIR / "data" / "raw" / MONTH

UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def curl_download(url, dest, timeout=180):
    """Download url -> dest via curl. Returns True on success (HTTP 200)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["curl", "-s", "-L", "--max-time", str(timeout),
         "-A", UA_BROWSER, "-o", str(dest), "-w", "%{http_code}", url],
        capture_output=True, text=True,
    )
    code = (r.stdout or "").strip()
    if code != "200" or not dest.exists() or dest.stat().st_size == 0:
        return False, code
    return True, code


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_head(url, timeout=40):
    """Cheap status probe. Returns dict with http_status / redirect target.

    Redirects are recorded without following: NasdaqTrader answers 302 ->
    /Trader.aspx?id=http404 for absent files, so a bare "302" must not read as
    a reachable file."""
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}|%{redirect_url}",
         "--max-time", str(timeout), "-A", UA_BROWSER, "-I", url],
        capture_output=True, text=True,
    )
    out = (r.stdout or "").strip()
    code, _, redirect = out.partition("|")
    return {"url": url, "http_status": code or None, "redirect_url": redirect or None,
            "ok": code not in ("", "000"),
            "checked_at": now_iso()}


def probe_get_status(url, timeout=40):
    r = subprocess.run(
        ["curl", "-s", "-L", "-o", "/dev/null", "-w", "%{http_code}",
         "--max-time", str(timeout), "-A", UA_BROWSER, url],
        capture_output=True, text=True,
    )
    code = (r.stdout or "").strip()
    return {"url": url, "http_status": code or None, "checked_at": now_iso()}


COVERAGE_PROBES = [
    # (label, list of urls) — coverage evidence. Absence signals: a direct 404 (NYSE), or
    # NasdaqTrader's 302 -> /Trader.aspx?id=http404 (its not-found convention).
    ("NYSE (NY) 202608", ["https://www.nyse.com/publicdocs/nyse/markets/nyse/N202608.zip"]),
    ("NYSE Arca 202608", ["https://www.nyse.com/publicdocs/nyse/markets/nyse-arca/PARCAX202608.zip"]),
    ("NYSE American 202608", ["https://www.nyse.com/publicdocs/nyse/markets/nyse-american/A202608.zip"]),
    ("NYSE National 202608", ["https://www.nyse.com/publicdocs/nyse/markets/nyse-national/C202608.zip"]),
    ("NYSE Texas 202608", ["https://www.nyse.com/publicdocs/nyse/markets/nyse-texas/M202608.zip"]),
    ("Nasdaq 202608", ["https://www.nasdaqtrader.com/content/MarketRegulation/NASDAQ/eqreport/eqreport_202608.zip"]),
    ("Nasdaq PSX 202608", ["https://www.nasdaqtrader.com/content/MarketRegulation/PSX/eqreport/psxeqreport_202608.zip"]),
    ("Nasdaq NTX 202608", ["https://www.nasdaqtrader.com/content/MarketRegulation/NTX/eqreport/ntxeqreport_202608.zip"]),
    ("MIAX Pearl 202608 (published, legacy format)",
     ["https://www.miaxglobal.com/sites/default/files/rule_report-files/heprl202608.zip"]),
    # Shared Download Site host (public.s3.com) — sampled directories; the full census of all
    # 107 reporter directories was re-listed on 2026-09-25 (see coverage.json census note).
    ("S3-host sample (robinhood)", ["https://public.s3.com/rule605/hood/?C=M;O=D"]),
    ("S3-host sample (charles schwab)", ["https://public.s3.com/rule605/chas/?C=M;O=D"]),
    ("S3-host sample (jane street)", ["https://public.s3.com/rule605/jany/?C=M;O=D"]),
    ("S3-host sample (jpm)", ["https://public.s3.com/rule605/jpms/?C=M;O=D"]),
]


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    receipts = {"month": MONTH, "fetched_at": now_iso(), "files": []}
    ok_all = True

    for rep in LIVE:
        ric = rep["ric"]
        rdir = RAW_DIR / ric
        for kind, url in (("detailed", rep["detailed_url"]), ("summary", rep["summary_url"])):
            ext = ".zip" if kind == "detailed" else ".csv"
            dest = rdir / f"{kind}{ext}"
            if kind == "summary" and rep.get("summary_from_detailed_zip"):
                # Venue bundles the OES summary CSV inside the detailed zip (IEX).
                zip_path = rdir / "detailed.zip"
                if not zip_path.exists():
                    ok_all = False
                    print(f"[fetch] FAILED {ric} summary: detailed.zip missing for in-zip extraction")
                    receipts["files"].append({"ric": ric, "kind": kind, "url": url, "ok": False,
                                              "http_status": None, "note": "detailed.zip missing"})
                    continue
                import zipfile
                with zipfile.ZipFile(zip_path) as zf:
                    members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                    if not members:
                        ok_all = False
                        print(f"[fetch] FAILED {ric} summary: no .csv member in detailed.zip")
                        receipts["files"].append({"ric": ric, "kind": kind, "url": url, "ok": False,
                                                  "http_status": None, "note": "no .csv member in detailed.zip"})
                        continue
                    member = members[0]
                    dest.write_bytes(zf.read(member))
                digest = sha256_file(dest)
                receipts["files"].append({
                    "ric": ric, "kind": kind, "url": url, "ok": True,
                    "bytes": dest.stat().st_size, "sha256": digest,
                    "note": f"extracted from detailed.zip member {member}",
                    "fetched_at": now_iso(),
                })
                print(f"[fetch] ok {dest.name} (from zip member {member}) {dest.stat().st_size} bytes sha256={digest[:16]}…")
                continue
            print(f"[fetch] {ric} {kind}: {url}")
            ok, code = curl_download(url, dest)
            if not ok:
                ok_all = False
                print(f"[fetch] FAILED {ric} {kind} (HTTP {code})")
                receipts["files"].append({"ric": ric, "kind": kind, "url": url, "ok": False, "http_status": code})
                continue
            digest = sha256_file(dest)
            receipts["files"].append({
                "ric": ric, "kind": kind, "url": url, "ok": True,
                "bytes": dest.stat().st_size, "sha256": digest,
                "fetched_at": now_iso(),
            })
            print(f"[fetch] ok {dest.name} {dest.stat().st_size} bytes sha256={digest[:16]}…")

    receipt_path = RAW_DIR / "receipts.json"
    receipt_path.write_text(json.dumps(receipts, indent=1) + "\n")

    # ---- Coverage probes -------------------------------------------------
    probes = []
    for label, urls in COVERAGE_PROBES:
        for u in urls:
            p = probe_head(u)
            p["label"] = label
            probes.append(p)
            print(f"[probe] {label}: HTTP {p['http_status']}")
    probes_path = RAW_DIR / "probes.json"
    probes_path.write_text(json.dumps({"checked_at": now_iso(), "probes": probes}, indent=1) + "\n")

    print(f"\n[fetch] receipts -> {receipt_path}")
    print(f"[fetch] probes   -> {probes_path}")
    if not ok_all:
        raise SystemExit("[fetch] one or more required live files failed to download")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
