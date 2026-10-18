// Venue comparison page.
(async () => {
  const [meta, summary, rollup] = await Promise.all([
    EQ.loadMonth("meta.json"),
    EQ.loadMonth("summary.json"),
    EQ.loadMonth("rollup.json"),
  ]);

  const venues = meta.venues; // {idx, ric, name, short, ...}
  const asOf = document.getElementById("filesAsOf");
  if (asOf && meta.files_as_of) {
    const restated = meta.restated_rics || [];
    const gone = meta.withdrawn || [];
    const parts = [`Files as fetched on ${meta.files_as_of} (UTC).`];
    if (restated.length) {
      parts.push(`${restated.length} reporter code${restated.length === 1 ? "" : "s"} replaced an August file after the first harvest (${restated.join(", ")}). Those rows are the current file. The first-filed copy is kept with the source build and is not what this page shows.`);
    }
    if (gone.length) {
      parts.push(`Not shown, because the host no longer serves the file: ${gone.map(g => `${g.name || g.ric} (${g.ric})`).join(", ")}.`);
    }
    asOf.hidden = false;
    asOf.textContent = parts.join(" ");
  }
  const statIdx = {};
  meta.summary_stat_fields.forEach((f, i) => { statIdx[f] = i; });
  const ROW_STAT_OFFSET = 4; // [venue, sp500, size, type, ...stats, flags]

  // Size options: meta sizes + any extra size codes present in summary rows (e.g. 199999).
  const sizeLabels = {};
  meta.sizes.forEach(([code, label]) => { sizeLabels[code] = label; });
  const extra = new Set();
  summary.rows.forEach(r => { if (!(r[2] in sizeLabels)) extra.add(r[2]); });
  extra.forEach(code => {
    if (code === "199999") sizeLabels[code] = "$0 – <$200,000 (combined, as filed)";
    else sizeLabels[code] = "$" + code;
  });

  const sizeSel = document.getElementById("sizeSel");
  const sizeOrder = ["0", "250", "1000", "5000", "10000", "20000", "50000", "199999", "200000"]
    .filter(c => c in sizeLabels);
  const allCodes = [...sizeOrder];
  Object.keys(sizeLabels).forEach(c => { if (!allCodes.includes(c)) allCodes.push(c); });
  for (const code of allCodes) {
    const opt = document.createElement("option");
    opt.value = code;
    opt.textContent = `${code} — ${sizeLabels[code]}`;
    sizeSel.appendChild(opt);
  }
  // Default: $1,000 – <$5,000 — the classic retail band.
  sizeSel.value = "1000";

  const COLUMNS = [
    ["PctExctdPI", "Price improvement rate", EQ.pct],
    ["WghtdAvgPctPI", "Weighted avg price improvement", EQ.pct],
    ["PctExctdAtQuotOrBetter", "Executed at quote or better", EQ.pct],
    ["AvgEffctvSprdPct", "Effective spread", EQ.pct],
    ["AvgPctQuotSprd", "Quoted spread", EQ.pct],
    ["AvgEFQPct", "Effective/quoted ratio (EFQ)", EQ.pct],
    ["AvgRealSprdPct15sec", "Realized spread (15s)", EQ.pct],
    ["AvgRealSprdPct1min", "Realized spread (1 min)", EQ.pct],
    ["WghtdAvgExctnTime", "Avg execution time (reported units)", v => EQ.num(v, 1)],
  ];

  // Reporter-type filter options (only kinds actually present).
  const kindSel = document.getElementById("kindSel");
  kindSel.innerHTML = '<option value="">All reporters (' + venues.length + ')</option>' +
    [...new Set(venues.map(v => v.kind))].map(k =>
      `<option value="${EQ.esc(k)}">${EQ.esc(EQ.kindLabel(k))} (${venues.filter(v => v.kind === k).length})</option>`).join("");

  // (venue idx, sp500, size, type) -> summary row
  const rowMap = new Map();
  summary.rows.forEach(r => rowMap.set(`${r[0]}|${r[1]}|${r[2]}|${r[3]}`, r));

  let sortCol = 0, sortDir = "asc";   // column 0 = reporter (index order: exchanges first, then A-Z)

  function renderTable() {
    const size = sizeSel.value;
    const sp500 = document.getElementById("sp500Sel").value;
    const type = document.getElementById("typeSel").value;
    const kind = kindSel.value;
    const q = document.getElementById("nameFilter").value.trim().toLowerCase();

    const thead = document.querySelector("#cmpTable thead");
    const tbody = document.querySelector("#cmpTable tbody");
    const arrow = i => (i === sortCol ? (sortDir === "asc" ? " ▲" : " ▼") : "");
    thead.innerHTML = `<tr><th data-col="0" class="sortable">Reporter${arrow(0)}</th>` +
      COLUMNS.map((c, i) => `<th class="num sortable" data-col="${i + 1}">${EQ.esc(c[1])}${arrow(i + 1)}</th>`).join("") + "</tr>";

    const rows = [];
    let withData = 0, hidden = 0;
    venues.forEach((v, vi) => {
      if (kind && v.kind !== kind) return;
      if (q && !(v.name.toLowerCase().includes(q) || v.ric.toLowerCase() === q)) return;
      const row = rowMap.get(`${v.idx}|${sp500}|${size}|${type}`);
      if (!row) { hidden++; return; }
      withData++;
      const flags = row[row.length - 1];
      const keys = [vi];
      const cells = [`<td><a href="${EQ.esc(v.link_site)}" rel="noopener">${EQ.esc(v.short)}</a> ` +
        `<span class="small muted">${EQ.esc(EQ.kindLabel(v.kind))}</span></td>`];
      COLUMNS.forEach((c, i) => {
        const val = row[ROW_STAT_OFFSET + statIdx[c[0]]];
        keys.push(val === undefined ? null : val);
        let txt = c[2](val);
        if (i === 0 && flags) txt += ' <span class="flag" title="' +
          EQ.esc(EQ.flagText(flags, meta.summary_flag_bits)) + '">⚠</span>';
        cells.push(`<td class="num">${txt}</td>`);
      });
      rows.push({ cells, keys });
    });
    const sorted = EQ.sortRows(rows, sortCol, sortDir);
    tbody.innerHTML = sorted.length
      ? sorted.map(r => `<tr>${r.cells.join("")}</tr>`).join("")
      : `<tr><td colspan="${COLUMNS.length + 1}" class="muted">no reporter matches this combination</td></tr>`;

    document.getElementById("cmpNote").innerHTML =
      `${EQ.esc(meta.month_label)} · ${EQ.esc(sizeLabels[size] || size)} · ` +
      (sp500 === "N" ? "non-S&P 500 stocks" : "S&P 500 stocks") + " · " +
      (type === "M" ? "market orders" : "marketable limit orders") +
      `. ${withData} reporter${withData === 1 ? "" : "s"} published a row for this combination` +
      (hidden ? ` (${hidden} more in the current filter did not — hidden)` : "") + `. ` +
      `Values as published by each reporter; one documented normalization applied to the EFQ column ` +
      `where a reporter files it in percent-points (see <a href="method.html">Method</a>). ` +
      `⚠ = sanity flag on the published row.`;
  }

  document.querySelector("#cmpTable thead").addEventListener("click", e => {
    const th = e.target.closest("th[data-col]");
    if (!th) return;
    const col = Number(th.dataset.col);
    if (col === sortCol) sortDir = sortDir === "asc" ? "desc" : "asc";
    else { sortCol = col; sortDir = col === 0 ? "asc" : "desc"; }
    renderTable();
  });
  ["sizeSel", "sp500Sel", "typeSel", "kindSel"].forEach(id =>
    document.getElementById(id).addEventListener("change", renderTable));
  document.getElementById("nameFilter").addEventListener("input", renderTable);
  renderTable();

  // ---- Scale table (from detailed records) ----
  const scaleHead = document.querySelector("#scaleTable thead");
  const scaleBody = document.querySelector("#scaleTable tbody");
  scaleHead.innerHTML = "<tr><th>Reporter</th><th style='text-align:left'>Type</th><th class='num'>Covered orders</th>" +
    "<th class='num'>Executed shares (all venues)</th><th class='num'>of which executed away</th>" +
    "<th class='num'>Price-improved shares</th><th class='num'>PI share of executed</th></tr>";
  const scaleRows = venues.map(v => ({ v, r: rollup[v.ric] }))
    .sort((a, b) => ((b.r && b.r.cnt) || 0) - ((a.r && a.r.cnt) || 0));
  scaleBody.innerHTML = scaleRows.map(({ v, r }) => {
    const kind = `<td class="small muted" style="text-align:left">${EQ.esc(EQ.kindLabel(v.kind))}</td>`;
    if (!r || v.nil_report) return `<tr><td>${EQ.esc(v.short)}</td>${kind}<td colspan="5" class="muted">` +
      `${v.nil_report ? "nil report — no covered-order rows filed" : "no data"}</td></tr>`;
    const piShare = r.exctd > 0 ? r.pi / r.exctd : null;
    const awayShare = r.exctd > 0 ? 1 - r.own / r.exctd : null;
    return `<tr><td>${EQ.esc(v.short)}</td>${kind}` +
      `<td class="num">${EQ.num(r.cnt)}</td>` +
      `<td class="num">${EQ.num(r.exctd)}</td>` +
      `<td class="num">${EQ.pct(awayShare)}</td>` +
      `<td class="num">${EQ.num(r.pi)}</td>` +
      `<td class="num">${EQ.pct(piShare)}</td></tr>`;
  }).join("");
  document.getElementById("scaleNote").textContent =
    `Summed from each reporter's detailed records (all order types, lot sizes and size buckets), as filed; sorted by covered orders. Reporters overlap (a broker's order executed on an exchange can appear in both), so do not add rows together. ` +
    `Executed shares = shares executed at the reporter plus shares executed at other venues (Rule 605 fields 12 + 13); ` +
    `PI share of executed = price-improved shares over that total.`;
})();
