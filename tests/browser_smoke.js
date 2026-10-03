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
    const summary = await (await fetch(base + 'data/summary.json')).json();
    const expectRows = (size, sp, type) =>
      summary.rows.filter(r => r[1] === sp && r[2] === size && r[3] === type).length;
    let response = await page.goto(base, { waitUntil: 'networkidle' });
    assert(response.ok(), `index returned ${response.status()}`);
    const nDefault = expectRows('1000', 'N', 'M');
    assert(nDefault > 50, `expected a populated default view, dataset gives ${nDefault}`);
    await page.waitForFunction((n) => document.querySelectorAll('#cmpTable tbody tr').length === n, nDefault);
    const headers = await page.$$eval('#cmpTable thead th', ths => ths.map(t => t.textContent.trim()));
    assert(headers.some(h => h.startsWith('Effective spread')), 'comparison header missing');
    const firstRow = await page.$eval('#cmpTable tbody tr', tr => tr.textContent);
    assert(firstRow.includes('Cboe BZX'), 'venue row missing');

    // effective spread cell should be populated for the default selection
    const effSpread = await page.$eval('#cmpTable tbody tr td:nth-child(5)', td => td.textContent.trim());
    assert(/%$/.test(effSpread), `expected a percent, got "${effSpread}"`);

    // click-to-sort: effective spread descending, then ascending; missing values stay last
    const effValues = async () => page.$$eval('#cmpTable tbody tr td:nth-child(5)', tds =>
      tds.map(td => td.textContent.replace('⚠', '').trim()).map(t => /%$/.test(t) ? parseFloat(t) : null));
    await page.click('#cmpTable thead th[data-col="4"]');
    await page.waitForFunction(() => document.querySelector('#cmpTable thead th[data-col="4"]').textContent.includes('▼'));
    let vals = (await effValues()).filter(v => v !== null);
    assert(vals.length > 20, 'sorted column should have values');
    for (let i = 1; i < vals.length; i++) assert(vals[i - 1] >= vals[i], `not descending at ${i}: ${vals[i - 1]} < ${vals[i]}`);
    await page.click('#cmpTable thead th[data-col="4"]');
    await page.waitForFunction(() => document.querySelector('#cmpTable thead th[data-col="4"]').textContent.includes('▲'));
    vals = (await effValues()).filter(v => v !== null);
    for (let i = 1; i < vals.length; i++) assert(vals[i - 1] <= vals[i], `not ascending at ${i}`);

    // reporter-type filter and name filter
    const nExchange = meta.venues.filter(v => v.kind === 'exchange').length;
    await page.selectOption('#kindSel', 'exchange');
    await page.waitForFunction(() => document.getElementById('cmpNote').textContent.includes('published a row'));
    const exRows = await page.$$eval('#cmpTable tbody tr', trs => trs.length);
    assert(exRows >= 5 && exRows <= nExchange, `exchange filter gave ${exRows} rows of ${nExchange}`);
    await page.selectOption('#kindSel', '');
    await page.fill('#nameFilter', 'robinhood');
    await page.waitForFunction(() => document.querySelectorAll('#cmpTable tbody tr').length === 2);
    assert((await page.$$eval('#cmpTable tbody tr', trs => trs.map(tr => tr.textContent).join(' '))).toLowerCase().includes('robinhood'),
      'name filter should find the Robinhood reporters');
    await page.fill('#nameFilter', '');

    // switch size bucket -> table must re-render with the size in the note
    await page.selectOption('#sizeSel', '5000');
    await page.waitForFunction(() => document.getElementById('cmpNote').textContent.includes('$5,000'));
    // switch universe
    await page.selectOption('#sp500Sel', 'Y');
    await page.waitForFunction(() => document.getElementById('cmpNote').textContent.includes('S&P 500 stocks'));
    await page.waitForFunction((n) => document.querySelectorAll('#cmpTable tbody tr').length === n,
      expectRows('5000', 'Y', 'M'));

    // scale table populated (one row per reporter, nil reports included)
    await page.waitForFunction((n) => document.querySelectorAll('#scaleTable tbody tr').length === n, nVenues);

    // ---- symbol explorer (hashed shards) ----
    response = await page.goto(base + 'symbol.html?s=AAL', { waitUntil: 'networkidle' });
    assert(response.ok(), `symbol page returned ${response.status()}`);
    await page.waitForFunction(() => document.querySelectorAll('#symTable tbody tr').length >= 1);
    await page.waitForFunction(() => document.getElementById('symTitle').textContent.includes('AAL'));
    const symRows = await page.$$eval('#symTable tbody tr', trs => trs.map(tr => tr.textContent));
    assert(symRows.length >= 20 && symRows.length <= nVenues, `AAL should list 20..${nVenues} reporters, got ${symRows.length}`);
    const batsRow = symRows.find(r => r.includes('Cboe BZX'));
    assert(batsRow && /\d/.test(batsRow), 'BATS row should have numbers');
    const counts = (await page.$$eval('#symTable tbody tr td:nth-child(2)', tds => tds.map(t => Number(t.textContent.replace(/,/g, '')))));
    for (let i = 1; i < counts.length; i++) assert(counts[i - 1] >= counts[i], 'symbol table should sort by covered orders');

    // a symbol living in a different shard resolves through the same hash
    response = await page.goto(base + 'symbol.html?s=SPY', { waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.getElementById('symTitle').textContent.includes('SPY'));
    assert((await page.$$eval('#symTable tbody tr', trs => trs.length)) >= 10, 'SPY should list many reporters');

    // search box: typing suggests symbols
    await page.fill('#symInput', 'SP');
    await page.waitForSelector('#results.open .row[data-sym]');
    const sug = await page.$$eval('#results .row[data-sym]', rows => rows.map(r => r.dataset.sym));
    assert(sug.length > 3, `expected suggestions, got ${sug.length}`);
    assert(/\d+ reporters?/.test(await page.$eval('#results .row[data-sym]', r => r.textContent)), 'suggestion should show reporter count');

    // ---- coverage page ----
    const cov = await (await fetch(base + 'data/coverage.json')).json();
    response = await page.goto(base + 'coverage.html', { waitUntil: 'networkidle' });
    assert(response.ok(), `coverage page returned ${response.status()}`);
    await page.waitForFunction(() => document.querySelectorAll('#covTable tbody tr').length >= 9);
    const badges = await page.$$eval('#covTable .badge', b => b.map(x => x.textContent));
    assert(badges.some(t => t.includes('Amended-format files live')), 'live badge missing');
    await page.waitForFunction((n) => document.querySelectorAll('#repTable tbody tr').length === n, cov.reporters.length);
    await page.selectOption('#repStatus', 'live_amended');
    await page.waitForFunction((n) => document.querySelectorAll('#repTable tbody tr').length === n,
      cov.reporters.filter(r => r.status === 'live_amended').length);
    await page.selectOption('#repStatus', '');
    await page.fill('#repFind', 'robinhood');
    await page.waitForFunction(() => document.querySelectorAll('#repTable tbody tr').length >= 1 &&
      document.querySelectorAll('#repTable tbody tr').length <= 3);
    assert((await page.$$eval('#repTable tbody tr', trs => trs.map(tr => tr.textContent).join(' '))).includes('HOOD'), 'ledger should show Robinhood code');

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
