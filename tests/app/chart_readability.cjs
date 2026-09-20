/* Actual ECharts geometry and product interactions, with synthetic API fixtures. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
    const pageErrors = [], writes = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const columns = ['bank_million', 'sector_million', 'ratio_percent'];
    const labels = { bank_million: 'Banka: Toplam aktif', sector_million: 'Sektör: Toplam aktif', ratio_percent: 'Büyüklük oranı' };
    const sourceUrl = 'https://www.bddk.org.tr/BultenAylik';
    const scopeMessage = 'Oran, seçilen pay ve paydanın sayısal karşılaştırmasıdır. Kaynak kapsamlarını inceleyin; resmî sektör veya pazar payı olduğu varsayılmaz.';
    const zeroMessage = 'Paydası sıfır olan oran veya değişim hesaplanmadı.';
    const fixture = {
      status: 'ok', complete: true, analysis_id: 'analysis_readability', chart_id: 'chart_readability',
      title: 'Tutar ve oran karşılaştırması', subtitle: '2026-01 → 2026-03', row_count: 3,
      periods: ['2026-01', '2026-02', '2026-03'],
      spec: { kind: 'bar', layout: 'panels', normalize: 'none', orientation: 'vertical', columns },
      series: [
        { column: columns[0], label: 'source_financial_facts:amount', unit: 'milyon TL', raw_unit: 'milyon TL', values: [5, 10, null], raw_values: [5, 10, null], summary: { first: 5, last: 10, first_period: '2026-01', last_period: '2026-02', missing_count: 1 } },
        { column: columns[1], label: 'sector_million', unit: 'milyon TL', raw_unit: 'milyon TL', values: [50, 100, 0], raw_values: [50, 100, 0], summary: { first: 50, last: 0, first_period: '2026-01', last_period: '2026-03' } },
        { column: columns[2], label: 'ratio_percent', unit: '%', raw_unit: '%', values: [10, 10, null], raw_values: [10, 10, null], summary: { first: 10, last: 10, first_period: '2026-01', last_period: '2026-02', missing_count: 1 } },
      ],
      sources: ['SESSION_DATASET', 'source_financial_facts:amount'],
      warnings: ['source_financial_facts:amount: kaynak açıklaması', 'SESSION_DATASET'],
      presentation: {
        labels,
        sources: [
          { label: 'Banka finansal raporu', page: 11 },
          { label: 'BDDK Aylık Bülten', url: sourceUrl },
          { label: 'Güvensiz bağlantı denemesi', url: 'javascript:window.unsafeSource=true' },
        ],
        warnings: [
          { code: 'cross_scope_comparison', level: 'warning', message: scopeMessage, columns: [columns[2]] },
          { code: 'zero_denominator', level: 'warning', message: zeroMessage, columns: [columns[2]] },
          { code: 'unit_groups_split', level: 'info', message: 'Tutar panelleri ortak ölçekte, yüzde paneli ayrı ölçekte gösterilir.' },
        ],
        unit_groups: [{ id: 'million_try', unit: 'milyon TL', columns: columns.slice(0, 2) }, { id: 'percent', unit: '%', columns: [columns[2]] }],
      },
      recommendations: [],
    };
    let chart = structuredClone(fixture);
    const analysis = {
      analysis_id: 'analysis_readability', row_count: 3, columns: ['period', ...columns],
      rows: fixture.periods.map((period, index) => ({ period, ...Object.fromEntries(fixture.series.map(series => [series.column, series.raw_values[index]])) })),
      presentation: { columns: ['period', ...columns], labels: { period: 'Dönem', ...labels } },
      schema: { bank_million: { unit: 'TRY', scale: 1000000 }, sector_million: { unit: 'TRY', scale: 1000000 }, ratio_percent: { unit: 'percent', scale: 1 } },
      plan: { start: '2026-01', end: '2026-03', frequency: 'monthly', columns: [], operations: [] }, sources: {},
      warnings: [{ code: 'cross_scope_comparison', detail: 'different reporting populations' }, { code: 'zero_denominator', detail: 'zero denominator' }],
    };
    const workspace = { workspace_id: 'workspace_readability', name: 'Synthetic chart readability', profile: 'finance', version: 1,
      analysis_head: analysis.analysis_id, runs: [{ message: 'Tutarları ve oranı karşılaştır.', result: { status: 'completed', message: 'Sentetik karşılaştırma hazır.', analysis_id: analysis.analysis_id } }] };
    await page.route('http://chart-readability.test/**', async route => {
      const request = route.request(), url = new URL(request.url());
      let value;
      if (request.method() !== 'GET') writes.push({ method: request.method(), path: url.pathname, body: request.postDataJSON() });
      if (url.pathname === '/api/status') value = { provider_ready: true };
      else if (url.pathname === '/api/workspaces') value = { workspaces: [workspace] };
      else if (url.pathname === '/api/workspaces/workspace_readability') value = workspace;
      else if (url.pathname.endsWith('/chart')) {
        if (request.method() === 'POST') chart = { ...chart, chart_id: 'chart_revision_' + writes.length, spec: { ...chart.spec, ...request.postDataJSON() } };
        value = chart;
      } else if (url.pathname.includes('/analyses/')) value = analysis;
      if (value) return route.fulfill({ json: value });
      const local = url.pathname === '/' ? 'app/static/index.html' : 'app' + url.pathname;
      if (fs.existsSync(local)) return route.fulfill({ body: fs.readFileSync(local), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(local)] || 'application/octet-stream' });
      return route.fulfill({ status: 404, body: 'Not found' });
    });
    await page.goto('http://chart-readability.test/?workspace=workspace_readability');
    await page.waitForSelector('#table-container tbody tr');
    await page.locator('.tab[data-tab="chart"]').click();
    await page.waitForFunction(() => window.echarts?.getInstanceByDom(document.querySelector('#chart'))?.getOption().series?.length === 3);
    assert.deepEqual(writes, [], 'Loading a saved chart must not replace its selected view');

    const geometry = async (horizontal) => page.evaluate(horizontal => {
      const chart = echarts.getInstanceByDom(document.querySelector('#chart'));
      const model = chart.getModel(), option = chart.getOption();
      const axis = horizontal ? 'xAxis' : 'yAxis';
      return {
        kinds: option.series.map(series => series.type),
        values: option.series.map(series => series.data.map(item => item.value)),
        axes: option.series.map(series => model.getComponent(axis, series[axis + 'Index'] || 0).axis.scale.getExtent()),
        rectangles: option.series.map((_, index) => {
          const item = model.getSeriesByIndex(index).getData().getItemLayout(0);
          return { width: Math.abs(item.width), height: Math.abs(item.height) };
        }),
      };
    }, horizontal);
    const assertGeometry = (actual, horizontal) => {
      assert.deepEqual(actual.kinds, ['bar', 'bar', 'bar']);
      assert.deepEqual(actual.values, [[5, 10, null], [50, 100, 0], [10, 10, null]], 'The saved numeric cells and nulls must remain unchanged');
      assert.deepEqual(actual.axes[0], actual.axes[1], 'Compatible million-TL panels must share the actual plotted range');
      assert.notDeepEqual(actual.axes[0], actual.axes[2], 'A percent panel must keep a distinct numeric range');
      const dimension = horizontal ? 'width' : 'height';
      const ratio = actual.rectangles[0][dimension] / actual.rectangles[1][dimension];
      assert.ok(Math.abs(ratio - 0.1) < 0.002, 'The two rendered bar lengths must reflect 5/50, not fill independent axes: ' + JSON.stringify(actual));
    };
    assertGeometry(await geometry(false), false);
    const readable = await page.locator('#chart-body').innerText();
    assert.ok(readable.includes(labels.bank_million) && readable.includes(labels.sector_million));
    assert.equal(/SESSION_DATASET|source_financial_facts|\[object Object\]/.test(readable), false);
    assert.equal(await page.locator('#chart-sources a[href="' + sourceUrl + '"]').count(), 1);
    assert.equal(await page.locator('#chart-sources a[href^="javascript:"]').count(), 0);
    assert.equal(await page.evaluate(() => window.unsafeSource), undefined);
    const visibleWarnings = async () => {
      const rows = [];
      for (const item of await page.locator('#warnings .warning, #chart-warnings .warning').all())
        if (await item.isVisible()) rows.push(await item.innerText());
      return rows;
    };
    let warningText = await visibleWarnings();
    assert.equal(warningText.filter(text => /resmî sektör veya pazar payı olduğu varsayılmaz/.test(text)).length, 1, 'Scope must be disclosed once across the two warning areas');
    assert.equal(warningText.filter(text => /sıfır/.test(text)).length, 1, 'A zero-denominator warning must remain visible exactly once');

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#chart-svg').click();
    const download = await downloadPromise;
    const stream = await download.createReadStream();
    let svg = '';
    for await (const chunk of stream) svg += chunk.toString();
    assert.match(svg, /Banka finansal raporu/);
    assert.match(svg, /BDDK Aylık Bülten/);
    assert.equal(/SESSION_DATASET|source_financial_facts|\[object Object\]/.test(svg), false, 'Export source text must use readable source names');
    const exportedText = await page.evaluate(svg => {
      const document = new DOMParser().parseFromString(svg, 'image/svg+xml');
      const parts = [];
      for (const text of document.querySelectorAll('text')) {
        const walker = document.createTreeWalker(text, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) parts.push(walker.currentNode.textContent);
      }
      return parts.join(' ').replace(/\s+/g, ' ').trim();
    }, svg);
    assert.match(exportedText, /resmî sektör veya pazar payı olduğu varsayılmaz/, 'The shared SVG must retain the scope caveat, including when it wraps across text nodes');

    await page.locator('#chart-settings-toggle').click();
    await page.locator('#chart-orientation').selectOption('horizontal');
    await page.locator('#chart-apply').click();
    await page.waitForFunction(() => {
      const chart = echarts.getInstanceByDom(document.querySelector('#chart'));
      return chart?.getOption().xAxis[0].type === 'value';
    });
    assert.equal(writes.length, 1);
    assert.equal(writes[0].body.orientation, 'horizontal');
    assert.deepEqual(writes[0].body.columns, columns);
    assertGeometry(await geometry(true), true);

    // Old immutable chart records can contain structured warning objects even
    // without the new presentation envelope. They must not display as objects.
    chart = structuredClone(fixture);
    delete chart.presentation;
    chart.sources = ['Sentetik kaynak'];
    chart.series.forEach(series => { series.label = labels[series.column]; });
    chart.warnings = [{ code: 'zero_denominator', detail: 'Old backend zero denominator warning' }, { code: 'cross_scope_comparison', detail: 'Different reporting populations' }, { code: 'unrecognized_technical_note', detail: 'OPAQUE_LEGACY_WARNING' }];
    await page.evaluate(() => window.AnalysisCharts.load('/api/workspaces/workspace_readability', 'analysis_readability', true));
    assert.equal(/\[object Object\]|OPAQUE_LEGACY_WARNING/.test(await page.locator('#chart-body').innerText()), false);
    warningText = await visibleWarnings();
    assert.equal(warningText.filter(text => /sıfır/.test(text)).length, 1);

    // Both saved single-period choices remain intact. The UI explains their
    // limit and only changes them after an explicit switch-button click.
    for (const [kind, label, otherLabel] of [['area', 'Alan', 'Çizgi'], ['line', 'Çizgi', 'Alan']]) {
      chart = structuredClone(fixture);
      chart.chart_id = 'chart_single_' + kind;
      chart.periods = ['2026-01']; chart.row_count = 1;
      chart.spec.kind = kind;
      chart.series.forEach(series => { series.values = series.values.slice(0, 1); series.raw_values = series.raw_values.slice(0, 1); });
      const writesBeforeLoad = writes.length;
      await page.evaluate(() => window.AnalysisCharts.load('/api/workspaces/workspace_readability', 'analysis_readability', true));
      assert.equal(writes.length, writesBeforeLoad);
      const current = page.locator('#chart-kinds button').filter({ hasText: new RegExp('^' + label + '$') });
      const other = page.locator('#chart-kinds button').filter({ hasText: new RegExp('^' + otherLabel + '$') });
      assert.equal(await current.getAttribute('aria-pressed'), 'true');
      assert.equal(await other.isDisabled(), true);
      assert.equal(await current.isDisabled(), false, 'The current saved choice remains identifiable and selected');
      const switchButton = page.locator('#chart-use-bars');
      assert.equal(await switchButton.count(), 1);
      assert.ok(/tek (?:dönem|gözlem)|bir (?:dönem|gözlem)/i.test(await page.locator('#chart-guidance').innerText()));
      await switchButton.click();
      await page.waitForFunction(() => echarts.getInstanceByDom(document.querySelector('#chart'))?.getOption().series.every(series => series.type === 'bar'));
      assert.equal(writes.length, writesBeforeLoad + 1);
      assert.equal(writes.at(-1).body.kind, 'bar');
      assert.deepEqual(writes.at(-1).body.columns, columns);
      assert.equal(await page.locator('#chart-kinds button').filter({ hasText: /^Çizgi$/ }).isDisabled(), true);
      assert.equal(await page.locator('#chart-kinds button').filter({ hasText: /^Alan$/ }).isDisabled(), true);
      assert.equal(await page.locator('#chart-kinds button').filter({ hasText: /^Çubuk$/ }).getAttribute('aria-pressed'), 'true');
      assert.deepEqual(await page.evaluate(() => echarts.getInstanceByDom(document.querySelector('#chart')).getOption().series.map(series => series.data.map(item => item.value))), [[5], [50], [10]]);
    }
    assert.deepEqual(pageErrors, []);
    assert.ok(writes.every(request => request.method === 'POST' && request.path.endsWith('/chart')), 'Only explicitly selected chart revisions may be written');
    process.stdout.write('Chart readability: functional Chromium assertions passed\n');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
