/* Replay the empty workspace/source dialog flow shown in bug-kkb.mp4.
   Controlled API fixtures isolate presentation from model/network variability. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    const pageErrors = [], submissions = [], sourceRequests = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const source = { status: 'ok', source_id: 'source_' + 'a'.repeat(64), filename: 'Financial_report.pdf', mime_type: 'application/pdf',
      total_pages: 141, processed_pages: Array.from({ length: 30 }, (_, i) => i + 1), inspection_complete: false,
      tables: Array.from({ length: 20 }, (_, i) => ({ table_id: 'table_p000011_' + i, page: 11, row_count: 3, layout_review_required: true })) };
    const workspace = { workspace_id: 'workspace_empty', name: 'Video workflow fixture', profile: 'generic', version: 0, runs: [] };
    const otherWorkspace = { ...workspace, workspace_id: 'workspace_other', name: 'Other workspace', runs: [] };
    let nextSource = source, sourceGate;
    let finished = false;
    const result = { status: 'partial', message: 'Kaynak değeri kaydedildi. Karşılaştırma ve grafik tamamlanamadı.',
      conversation_id: 'conversation_fixture', errors: [{ code: 'DECISION_BUDGET', message: 'Model decision limit reached' }],
      tool_results: [{ tool: 'inspect_source', result: source }, { tool: 'ingest_source_table', result: {
        status: 'ok', source_id: source.source_id, dataset_id: 'dataset_fixture', row_count: 1 } }] };
    await page.route('https://source-workflow.test/**', async route => {
      const request = route.request(), url = new URL(request.url());
      let value;
      if (url.pathname === '/api/status') value = { provider_ready: true };
      else if (url.pathname === '/api/workspaces') value = { workspaces: [workspace, otherWorkspace] };
      else if (url.pathname === '/api/workspaces/workspace_empty') value = workspace;
      else if (url.pathname === '/api/workspaces/workspace_other') value = otherWorkspace;
      else if (url.pathname.endsWith('/sources')) value = { sources: [] };
      else if (/\/sources\/(url|upload)$/.test(url.pathname)) {
        sourceRequests.push({ path: url.pathname, url: url.pathname.endsWith('/url') ? request.postDataJSON().url : null });
        const responseSource = nextSource, gate = sourceGate;
        sourceGate = undefined;
        if (gate) { gate.started(); await gate.released; }
        value = responseSource;
      }
      else if (url.pathname.endsWith('/runs')) {
        submissions.push(request.postDataJSON());
        value = { job_id: 'job_fixture' };
      } else if (url.pathname === '/api/jobs/job_fixture') value = finished
        ? { status: 'finished', result }
        : { status: 'running', journey: { status: 'running', title: 'Çalışma sürüyor', detail: 'Raporun ilgili sayfaları inceleniyor.', stages: [] } };
      if (value) return route.fulfill({ json: value });
      const local = url.pathname === '/' ? 'app/static/index.html' : 'app' + url.pathname;
      if (fs.existsSync(local)) return route.fulfill({ body: fs.readFileSync(local), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(local)] || 'application/octet-stream' });
      return route.fulfill({ status: 404, body: 'Not found' });
    });
    await page.goto('https://source-workflow.test/?workspace=workspace_empty');
    await page.waitForFunction(() => document.querySelector('#workspace-title').textContent === 'Video workflow fixture');
    await page.locator('#sources-button').click();
    await page.locator('#source-url').fill('https://example.test/Financial_report.pdf');
    await page.locator('#url-button').click();
    await page.getByRole('button', { name: 'Bu kaynağı konuşmada kullan' }).click();
    const prompt = await page.locator('#question').inputValue();
    await page.locator('#question').fill(prompt + '\n31 Mart 2026 toplam aktiflerini sektörle karşılaştır ve grafik göster.');
    await page.locator('#send').click();
    await page.waitForFunction(() => document.querySelector('.message.pending'));
    const pending = await page.locator('#result-empty').innerText();
    finished = true;
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    const outcome = await page.locator('#result-empty').innerText();
    const observations = { raw_id_in_prompt: prompt.includes(source.source_id), source_ids: submissions[0]?.source_ids,
      pending_invites_first_question: pending.includes('İlk sorunuzla başlayın'), failure_invites_first_question: outcome.includes('İlk sorunuzla başlayın') };
    assert.equal(observations.raw_id_in_prompt, false, 'Source IDs must travel as attachments, not user prose');
    assert.deepEqual(observations.source_ids, [source.source_id]);
    assert.equal(observations.pending_invites_first_question, false, 'A running analysis must not look unstarted');
    assert.equal(observations.failure_invites_first_question, false, 'A failed comparison must not look unstarted');
    assert.match(outcome, /tamamlanamadı/i);
    assert.equal(await page.locator('#extra-result .source-summary').count(), 1);
    for (const size of [{ width: 1920, height: 1080 }, { width: 1366, height: 768 }]) {
      await page.setViewportSize(size);
      const geometry = await page.evaluate(() => ({ bottom: document.querySelector('#send').getBoundingClientRect().bottom,
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth, height: innerHeight }));
      assert.ok(geometry.bottom <= geometry.height, 'Composer stays visible after failure');
      assert.equal(geometry.horizontalOverflow, false);
    }
    workspace.runs = [{ message: submissions[0].message, result }];
    await page.reload();
    await page.waitForSelector('.source-summary');
    assert.equal((await page.locator('#result-empty').innerText()).includes('İlk sorunuzla başlayın'), false, 'Reload restores the actual outcome');
    await page.locator('#sources-button').click();
    await page.locator('#source-url').fill('https://example.test/Financial_report.pdf');
    await page.locator('#url-button').click();
    await page.getByRole('button', { name: 'Bu kaynağı konuşmada kullan' }).waitFor();
    assert.equal(await page.locator('.source-tables').evaluate(node => node.open), false, 'Candidate tables start collapsed');
    assert.equal((await page.locator('#source-feedback').innerText()).includes('table_p000011_'), false);
    assert.match(await page.locator('#source-feedback').innerText(), /30.*141|141.*30/);
    await page.locator('.source-tables > summary').click();
    assert.match(await page.locator('.source-tables').innerText(), /Sayfa 11/);
    await page.getByRole('button', { name: 'Bu kaynağı konuşmada kullan' }).click();
    assert.equal(await page.locator('#selected-sources [data-source-id]').count(), 1);
    await page.getByRole('button', { name: 'Kaynak seçimini kaldır' }).click();
    assert.equal(await page.locator('#selected-sources [data-source-id]').count(), 0);

    // Choosing another document adds it, including when filenames are identical.
    // Re-selecting the same registered document remains idempotent.
    async function selectSource(selected) {
      nextSource = selected;
      await page.locator('#sources-button').click();
      await page.locator('#source-url').fill('https://example.test/' + selected.filename);
      await page.locator('#url-button').click();
      await page.getByRole('button', { name: 'Bu kaynağı konuşmada kullan' }).click();
    }
    const second = { ...source, source_id: 'source_' + 'b'.repeat(64) };
    await page.locator('#question').fill('Bu iki raporu birlikte karşılaştır.');
    await selectSource(source);
    await selectSource(second);
    await selectSource(source);
    assert.deepEqual(await page.locator('#selected-sources [data-source-id]').evaluateAll(nodes => nodes.map(node => node.dataset.sourceId)), [source.source_id, second.source_id]);
    assert.equal(await page.locator('#question').inputValue(), 'Bu iki raporu birlikte karşılaştır.');
    await page.locator('#send').click();
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    assert.deepEqual(submissions.at(-1).source_ids, [source.source_id, second.source_id]);

    const twelve = Array.from({ length: 12 }, (_, i) => ({ ...source, source_id: 'source_' + i.toString(16).padStart(64, '0') }));
    for (const selected of twelve) await selectSource(selected);
    await selectSource(twelve[0]);
    assert.equal(await page.locator('#selected-sources [data-source-id]').count(), 12);
    await selectSource(second);
    assert.equal(await page.locator('#source-dialog').evaluate(node => node.open), true);
    assert.match(await page.locator('#source-feedback [role="status"]').innerText(), /en fazla 12 kaynak/);
    assert.deepEqual(await page.locator('#selected-sources [data-source-id]').evaluateAll(nodes => nodes.map(node => node.dataset.sourceId)), twelve.map(item => item.source_id));
    await page.keyboard.press('Escape');

    // A network reply from the old workspace must not replace the source dialog
    // or attach its document after the user has switched workspaces.
    for (const kind of ['url', 'upload']) {
      await page.locator('#workspace-list .workspace-item').filter({ hasText: workspace.name }).click();
      await page.waitForFunction(name => document.querySelector('#workspace-title').textContent === name, workspace.name);
      let started, release;
      const startedPromise = new Promise(resolve => { started = resolve; });
      sourceGate = { started, released: new Promise(resolve => { release = resolve; }) };
      nextSource = { ...source, filename: 'Late_old_workspace.pdf' };
      await page.locator('#sources-button').click();
      if (kind === 'url') {
        await page.locator('#source-url').fill('https://example.test/Late_old_workspace.pdf');
        await page.locator('#url-button').click();
      } else {
        await page.locator('#upload').setInputFiles({ name: 'Late_old_workspace.pdf', mimeType: 'application/pdf', buffer: Buffer.from('fixture PDF') });
      }
      await startedPromise;
      await page.keyboard.press('Escape');
      await page.locator('#workspace-list .workspace-item').filter({ hasText: otherWorkspace.name }).click();
      await page.waitForFunction(name => document.querySelector('#workspace-title').textContent === name, otherWorkspace.name);
      await page.locator('#sources-button').click();
      await page.waitForFunction(() => !document.querySelector('#source-feedback').children.length && !document.querySelector('#source-feedback').textContent);
      assert.equal(await page.locator('#url-button').isDisabled(), false, 'Changing workspace restores its URL submission button');
      const currentSource = { ...source, source_id: 'source_' + (kind === 'url' ? 'c' : 'd').repeat(64), filename: 'Current_' + kind + '.pdf' };
      nextSource = currentSource;
      const currentUrl = 'https://example.test/' + currentSource.filename;
      await page.locator('#source-url').fill(currentUrl);
      await page.locator('#url-button').click();
      await page.getByRole('button', { name: 'Bu kaynağı konuşmada kullan' }).waitFor();
      assert.deepEqual(sourceRequests.at(-1), { path: '/api/workspaces/workspace_other/sources/url', url: currentUrl }, 'The new workspace can submit its own URL while the old request is pending');
      const currentFeedback = await page.locator('#source-feedback').innerText();
      assert.ok(currentFeedback.includes(currentSource.filename.replaceAll('_', ' ')));
      const response = page.waitForResponse(item => item.url().endsWith('/workspace_empty/sources/' + kind));
      release();
      await response;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.locator('#source-feedback').innerText(), currentFeedback, 'Late reply cannot replace the current workspace source');
      assert.equal(await page.locator('#selected-sources [data-source-id]').count(), 0);
      assert.equal(await page.locator('#url-button').isDisabled(), false);
      await page.getByRole('button', { name: 'Bu kaynağı konuşmada kullan' }).click();
      assert.deepEqual(await page.locator('#selected-sources [data-source-id]').evaluateAll(nodes => nodes.map(node => node.dataset.sourceId)), [currentSource.source_id]);
    }
    assert.deepEqual(pageErrors, []);
    if (process.env.SOURCE_WORKFLOW_SCREENSHOT) await page.screenshot({ path: process.env.SOURCE_WORKFLOW_SCREENSHOT, fullPage: true });
    console.log('Source workflow: functional browser assertions passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
