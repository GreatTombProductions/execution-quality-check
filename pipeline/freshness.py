"""Re-fetch a month's receipted filings, rebuild in isolation, compare published values.

No HEAD shortcuts: equal length/date does not establish equal contents. The three
receipt collections define the filing scope, not a census of newly published firms.
Historical discovery evidence (FINRA sweep, probes, directory) is retained, not
re-crawled. Run from the source checkout with its raw inputs; see README.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

PROJECT = Path(__file__).resolve().parents[1]
RECEIPTS = ("receipts.json", "harvest-receipts.json", "firm-harvest-receipts.json")
ARTIFACTS = ("meta.json", "summary.json", "rollup.json", "coverage.json",
             "search_index.json", "validation.json")


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n")


def fetch(url, dest, timeout=180):
    """GET only, with both curl and subprocess wall-clock deadlines."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl", "--silent", "--show-error", "--location", "--proto", "=https,http",
                        "--max-redirs", "5", "--retry", "2", "--retry-delay", "1",
                        "--max-time", str(timeout), "--max-filesize", str(MAX_MEMBER_BYTES), "--output", str(dest),
                        "--write-out", "%{http_code}", url], capture_output=True, text=True,
                       timeout=timeout + 5)
    code = r.stdout.strip()
    if r.returncode or code != "200":
        raise RuntimeError(f"{url}: HTTP {code}; curl {r.returncode}: {r.stderr.strip()[:200]}")
    return dest


MAX_MEMBER_BYTES = 512 * 1024 * 1024


def leaves(data, name="", depth=0, budget=None):
    """Zip/nested zip/gzip/plain, with bounded decompression and no extraction paths."""
    budget = [MAX_MEMBER_BYTES] if budget is None else budget
    if depth > 5:
        raise ValueError("container nesting exceeds 5")
    if data.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                n = info.filename
                if info.is_dir() or n.startswith("__MACOSX") or n.lower().endswith(".pdf"):
                    continue
                if info.file_size > budget[0]:
                    raise ValueError("archive exceeds decompression budget")
                budget[0] -= info.file_size
                yield from leaves(z.read(info), n, depth + 1, budget)
    elif data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            content = gz.read(budget[0] + 1)
        if len(content) > budget[0]:
            raise ValueError("gzip exceeds decompression budget")
        budget[0] -= len(content)
        yield from leaves(content, name.removesuffix(".gz"), depth + 1, budget)
    else:
        yield name, data


def payload(path, kind, header=None, month=None):
    """Select exactly one filing member; normalize an added canonical header only.

    Refuse ambiguous bundles, HTML masquerading as data, legacy layouts, mixed
    months and invalid row widths. Empty OES reports and header-only nil detailed
    files are valid. Values are not rounded or invented here.
    """
    if path.stat().st_size > MAX_MEMBER_BYTES:
        raise ValueError("download exceeds filing size limit")
    candidates = []
    for name, data in leaves(path.read_bytes()):
        if name.lower().endswith(".pdf"):
            continue
        text = data.decode("utf-8-sig", errors="strict").replace("\r\n", "\n").strip()
        first = text.split("\n", 1)[0]
        delim = "|" if kind == "detailed" else ","
        cols = next(csv.reader([first], delimiter=delim), [])
        cols = [c.strip() for c in cols]
        has_header = "RprtEntityCd" in cols and "RptDate" in cols
        headerless = kind == "detailed" and len(cols) == 55 and re.fullmatch(r"\d{6}", cols[2] or "")
        if kind == "detailed" and not ((has_header and len(cols) == 55) or headerless):
            continue
        if kind == "summary" and not (has_header and "AvgEFQPct" in cols):
            continue
        if headerless:
            if not header or len(header) != 55:
                raise ValueError("headerless file without a verified canonical 55-field header")
            cols = list(header)
            text = "|".join(cols) + "\n" + text
        dates, codes = set(), set()
        rows = csv.reader(io.StringIO(text), delimiter=delim)
        next(rows)
        for row in rows:
            if not row:
                continue
            if len(row) != len(cols):
                raise ValueError(f"invalid {kind} row width {len(row)} != {len(cols)}")
            dates.add(row[cols.index("RptDate")].strip())
            codes.add(row[cols.index("RprtEntityCd")].strip())
        if month and dates - {month}:
            raise ValueError(f"filing month {dates} is not {month}")
        candidates.append((text.encode(), cols, codes))
    if len(candidates) != 1:
        raise ValueError(f"expected one {kind} member, found {len(candidates)}")
    return candidates[0]


