// Venue comparison page.
(async () => {
  const [meta, summary, rollup] = await Promise.all([
    EQ.loadJSON("data/meta.json"),
    EQ.loadJSON("data/summary.json"),
    EQ.loadJSON("data/rollup.json"),
  ]);

  const venues = meta.venues; // {idx, ric, name, short, ...}
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

  function renderTable() {
    const size = sizeSel.value;
    const sp500 = document.getElementById("sp500Sel").value;
    const type = document.getElementById("typeSel").value;

    const thead = document.querySelector("#cmpTable thead");
    const tbody = document.querySelector("#cmpTable tbody");
    thead.innerHTML = "<tr><th>Venue</th>" +
      COLUMNS.map(c => `<th class="num">${EQ.esc(c[1])}</th>`).join("") + "</tr>";

    const rows = [];
    for (const v of venues) {
      const row = summary.rows.find(r =>
        r[0] === v.idx && r[1] === sp500 && r[2] === size && r[3] === type);
      rows.push({ v, row });
    }
    tbody.innerHTML = rows.map(({ v, row }) => {
      let cells = "";
      if (!row) {
        cells = `<td colspan="${COLUMNS.length}" class="muted">not reported for this combination</td>`;
      } else {
        const flags = row[row.length - 1];
        cells = COLUMNS.map((c, i) => {
          const val = row[ROW_STAT_OFFSET + statIdx[c[0]]];
          let txt = c[2](val);
          if (i === 0 && flags) txt += ' <span class="flag" title="' +
            EQ.esc(EQ.flagText(flags, meta.summary_flag_bits)) + '">⚠</span>';
          return `<td class="num">${txt}</td>`;
        }).join("");
      }
      return `<tr><td><a href="${EQ.esc(v.link_site)}" rel="noopener">${EQ.esc(v.short)}</a></td>${cells}</tr>`;
    }).join("");

    document.getElementById("cmpNote").innerHTML =
      `August 2026 · ${EQ.esc(sizeLabels[size] || size)} · ` +
      (sp500 === "N" ? "non-S&P 500 stocks" : "S&P 500 stocks") + " · " +
      (type === "M" ? "market orders" : "marketable limit orders") +
      `. Values as published by each venue; one documented normalization applied to the EFQ column ` +
      `(see <a href="method.html">Method</a>). ⚠ = sanity flag on the published row.`;
  }

  ["sizeSel", "sp500Sel", "typeSel"].forEach(id =>
    document.getElementById(id).addEventListener("change", renderTable));
  renderTable();

  // ---- Scale table (from detailed records) ----
  const scaleHead = document.querySelector("#scaleTable thead");
  const scaleBody = document.querySelector("#scaleTable tbody");
  scaleHead.innerHTML = "<tr><th>Venue</th><th class='num'>Covered orders</th>" +
    "<th class='num'>Executed shares</th><th class='num'>Price-improved shares</th>" +
    "<th class='num'>PI share of executed</th></tr>";
  scaleBody.innerHTML = venues.map(v => {
    const r = rollup[v.ric];
    if (!r) return `<tr><td>${EQ.esc(v.short)}</td><td colspan="4" class="muted">no data</td></tr>`;
    const piShare = r.exctd > 0 ? r.pi / r.exctd : null;
    return `<tr><td>${EQ.esc(v.short)}</td>` +
      `<td class="num">${EQ.num(r.cnt)}</td>` +
      `<td class="num">${EQ.num(r.exctd)}</td>` +
      `<td class="num">${EQ.num(r.pi)}</td>` +
      `<td class="num">${EQ.pct(piShare)}</td></tr>`;
  }).join("");
  document.getElementById("scaleNote").textContent =
    `Summed from the venues' detailed records (all order types, lot sizes and size buckets), as filed. ` +
    `PI share of executed = price-improved shares over executed shares.`;
})();
