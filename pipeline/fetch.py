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


def curl_download(url, dest, timeout=600):
    """Download url -> dest via curl. Returns (ok, http_code, last_modified)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    hdr = dest.with_suffix(dest.suffix + ".hdr")
    r = subprocess.run(
        ["curl", "-s", "-L", "--max-time", str(timeout), "-D", str(hdr),
         "-A", UA_BROWSER, "-o", str(dest), "-w", "%{http_code}", url],
        capture_output=True, text=True,
    )
    code = (r.stdout or "").strip()
    last_mod = None
    if hdr.exists():
        for line in hdr.read_text(errors="replace").splitlines():
            if line.lower().startswith("last-modified:"):
                last_mod = line.split(":", 1)[1].strip()
        hdr.unlink()
    if code != "200" or not dest.exists() or dest.stat().st_size == 0:
        return False, code, last_mod
    return True, code, last_mod


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
    # (label, list of urls) — coverage evidence from the exchange surfaces that are NOT on the
    # shared host. Absence signals: a direct 404, or NasdaqTrader's 302 -> /Trader.aspx?id=http404
    # (its not-found convention) — a bare 302 must not read as a reachable file.
    ("Cboe BZX 202608", ["https://cdn.cboe.com/resources/us/equities/regulation/605/bzx/BATS202608.zip"]),
    ("IEX 202608", ["https://storage.googleapis.com/iex/regulation/605/reports/IEXG_202608.zip"]),
    ("MIAX Pearl 202608 (restated, amended format)",
     ["https://www.miaxglobal.com/sites/default/files/rule_report-files/heprl202608.zip"]),
    ("MEMX report list API", ["https://www.memxtrading.com/api/v1/report/605"]),
    ("MEMX 202608 (report id 40)", ["https://www.memxtrading.com/api/v1/report/605/40"]),
    ("Nasdaq 202608", ["https://www.nasdaqtrader.com/content/MarketRegulation/NASDAQ/eqreport/eqreport_202608.zip"]),
    ("Nasdaq PSX 202608", ["https://www.nasdaqtrader.com/content/MarketRegulation/PSX/eqreport/psxeqreport_202608.zip"]),
    ("Nasdaq NTX-path 202608", ["https://www.nasdaqtrader.com/content/MarketRegulation/NTX/eqreport/ntxeqreport_202608.zip"]),
    ("Nasdaq BX-path 202608", ["https://www.nasdaqtrader.com/content/MarketRegulation/BX/eqreport/bxeqreport_202608.zip"]),
    # NYSE group, 24X and LTSE publish through the shared Download Site host (public.s3.com).
    ("NYSE group directory (shared host)", ["https://public.s3.com/rule605/nyse/?C=M;O=D"]),
    ("24X directory (shared host)", ["https://public.s3.com/rule605/24eq/?C=M;O=D"]),
    ("LTSE directory (shared host)", ["https://public.s3.com/rule605/ltse/?C=M;O=D"]),
    ("Shared host root (reporter directories)", ["https://public.s3.com/rule605/"]),
    ("FINRA Rule 605 Link Site", ["https://www.finra.org/filing-reporting/regulation-nms/sec-rule-605-reports"]),
]


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    receipts = {"month": MONTH, "fetched_at": now_iso(), "files": []}
    ok_all = True
    prior = {}
    prior_path = RAW_DIR / "receipts.json"
    if prior_path.exists():
        for f in json.loads(prior_path.read_text()).get("files", []):
            prior[(f["ric"], f["kind"], f.get("sha256"))] = f

    for rep in LIVE:
        if rep.get("harvested"):
            continue            # shared-host reporters are fetched by harvest_s3.py
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
            if dest.exists() and dest.stat().st_size > 0 and "--refresh" not in sys.argv:
                digest = sha256_file(dest)
                receipts["files"].append({
                    "ric": ric, "kind": kind, "url": url, "ok": True,
                    "bytes": dest.stat().st_size, "sha256": digest,
                    "last_modified": prior.get((ric, kind, digest), {}).get("last_modified"),
                    "note": "reused local copy (pass --refresh to re-download)",
                    "fetched_at": now_iso(),
                })
                print(f"[fetch] reuse {dest.name} {dest.stat().st_size} bytes sha256={digest[:16]}…")
                continue
            print(f"[fetch] {ric} {kind}: {url}")
            ok, code, last_mod = curl_download(url, dest)
            if rep.get("api_generated"):
                last_mod = None      # header is the API response time, not a publication time
            if not ok:
                ok_all = False
                print(f"[fetch] FAILED {ric} {kind} (HTTP {code})")
                receipts["files"].append({"ric": ric, "kind": kind, "url": url, "ok": False, "http_status": code})
                continue
            digest = sha256_file(dest)
            receipts["files"].append({
                "ric": ric, "kind": kind, "url": url, "ok": True,
                "bytes": dest.stat().st_size, "sha256": digest,
                "last_modified": last_mod, "fetched_at": now_iso(),
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