def content_hash(data, kind, drop_identity=False):
    """Canonical column order and row multiset; keep duplicate-row multiplicity."""
    rows = csv.reader(io.StringIO(data.decode()), delimiter="|" if kind == "detailed" else ",")
    header = next(rows)
    order = sorted(range(len(header)), key=lambda i: header[i])
    if drop_identity:
        order = [i for i in order if header[i] != "RprtEntityCd"]
    hashes = []
    for row in rows:
        if row:
            hashes.append(hashlib.sha256(json.dumps([row[i].strip() for i in order],
                                                    separators=(",", ":")).encode()).digest())
    h = hashlib.sha256(json.dumps([header[i] for i in order]).encode())
    for digest in sorted(hashes):
        h.update(digest)
    return h.hexdigest()


def install(dest, data, kind):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if kind == "detailed":
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("filing.txt", data)
    else:
        dest.write_bytes(data)


def filing_path(raw, rec):
    return raw / rec["ric"] / ("detailed.zip" if rec["kind"] == "detailed" else "summary.csv")


def recode_candidate(rec, stored, month, downloads, get):
    """A broken URL is not withdrawal when its landing page links a proven recode.

    Require exact row-multiset equality after projecting out RprtEntityCd, and a
    single different code. A merely similar firm name/filename is not evidence.
    """
    if not rec.get("page"):
        return None
    page = get(rec["page"], downloads / (rec["ric"] + "-page"))
    html = page.read_text(errors="replace")
    suffix = (".zip", ".txt", ".dat", ".gz") if rec["kind"] == "detailed" else (".csv", ".zip")
    links = sorted(set(urljoin(rec["page"], s.replace("&amp;", "&")) for s in
                       re.findall(r'(?:href|src)\s*=\s*[\"\']([^\"\']+)[\"\']', html, re.I)
                       if month in s and any(ext in s.lower() for ext in suffix)))
    matches = []
    for i, url in enumerate(links):
        try:
            p = get(url, downloads / f"{rec['ric']}-{rec['kind']}-candidate-{i}")
            data, _, codes = payload(p, rec["kind"], stored[1], month)
            if (len(codes) == 1 and codes != {rec["ric"]} and
                content_hash(data, rec["kind"], True) == content_hash(stored[0], rec["kind"], True)):
                matches.append((url, data, next(iter(codes))))
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            continue
    return matches[0] if len(matches) == 1 else None


