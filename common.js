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
      legacy_only: ["legacy", "Legacy files only (no August file yet)"],
      registered_only: ["registered", "Registered — no report content yet"],
      surface_unresolved: ["unresolved", "Reporting surface unresolved"],
    };
    const [cls, label] = map[status] || ["unresolved", status];
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }

  return { loadJSON, pct, num, bp, esc, flagText, statusBadge };
})();
