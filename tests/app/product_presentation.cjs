/* Exercise the real product JS and DOM with synthetic, explicitly controlled API data. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    const pageErrors = [], proofRequests = [], writes = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const giant = 'PRIVATE_TECHNICAL_SENTINEL_' + 'x'.repeat(200000);
    const inspection = { status: 'ok', source_id: 'source_fixture', filename: 'Fixture_report.pdf', text: giant, tables: [] };
    const result = { status: 'completed', analysis_id: 'analysis_fixture', message: 'Verbose original alias\\_raw',
      display_message: '**Kaynak karşılaştırması**\n\nÖlçekler eşitlendi. Kalem\\_adı ve \\*düz yazı\\*. [Rapor \\[kaynak\\]](https://example.test/report)\n\n<script>window.injected=true</script>\n\n[unsafe](javascript:alert(1))',
      tool_results: [1, 2, 3].map(index => ({ tool: 'inspect_source', result: { ...inspection, processed_pages: [index] } })) };
    result.tool_results.push({ tool: 'ingest_source_table', result: { status: 'ok', import_status: 'published', source_id: 'source_fixture', dataset_id: 'dataset_fixture', row_count: 1,
      available_series: [{ observed_periods: ['2026-03-31'], status: 'ready' }] } });
    const workspace = { workspace_id: 'workspace_fixture', name: 'Synthetic presentation test', profile: 'finance', version: 1,
      analysis_head: 'analysis_fixture', runs: [{ message: 'Synthetic source comparison', result }] };
    const analysis = { analysis_id: 'analysis_fixture', row_count: 1, columns: ['period', 'alias_raw', 'alias_scaled', 'share_pct'],
      rows: [{ period: '2026-03', alias_raw: 1000, alias_scaled: 1, share_pct: 10 }],
      presentation: { columns: ['period', 'alias_scaled', 'share_pct'], labels: { period: 'Dönem', alias_raw: 'Kaynak tutarı', alias_scaled: 'Kaynak tutarı', share_pct: 'Büyüklük oranı' } },
      schema: { alias_raw: { unit: 'TRY', scale: 1000 }, alias_scaled: { unit: 'TRY', scale: 1000000 }, share_pct: { unit: 'percent', scale: 1 } },
      plan: { start: '2026-03', end: '2026-03', frequency: 'monthly', columns: [{ name: 'alias_raw', metric_id: 'fixture', alignment: 'period_end' }], operations: [{ op: 'scale', column: 'alias_raw', output: 'alias_scaled', target_scale: 1000000 }] },
      sources: { alias_raw: { title: 'source_financial_facts:amount', source_system: 'SESSION_DATASET', unit: 'TRY', scale: 1000 } },
      warnings: [{ code: 'cross_scope_comparison', detail: 'Different reporting populations' }, { code: 'cross_scope_comparison' },
        { code: 'exact_event_period_end', detail: 'Actual event dates match calendar endpoints exactly.' }, { code: 'opaque_code', detail: 'OPAQUE_TECHNICAL_WARNING' }] };
    const chart = { status: 'ok', complete: true, analysis_id: 'analysis_fixture', chart_id: 'chart_fixture',
      title: 'Synthetic source comparison', subtitle: '2026-03', row_count: 1, periods: ['2026-03'], sources: ['Synthetic source'],
      spec: { kind: 'bar', layout: 'panels', normalize: 'none', orientation: 'vertical', columns: ['alias_scaled', 'share_pct'] },
      series: [{ column: 'alias_scaled', label: 'alias_scaled', unit: 'milyon TL', raw_unit: 'milyon TL', values: [1], raw_values: [1], summary: { first: 1, last: 1, change: 0, change_percent: 0, first_period: '2026-03', last_period: '2026-03' } },
        { column: 'share_pct', label: 'share_pct', unit: '%', raw_unit: '%', values: [10], raw_values: [10], summary: { last: 10, last_period: '2026-03' } }],
      warnings: [], recommendations: [{ label: 'İlk dönem 100', prompt: 'Endeks 100 karşılaştırması yap' }, { label: 'İlişkiyi incele', prompt: 'Korelasyon hesapla' }, { label: 'Kaynağı incele', prompt: 'Kaynak kaydını incele' }] };
    await page.route('http://presentation.test/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (request.method() !== 'GET') writes.push({ method: request.method(), path: url.pathname });
      let value;
      if (url.pathname === '/api/status') value = { provider_ready: true };
      else if (url.pathname === '/api/workspaces') value = { workspaces: [workspace] };
      else if (url.pathname.endsWith('/chart')) value = chart;
      else if (url.pathname.endsWith('/explain')) {
        proofRequests.push(Object.fromEntries(url.searchParams));
        value = { value: analysis.rows[0][url.searchParams.get('column')], source_references_complete: true, source_files_verified: true, lineage: { immutable_proof: giant } };
      } else if (url.pathname.includes('/analyses/')) value = analysis;
      else if (url.pathname === '/api/workspaces/workspace_fixture') value = workspace;
      if (value) return route.fulfill({ json: value });
      const local = url.pathname === '/' ? 'app/static/index.html' : 'app' + url.pathname;
      if (fs.existsSync(local)) return route.fulfill({ body: fs.readFileSync(local), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(local)] || 'application/octet-stream' });
      return route.fulfill({ status: 404, body: 'Not found' });
    });
    await page.goto('http://presentation.test/?workspace=workspace_fixture');
    await page.waitForSelector('#table-container tbody tr');
    await page.waitForFunction(() => document.querySelector('#chart-kpis').children.length === 2);
    assert.equal(await page.locator('#table-container th').count(), 3);
    assert.deepEqual(await page.locator('#table-container tbody td').allTextContents(), ['Mart 2026', '1', '10']);
    assert.equal(await page.locator('.source-summary').count(), 1, 'Repeated inspection must produce one source card');
    assert.equal(await page.locator('#extra-result pre').count(), 0, 'Closed details must not eagerly create JSON');
    assert.equal(await page.locator('#analysis-plan').textContent(), '');
    assert.equal((await page.locator('body').textContent()).includes('PRIVATE_TECHNICAL_SENTINEL'), false);
    assert.equal((await page.locator('#messages').textContent()).includes('Verbose original'), false);
    assert.match(await page.locator('#messages').textContent(), /Kalem_adı ve \*düz yazı\*/);
    assert.equal(await page.locator('#messages script').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.equal(await page.locator('#messages a[href^="javascript:"]').count(), 0);
    assert.equal(await page.locator('#messages a').textContent(), 'Rapor [kaynak]');
    assert.equal(await page.locator('#warnings .warning').count(), 1, 'One material scope disclosure covers generic overlap; successful alignment belongs in the method');
    assert.match(await page.locator('#analysis-method').textContent(), /son günüyle eşleştirildi/);
    assert.match(await page.locator('#warnings').textContent(), /resmi sektör\/pazar payı değildir/);
    assert.equal((await page.locator('#warnings').textContent()).includes('OPAQUE_TECHNICAL_WARNING'), false);
    assert.equal((await page.locator('#chart-kpis').textContent()).includes('İlk geçerli gözleme göre'), false);
    assert.equal((await page.locator('#chart-legend').textContent()).includes('alias_scaled'), false);
    assert.deepEqual(await page.locator('#chart-recommendations .chart-suggestion > span:first-child').allTextContents(), ['Kaynağı incele']);
    assert.equal(await page.locator('#chart-normalize').isDisabled(), true);
    await page.locator('#table-container .value-cell').first().click();
    await page.waitForFunction(() => document.querySelector('#evidence-content').textContent.includes('Kaynak dosya baytları doğrulandı'));
    assert.equal(proofRequests[0].column, 'alias_scaled', 'Human headers must not change the proof key');
    assert.equal(proofRequests[0].period, '2026-03');
    assert.equal(await page.locator('#evidence-content pre').count(), 0);
    assert.match(await page.locator('#evidence-title').textContent(), /Kaynak tutarı/);
    await page.locator('#evidence-dialog button[data-close]').click();
    await page.locator('#toggle-columns').click();
    assert.equal(await page.locator('#table-container th').count(), 4);
    assert.deepEqual(await page.locator('#table-container tbody td').allTextContents(), ['Mart 2026', '1.000', '1', '10']);
    await page.locator('#toggle-columns').click();
    assert.equal(await page.locator('#table-container th').count(), 3);
    await page.locator('.tool-ledger > summary').click();
    assert.equal(await page.locator('.tool-ledger pre').count(), 0, 'Opening the ledger still must not create every tool payload');
    await page.locator('.tool-ledger > details').filter({ hasText: '1. inspect_source' }).locator('summary').click();
    await page.waitForSelector('.tool-ledger pre');
    assert.match(await page.locator('.tool-ledger pre').textContent(), /PRIVATE_TECHNICAL_SENTINEL/);
    assert.equal(await page.locator('.tool-ledger pre').count(), 1);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(writes, [], 'Presentation must not mutate analyses or saved chart selections');
    process.stdout.write('Product presentation: functional browser assertions passed\n');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
