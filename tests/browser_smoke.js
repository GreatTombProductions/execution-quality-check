'use strict';

// browser_smoke.js — receiver smoke for Execution Quality Check.
// Run via tests/run_browser_smoke.py (stages a static copy and serves it).

const assert = require('assert');
const { chromium } = require('playwright');

(async () => {
  const base = process.env.SMOKE_BASE || 'http://127.0.0.1:8765/';
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    // Venue count is data, not a constant: read it from the staged dataset so this
    // smoke stays correct as publishers come online through the filing window.
    const meta = await (await fetch(base + 'data/meta.json')).json();
    const nVenues = meta.venues.length;

    const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(e.message));

    // ---- comparison page ----
    let response = await page.goto(base, { waitUntil: 'networkidle' });
    assert(response.ok(), `index returned ${response.status()}`);
    await page.waitForFunction((n) => document.querySelectorAll('#cmpTable tbody tr').length === n, nVenues);
    const headers = await page.$$eval('#cmpTable thead th', ths => ths.map(t => t.textContent.trim()));
    assert(headers.includes('Effective spread'), 'comparison header missing');
    const firstRow = await page.$eval('#cmpTable tbody tr', tr => tr.textContent);
    assert(firstRow.includes('Cboe BZX'), 'venue row missing');

    // effective spread cell should be populated for the default selection
    const effSpread = await page.$eval('#cmpTable tbody tr td:nth-child(5)', td => td.textContent.trim());
    assert(/%$/.test(effSpread), `expected a percent, got "${effSpread}"`);

    // switch size bucket -> table must re-render with the size in the note
    await page.selectOption('#sizeSel', '5000');
    await page.waitForFunction(() => document.getElementById('cmpNote').textContent.includes('$5,000'));
    // switch universe
    await page.selectOption('#sp500Sel', 'Y');
    await page.waitForFunction(() => document.getElementById('cmpNote').textContent.includes('S&P 500 stocks'));

    // scale table populated
    await page.waitForFunction((n) => document.querySelectorAll('#scaleTable tbody tr').length === n, nVenues);

    // ---- symbol explorer ----
    response = await page.goto(base + 'symbol.html?s=AAL', { waitUntil: 'networkidle' });
    assert(response.ok(), `symbol page returned ${response.status()}`);
    await page.waitForFunction(() => document.querySelectorAll('#symTable tbody tr').length >= 1);
    await page.waitForFunction(() => document.getElementById('symTitle').textContent.includes('AAL'));
    const symRows = await page.$$eval('#symTable tbody tr', trs => trs.map(tr => tr.textContent));
    assert(symRows.length === nVenues, `expected ${nVenues} venue rows, got ${symRows.length}`);
    const batsRow = symRows.find(r => r.includes('Cboe BZX'));
    assert(batsRow && /\d/.test(batsRow), 'BATS row should have numbers');

    // search box: typing suggests symbols
    await page.fill('#symInput', 'SP');
    await page.waitForSelector('#results.open .row[data-sym]');
    const sug = await page.$$eval('#results .row[data-sym]', rows => rows.map(r => r.dataset.sym));
    assert(sug.length > 3, `expected suggestions, got ${sug.length}`);

    // ---- coverage page ----
    response = await page.goto(base + 'coverage.html', { waitUntil: 'networkidle' });
    assert(response.ok(), `coverage page returned ${response.status()}`);
    await page.waitForFunction(() => document.querySelectorAll('#covTable tbody tr').length >= 8);
    const badges = await page.$$eval('#covTable .badge', b => b.map(x => x.textContent));
    assert(badges.some(t => t.includes('Amended-format files live')), 'live badge missing');

    // ---- method page ----
    response = await page.goto(base + 'method.html', { waitUntil: 'networkidle' });
    assert(response.ok(), `method page returned ${response.status()}`);
    await page.waitForFunction((n) => document.querySelectorAll('#receiptsTable tbody tr').length === n, nVenues * 2);
    await page.waitForFunction(() => document.querySelectorAll('#checksTable tbody tr').length >= 4);
    const norm = await page.$eval('#normList', el => el.textContent);
    assert(norm.includes('EFQ'), 'normalization note missing');

    assert(errors.length === 0, `console errors: ${errors.join(' | ')}`);
    console.log('[smoke] all checks passed');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