def prepare(project, scratch, month, get=fetch):
    """Populate scratch with fresh filings only. Never hardlink/symlink mutable inputs."""
    project, scratch = project.resolve(), scratch.resolve()
    if project == scratch or project in scratch.parents or scratch in project.parents:
        raise ValueError("scratch and source checkout must be disjoint")
    raw = project / "data" / "raw" / month
    target = scratch / "data" / "raw" / month
    target.mkdir(parents=True)
    shutil.copytree(project / "pipeline", scratch / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))
    for p in (project / "data").glob("*.json"):
        shutil.copy2(p, scratch / "data" / p.name)
    for p in raw.iterdir():
        if p.is_file():
            shutil.copy2(p, target / p.name)  # Includes MEMX's month-specific .txt sidecar.
    collections = {name: read(raw / name) for name in RECEIPTS}
    records = [dict(r, receipt_file=name) for name, d in collections.items() for r in d["files"]]
    duplicates = [k for k, n in Counter((r["ric"], r["kind"]) for r in records).items() if n > 1]
    if duplicates:
        raise ValueError(f"ambiguous duplicate receipt destinations: {duplicates}")
    if any(not re.fullmatch(r"[A-Z0-9]+", r["ric"]) for r in records):
        raise ValueError("unsafe reporter code in receipts")
    urls = sorted({r["url"] for r in records if r.get("url")})
    downloads = scratch / "downloads"
    downloads.mkdir()
    fetched, failures = {}, {}
    def download(pair):
        i, url = pair
        try:
            return url, get(url, downloads / str(i)), None
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return url, None, str(exc)
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for url, path, error in pool.map(download, enumerate(urls)):
            if error:
                failures[url] = error
            else:
                fetched[url] = path
    events, errors, recodes = [], [], {}
    detailed_urls = {r.get("url") for r in records if r["kind"] == "detailed"}
    # Headerless firm files use the same authoritative header the build's harvester used.
    with zipfile.ZipFile(raw / "BATS" / "detailed.zip") as z:
        n = next(n for n in z.namelist() if n.lower().endswith((".txt", ".dat")))
        header = z.open(n).readline().decode("utf-8-sig").strip().split("|")
    for name, collection in collections.items():
        for rec in collection["files"]:
            kind, ric, url = rec["kind"], rec["ric"], rec.get("url")
            event = {"receipt_file": name, "ric": ric, "kind": kind, "url": url,
                     "retrieval": "synthetic" if not url else (
                         "member_of_detailed_object" if kind == "summary" and url in detailed_urls else "direct")}
            try:
                if not url:
                    # These are explicitly synthetic, empty summaries, not unchecked filings.
                    if kind != "summary" or "no summary file located" not in (rec.get("note") or ""):
                        raise ValueError("missing URL without an explicit synthetic-summary receipt")
                    p = filing_path(raw, rec)
                    data, _, codes = payload(p, kind, header, month)
                    if codes:
                        raise ValueError("unreceipted nonempty local summary")
                    install(filing_path(target, rec), data, kind)
                    event["status"] = "synthetic_empty_summary"
                elif url in failures:
                    if not rec.get("ok") and "HTTP 404" in failures[url]:
                        event["status"] = "confirmed_previously_absent"
                    else:
                        replacement = None
                        if "HTTP 404" in failures[url] and rec.get("page"):
                            try:
                                old = payload(filing_path(raw, rec), kind, header, month)
                                replacement = recode_candidate(rec, old, month, downloads, get)
                            except Exception as exc:
                                raise RuntimeError(f"{failures[url]}; recode check: {exc}") from exc
                        if replacement is None:
                            raise RuntimeError(failures[url])
                        new_url, data, new_ric = replacement
                        if not re.fullmatch(r"[A-Z0-9]+", new_ric):
                            raise ValueError("unsafe reporter code")
                        recodes.setdefault(ric, set()).add(new_ric)
                        rec.update(ric=new_ric, url=new_url, recoded_from=ric)
                        install(filing_path(target, rec), data, kind)
                        event.update(status="recoded", to_ric=new_ric, replacement_url=new_url)
                else:
                    # Byte identity proves content identity; differing containers still
                    # require the inner-row comparison, never the converse inference.
                    old_path = filing_path(raw, rec)
                    if not old_path.exists():
                        old_path = filing_path(raw / "first-filed", rec)
                    def digest(path):
                        with path.open("rb") as stream:
                            return hashlib.file_digest(stream, "sha256").digest()
                    old_digest, fetched_digest = digest(old_path), digest(fetched[url])
                    event.update(stored_input_sha256=old_digest.hex(), download_sha256=fetched_digest.hex(),
                                 download_hash_matches_stored_input=old_digest == fetched_digest)
                    if old_digest == fetched_digest:
                        _, _, codes = payload(fetched[url], kind, header, month)
                        if codes - {ric}:
                            raise ValueError(f"unexpected reporter codes {codes}; expected {ric}")
                        dest = filing_path(target, rec)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(fetched[url], dest)
                        same = True
                    else:
                        data, _, codes = payload(fetched[url], kind, header, month)
                        if codes - {ric}:
                            raise ValueError(f"unexpected reporter codes {codes}; expected {ric}")
                        old = payload(old_path, kind, header, month)
                        same = content_hash(old[0], kind) == content_hash(data, kind)
                        install(filing_path(target, rec), data, kind)
                    rec["ok"] = True
                    event["status"] = "content_same" if same else "content_changed"
                events.append(event)
            except Exception as exc:  # Decoding failures are incomplete, never exit-1 differences.
                event.update(status="unavailable", error=str(exc))
                events.append(event)
                errors.append(event)
    for ric, codes in recodes.items():
        if len(codes) != 1:
            errors.append({"ric": ric, "error": "inconsistent detailed/summary recodes"})
        else:
            ledger = read(target / "restatement-ledger.json") if (target / "restatement-ledger.json").exists() else {}
            new_code = next(iter(codes))
            directory = read(scratch / "data" / "reporter_directory.json") if (scratch / "data" / "reporter_directory.json").exists() else {}
            ledger.setdefault("recoded", []).append({
                "from_ric": ric, "to_ric": new_code,
                "name": directory.get(new_code, {}).get("name", f"Reporter {ric}"),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "note": "Download Site links a replacement whose rows match except for RprtEntityCd.",
            })
            write(target / "restatement-ledger.json", ledger)
    for name, collection in collections.items():
        write(target / name, collection)
    # Every active reporter must have both inputs; failed fetches cannot fall back to stored bytes.
    active = {r["ric"] for c in collections.values() for r in c["files"] if r.get("ok")}
    for ric in active:
        if not all((target / ric / n).exists() for n in ("detailed.zip", "summary.csv")):
            errors.append({"ric": ric, "error": "incomplete fresh filing pair"})
    return {"receipt_records": len(records), "sources_total": len(urls),
            "sources_opened": len(fetched), "expected_absent": sum(e["status"] == "confirmed_previously_absent" for e in events),
            "events": events, "errors": errors}


