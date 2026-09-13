/* Actual Chromium and shipped application code, with explicit synthetic HTTP fixtures. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1050 } });
    const errors = [], requests = [], held = new Map();
    page.on('pageerror', error => errors.push(error.message));
    const makeRun = (workspace, name, status = 'completed') => ({
      workspace_id: workspace, run_id: 'run_' + name, conversation_id: 'conversation_' + workspace,
      status, message: name === 'alpha' ? 'İşletmenin Mart 2026 nakit tutarını sektörle karşılaştır.' : name + ' sorusu',
      result: { status, message: name + ' cevabı hazır.', analysis_id: null, tool_results: [] },
    });
    const alpha = makeRun('workspace_alpha', 'alpha'), beta = makeRun('workspace_beta', 'beta');
    const workspaces = {
      workspace_alpha: { workspace_id: 'workspace_alpha', name: 'Alpha source comparison', version: 2, profile: 'finance', runs: [alpha] },
      workspace_beta: { workspace_id: 'workspace_beta', name: 'Beta research', version: 1, profile: 'finance', runs: [beta] },
    };
    const records = { [alpha.run_id]: alpha, [beta.run_id]: beta };
    const modes = new Map([[alpha.run_id, 'pending'], [beta.run_id, 'ready']]);
    const items = (run) => [
      { id: run.run_id + '_prior', label: 'Önceki bilanço dönemi', prompt: run.run_id + ': Mart 2026 nakit tutarını aynı rapordaki önceki dönemle karşılaştırır mısın?', reason: 'Son yanıtta tek dönemin nakit tutarı karşılaştırıldı.', requires_new_data: false },
      { id: run.run_id + '_scope', label: 'Karşılaştırılabilir kapsam', prompt: run.run_id + ': Konsolide grup ile sektör arasındaki kapsam farkını rapor notlarından açıklar mısın?', reason: 'Açıklanan oran eşdeğer kapsamları temsil etmiyor.', requires_new_data: false },
      { id: run.run_id + '_new', label: 'Ek kaynak', prompt: run.run_id + ': Diğer şirketin aynı dönem bilançosunu bulup karşılaştırmayı genişletir misin? <img src=x onerror="window.followupInjected=true">', reason: 'Bu şirket henüz çalışma alanındaki sonuçta yer almıyor.', requires_new_data: true },
    ];
    const packet = (run, status = 'ready') => ({ status, workspace_id: run.workspace_id, run_id: run.run_id,
      conversation_id: run.conversation_id, analysis_id: run.result.analysis_id, context_digest: 'digest_' + run.run_id,
      items: status === 'ready' ? items(run) : [] });
    const chart = { status: 'ok', complete: true, analysis_id: 'analysis_shared', chart_id: 'chart_fixture', title: 'Synthetic shared chart',
      subtitle: '2026-03', row_count: 1, periods: ['2026-03'], sources: [], warnings: [],
      spec: { kind: 'bar', layout: 'auto', normalize: 'none', orientation: 'vertical', columns: ['cash'] },
      series: [{ column: 'cash', label: 'Nakit', unit: 'milyon TL', values: [50], summary: { last: 50, last_period: '2026-03' } }],
      recommendations: [{ label: 'Grafik kaynağı', prompt: 'Grafiğin kaynak kaydını incele.', reason: 'Grafik görünümü önerisi.' }] };
    let submitRoute = null, nextRun = null;
    await page.route('http://127.0.0.1:8998/**', async route => {
      const request = route.request(), url = new URL(request.url());
      requests.push({ method: request.method(), path: url.pathname });
      const match = url.pathname.match(/^\/api\/workspaces\/([^/]+)\/runs\/([^/]+)\/followups$/);
      if (match) {
        const run = records[match[2]], mode = modes.get(match[2]) || 'ready';
        if (mode === 'held') { held.set(match[2], route); return; }
        if (mode === 'unavailable' || mode === 'stale') return route.fulfill({ json: packet(run, mode) });
        const value = packet(run, mode === 'pending' && request.method() === 'POST' ? 'pending' : 'ready');
        if (mode === 'wrong-owner') value.conversation_id = 'conversation_other';
        if (mode === 'derived-analysis') value.analysis_id = 'analysis_proven_from_current_run_tools';
        if (mode === 'analysis-change') {
          value.status = request.method() === 'POST' ? 'pending' : 'ready';
          value.analysis_id = request.method() === 'POST' ? 'analysis_first' : 'analysis_other';
        }
        if (mode === 'digest-change') {
          value.status = request.method() === 'POST' ? 'pending' : 'ready';
          if (request.method() === 'GET') value.context_digest = 'changed_after_generation';
        }
        return route.fulfill({ json: value });
      }
      if (url.pathname.endsWith('/runs') && request.method() === 'POST') { submitRoute = route; return; }
      let value;
      if (url.pathname === '/api/status') value = { provider_ready: true };
      else if (url.pathname === '/api/workspaces') value = { workspaces: Object.values(workspaces) };
      else if (url.pathname === '/api/jobs/job_next') value = { status: 'finished', result: nextRun.result, run: nextRun, activity: [] };
      else if (url.pathname.endsWith('/chart')) value = chart;
      else if (url.pathname.startsWith('/api/workspaces/')) value = workspaces[url.pathname.split('/')[3]];
      if (value) return route.fulfill({ json: value });
      const local = url.pathname === '/' ? 'app/static/index.html' : 'app' + url.pathname;
      if (fs.existsSync(local)) return route.fulfill({ body: fs.readFileSync(local), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(local)] || 'application/octet-stream' });
      return route.fulfill({ status: 404, body: 'Not found' });
    });
    const buttons = page.locator('#conversation-followups .followup-question');
    await page.goto('http://127.0.0.1:8998/?workspace=workspace_alpha');
    await page.waitForSelector('#conversation-followups[data-status="pending"]');
    assert.match(await page.locator('#conversation-followups').innerText(), /Sonuca göre yeni sorular/);
    assert.equal(await page.locator('#send').isEnabled(), true, 'Background suggestions cannot block the main composer');
    await page.waitForSelector('#conversation-followups[data-status="ready"]');
    assert.equal(await buttons.count(), 3);
    assert.deepEqual(await page.locator('.followup-prompt').allTextContents(), items(alpha).map(item => item.prompt));
    assert.equal(await page.locator('.followup-reason').count(), 3, 'Reasons are visible, not discarded like old chart tips');
    assert.equal(await page.locator('.followup-data-hint').count(), 1);
    assert.equal(await page.locator('#conversation-followups img').count(), 0);
    assert.equal(await page.evaluate(() => window.followupInjected), undefined);
    const requestsBeforePrefill = requests.length;
    await page.locator('#question').fill('Bir kullanıcı taslağı');
    await buttons.first().focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('#question').inputValue(), items(alpha)[0].prompt, 'Explicit choice fills the full question');
    assert.equal(await page.locator('#question').evaluate(node => node === document.activeElement), true);
    assert.equal(requests.length, requestsBeforePrefill, 'Prefill makes no network request and never submits an analysis');
    await page.evaluate(() => { state.busy = true; window.ContextualFollowups.updateBusy(); });
    assert.equal(await buttons.first().isDisabled(), true);
    await buttons.last().evaluate(node => node.click());
    assert.equal(await page.locator('#question').inputValue(), items(alpha)[0].prompt);
    await page.evaluate(() => { state.busy = false; window.ContextualFollowups.updateBusy(); });
    // A chart refresh/clear must no longer create or remove conversation suggestions.
    await page.evaluate(() => window.AnalysisCharts.load(base(), 'analysis_shared'));
    assert.equal(await buttons.count(), 3);
    assert.equal(await page.locator('#chart-recommendations button').count(), 0, 'Legacy chart tips do not appear alongside contextual questions');
    await page.evaluate(() => window.AnalysisCharts.clear());
    assert.equal(await buttons.count(), 3);

    // Real submitQuestion wiring clears suggestions immediately, then accepts
    // new run identity even when the analysis ID remains the same (null here).
    await page.locator('#question').fill('Aynı kaynak için ikinci soru');
    await page.locator('#send').click();
    await page.waitForFunction(() => state.busy === true);
    assert.equal(await buttons.count(), 0);
    nextRun = makeRun('workspace_alpha', 'alpha_second');
    nextRun.result.run_id = nextRun.run_id;
    nextRun.result.conversation_id = nextRun.conversation_id;
    records[nextRun.run_id] = nextRun;
    workspaces.workspace_alpha.runs.unshift(nextRun);
    while (!submitRoute) await new Promise(resolve => setTimeout(resolve, 10));
    await submitRoute.fulfill({ json: { job_id: 'job_next' } });
    await page.waitForSelector('#conversation-followups[data-run-id="run_alpha_second"] [data-followup-id]');
    assert.ok((await buttons.first().innerText()).includes('run_alpha_second'));
    assert.equal(await page.locator('#send').isEnabled(), true);

    // New conversation keeps the saved chart available, but no old chart
    // callback or old answer may reintroduce prior conversation questions.
    await page.locator('#new-conversation').click();
    assert.equal(await buttons.count(), 0);
    await page.evaluate(() => window.AnalysisCharts.load(base(), 'analysis_shared'));
    assert.equal(await buttons.count(), 0);
    assert.equal(await page.evaluate(() => state.conversation), null);

    // Restore cached historical questions through one explicit eligible POST.
    await page.evaluate(() => selectWorkspace('workspace_alpha'));
    await page.waitForSelector('#conversation-followups[data-run-id="run_alpha_second"] [data-followup-id]');
    modes.set(nextRun.run_id, 'held');
    await page.evaluate(() => selectWorkspace('workspace_alpha'));
    await page.waitForSelector('#conversation-followups[data-status="pending"]');
    await page.evaluate(() => selectWorkspace('workspace_beta'));
    await page.waitForSelector('#conversation-followups[data-run-id="run_beta"] [data-followup-id]');
    await held.get(nextRun.run_id).fulfill({ json: packet(nextRun) }).catch(() => {});
    await page.waitForTimeout(50);
    assert.ok((await buttons.first().innerText()).includes('run_beta'), 'A late previous-workspace response must not win');

    // A delayed response in the same workspace cannot resurrect cleared chat.
    modes.set(beta.run_id, 'held');
    await page.evaluate(() => selectWorkspace('workspace_beta'));
    await page.waitForSelector('#conversation-followups[data-status="pending"]');
    await page.locator('#new-conversation').click();
    await held.get(beta.run_id).fulfill({ json: packet(beta) }).catch(() => {});
    await page.waitForTimeout(50);
    assert.equal(await buttons.count(), 0);

    modes.set(beta.run_id, 'derived-analysis');
    await page.evaluate(() => selectWorkspace('workspace_beta'));
    await page.waitForSelector('#conversation-followups[data-status="ready"]');
    assert.equal(await buttons.count(), 3, 'An analysis proved by the endpoint is allowed when the public run omits it');
    for (const mode of ['unavailable', 'stale', 'wrong-owner', 'digest-change', 'analysis-change']) {
      modes.set(beta.run_id, mode);
      await page.evaluate(() => selectWorkspace('workspace_beta'));
      await page.waitForFunction(() => !document.querySelector('#conversation-followups'), null, { timeout: 5000 });
      assert.equal(await buttons.count(), 0, mode + ' must quietly discard suggestions');
      assert.equal(await page.locator('#notice').isVisible(), false, 'Optional generation failures do not turn into a main error');
    }
    for (const status of ['blocked', 'failed', 'needs_input']) {
      const previousCount = requests.filter(request => request.path.endsWith('/followups')).length;
      beta.status = beta.result.status = status;
      await page.evaluate(() => selectWorkspace('workspace_beta'));
      assert.equal(await buttons.count(), 0);
      assert.equal(requests.filter(request => request.path.endsWith('/followups')).length, previousCount);
    }
    beta.status = beta.result.status = 'completed';
    beta.result.message = 'Bir sonuç var.\n\nDevam için soru: Hangi dönemi seçelim?';
    const previousCount = requests.filter(request => request.path.endsWith('/followups')).length;
    await page.evaluate(() => selectWorkspace('workspace_beta'));
    assert.equal(requests.filter(request => request.path.endsWith('/followups')).length, previousCount);

    beta.result.message = 'Beta sonucu tamamlandı.'; modes.set(beta.run_id, 'ready');
    await page.evaluate(() => selectWorkspace('workspace_beta'));
    await page.waitForSelector('#conversation-followups[data-status="ready"]');
    await page.setViewportSize({ width: 390, height: 844 });
    const geometry = await page.locator('#conversation-followups').evaluate(node => ({ width: innerWidth, pageWidth: document.documentElement.scrollWidth,
      rect: node.getBoundingClientRect().toJSON(), buttons: [...node.querySelectorAll('button')].map(button => button.getBoundingClientRect().toJSON()) }));
    assert.ok(geometry.pageWidth <= 390 && geometry.rect.left >= -1 && geometry.rect.right <= 391);
    assert.ok(geometry.buttons.every(rect => rect.height >= 44 && rect.right <= 391));
    assert.equal(requests.filter(request => request.method === 'POST' && /\/runs$/.test(request.path)).length, 1, 'Only the explicit send click submits a run');
    assert.ok(requests.filter(request => request.method === 'POST').every(request => /\/followups$|\/runs$/.test(request.path)));
    assert.deepEqual(errors, []);
    console.log('contextual followups functional Chromium assertions passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
