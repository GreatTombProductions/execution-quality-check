// Symbol explorer: search index -> letter shard -> per-venue comparison.
(async () => {
  const [meta, index] = await Promise.all([
    EQ.loadJSON("data/meta.json"),
    EQ.loadJSON("data/search_index.json"),
  ]);
  const venues = meta.venues;
  const symRow = {};
  index.rows.forEach(r => { symRow[r[0]] = r; }); // [sym, [venue idx...], cnt, exctd, effW, efqW]
  const shardCache = {};

  const input = document.getElementById("symInput");
  const resultsEl = document.getElementById("results");
  const detailEl = document.getElementById("detail");
  const sizes = meta.sizes.map(([code, label]) => ({ code, label }));

  async function getShard(id) {
    if (!shardCache[id]) {
      shardCache[id] = await EQ.loadJSON(`data/symbols/${id}.json`);
    }
    return shardCache[id];
  }

  function shardName(sym) { return EQ.shardId(sym); }

  let selected = null;

  function renderResults() {
    const q = input.value.trim().toUpperCase();
    resultsEl.innerHTML = "";
    if (!q) { resultsEl.classList.remove("open"); return; }
    const matches = index.rows
      .filter(r => r[0].startsWith(q))
      .sort((a, b) => b[2] - a[2])
      .slice(0, 20);
    if (!matches.length) {
      resultsEl.innerHTML = `<div class="row"><span class="muted">no symbol starts with “${EQ.esc(q)}”</span></div>`;
      resultsEl.classList.add("open");
      return;
    }
    resultsEl.innerHTML = matches.map(r => {
      const n = r[1].length;
      const avail = `${n} reporter${n === 1 ? "" : "s"}`;
      return `<div class="row" data-sym="${EQ.esc(r[0])}">
        <span><span class="sym">${EQ.esc(r[0])}</span>
        <span class="meta"> — ${EQ.num(r[2])} covered orders · ${avail}</span></span>
        <span class="meta">eff. spread ${EQ.pct(r[4])}</span></div>`;
    }).join("");
    resultsEl.classList.add("open");
  }

  async function selectSymbol(sym) {
    selected = sym;
    input.value = sym;
    resultsEl.classList.remove("open");
    const shard = await getShard(shardName(sym));
    const rec = shard[sym];
    if (!rec) { detailEl.style.display = "none"; return; }
    detailEl.style.display = "";

    document.getElementById("symTitle").innerHTML =
      `${EQ.esc(sym)} <span class="small muted">· August 2026</span>`;
    const sizeSel = document.getElementById("sizeSel");
    sizeSel.innerHTML = "";
    const present = new Set();
    Object.values(rec).forEach(v => v.s.forEach(s => present.add(s[0])));
    const opts = [["ALL", "All sizes combined"]];
    sizes.forEach(s => { if (present.has(s.code)) opts.push([s.code, `${s.code} — ${s.label}`]); });
    opts.forEach(([code, label]) => {
      const o = document.createElement("option");
      o.value = code; o.textContent = label;
      sizeSel.appendChild(o);
    });
    renderDetail(rec);
  }

  // Size row: [size, covCnt, exctdQty, piQty, effW, efqW]
  // ALL row: [covCnt, exctdQty, piQty, effW, efqW]
  function renderDetail(rec) {
    const view = document.getElementById("viewSel").value;
    const size = document.getElementById("sizeSel").value;
    const thead = document.querySelector("#symTable thead");
    const tbody = document.querySelector("#symTable tbody");

    thead.innerHTML = "<tr><th>Reporter</th><th class='num'>Covered orders</th>" +
      "<th class='num'>Executed shares (all venues)</th><th class='num'>Price-improved shares</th>" +
      "<th class='num'>PI share of executed</th><th class='num'>Effective spread</th>" +
      "<th class='num'>EFQ</th></tr>";

    const out = [];
    venues.forEach((v, vi) => {
      const entry = rec[v.ric];
      if (!entry) return;                    // reporters with no record for this symbol are not listed
      let r;
      if (view === "sizes" || size === "ALL") {
        r = [entry.a[0], entry.a[1], entry.a[2], entry.a[3], entry.a[4]];   // covCnt, exctd, pi, eff, efq
      } else {
        const row = entry.s.find(x => x[0] === size);
        if (!row) return;
        r = [row[1], row[2], row[3], row[4], row[5]];
      }
      const piShare = r[1] > 0 ? r[2] / r[1] : null;
      out.push({ cnt: r[0], html: `<tr><td><a href="${EQ.esc(v.link_site)}" rel="noopener">${EQ.esc(v.short)}</a> ` +
        `<span class="small muted">${EQ.esc(EQ.kindLabel(v.kind))}</span></td>` +
        `<td class="num">${EQ.num(r[0])}</td>` +
        `<td class="num">${EQ.num(r[1])}</td>` +
        `<td class="num">${EQ.num(r[2])}</td>` +
        `<td class="num">${EQ.pct(piShare)}</td>` +
        `<td class="num">${EQ.pct(r[3])}</td>` +
        `<td class="num">${EQ.pct(r[4])}</td></tr>` });
    });
    out.sort((a, b) => b.cnt - a.cnt);
    const rows = out.length ? out.map(o => o.html).join("")
      : `<tr><td colspan="7" class="muted">no reporter has records for this size bucket</td></tr>`;
    tbody.innerHTML = rows;

    document.getElementById("symNote").innerHTML =
      `Share-weighted averages computed from each reporter’s detailed records ` +
      `(all order types combined; rows failing sanity checks are excluded from weighted statistics — ` +
      `exclusions are counted in <a href="method.html">Method</a>). ` +
      `${out.length} of ${venues.length} reporters have records for this view, sorted by covered orders. ` +
      `EFQ = effective spread ÷ quoted spread.`;
  }

  input.addEventListener("input", renderResults);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      const first = resultsEl.querySelector(".row[data-sym]");
      if (first) selectSymbol(first.dataset.sym);
    }
  });
  resultsEl.addEventListener("click", e => {
    const row = e.target.closest(".row[data-sym]");
    if (row) selectSymbol(row.dataset.sym);
  });
  document.getElementById("viewSel").addEventListener("change", () => {
    document.getElementById("sizeSel").disabled =
      document.getElementById("viewSel").value === "sizes";
  });
  document.getElementById("sizeSel").addEventListener("change", () => {
    if (selected) getShard(shardName(selected)).then(s => renderDetail(s[selected]));
  });

  // Deep link: ?s=SYM
  const params = new URLSearchParams(location.search);
  const s = (params.get("s") || "").trim().toUpperCase();
  if (s && symRow[s]) selectSymbol(s);
})();