def canonical(name, obj, meta):
    """Compare values, not position-dependent venue ids or transport receipts.

    Exact numeric values backing the UI are compared (conservative: may flag a
    change below screen rounding). Historical timestamps and container receipts
    are not financial/statistical differences. Discovery evidence is retained.
    """
    ids = {v["idx"]: v["ric"] for v in meta["venues"]}
    if len(ids) != len(meta["venues"]) or len({v["ric"] for v in meta["venues"]}) != len(ids):
        raise ValueError("duplicate reporter identity or index in meta")
    if name == "meta.json":
        receipts = {}
        for r in obj.get("receipts", {}).get("files", []):
            bucket = receipts.setdefault(r["ric"], {})
            if r["kind"] in bucket:
                raise ValueError("duplicate displayed receipt identity")
            bucket[r["kind"]] = {k: r.get(k) for k in ("url", "ok", "restated", "recoded_from", "note")}
        return {**{k: v for k, v in obj.items() if k not in ("built_at", "receipts", "venues")},
                "receipts": receipts,
                "venues": {v["ric"]: {k: x for k, x in v.items() if k != "idx"} for v in obj["venues"]}}
    if name == "summary.json":
        fields = meta["summary_stat_fields"] + ["flags"]
        result = {}
        for r in obj["rows"]:
            if len(r) != 4 + len(fields):
                raise ValueError("unsupported published summary row layout")
            key = "/".join([ids[r[0]], *map(str, r[1:4])])
            if key in result:
                raise ValueError(f"ambiguous duplicate summary key {key}")
            result[key] = dict(zip(fields, r[4:]))
        return result
    if name == "search_index.json":
        if len({r[0] for r in obj["rows"]}) != len(obj["rows"]):
            raise ValueError("duplicate search symbol identity")
        return {r[0]: [sorted(obj["venues"][i] for i in r[1]), *r[2:]] for r in obj["rows"]}
    if name == "coverage.json":
        if len({r["ric"] for r in obj["reporters"]}) != len(obj["reporters"]):
            raise ValueError("duplicate coverage reporter identity")
        return {**{k: v for k, v in obj.items() if k != "reporters"},
                "reporters": {r["ric"]: r for r in obj["reporters"]}}
    if name == "validation.json":
        return {k: v for k, v in obj.items() if k != "generated_at"}
    return obj


def differences(a, b, path=""):
    if a == b:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(a.keys() | b.keys()):
            if k not in a or k not in b:
                yield path + "/" + k, a.get(k), b.get(k)
            else:
                yield from differences(a[k], b[k], path + "/" + k)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from differences(x, y, path + "/" + str(i))
    else:
        yield path, a, b


