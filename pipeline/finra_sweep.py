"""Per-firm FINRA-registered coverage sweep -> data/raw/<month>/finra-sweep.json.

Inputs: data/reporter_directory.json (223 FINRA-registered reporters), harvest-receipts.json
(shared-host census), and the crawl of FINRA's per-firm Link Site pages (Reporter ID, Current
Report URL, Submission History) saved at CRAWL (default data/raw/<month>/finra-crawl.json; optional; firms the crawl has
not reached are reported as such, never guessed).

Per firm that has no file in this build, the status is one of:
  published_legacy     — an August file is published but not in the amended 55-field layout
  published_pdf_only   — the firm's page links August files, but only PDFs
  registered_only      — registered; no August file located (note says where we looked)
  surface_unresolved   — Download Site URL not retrieved / refused / unreachable / not machine-probeable
Firm-hosted pages are read by harvest_firms.py (firm-harvest-receipts.json); the in-line probe below is a
fallback for pages that script has not read.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import reporters  # noqa: E402
reporters.activate(reporters.month_from_argv())
LIVE = reporters.LIVE
MONTH = reporters.MONTH
MONTH_LABEL = reporters.MONTH_LABEL

RAW = PROJECT_DIR / "data" / "raw" / MONTH
_POS = reporters.positionals()
CRAWL = Path(_POS[0]) if _POS else RAW / "finra-crawl.json"
_WHEN = "August" if MONTH == "202608" else MONTH_LABEL.split(" ")[0]
_WHEN_LONG = "August 2026" if MONTH == "202608" else reporters.MONTH_LABEL
_MENTION = reporters.month_mention_re(MONTH)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def probe(url):
    r = subprocess.run(["curl", "-s", "-L", "--max-time", "25", "-A", UA, "-w", "\n---STATUS:%{http_code}", url],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"---STATUS:(\d+)$", r.stdout.strip())
    code = m.group(1) if m else "000"
    body = r.stdout[:m.start()] if m else r.stdout
    hit = bool(_MENTION.search(body))
    # August sweeps stored this key. Later months use month_mention. Readers accept both.
    key = "august_2026_mention" if MONTH == "202608" else "month_mention"
    return {"url": url, "http_status": code, key: hit, "bytes": len(body), "checked_at": now()}


def firm_page_status(pg, host):
    """Status + note for a firm-hosted Download Site from its harvest_firms.py page record."""
    code = pg.get("http_status")
    kinds = {}
    for f in pg.get("findings", []):
        kinds.setdefault(f["kind"], []).append(f)
    if code != "200":
        refused = code in ("401", "403", "429")
        why = ("refused this request" if refused else "did not answer usefully")
        return {"status": "surface_unresolved",
                "note": f"Registered Download Site {host} {why} (HTTP {code}) from the sweep host; not read."}
    if "amended_detailed" in kinds:
        return {"status": "registered_only",
                "note": f"Registered Download Site {host} carries amended-format files under other reporter codes "
                        f"({', '.join(f['ric'] for f in kinds['amended_detailed'])}), not under this one."}
    if "duplicate" in kinds and "amended_detailed" not in kinds:
        return {"status": "registered_only",
                "note": f"Registered Download Site {host} links the same amended-format files already read from another "
                        f"registered page ({'; '.join(sorted({(f.get('why') or '').split(' already')[0] for f in kinds['duplicate']}))}); "
                        f"no {_WHEN} file under this reporter code."}
    if "legacy" in kinds:
        why = "; ".join(sorted({(f.get("why") or "") for f in kinds["legacy"]}))
        return {"status": "published_legacy",
                "note": f"Registered Download Site {host} links an {_WHEN} file in a layout other than the amended "
                        f"55-field detailed format ({why}); not used."}
    if "pdf" in kinds and len(kinds) == 1:
        return {"status": "published_pdf_only",
                "note": f"Registered Download Site {host} links {_WHEN_LONG} PDFs only — no machine-readable file."}
    if not pg.get("candidates"):
        return {"status": "registered_only",
                "note": f"Registered Download Site {host} answered HTTP 200; no link naming {_WHEN_LONG} ({MONTH}) "
                        f"on the page (a page that builds its links in the browser would not show them here)."}
    return {"status": "surface_unresolved",
            "note": f"Registered Download Site {host} links {_WHEN} files that could not be used "
                    f"({', '.join(sorted(kinds))})."}


def main():
    directory = json.loads((PROJECT_DIR / "data" / "reporter_directory.json").read_text())
    harvest = json.loads((RAW / "harvest-receipts.json").read_text())
    census = harvest["census"]
    crawl = json.loads(CRAWL.read_text()) if CRAWL.exists() else {}
    live = {r["ric"] for r in LIVE}
    fp = RAW / "firm-harvest-receipts.json"
    pages = {}
    if fp.exists():
        for pg in json.loads(fp.read_text()).get("pages", []):
            pages[pg["page_url"]] = pg
    firms = {d["slug"]: d for d in directory.values() if d.get("finra_url")}
    todo, out = [], {}
    for slug, d in firms.items():
        c = crawl.get(slug, {})
        rec = {"ric": d["ric"], "name": d["name"], "type": d["type"]}
        if c.get("ok"):
            rec["report_url"] = c.get("report_url")
            rec["submissions"] = c.get("submissions", [])
        out[slug] = rec
        if d["ric"] in live:
            rec["status"] = "live_amended"
            continue
        url = rec.get("report_url")
        if not c.get("ok"):
            rec["status"] = "surface_unresolved"
            rec["note"] = ("Registered with FINRA; the firm's Download Site URL was not retrieved (FINRA's Link Site "
                           "rate-limited the per-firm sweep, so this firm's page was not reached). No " + _WHEN + " file "
                           "was found on the shared host under this reporter code.")
            continue
        host = urlparse(url or "").netloc
        if "public.s3.com" in host:
            m = re.search(r"/rule605/([a-z0-9._-]+)/?", url)
            d_ = m.group(1) if m else None
            c_ = census.get(d_, {})
            rec["status"] = "registered_only"
            others = sorted({re.sub(rf"{MONTH}\..*$", "", f) for f in (c_.get(f"files_{MONTH}") or c_.get("month_files") or [])})
            rec["note"] = (f"Registered Download Site is shared-host directory '{d_}'; listing re-read "
                           f"{harvest['harvested_at']}: {c_.get('n_files', 0)} files, none for {_WHEN_LONG} under this reporter code"
                           + (f" (the directory does carry {_WHEN} files under other codes: {', '.join(others)})." if others else "."))
        elif "vista-one-solutions" in host:
            rec["status"] = "surface_unresolved"
            rec["note"] = ("Registered Download Site is a Vista One report viewer (form-driven, no file listing); "
                           f"not machine-probed. No {_WHEN} file was found on the shared host under this reporter code.")
        elif not url:
            rec["status"] = "surface_unresolved"
            rec["note"] = "No Current Report URL on the firm's FINRA page."
        elif url in pages:
            rec.update(firm_page_status(pages[url], urlparse(url).netloc))
        else:
            todo.append(slug)
    with cf.ThreadPoolExecutor(6) as ex:
        results = dict(zip(todo, ex.map(lambda s: probe(out[s]["report_url"]), todo)))
    for slug, p in results.items():
        rec = out[slug]
        rec["probe"] = p
        ok = p["http_status"] == "200"
        rec["status"] = "registered_only" if ok else "surface_unresolved"
        rec["note"] = (f"Registered Download Site {urlparse(p['url']).netloc} answered HTTP {p['http_status']}; "
                       + (f"the page text mentions {_WHEN_LONG} / {MONTH} — not verified as an amended-format file, "
                          "not used in this build." if p.get("august_2026_mention", p.get("month_mention")) else
                          f"no {_WHEN_LONG} file link found in the page text.")
                       if ok else
                       f"Registered Download Site {urlparse(p['url']).netloc} did not answer (HTTP {p['http_status']}).")
    (RAW / "finra-sweep.json").write_text(json.dumps(out, indent=1) + "\n")
    from collections import Counter
    print(Counter(r["status"] for r in out.values()), "crawled:", sum(1 for s in crawl.values() if s.get("ok")))


if __name__ == "__main__":
    main()
