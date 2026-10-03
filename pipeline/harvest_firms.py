"""Harvest August-2026 amended-format Rule 605 files from firm-hosted Download Sites.

Many FINRA-registered firms publish on their own sites (or a vendor's) rather than the shared host.
For every registered firm that is not already in the build, this reads the firm's registered Current
Report URL (from the FINRA sweep, data/raw/<month>/finra-crawl.json), collects links that name the
reporting month, downloads the candidates, and keeps only files that pass a content check: a
pipe-delimited detailed file whose header carries the 55 amended-format fields (OES summary CSVs are
paired by the reporter code inside them). Everything else is recorded, not guessed: PDF-only
publication, legacy-format files, unreachable pages, unsupported containers.

Writes raw files under data/raw/<month>/<RIC>/ (detailed.zip + summary.csv — the layout fetch.py and
harvest_s3.py use) and the tracked receipts data/raw/<month>/firm-harvest-receipts.json.
Resumable: files already on disk with the recorded size are not re-downloaded.
"""
from __future__ import annotations

import concurrent.futures as cf
import csv
import gzip
import io
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
from reporters import MONTH  # noqa: E402

RAW = PROJECT_DIR / "data" / "raw" / MONTH
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
# Transport divergence is real (a CDN may refuse the browser UA yet serve curl's default, or the reverse):
# try each before recording a page as unreachable.
UAS = ("curl/8.5.0", UA)
SKIP_HOSTS = ("public.s3.com", "vista-one-solutions.com")
# A link names the reporting month as 202608, 2026-08, a "aug...605" file name, or a year=2026&month=8 query.
MONTH_RE = re.compile(rf"{MONTH}|{MONTH[:4]}[-_/]{MONTH[4:]}|aug(?:ust)?[-_]?(?:{MONTH[:4]})?[-_a-z]*605"
                      rf"|year={MONTH[:4]}&month=0?{int(MONTH[4:])}(?!\d)", re.I)
EXTS = (".zip", ".csv", ".gz", ".txt", ".dat", ".pdf")
REQUIRED = ("DsgntParticipant", "RprtEntityCd", "RptDate", "Symbol", "OrderSize", "OrderType")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def curl_text(url, timeout=40):
    code, final, body = "000", url, ""
    for ua in UAS:
        r = subprocess.run(["curl", "-s", "-L", "--max-time", str(timeout), "-A", ua, "-w", "\n---STATUS:%{http_code}|%{url_effective}", url],
                           capture_output=True, text=True, errors="replace")
        m = re.search(r"---STATUS:(\d+)\|(.*)$", r.stdout.strip())
        if not m:
            continue
        code, final, body = m.group(1), m.group(2), r.stdout[:m.start()]
        if code == "200":
            break
    return code, final, body


def curl_file(url, dest, timeout=900):
    hdr = dest.with_suffix(dest.suffix + ".hdr")
    code, lm = "000", None
    for ua in UAS:
        r = subprocess.run(["curl", "-s", "-L", "--max-time", str(timeout), "-A", ua, "-D", str(hdr), "-o", str(dest),
                            "-w", "%{http_code}", url], capture_output=True, text=True)
        code = (r.stdout or "").strip()
        lm = None
        if hdr.exists():
            for line in hdr.read_text(errors="replace").splitlines():
                if line.lower().startswith("last-modified:"):
                    lm = line.split(":", 1)[1].strip()
            hdr.unlink()
        if code == "200":
            break
    return code, lm


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def url_name(url):
    """File name of a link: the path's last segment, or a name=/file=/filename= query value when the
    path is an opaque download route (e.g. /file/<id>?name=BFEC202608.zip)."""
    u = urlparse(url)
    q = parse_qs(u.query)
    for key in ("name", "file", "filename", "fn"):
        if q.get(key):
            return q[key][0]
    return Path(u.path).name or "file"


def is_download_route(href):
    return "action=download" in href.lower() or "/download" in href.lower().split("?")[0]


def sniff_ext(path):
    """Effective extension from content when the link's name carries none we know."""
    head = path.read_bytes()[:512]
    if head[:2] == b"PK":
        return ".zip"
    if head[:2] == b"\x1f\x8b":
        return ".gz"
    if head[:4] == b"%PDF":
        return ".pdf"
    text = head.decode("utf-8-sig", "replace")
    first = text.split("\n", 1)[0]
    if "RprtEntityCd" in first and "," in first and "|" not in first:
        return ".csv"
    if "|" in first:
        return ".txt"
    return ""