def symbol_shard(sym):
    # Same FNV-1a contract as build.py and frontend/common.js.
    h = 0x811c9dc5
    for b in sym.encode():
        h = ((h ^ b) * 0x01000193) & 0xffffffff
    return f"{h & 0xff:02x}"


def compare(published, fresh):
    """Scan every shard, retain bounded examples per reporter/surface, count all diffs."""
    ma, mb = read(published / "meta.json"), read(fresh / "meta.json")
    for root in (published, fresh):
        for sid in {symbol_shard(r[0]) for r in read(root / "search_index.json")["rows"]}:
            if not (root / "symbols" / f"{sid}.json").exists():
                raise ValueError(f"missing referenced symbol shard {sid} in {root}")
    names = {v["ric"]: v["name"] for m in (ma, mb) for v in m["venues"]}
    old_codes, new_codes = {v["ric"] for v in ma["venues"]}, {v["ric"] for v in mb["venues"]}
    recoded = [r for r in mb.get("recoded", []) if r["from_ric"] in old_codes and r["to_ric"] in new_codes]
    counts, samples, changed_reporters = Counter(), {}, set()
    files = set(ARTIFACTS) | {str(p.relative_to(published)) for p in (published / "symbols").glob("*.json")} | {str(p.relative_to(fresh)) for p in (fresh / "symbols").glob("*.json")}
    for name in sorted(files):
        pa, pb = published / name, fresh / name
        # Missing core files are not an empty dataset; missing shards can mean added/deleted symbols.
        if name in ARTIFACTS and (not pa.exists() or not pb.exists()):
            raise ValueError(f"missing comparison artifact {name}")
        a = canonical(name, read(pa), ma) if pa.exists() else {}
        b = canonical(name, read(pb), mb) if pb.exists() else {}
        for path, before, after in differences(a, b):
            # Attribute by schema position, not any matching token: a symbol can
            # itself be named X, B, Q or U (also valid reporter codes).
            parts = path.strip("/").split("/")
            ric = None
            if name.startswith("symbols/") and len(parts) > 1:
                ric = parts[1]
            elif name in ("summary.json", "rollup.json"):
                ric = parts[0]
            elif name == "meta.json" and parts[0] in ("venues", "receipts") and len(parts) > 1:
                ric = parts[1]
            elif name == "coverage.json" and parts[0] == "reporters" and len(parts) > 1:
                ric = parts[1]
            if ric not in names:
                ric = None
            if ric:
                changed_reporters.add(ric)
            group = name.split("/")[0] + ":" + (ric or "overall")
            counts[group] += 1
            bucket = samples.setdefault(group, [])
            if len(bucket) < 3:
                bucket.append({"file": name, "path": path, "published": before, "fresh": after})
    return {"differences": sum(counts.values()), "counts_by_surface_reporter": dict(counts),
            "reporters": {r: names[r] for r in sorted(changed_reporters)},
            "recoded": recoded, "examples": samples, "artifacts_compared": len(files)}


def check(project, published, month, scratch, get=fetch, rebuild=None):
    result = {"month": month, "checked_at": datetime.now(timezone.utc).isoformat(),
              "scope": "One month's receipted filings; synthetic empty summaries are local, not fetched. FINRA directory, sweep, census and coverage probes remain historical evidence. The month picker is not compared. Receipt identities are compared; transport hashes/timestamps are informational, not value differences."}
    result.update(prepare(project, scratch, month, get))
    if result["errors"]:
        result["exit_code"] = 2
        return result
    try:
        if rebuild is None:
            log = scratch / "build.log"
            with log.open("w") as f:
                subprocess.run([sys.executable, str(scratch / "pipeline" / "build.py"), "--month", month],
                               stdout=f, stderr=subprocess.STDOUT, check=True, timeout=1800)
        else:
            rebuild(scratch, month)
        result["comparison"] = compare(published, scratch / "data" / "generated" / month)
        result["exit_code"] = int(bool(result["comparison"]["differences"]))
    except Exception as exc:  # Unexpected parser/schema failures must not masquerade as differences.
        result.update(exit_code=2, error=str(exc))
        if (scratch / "build.log").exists():
            result["build_log_tail"] = (scratch / "build.log").read_text()[-4000:]
    return result


