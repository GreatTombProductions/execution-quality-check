"""The frontend and the build must place every symbol in the same shard (FNV-1a mirror pair)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "pipeline"))
from build import shard_id  # noqa: E402

JS = """
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const EQ = new Function(src + '; return EQ;')();
const syms = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
console.log(JSON.stringify(syms.map(s => EQ.shardId(s))));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_shard_id_parity_all_symbols(tmp_path):
    idx = json.loads((PROJECT / "data" / "generated" / "search_index.json").read_text())
    syms = [r[0] for r in idx["rows"]] + ["BRK.B", "A", "ZZZZ", "TEST-1"]
    (tmp_path / "syms.json").write_text(json.dumps(syms))
    (tmp_path / "run.js").write_text(JS)
    out = subprocess.run(
        ["node", str(tmp_path / "run.js"), str(PROJECT / "frontend" / "common.js"), str(tmp_path / "syms.json")],
        capture_output=True, text=True, check=True).stdout
    js_ids = json.loads(out)
    assert js_ids == [shard_id(s) for s in syms]
    assert len(set(js_ids)) == 256 or len(syms) < 5000   # spread across all shards


def test_shard_id_known_values():
    assert shard_id("") == "c5"          # FNV offset basis 0x811c9dc5 -> low byte
    assert len(shard_id("AAPL")) == 2