def candidate_links(html, base):
    seen, out = set(), []
    for m in re.finditer(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", html, re.I):
        href = m.group(1).strip().replace("&amp;", "&")
        if MONTH_RE.search(href) and (url_name(href).lower().endswith(EXTS) or is_download_route(href)):
            url = urljoin(base, href)
            if url not in seen:
                seen.add(url)
                out.append(url)
    return out


_CANON = []


def canonical_header():
    """The 55 Exhibit A field names in order, read from a conformant file already in the build."""
    if not _CANON:
        for d in sorted(RAW.iterdir()):
            z = d / "detailed.zip"
            if d.is_dir() and z.exists():
                with zipfile.ZipFile(z) as zf:
                    txt = [n for n in zf.namelist() if n.lower().endswith((".txt", ".dat"))]
                    with zf.open(txt[0]) as f:
                        cols = f.readline().decode("utf-8", "replace").rstrip("\r\n").split("|")
                if len(cols) == 55 and all(c in cols for c in REQUIRED):
                    _CANON.extend(cols)
                    break
    return _CANON


def line_kind(first_line, second_line=""):
    """'header' (named 55-field header), 'headerless' (data rows in the 55-field layout, no header row), or 'legacy'."""
    cols = first_line.rstrip("\r\n").split("|")
    if len(cols) == 55 and all(c in cols for c in REQUIRED):
        return "header"
    if len(cols) == 55 and re.fullmatch(r"[A-Z0-9]", cols[0] or "") and cols[2] == MONTH and cols[1]:
        return "headerless"
    return "legacy"


def _zip_detailed_info(zf, label=""):
    names = zf.namelist()
    txt = [n for n in names if n.lower().endswith((".txt", ".dat"))]
    if not txt:
        return None
    with zf.open(txt[0]) as f:
        first = f.readline().decode("utf-8", "replace")
        second = f.readline().decode("utf-8", "replace")
    lk = line_kind(first, second)
    if lk == "legacy":
        return {"kind": "legacy", "why": f"{len(first.rstrip(chr(13) + chr(10)).split('|'))}-field first line"}
    row = (second if lk == "header" else first).rstrip("\r\n").split("|")
    cols = first.rstrip("\r\n").split("|") if lk == "header" else canonical_header()
    ric = row[cols.index("RprtEntityCd")].strip() if len(row) == 55 else None
    csvs = [n for n in names if n.lower().endswith(".csv")]
    return {"kind": "detailed", "ric": ric, "zip_csv": csvs[0] if csvs else None, "txt": txt[0], "headerless": lk == "headerless"}


def classify(path, url):
    """Classify a downloaded candidate: detailed (zip / nested zip / gz / txt), summary, pdf, legacy, unsupported."""
    low = url_name(url).lower()
    if not low.endswith(EXTS):
        low += sniff_ext(path)          # opaque download route: decide the container from the bytes
    if low.endswith(".pdf"):
        return {"kind": "pdf"}
    try:
        if low.endswith(".zip"):
            zf = zipfile.ZipFile(path)
            info = _zip_detailed_info(zf)
            if info is not None:
                return info
            inner = [n for n in zf.namelist() if n.lower().endswith(".zip")]
            if inner:                      # e.g. an outer zip bundling inner zip + summary csv + pdf
                with zipfile.ZipFile(io.BytesIO(zf.read(inner[0]))) as izf:
                    info = _zip_detailed_info(izf)
                if info is not None:
                    info["nested_zip"] = inner[0]
                    outer_csv = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                    info["zip_csv_outer"] = outer_csv[0] if outer_csv else None
                    return info
            return {"kind": "unsupported", "why": f"zip without .txt/.dat member ({zf.namelist()[:4]})"}
        if low.endswith((".gz", ".txt", ".dat")):
            raw = gzip.open(path).read(1 << 20) if low.endswith(".gz") else path.read_bytes()[:1 << 20]
            lines = raw.decode("utf-8", "replace").split("\n")
            lk = line_kind(lines[0], lines[1] if len(lines) > 1 else "")
            if lk == "legacy":
                return {"kind": "legacy", "why": f"{len(lines[0].rstrip(chr(13)).split('|'))}-field first line"}
            row = (lines[1] if lk == "header" else lines[0]).rstrip("\r").split("|")
            cols = lines[0].rstrip("\r").split("|") if lk == "header" else canonical_header()
            ric = row[cols.index("RprtEntityCd")].strip() if len(row) == 55 else None
            return {"kind": "detailed_raw", "ric": ric, "gz": low.endswith(".gz"), "headerless": lk == "headerless"}
        if low.endswith(".csv"):
            head = path.read_bytes()[:4000].decode("utf-8-sig", "replace")
            rows = list(csv.reader(io.StringIO(head)))
            cols = [c.strip() for c in rows[0]] if rows else []
            if "RprtEntityCd" not in cols or "AvgEFQPct" not in cols:
                return {"kind": "unsupported", "why": "csv is not an OES summary"}
            ric = rows[1][cols.index("RprtEntityCd")].strip() if len(rows) > 1 and len(rows[1]) == len(cols) else None
            return {"kind": "summary", "ric": ric, "empty": len(rows) < 2}
    except Exception as e:  # noqa: BLE001 — recorded, not fatal
        return {"kind": "unsupported", "why": repr(e)[:120]}
    return {"kind": "unsupported", "why": "extension"}


def process_firm(job):
    slug, rec = job
    page_url = rec["report_url"]
    out = {"slug": slug, "ric": rec["ric"], "name": rec["name"], "page_url": page_url, "checked_at": now_iso(),
           "also_registered_here": rec.get("also", []), "files": [], "notes": []}
    code, final, html = curl_text(page_url)
    out["http_status"] = code
    if code != "200":
        out["notes"].append(f"page answered HTTP {code}")
        return out
    links = candidate_links(html, final)
    out["candidates"] = links
    if not links:
        out["notes"].append("page carries no link naming the month")
        return out
    tmp = RAW / "_firm_tmp" / slug
    tmp.mkdir(parents=True, exist_ok=True)
    detailed, summaries = [], []
    for u in links:
        name = url_name(u)
        dest = tmp / name
        status, lm = curl_file(u, dest)
        info = {"url": u, "http_status": status, "last_modified": lm}
        if status != "200" or not dest.exists() or dest.stat().st_size == 0:
            info["kind"] = "unreachable"
            out["files"].append(info)
            continue
        c = classify(dest, u)
        info.update(c)
        info["bytes"] = dest.stat().st_size
        info["_path"] = str(dest)
        out["files"].append(info)
        if c["kind"] in ("detailed", "detailed_raw"):
            detailed.append(info)
        elif c["kind"] == "summary":
            summaries.append(info)
    return out


def install(results):
    """Move verified files into data/raw/<month>/<RIC>/ and write receipts."""
    receipts = {"month": MONTH, "harvested_at": now_iso(), "files": [], "pages": []}
    claimed = {}
    for res in results:
        page = {k: v for k, v in res.items() if k != "files"}
        page["findings"] = []
        det = [f for f in res["files"] if f.get("kind") in ("detailed", "detailed_raw")]
        summ = {f["ric"]: f for f in res["files"] if f.get("kind") == "summary" and f.get("ric")}
        for f in res["files"]:
            if f.get("kind") in ("pdf", "legacy", "unsupported", "unreachable"):
                page["findings"].append({"url": f["url"], "kind": f["kind"], "why": f.get("why"), "http_status": f.get("http_status")})
        for f in det:
            src = Path(f["_path"])
            ric = f.get("ric")
            if not ric:
                page["findings"].append({"url": f["url"], "kind": "nil_or_unreadable", "why": "no data row to read the reporter code from"})
                continue
            if ric in claimed:
                page["findings"].append({"url": f["url"], "kind": "duplicate", "why": f"{ric} already taken from {claimed[ric]}"})
                continue
            claimed[ric] = f["url"]
            d = RAW / ric
            d.mkdir(parents=True, exist_ok=True)
            dest = d / "detailed.zip"
            notes = []
            if f["kind"] == "detailed" and not f.get("nested_zip") and not f.get("headerless"):
                dest.write_bytes(src.read_bytes())
            else:
                # Repackage as a plain detailed zip: unwrap nested zips, decompress gz, supply the canonical
                # Exhibit A header when the file ships data rows only.
                if f["kind"] == "detailed":
                    outer = zipfile.ZipFile(src)
                    zf = zipfile.ZipFile(io.BytesIO(outer.read(f["nested_zip"]))) if f.get("nested_zip") else outer
                    data = zf.read(f["txt"])
                    if f.get("nested_zip"):
                        notes.append(f"unwrapped from nested zip {f['nested_zip']}")
                else:
                    data = gzip.open(src).read() if f.get("gz") else src.read_bytes()
                    notes.append("repackaged from " + ("gz" if f.get("gz") else "plain text"))
                if f.get("headerless"):
                    data = ("|".join(canonical_header()) + "\n").encode() + data
                    notes.append("file ships data rows without a header row; canonical Exhibit A column order applied")
                with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf2:
                    zf2.writestr(f"{ric}{MONTH}.txt", data)
            receipts["files"].append({"ric": ric, "kind": "detailed", "url": f["url"], "ok": True, "bytes": dest.stat().st_size,
                                      "sha256": sha256(dest), "last_modified": f.get("last_modified"), "fetched_at": now_iso(),
                                      "page": res["page_url"], "slug": res["slug"], "headerless": bool(f.get("headerless")),
                                      "note": "; ".join(notes) or None})
            sdest = d / "summary.csv"
            s = summ.get(ric)
            if s:
                sdest.write_bytes(Path(s["_path"]).read_bytes())
                s_url, s_lm, s_note = s["url"], s.get("last_modified"), None
            elif f["kind"] == "detailed" and (f.get("zip_csv") or f.get("zip_csv_outer")):
                with zipfile.ZipFile(src) as zf:
                    member = f.get("zip_csv_outer") or f["zip_csv"]
                    sdest.write_bytes(zf.read(member))
                s_url, s_lm, s_note = f["url"], f.get("last_modified"), f"extracted from zip member {member}"
            else:
                sdest.write_text("DsgntParticipant,RprtEntityCd,RptDate,Sp500,OrderSize,OrderType,AvgOrderQty,AvgOrderSize,"
                                 "ExctdOrderMidPtAvg,PctExctdAtQuotOrBetter,PctExctdPI,WghtdAvgPctPI,AvgEffctvSprdPct,"
                                 "AvgPctQuotSprd,AvgEFQPct,AvgRealSprdPct15sec,AvgRealSprdPct1min,WghtdAvgExctnTime\n")
                s_url, s_lm, s_note = None, None, "no summary file located; empty summary written (detailed file is authoritative)"
            receipts["files"].append({"ric": ric, "kind": "summary", "url": s_url, "ok": True, "bytes": sdest.stat().st_size,
                                      "sha256": sha256(sdest), "last_modified": s_lm, "fetched_at": now_iso(),
                                      "page": res["page_url"], "slug": res["slug"], "note": s_note})
            page["findings"].append({"url": f["url"], "kind": "amended_detailed", "ric": ric})
        receipts["pages"].append(page)
    return receipts


def main():
    crawl = json.loads((RAW / "finra-crawl.json").read_text())
    s3 = json.loads((RAW / "harvest-receipts.json").read_text())
    taken = {f["ric"] for f in s3["files"] if f["ok"]}
    only = set(sys.argv[1:])
    jobs = []
    for slug, rec in crawl.items():
        if not rec.get("ok"):
            continue
        ric = rec.get("Reporter ID")
        url = rec.get("report_url")
        if not url or ric in taken or any(h in urlparse(url).netloc for h in SKIP_HOSTS):
            continue
        if only and ric not in only and slug not in only:
            continue
        jobs.append((slug, {"ric": ric, "name": rec["name"], "report_url": url}))
    # firms sharing one page (several codes at one URL) are processed once
    by_url, uniq = {}, []
    for slug, rec in jobs:
        if rec["report_url"] in by_url:
            by_url[rec["report_url"]]["also"].append(rec["ric"])
            continue
        rec["also"] = []
        by_url[rec["report_url"]] = rec
        uniq.append((slug, rec))
    print(f"[firms] {len(uniq)} distinct firm pages to read ({len(jobs)} firms)", flush=True)
    with cf.ThreadPoolExecutor(6) as ex:
        results = list(ex.map(process_firm, uniq))
    receipts = install(results)
    # merge with prior receipts (keep earlier pages for firms not re-read this run)
    path = RAW / "firm-harvest-receipts.json"
    if path.exists() and only:
        prev = json.loads(path.read_text())
        done = {p["page_url"] for p in receipts["pages"]}
        receipts["pages"] += [p for p in prev["pages"] if p["page_url"] not in done]
        have = {(f["ric"], f["kind"]) for f in receipts["files"]}
        receipts["files"] += [f for f in prev["files"] if (f["ric"], f["kind"]) not in have]
    path.write_text(json.dumps(receipts, indent=1) + "\n")
    tmp = RAW / "_firm_tmp"
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp)
    n = len({f["ric"] for f in receipts["files"]})
    print(f"[firms] {n} reporters with amended-format files; receipts -> {path}")


if __name__ == "__main__":
    main()