def capture_published(month, dest, get=fetch):
    """Read the live Pages receiver, including every shard referenced by its index."""
    base = "https://greattombproductions.github.io/execution-quality-check/data/"
    index = read(get(base + "months.json", dest / "months.json"))
    if not any(m["id"] == month and m.get("published") for m in index["months"]):
        raise ValueError(f"{month} is not a published month")
    for name in ARTIFACTS:
        get(base + month + "/" + name, dest / month / name)
    if read(dest / month / "meta.json")["month"] != month:
        raise ValueError("live snapshot month mismatch")
    shards = {symbol_shard(r[0]) for r in read(dest / month / "search_index.json")["rows"]}
    def one(sid):
        get(base + month + f"/symbols/{sid}.json", dest / month / "symbols" / f"{sid}.json")
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, sorted(shards)))
    confirm = get(base + month + "/meta.json", dest / "meta-confirm.json")
    if confirm.read_bytes() != (dest / month / "meta.json").read_bytes():
        raise ValueError("published meta changed during capture; retry against a stable revision")
    return dest / month


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--month", required=True, help="Reporting month YYYYMM; one invocation checks exactly this month")
    p.add_argument("--published", type=Path, help="Override live Pages baseline with a published month's data directory, e.g. a historical gh-pages checkout's data/202608")
    p.add_argument("--output", type=Path, help="JSON receipt with full counts and bounded examples (outside source checkout)")
    args = p.parse_args(argv)
    if not re.fullmatch(r"\d{4}(0[1-9]|1[0-2])", args.month):
        p.error("--month must be YYYYMM")
    for protected in (PROJECT, args.published):
        if args.output and protected and (args.output.resolve() == protected.resolve() or protected.resolve() in args.output.resolve().parents):
            p.error("--output must be outside both the source checkout and the published baseline")
    result = {"month": args.month, "exit_code": 2}
    try:
        with tempfile.TemporaryDirectory(prefix="eqc-freshness-") as tmp:
            root = Path(tmp)
            published = args.published or capture_published(args.month, root / "published")
            if read(published / "meta.json")["month"] != args.month:
                raise ValueError("published snapshot month does not match --month")
            result = check(PROJECT, published, args.month, root / "scratch")
            result["published_baseline"] = str(args.published) if args.published else "live GitHub Pages"
    except Exception as exc:  # Final CLI boundary: all incomplete checks have status 2.
        result.update(exit_code=2, error=str(exc))
    if args.output:
        try:
            write(args.output, result)
        except OSError as exc:
            result.update(exit_code=2, error=f"could not write receipt: {exc}")
    print(f"{args.month}: opened {result.get('sources_opened', 0)} of {result.get('sources_total', 'unknown')} sources; "
          f"{result.get('expected_absent', 0)} previously absent URLs confirmed absent; exit {result['exit_code']}")
    print(result.get("scope", "Source check did not complete."))
    print("Source results:", dict(Counter(e["status"] for e in result.get("events", []))))
    containers = sum(e["status"] == "content_same" and e.get("download_hash_matches_stored_input") is False for e in result.get("events", []))
    print(f"Container-only / harvester-wrapper differences: {containers}; download and stored-input hashes in --output JSON.")
    if result.get("error"):
        print("INCOMPLETE:", result["error"], result.get("build_log_tail", ""))
    for error in result.get("errors", []):
        print("INCOMPLETE:", json.dumps(error, ensure_ascii=False))
    diff = result.get("comparison", {})
    for ric, name in diff.get("reporters", {}).items():
        print(f"{args.month}: {ric} — {name}")
    for recode in diff.get("recoded", []):
        print(f"{args.month}: RECODE {recode['from_ric']} -> {recode['to_ric']}")
    for group, examples in diff.get("examples", {}).items():
        print(f"{args.month}: {group}: {diff['counts_by_surface_reporter'][group]} differing values")
        for example in examples:
            text = json.dumps(example, ensure_ascii=False)
            print(" ", text if len(text) < 1200 else text[:1200] + " … (full example in --output JSON)")
    if result["exit_code"] == 0:
        print(f"{args.month}: no differences in the numeric values and metadata backing the displayed pages.")
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
