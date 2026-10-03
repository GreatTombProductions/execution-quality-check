// Shared helpers for Execution Quality Check pages.
const EQ = (() => {
  async function loadJSON(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`failed to load ${path}: ${r.status}`);
    return r.json();
  }

  // Percent formatting: values are fractions (0.000691 -> "0.069%").
  function pct(v, dash = "—") {
    if (v === null || v === undefined) return dash;
    const a = Math.abs(v) * 100;
    let s;
    if (a === 0) s = "0";
    else if (a < 0.001) s = (v * 100).toExponential(2);
    else if (a < 0.01) s = (v * 100).toFixed(4);
    else if (a < 0.1) s = (v * 100).toFixed(3);
    else if (a < 10) s = (v * 100).toFixed(2);
    else s = (v * 100).toFixed(1);
    return s + "%";
  }

  function num(v, dp = 0) {
    if (v === null || v === undefined) return "—";
    return Number(v).toLocaleString(undefined, { maximumFractionDigits: dp });
  }

  function bp(v) {
    if (v === null || v === undefined) return "—";
    return (v * 10000).toFixed(v === 0 ? 0 : (Math.abs(v * 10000) < 10 ? 1 : 0)) + " bp";
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function flagText(bits, metaBits) {
    const out = [];
    for (const k of ["1", "2", "4", "8"]) {
      if (bits & Number(k)) out.push(metaBits && metaBits[k] ? metaBits[k] : `flag ${k}`);
    }
    return out.join("; ");
  }

  function statusBadge(status) {
    const map = {
      live_amended: ["live", "Amended-format files live"],
      published_legacy: ["legacy", "August file published — legacy format"],
      published_pdf_only: ["legacy", "August file published — PDF only"],
      legacy_only: ["legacy", "Legacy files only (no August file yet)"],
      registered_only: ["registered", "Registered — no August file located"],
      surface_unresolved: ["unresolved", "Reporting surface unresolved"],
    };
    const [cls, label] = map[status] || ["unresolved", status];
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }

  // 256-way symbol shard id: FNV-1a (32-bit) over the symbol's UTF-8 bytes, low byte as hex.
  // Exact mirror of pipeline/build.py shard_id (tests/test_shard_parity.py compares them).
  function shardId(sym) {
    const bytes = new TextEncoder().encode(sym);
    let h = 0x811c9dc5;
    for (const b of bytes) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0; }
    return (h & 0xff).toString(16).padStart(2, "0");
  }

  const KINDS = {
    "exchange": "Exchange",
    "broker-dealer": "Broker-dealer",
    "otc-market-maker": "OTC market maker",
    "ats": "Alternative trading system",
    "single-dealer-system": "Single-dealer trading system",
    "exchange-market-maker": "Exchange market maker",
    "other": "Other",
  };
  const kindLabel = k => KINDS[k] || k;

  // Click-to-sort for generated tables: rows is an array of {cells: [html...], keys: [sortKey...]}.
  function sortRows(rows, col, dir) {
    const sign = dir === "desc" ? -1 : 1;
    return rows.slice().sort((a, b) => {
      const x = a.keys[col], y = b.keys[col];
      const xn = x === null || x === undefined, yn = y === null || y === undefined;
      if (xn || yn) return xn && yn ? 0 : (xn ? 1 : -1);   // missing values always last
      if (typeof x === "string" || typeof y === "string") return sign * String(x).localeCompare(String(y));
      return sign * (x - y);
    });
  }

  return { loadJSON, pct, num, bp, esc, flagText, statusBadge, kindLabel, KINDS, sortRows, shardId };

})();
