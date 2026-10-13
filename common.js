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

  let monthId = null;
  let monthLabel = null;
  let publishedMonths = [];

  async function openMonth() {
    if (monthId) return { id: monthId, label: monthLabel };
    const idx = await loadJSON("data/months.json");
    publishedMonths = (idx.months || []).filter(m => m.published);
    const q = new URLSearchParams(location.search).get("month");
    const known = publishedMonths.map(m => `${m.label} (${m.id})`).join(", ") || "none";
    if (q && !publishedMonths.some(m => m.id === q)) {
      const note = document.createElement("p");
      note.className = "card";
      note.id = "monthError";
      note.textContent = `Month ${q} is not published. Published: ${known}.`;
      (document.querySelector(".wrap") || document.body).prepend(note);
      return new Promise(() => {});
    }
    const rec = publishedMonths.find(m => m.id === (q || idx.default));
    if (!rec) {
      const note = document.createElement("p");
      note.className = "card";
      note.id = "monthError";
      note.textContent = `No published month. Published: ${known}.`;
      (document.querySelector(".wrap") || document.body).prepend(note);
      return new Promise(() => {});
    }
    monthId = rec.id;
    monthLabel = rec.label;
    const nav = document.querySelector("header.site nav");
    if (nav && !document.getElementById("monthSel")) {
      const sel = document.createElement("select");
      sel.id = "monthSel";
      sel.setAttribute("aria-label", "Reporting month");
      for (const m of publishedMonths) {
        const o = document.createElement("option");
        o.value = m.id;
        o.textContent = m.label;
        if (m.id === monthId) o.selected = true;
        sel.appendChild(o);
      }
      sel.addEventListener("change", () => {
        const u = new URL(location.href);
        u.searchParams.set("month", sel.value);
        location.href = u.pathname.split("/").pop() + u.search;
      });
      nav.appendChild(sel);
    }
    document.querySelectorAll("header.site nav a").forEach(a => {
      const u = new URL(a.getAttribute("href"), location.href);
      u.searchParams.set("month", monthId);
      a.setAttribute("href", (u.pathname.split("/").pop() || a.getAttribute("href")) + u.search);
    });
    document.querySelectorAll("[data-month-label]").forEach(el => { el.textContent = monthLabel; });
    return rec;
  }

  async function loadMonth(name) {
    await openMonth();
    return loadJSON(`data/${monthId}/${name}`);
  }

  function monthFile(name) {
    return `data/${monthId}/${name}`;
  }

  function statusBadge(status) {
    const when = monthLabel || "This month";
    const map = {
      live_amended: ["live", "Amended-format files live"],
      published_legacy: ["legacy", `${when} file published — legacy format`],
      published_pdf_only: ["legacy", `${when} file published — PDF only`],
      legacy_only: ["legacy", `Legacy files only (no ${when} file yet)`],
      registered_only: ["registered", `Registered — no ${when} file located`],
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

  return { loadJSON, loadMonth, openMonth, monthFile, pct, num, bp, esc, flagText, statusBadge, kindLabel, KINDS, sortRows, shardId };

})();
