"""Compare stored August files to the URLs they were fetched from.

Content-Length first. A length that moved is downloaded to a temp file and hashed.
The stored zip and the tracked receipts are never overwritten. A changed hash is
an event in data/raw/<month>/restatement-check.json.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import reporters  # noqa: E402

UA = "curl/8.5.0"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def head(url):
    out = subprocess.run(
        ["curl", "-s", "-I", "--max-time", "40", "-A", UA, url],
        capture_output=True, text=True, errors="replace",
    ).stdout
    status = None
    for line in out.splitlines():
        if line.startswith("HTTP/"):
            parts = line.split()
            if len(parts) > 1:
                status = parts[1]
    length = None
    for line in out.splitlines():
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip() or "0")
    return status, length


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    month = reporters.month_from_argv()
    raw = PROJECT_DIR / "data" / "raw" / month
    records = []
    for name in ("receipts.json", "harvest-receipts.json"):
        path = raw / name
        if not path.exists():
            continue
        for rec in json.loads(path.read_text()).get("files", []):
            if rec.get("url") and rec.get("sha256"):
                records.append(rec)
    # A summary row that points at the detailed zip is the extracted CSV, not a
    # second HTTP object. Comparing the zip's Content-Length to the CSV's
    # byte count is not a restatement.
    detailed_urls = {
        (rec.get("ric"), rec["url"])
        for rec in records
        if rec.get("kind") == "detailed" and rec.get("url")
    }
    events = []
    unchanged = 0
    unknown = 0
    same_url = 0
    for rec in records:
        if rec.get("kind") == "summary" and (rec.get("ric"), rec["url"]) in detailed_urls:
            same_url += 1
            continue
        status, length = head(rec["url"])
        local = rec.get("bytes")
        event = {
            "ric": rec.get("ric"), "kind": rec.get("kind"), "url": rec["url"],
            "stored_sha256": rec.get("sha256"), "stored_bytes": local,
            "http_status": status, "content_length": length, "checked_at": now_iso(),
        }
        if status != "200" or length is None or local is None:
            event["result"] = "length_unavailable"
            unknown += 1
        elif int(length) == int(local):
            event["result"] = "length_unchanged"
            unchanged += 1
        else:
            event["result"] = "length_changed"
            with tempfile.NamedTemporaryFile(prefix="eqc-restate-") as tmp:
                subprocess.run(
                    ["curl", "-s", "--max-time", "900", "-A", UA, "-o", tmp.name, rec["url"]],
                    check=False,
                )
                digest = sha256(Path(tmp.name))
            event["new_sha256"] = digest
            event["changed"] = digest != rec.get("sha256")
        events.append(event)
        if event["result"] != "length_unchanged":
            print(f"[restate] {event['ric']} {event['kind']} {event['result']}")
    out = {
        "month": month,
        "checked_at": now_iso(),
        "files": len(events),
        "length_unchanged": unchanged,
        "length_unavailable": unknown,
        "length_changed": sum(1 for e in events if e["result"] == "length_changed"),
        "hash_changed": sum(1 for e in events if e.get("changed")),
        "same_url_as_detailed": same_url,
        "events": [e for e in events if e["result"] != "length_unchanged"],
    }
    dest = raw / "restatement-check.json"
    dest.write_text(json.dumps(out, indent=1) + "\n")
    print(f"[restate] {unchanged} unchanged, {unknown} unknown, {out['length_changed']} length-changed -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
