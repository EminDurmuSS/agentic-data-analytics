/* Real Chromium interactions with the shipped UI. All HTTP data is synthetic. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1050 } });
    const pageErrors = [], requests = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const sourceText = '<img src=x onerror="window.journeyInjected=true"> Banka & ortaklık raporu';
    const technicalSentinel = 'TECHNICAL_JOURNEY_SENTINEL_' + 'x'.repeat(8000);
    const tools = ['inspect_source', 'ingest_source_table', 'execute', 'explain_value', 'build_chart'];
    const activity = Array.from({ length: 63 }, (_, index) => ({
      id: 'technical_' + index, tool: tools[index % tools.length],
      title: 'Teknik çağrı: ' + tools[index % tools.length],
      detail: index === 0 ? technicalSentinel : 'call_' + index + ' {"status":"ok"}',
    }));
    const definitions = [
      ['sources', 'Kaynaklar', 'Finansal rapor ve sektör kaynağı incelendi.', 'Kaynak rapor incelendi', sourceText],
      ['data', 'Veri hazırlığı', 'Kaynak dönemleri ve birimleri korundu.', 'Kaynak gözlemleri hazırlandı', '31 Mart 2026 · bin TL'],
      ['calculation', 'Hesaplama', 'Tutarlar ortak birime çevrildi.', 'Ölçekler eşitlendi', 'Dönem veya kaynak değerleri değiştirilmedi.'],
      ['checks', 'Kontroller', 'Hesaplar kaynak kayıtlarıyla karşılaştırıldı.', 'Kaynak izi doğrulandı', 'Farklı kapsamlar resmi pazar payı kabul edilmedi.'],
      ['presentation', 'Sunum', 'Tablo ve grafik hazırlandı.', 'Sonuçlar gösterildi', 'Özgün değerler ve kaynak bağlantıları korunuyor.'],
    ];
    const completed = { status: 'completed', title: 'Analiz tamamlandı', detail: 'Kaynaklardan sonuçlara beş aşama.', event_count: 63,
      stages: definitions.map(([id, label, summary, itemLabel, detail], index) => ({
        id, label, status: 'complete', summary,
        items: [{ label: itemLabel, detail, status: 'complete', count: index < 3 ? 13 : 12 }],
      })) };
    const workspace = { workspace_id: 'workspace_journey', name: 'Synthetic journey test', profile: 'finance', version: 1,
      latest_activity: activity, latest_journey: completed,
      runs: [{ message: 'Kaynakları incele ve karşılaştır.', result: { status: 'completed', display_message: 'Kaynak karşılaştırması hazır.', message: 'Kaynak karşılaştırması hazır.' } }] };
    await page.route('http://journey.test/**', async route => {
      const request = route.request(), url = new URL(request.url());
      requests.push({ method: request.method(), path: url.pathname });
      let value;
      if (url.pathname === '/api/status') value = { provider_ready: true };
      else if (url.pathname === '/api/workspaces') value = { workspaces: [workspace] };
      else if (url.pathname === '/api/workspaces/workspace_journey') value = workspace;
      if (value) return route.fulfill({ json: value });
      const local = url.pathname === '/' ? 'app/static/index.html' : 'app' + url.pathname;
      if (fs.existsSync(local)) return route.fulfill({ body: fs.readFileSync(local), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(local)] || 'application/octet-stream' });
      return route.fulfill({ status: 404, body: 'Not found' });
    });
    await page.goto('http://journey.test/?workspace=workspace_journey');
    await page.waitForSelector('#journey-summary');
    await page.waitForFunction(() => document.querySelector('#journey-summary').textContent.includes('Analiz tamamlandı'));
    const outer = page.locator('#activity'), summary = page.locator('#journey-summary');
    assert.equal(await outer.evaluate(node => node.open), false, 'Completed journeys start compact');
    assert.equal(await page.locator('#events > *').count(), 0, '63 technical records must not render eagerly');
    assert.equal(await page.locator('#activity pre').count(), 0);
    assert.equal((await outer.textContent()).includes(technicalSentinel), false);
    for (const name of tools) assert.equal((await outer.textContent()).includes(name), false, 'Default journey contains business labels, not tool names');
    assert.equal(await page.locator('#journey-stages > [data-status="complete"]').count(), 5);
    const chipLabels = await page.locator('#journey-stages > [aria-label]').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')));
    for (const [, label] of definitions) assert.ok(chipLabels.some(value => value.startsWith(label + ':')));

    // Native details controls must work with a keyboard, without pointer clicks.
    await summary.focus();
    await page.keyboard.press('Enter');
    assert.equal(await outer.evaluate(node => node.open), true);
    assert.equal(await page.locator('#journey-content .journey-stage').count(), 5);
    assert.equal(await page.locator('#events > *').count(), 0, 'Opening the readable journey does not open technical history');
    const sourceStage = page.locator('.journey-stage[data-stage="sources"]');
    const sourceSummary = sourceStage.locator('summary').first();
    await sourceSummary.focus();
    await page.keyboard.press('Space');
    assert.equal(await sourceStage.evaluate(node => node.open), true);
    assert.match(await sourceStage.innerText(), /<img src=x onerror="window\.journeyInjected=true"> Banka & ortaklık raporu/);
    assert.equal(await page.locator('#journey-content img, #journey-content script').count(), 0);
    assert.equal(await page.evaluate(() => window.journeyInjected), undefined);

    const active = structuredClone(completed);
    active.status = 'running'; active.title = 'Kaynak kontrolleri sürüyor'; active.detail = 'Hesap sonuçları doğrulanıyor.';
    active.stages = active.stages.slice(0, 4);
    active.stages[3].status = 'active'; active.stages[3].summary = 'Kaynak eşleşmeleri kontrol ediliyor.';
    active.stages[3].items[0].status = 'active';
    await page.evaluate(({ activity, active }) => {
      window.journeyPendingMessage = appendMessage('assistant', 'Başlıyor', true);
      showPendingActivity(window.journeyPendingMessage, activity, active);
    }, { activity, active });
    assert.equal(await page.locator('.journey-live').getAttribute('role'), 'status');
    assert.equal(await page.locator('.journey-live').getAttribute('aria-live'), 'polite');
    assert.match(await page.locator('.journey-live').textContent(), /Kontroller/);
    for (const name of tools) assert.equal((await page.locator('.journey-live').textContent()).includes(name), false);
    // Several realistic repeated polling updates must preserve an in-progress
    // keyboard interaction, not replace the disclosure/focused summary.
    await page.evaluate(() => { window.savedSourceSummary = document.querySelector('.journey-stage[data-stage="sources"] > summary'); });
    await page.evaluate(({ activity, active }) => {
      for (let index = 0; index < 12; index++) {
        showEvents(activity, { ...active, detail: 'Kontrol ediliyor · ' + (index + 1) });
        showPendingActivity(window.journeyPendingMessage, activity, active);
      }
    }, { activity, active });
    assert.equal(await outer.evaluate(node => node.open), true);
    assert.equal(await sourceStage.evaluate(node => node.open), true);
    assert.equal(await page.evaluate(() => document.activeElement === window.savedSourceSummary), true, 'Polling must preserve keyboard focus');
    assert.equal(await page.locator('.journey-stage[data-stage="checks"]').getAttribute('data-status'), 'active');
    assert.equal(await page.locator('#events > *').count(), 0);
    assert.match(await summary.textContent(), /Kaynak kontrolleri sürüyor/);

    const technical = page.locator('#activity-technical');
    await technical.locator('summary').first().focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#events').children.length === 63);
    assert.match(await page.locator('#events').textContent(), /inspect_source/);
    assert.match(await page.locator('#events').textContent(), /TECHNICAL_JOURNEY_SENTINEL/);
    assert.equal(await technical.evaluate(node => node.open), true);
    const added = [...activity, { id: 'technical_63', tool: 'explain_value', title: 'Teknik kontrol yenilendi', detail: 'Son kaynak eşleşmesi' }];
    await page.evaluate(({ added, active }) => showEvents(added, { ...active, event_count: 64 }), { added, active });
    assert.equal(await technical.evaluate(node => node.open), true, 'Polling must preserve the explicit technical disclosure');
    assert.equal(await page.locator('#events > *').count(), 64, 'An explicitly open technical ledger updates with the latest records');

    for (const status of ['failed', 'needs_input']) {
      const attention = structuredClone(active);
      attention.status = status;
      attention.title = status === 'failed' ? 'Kaynak doğrulanamadı' : 'Dönem seçiminiz gerekiyor';
      attention.detail = 'Sonuç tamamlanmış kabul edilmedi.';
      attention.stages[3].status = 'attention';
      attention.stages[3].summary = status === 'failed' ? 'Kaynak izi doğrulanamadı.' : 'Karşılaştırılacak dönem belirsiz.';
      attention.stages[3].items[0].status = 'attention';
      await page.evaluate(({ added, attention }) => {
        showEvents(added, attention);
        showPendingActivity(window.journeyPendingMessage, added, attention);
      }, { added, attention });
      assert.match(await summary.textContent(), new RegExp(attention.title));
      assert.match(await page.locator('.journey-live').textContent(), new RegExp(attention.title));
      assert.equal(await page.locator('.journey-stage[data-stage="checks"]').getAttribute('data-status'), 'attention');
      assert.equal(await page.locator('.journey-stage[data-status="complete"]').count(), 3);
      assert.equal(await page.locator('.journey-stage[data-stage="presentation"]').count(), 0, 'Failed or waiting work must not acquire a completed presentation stage');
      assert.equal(await sourceStage.evaluate(node => node.open), true);
    }

    // A later poll may discover a previously absent stage. Insert it in API
    // order without recreating or moving away the user's focused disclosure.
    const sparse = { ...active, stages: [active.stages[0], active.stages[2]] };
    await page.evaluate(({ activity, sparse }) => showEvents(activity, sparse), { activity, sparse });
    const calculationStage = page.locator('.journey-stage[data-stage="calculation"]');
    await calculationStage.locator('summary').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await calculationStage.evaluate(node => node.open), true);
    await page.evaluate(() => { window.savedCalculationSummary = document.activeElement; });
    const inserted = { ...active, stages: active.stages.slice(0, 3) };
    await page.evaluate(({ activity, inserted }) => showEvents(activity, inserted), { activity, inserted });
    assert.deepEqual(await page.locator('#journey-content > .journey-stage').evaluateAll(nodes => nodes.map(node => node.dataset.stage)), ['sources', 'data', 'calculation']);
    assert.deepEqual(await page.locator('#journey-stages > [aria-label]').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label').split(':')[0])), ['Kaynaklar', 'Veri hazırlığı', 'Hesaplama']);
    assert.equal(await page.evaluate(() => document.activeElement === window.savedCalculationSummary), true, 'Inserting an earlier stage must preserve focus on the existing calculation summary');
    assert.equal(await calculationStage.evaluate(node => node.open), true);

    // An expanded mobile journey must fit without horizontal page scrolling.
    await technical.locator('summary').first().click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.evaluate(({ activity, active }) => showEvents(activity, active), { activity, active });
    await page.locator('#activity').scrollIntoViewIfNeeded();
    const mobile = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth,
      journey: document.querySelector('#activity').getBoundingClientRect().toJSON() }));
    assert.ok(mobile.document <= mobile.width + 1, 'The mobile page must not scroll horizontally: ' + JSON.stringify(mobile));
    assert.ok(mobile.journey.left >= -1 && mobile.journey.right <= mobile.width + 1);
    const checkComposer = async (stacked) => {
      await page.locator('#send').scrollIntoViewIfNeeded();
      const boxes = await page.evaluate(() => {
        const send = document.querySelector('#send'), button = send.getBoundingClientRect();
        const hit = document.elementFromPoint(button.left + button.width / 2, button.top + button.height / 2);
        return {
          composer: document.querySelector('#composer').getBoundingClientRect().toJSON(),
          results: document.querySelector('#result-pane').getBoundingClientRect().toJSON(),
          conversation: document.querySelector('#conversation-pane').getBoundingClientRect().toJSON(),
          send: button.toJSON(), viewportHeight: innerHeight,
          receivesPointer: hit === send || send.contains(hit),
        };
      });
      if (stacked) {
        assert.ok(boxes.composer.bottom <= boxes.results.top + 1, 'Expanded mobile activity must not push the composer over the result pane: ' + JSON.stringify(boxes));
        assert.ok(boxes.send.bottom <= boxes.results.top + 1, 'The send button must stay above the mobile result pane');
      } else {
        assert.ok(boxes.composer.bottom <= boxes.conversation.bottom + 1, 'The composer must remain inside the desktop conversation pane');
      }
      assert.ok(boxes.send.top >= -1 && boxes.send.bottom <= boxes.viewportHeight + 1, 'Scrolling must bring the full send button into view');
      assert.equal(boxes.receivesPointer, true, 'The send button must receive pointer events, not be covered by the result pane');
      await page.locator('#send').click({ trial: true });
    };
    await checkComposer(true);
    const moving = await page.evaluate(() => [...document.querySelectorAll('#activity, #activity *')].flatMap(node => {
      if (!node.getClientRects().length || node.closest('details:not([open])') && !node.matches('summary')) return [];
      return [null, '::before', '::after'].flatMap(pseudo => {
        const css = getComputedStyle(node, pseudo);
        return css.animationName !== 'none' && css.animationDuration.split(',').some(value => parseFloat(value) > 0.01)
          ? [{ tag: node.tagName, className: node.className, pseudo, animation: css.animationName, duration: css.animationDuration }] : [];
      });
    }));
    assert.deepEqual(moving, [], 'Reduced-motion preference must disable journey pulse animations');
    await summary.focus();
    await page.keyboard.press('Enter');
    assert.equal(await outer.evaluate(node => node.open), false);
    await page.keyboard.press('Enter');
    assert.equal(await outer.evaluate(node => node.open), true);
    await page.setViewportSize({ width: 1500, height: 900 });
    await checkComposer(false);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(requests.filter(request => request.method !== 'GET'), [], 'Read-only journey interactions must not mutate workspace state');
    process.stdout.write('Activity journey: functional Chromium assertions passed\n');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
