"""Source-check invariants and regressions.

Quest's operator contract (release reviewer, not Ray policy): never mutate the
producer tree; partial/unparsed inputs exit 2, not clean; compare content rather
than zip hashes. S149/S150 regressions: headerless repackaging and PASC -> PAWM.
The payload and compare functions are the policy-change points.
"""
import gzip
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import freshness as f

MONTH = "202608"
HEADER = ["DsgntParticipant", "RprtEntityCd", "RptDate"] + [f"f{i}" for i in range(52)]
SUMHEADER = ["DsgntParticipant", "RprtEntityCd", "RptDate", "AvgEFQPct"]


def detailed(ric="BATS", val="1"):
    return ("|".join(HEADER) + "\n" + "|".join(["T", ric, MONTH] + [val] * 52) + "\n").encode()


def summary(ric="BATS", val="1"):
    return (",".join(SUMHEADER) + f"\nT,{ric},{MONTH},{val}\n").encode()


def zipbytes(data, name="data.txt"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, data)
    return buf.getvalue()


def test_container_and_header_are_not_restatements(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_bytes(zipbytes(detailed()))
    # Nested zip/gz + missing header, the exact S149 false change class.
    b.write_bytes(zipbytes(gzip.compress(detailed().split(b"\n", 1)[1]), "nested.gz"))
    pa = f.payload(a, "detailed", HEADER, MONTH)[0]
    pb = f.payload(b, "detailed", HEADER, MONTH)[0]
    assert f.content_hash(pa, "detailed") == f.content_hash(pb, "detailed")
    assert f.content_hash(pa + pa.split(b"\n", 1)[1], "detailed") != f.content_hash(pa, "detailed")


@pytest.mark.parametrize("data", [b"<html>error</html>", detailed().replace(b"202608", b"202609"),
                                  detailed().replace(b"|1\n", b"\n")])
def test_unparsed_wrong_month_and_bad_width_fail_closed(tmp_path, data):
    p = tmp_path / "file"
    p.write_bytes(data)
    with pytest.raises(ValueError):
        f.payload(p, "detailed", HEADER, MONTH)


def source_tree(tmp_path):
    project = tmp_path / "project"
    raw = project / "data" / "raw" / MONTH
    (project / "pipeline").mkdir(parents=True)
    raw.mkdir(parents=True)
    urls = {}
    for i, name in enumerate(f.RECEIPTS):
        ric = ["BATS", "SHRD", "FIRM"][i]
        durl, surl = f"https://test/{ric}.zip", f"https://test/{ric}.csv"
        d, s = detailed(ric), summary(ric)
        f.install(raw / ric / "detailed.zip", d, "detailed")
        f.install(raw / ric / "summary.csv", s, "summary")
        urls[durl] = zipbytes(d)
        urls[surl] = s
        f.write(raw / name, {"files": [{"ric": ric, "kind": kind, "url": url, "ok": True}
                                      for kind, url in [("detailed", durl), ("summary", surl)]]})
    def get(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if url not in urls:
            raise RuntimeError("HTTP 404")
        dest.write_bytes(urls[url])
        return dest
    return project, raw, urls, get


def tree_hashes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_all_three_receipts_full_get_and_no_source_writes(tmp_path):
    project, raw, urls, get = source_tree(tmp_path)
    before = tree_hashes(project)
    result = f.prepare(project, tmp_path / "scratch", MONTH, get)
    assert result["sources_opened"] == result["sources_total"] == len(urls)
    assert not result["errors"]
    assert {e["receipt_file"] for e in result["events"]} == set(f.RECEIPTS)
    assert tree_hashes(project) == before
    for p in (tmp_path / "scratch" / "data" / "raw" / MONTH).rglob("*"):
        assert not p.is_symlink()


def test_fetch_failure_cannot_rebuild_from_stored_copy(tmp_path):
    project, raw, urls, get = source_tree(tmp_path)
    del urls["https://test/FIRM.zip"]
    def rebuild(*args):
        pytest.fail("partial source check must not rebuild with old bytes")
    result = f.check(project, tmp_path / "published", MONTH, tmp_path / "scratch", get, rebuild)
    assert result["exit_code"] == 2
    assert result["sources_opened"] == result["sources_total"] - 1
    assert any(e.get("ric") == "FIRM" for e in result["errors"])


def test_404_recode_requires_landing_link_and_full_content_equality(tmp_path):
    project, raw, urls, get = source_tree(tmp_path)
    doc = f.read(raw / f.RECEIPTS[-1])
    for rec in doc["files"]:
        rec["page"] = "https://test/page"
    f.write(raw / f.RECEIPTS[-1], doc)
    del urls["https://test/FIRM.zip"]
    del urls["https://test/FIRM.csv"]
    urls["https://test/page"] = b'<a href="NEW202608.zip">zip</a><a href="NEW202608.csv">csv</a>'
    urls["https://test/NEW202608.zip"] = zipbytes(detailed("NEW"))
    urls["https://test/NEW202608.csv"] = summary("NEW")
    result = f.prepare(project, tmp_path / "scratch", MONTH, get)
    assert not result["errors"]
    assert {e.get("to_ric") for e in result["events"] if e["status"] == "recoded"} == {"NEW"}
    assert not (tmp_path / "scratch" / "data" / "raw" / MONTH / "FIRM").exists()
    # Name-like links do not prove equality; a changed financial cell invalidates recode.
    urls["https://test/NEW202608.csv"] = summary("NEW", "2")
    result = f.prepare(project, tmp_path / "scratch2", MONTH, get)
    assert result["errors"]


def artifacts(root, value=1, idx=0):
    root.mkdir(parents=True, exist_ok=True)
    meta = {"month": MONTH, "venues": [{"ric": "TEST", "idx": idx, "name": "Test Firm"}],
            "summary_stat_fields": ["metric"], "built_at": "old"}
    objs = {"meta.json": meta, "summary.json": {"rows": [[idx, "N", "0", "M", value, 0]]},
            "rollup.json": {"TEST": {"cnt": value}},
            "coverage.json": {"reporters": [{"ric": "TEST", "status": "live"}]},
            "search_index.json": {"venues": ["TEST"], "rows": [["ABC", [0], value]]},
            "validation.json": {"generated_at": "old", "checks": []},
            f"symbols/{f.symbol_shard('ABC')}.json": {"ABC": {"TEST": {"a": [value]}}}}
    for name, obj in objs.items():
        f.write(root / name, obj)


def test_diff_uses_reporter_keys_not_indices_and_scans_symbol_shards(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    artifacts(a)
    artifacts(b, idx=42)
    assert f.compare(a, b)["differences"] == 0
    f.write(b / f"symbols/{f.symbol_shard('ABC')}.json", {"ABC": {"TEST": {"a": [2]}}})
    result = f.compare(a, b)
    assert result["reporters"] == {"TEST": "Test Firm"}
    assert result["examples"]["symbols:TEST"][0]["fresh"] == 2
    assert result["differences"] == 1


@pytest.mark.parametrize("value,exit_code", [(1, 0), (2, 1)])
def test_complete_scratch_rebuild_exit_contract(tmp_path, value, exit_code):
    project, raw, urls, get = source_tree(tmp_path)
    pub = tmp_path / "published"
    artifacts(pub)
    before = tree_hashes(project)
    result = f.check(project, pub, MONTH, tmp_path / "scratch", get,
                     lambda s, m: artifacts(s / "data" / "generated" / m, value))
    assert result["exit_code"] == exit_code
    assert tree_hashes(project) == before


def test_missing_artifact_is_incomplete_not_clean(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    artifacts(a)
    artifacts(b)
    (b / "coverage.json").unlink()
    with pytest.raises(ValueError):
        f.compare(a, b)


def test_scratch_must_be_disjoint_before_any_write(tmp_path):
    project, raw, urls, get = source_tree(tmp_path)
    before = tree_hashes(project)
    with pytest.raises(ValueError, match="disjoint"):
        f.prepare(project, project / "scratch", MONTH, get)
    assert tree_hashes(project) == before


def test_missing_referenced_shard_is_incomplete(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    artifacts(a)
    artifacts(b)
    (a / f"symbols/{f.symbol_shard('ABC')}.json").unlink()
    with pytest.raises(ValueError, match="missing referenced symbol shard"):
        f.compare(a, b)


def test_synthetic_summary_must_be_explicit_and_empty(tmp_path):
    project, raw, urls, get = source_tree(tmp_path)
    doc = f.read(raw / f.RECEIPTS[-1])
    rec = doc["files"][1]
    rec.update(url=None, note="no summary file located; empty summary written")
    f.write(raw / f.RECEIPTS[-1], doc)
    (raw / "FIRM" / "summary.csv").write_text(",".join(SUMHEADER) + "\n")
    result = f.prepare(project, tmp_path / "scratch", MONTH, get)
    assert not result["errors"]
    assert result["sources_opened"] == result["sources_total"] == len(urls) - 1
    assert result["events"][-1]["status"] == "synthetic_empty_summary"
    rec["note"] = None
    f.write(raw / f.RECEIPTS[-1], doc)
    result = f.prepare(project, tmp_path / "scratch2", MONTH, get)
    assert result["errors"]


def test_month_mismatch_cannot_certify_another_month(tmp_path, capsys):
    pub = tmp_path / "pub"
    artifacts(pub)
    assert f.main(["--month", "202609", "--published", str(pub)]) == 2
    assert "month does not match" in capsys.readouterr().out


def test_freshness_shard_function_matches_producer():
    from build import shard_id
    for sym in ("A", "ABC", "BRK.B", "123", "ZZZZ"):
        assert f.symbol_shard(sym) == shard_id(sym)


# Cold-review regressions (S151): exit-1 corruption, last-writer-wins receipt
# collision, lost MEMX sidecar, duplicate-key collapse and symbol/code attribution.

def test_truncated_container_is_incomplete_and_not_exit_one(tmp_path):
    project, raw, urls, get = source_tree(tmp_path)
    urls["https://test/FIRM.zip"] = zipbytes(gzip.compress(detailed("FIRM"))[:-8], "filing.gz")
    result = f.check(project, tmp_path / "pub", MONTH, tmp_path / "scratch", get,
                     lambda *a: pytest.fail("corrupt source cannot rebuild"))
    assert result["exit_code"] == 2
    assert any(e.get("ric") == "FIRM" and "end-of-stream" in e.get("error", "") for e in result["errors"])


def test_receipt_collision_refused_before_fetch_or_overwrite(tmp_path, monkeypatch):
    project, raw, urls, get = source_tree(tmp_path)
    doc = f.read(raw / f.RECEIPTS[-1])
    doc["files"].append({"ric": "BATS", "kind": "detailed", "url": "https://test/changed", "ok": True})
    f.write(raw / f.RECEIPTS[-1], doc)
    pub = tmp_path / "pub"
    artifacts(pub)
    monkeypatch.setattr(f, "PROJECT", project)
    receipt = tmp_path / "result.json"
    assert f.main(["--month", MONTH, "--published", str(pub), "--output", str(receipt)]) == 2
    assert "duplicate receipt" in f.read(receipt)["error"]


def test_non_json_month_sidecar_is_retained(tmp_path):
    project, raw, urls, get = source_tree(tmp_path)
    (raw / "memx-report-id.txt").write_text("41\n")
    f.prepare(project, tmp_path / "scratch", MONTH, get)
    assert (tmp_path / "scratch" / "data" / "raw" / MONTH / "memx-report-id.txt").read_text() == "41\n"


def test_receipt_identity_compared_but_transport_is_informational(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    artifacts(a)
    artifacts(b)
    for root, sha in ((a, "old"), (b, "new")):
        m = f.read(root / "meta.json")
        m["receipts"] = {"files": [{"ric": "TEST", "kind": "detailed", "url": "https://test/a", "ok": True, "sha256": sha}]}
        f.write(root / "meta.json", m)
    assert f.compare(a, b)["differences"] == 0
    m["receipts"]["files"][0]["url"] = "https://test/b"
    f.write(b / "meta.json", m)
    assert f.compare(a, b)["reporters"] == {"TEST": "Test Firm"}


@pytest.mark.parametrize("name", ["summary.json", "coverage.json", "meta.json"])
def test_duplicate_display_keys_cannot_hide_changed_first_row(tmp_path, name):
    a, b = tmp_path / "a", tmp_path / "b"
    artifacts(a)
    artifacts(b)
    data = f.read(b / name)
    key = {"summary.json": "rows", "coverage.json": "reporters", "meta.json": "venues"}[name]
    data[key].append(data[key][0])
    f.write(b / name, data)
    with pytest.raises(ValueError, match="duplicate"):
        f.compare(a, b)


def test_symbol_named_like_reporter_does_not_steal_attribution(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root, value in ((a, 1), (b, 2)):
        artifacts(root)
        m = f.read(root / "meta.json")
        m["venues"].append({"ric": "X", "idx": 1, "name": "Exchange X"})
        f.write(root / "meta.json", m)
        f.write(root / "search_index.json", {"venues": ["TEST"], "rows": [["X", [0], 1]]})
        (root / f"symbols/{f.symbol_shard('ABC')}.json").unlink()
        f.write(root / f"symbols/{f.symbol_shard('X')}.json", {"X": {"TEST": {"a": [value]}}})
    assert f.compare(a, b)["reporters"] == {"TEST": "Test Firm"}


def test_decompression_budget_is_enforced(monkeypatch):
    monkeypatch.setattr(f, "MAX_MEMBER_BYTES", 64)
    with pytest.raises(ValueError, match="budget"):
        list(f.leaves(zipbytes(b"A" * 1000)))


def test_previously_absent_500_keeps_transport_error(tmp_path):
    project, raw, urls, get = source_tree(tmp_path)
    doc = f.read(raw / f.RECEIPTS[-1])
    doc["files"][0]["ok"] = False
    f.write(raw / f.RECEIPTS[-1], doc)
    (raw / "FIRM" / "detailed.zip").unlink()
    def unavailable(url, dest):
        if url == "https://test/FIRM.zip":
            raise RuntimeError("HTTP 500")
        return get(url, dest)
    result = f.prepare(project, tmp_path / "scratch", MONTH, unavailable)
    assert any(e.get("error") == "HTTP 500" for e in result["errors"])


def test_capture_published_downloads_every_index_referenced_shard(tmp_path):
    source, dest = tmp_path / "source", tmp_path / "capture"
    artifacts(source / MONTH)
    f.write(source / "months.json", {"months": [{"id": MONTH, "published": True}]})
    calls = []
    def get(url, path):
        calls.append(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((source / url.split("/data/")[1]).read_bytes())
        return path
    assert f.capture_published(MONTH, dest, get) == dest / MONTH
    assert (dest / MONTH / f"symbols/{f.symbol_shard('ABC')}.json").exists()
    assert len(calls) == 1 + len(f.ARTIFACTS) + 1 + 1  # picker + core + shard + stability re-read


def test_output_failure_is_exit_two(tmp_path, monkeypatch):
    pub = tmp_path / "pub"
    artifacts(pub)
    monkeypatch.setattr(f, "check", lambda *a: {"month": MONTH, "exit_code": 0})
    output_dir = tmp_path / "directory"
    output_dir.mkdir()
    assert f.main(["--month", MONTH, "--published", str(pub), "--output", str(output_dir)]) == 2
