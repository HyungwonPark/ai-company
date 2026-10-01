/* Independent graph UX checks: ephemeral real API + explicitly injected read responses. */
'use strict';
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {createHash} = require('node:crypto');

(async () => {
  const base = process.env.BASE_URL, fixtures = JSON.parse(process.env.GRAPH_FIXTURES);
  const output = process.env.UI_OUTPUT || '/tmp/ai-company-graph-independent';
  await fs.mkdir(output, {recursive: true});
  assert.ok(/^http:\/\/127\.0\.0\.1:\d+$/.test(base), 'only the isolated runner is allowed');
  const browser = await chromium.launch({headless: true, chromiumSandbox: true,
    ...(process.env.CHROME_CHANNEL ? {channel: process.env.CHROME_CHANNEL} : {}),
    ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {})});
  const context = await browser.newContext({viewport: {width: 390, height: 844}, serviceWorkers: 'block', reducedMotion: 'reduce'});
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  const writes = [], violations = [], errors = [], checks = [];
  let latestRead = null, injection = null;
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== base || request.method() !== 'GET' && !['/api/login', '/api/password'].includes(url.pathname)) {
      violations.push({path: url.pathname, method: request.method()}); await route.abort(); return;
    }
    if (request.method() !== 'GET') writes.push(url.pathname);
    if (url.pathname === `/api/projects/${fixtures.project_id}/overview`) {
      const response = await route.fetch();
      const body = await response.json();
      if (response.ok()) latestRead = structuredClone(body);
      await route.fulfill({response, json: injection ? structuredClone(injection) : body}); return;
    }
    await route.continue();
  });
  const action = name => page.locator(`[data-rg-action="${name}"]`);
  const go = async hash => { await page.evaluate(value => { location.hash = value; }, hash); };
  const screenshot = async name => { await page.evaluate(() => document.fonts.ready); await page.screenshot({path: path.join(output, `graph-independent-${name}.png`), fullPage: true}); };
  const withFingerprint = body => {
    body.workspace_graph.fingerprint = createHash('sha256').update(JSON.stringify(body.workspace_graph.snapshots)).digest('hex');
    return body;
  };
  async function refresh() {
    const [response] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === `/api/projects/${fixtures.project_id}/overview`),
      page.locator('.page-heading button[data-action="refresh"]').click(),
    ]);
    await response.finished();
  }
  try {
    await page.goto(base + '/?workspace=integrated#projects');
    await page.getByLabel('비밀번호', {exact: true}).fill(process.env.TEST_PASSWORD);
    await page.getByRole('button', {name: '로그인', exact: true}).click();
    await page.getByLabel('현재 비밀번호', {exact: true}).fill(process.env.TEST_PASSWORD);
    await page.getByLabel('새 비밀번호', {exact: true}).fill('independent-graph-only-password');
    await page.getByLabel('새 비밀번호 확인', {exact: true}).fill('independent-graph-only-password');
    await page.getByRole('button', {name: '비밀번호 변경 후 계속'}).click();
    await page.locator('.nav').waitFor();
    await go('#progress?project=' + fixtures.project_id);
    await page.locator('[data-rg-root]').waitFor();
    const original = structuredClone(latestRead);
    const current = original.workspace_graph.snapshots.find(snapshot => snapshot.run_id === fixtures.run_ids[1]);
    const old = original.workspace_graph.snapshots.find(snapshot => snapshot.run_id === fixtures.run_ids[0]);
    await page.locator('#journey-run').selectOption(current.id);
    await action('fit').click();
    await page.locator('.rg-viewport').scrollIntoViewIfNeeded();
    // A physical pointer must hit a visible SVG stroke, not a programmatic dispatch or list fallback.
    const hit = await page.locator('.rg-edge-hit').evaluateAll(paths => {
      for (const edge of paths) {
        const matrix = edge.getScreenCTM(), length = edge.getTotalLength();
        if (!matrix) continue;
        for (const fraction of [0.5, 0.35, 0.65, 0.2, 0.8]) {
          const point = edge.getPointAtLength(length * fraction).matrixTransform(matrix);
          if (point.x < 0 || point.y < 0 || point.x >= innerWidth || point.y >= innerHeight) continue;
          if (document.elementFromPoint(point.x, point.y)?.closest('[data-rg-edge]') === edge)
            return {x: point.x, y: point.y, id: edge.dataset.rgEdge};
        }
      }
      return null;
    });
    assert.ok(hit, 'a visible SVG relationship must be physically selectable on mobile');
    await page.mouse.click(hit.x, hit.y);
    await page.locator('.rg-direction').waitFor();
    const selectedEdge = current.edges.find(edge => edge.id === hit.id);
    assert.ok((await page.locator('.rg-detail').textContent()).includes(selectedEdge.reason || selectedEdge.title));
    await screenshot('mobile-svg-edge');
    await action('list').click();
    await page.locator(`[data-rg-edge="${hit.id}"]`).click();
    await page.locator('.rg-direction').waitFor();
    checks.push('390px: real pointer hits SVG relationship; same ID and detail remain available in list');

    await page.locator('#journey-run').selectOption(old.id);
    await page.locator('.rg-reference-links a[href^="#approvals"]').click();
    await page.locator('.approval-reading').waitFor();
    assert.ok(page.url().includes('run=' + encodeURIComponent(old.run_id)));
    await page.locator('.nav a[aria-label="승인"]').click();
    await page.waitForFunction(id => new URLSearchParams(location.hash.split('?')[1]).get('run')===id,old.run_id);
    await go('#approvals?project=' + fixtures.project_id + '&run=' + current.run_id);
    await page.getByText('이 대상의 승인 요청 없음', {exact: true}).waitFor();
    await page.evaluate(hash=>{location.hash=hash;window.dispatchEvent(new Event('offline'));},'#progress?project=' + fixtures.other_project_id);
    await page.locator(`[data-rg-root][data-project="${fixtures.other_project_id}"]`).waitFor();
    assert.equal(await page.locator('.rg-node').count(), 0);
    assert.equal(await page.locator('.rg-reference-links a').count(), 0);
    checks.push('Actual API: historical approval stays in its run; journey navigation retains execution scope; empty project borrows no nodes/documents; synchronous old-view render cannot overwrite pending navigation');

    await go('#progress?project=' + fixtures.project_id);
    await page.locator(`[data-rg-root][data-project="${fixtures.project_id}"]`).waitFor();
    await page.locator('#journey-run').selectOption(current.id);
    await action('list').click();
    const selectedNode = current.nodes.find(node => node.kind === 'role');
    await page.locator(`[data-rg-node="${selectedNode.id}"]`).click();
    await page.locator('.rg-detail details').filter({has: page.locator('summary').filter({hasText: '원본 참조'})}).locator('summary').click();
    const panelScroll = await page.locator('.rg-detail').evaluate(panel => { panel.scrollTop = 180; return panel.scrollTop; });
    assert.ok(panelScroll > 0, 'the selected mobile detail must have a scrollable reading region');
    // Inject malformed delivery order, not changes to the real store or model facts.
    const newer = structuredClone(latestRead);
    newer.workspace_graph.cursor += 100;
    newer.workspace_graph.observed_at += 100;
    const newerSnapshot = newer.workspace_graph.snapshots.find(snapshot => snapshot.id === current.id);
    newerSnapshot.nodes.find(node => node.id === selectedNode.id).name = '독립 검수 최신 응답';
    injection = withFingerprint(newer); await refresh();
    await page.locator('.rg-detail h3').filter({hasText: '독립 검수 최신 응답'}).waitFor();
    assert.equal(await page.locator('.rg-detail').evaluate(panel => panel.scrollTop), panelScroll, 'an accepted changed response preserves the reading position');
    assert.ok(await page.locator('.rg-detail details').filter({has: page.locator('summary').filter({hasText: '원본 참조'})}).evaluate(detail => detail.open), 'an accepted changed response preserves expanded evidence');
    const older = structuredClone(newer);
    older.workspace_graph.observed_at -= 1;
    older.workspace_graph.snapshots.find(snapshot => snapshot.id === current.id).nodes.find(node => node.id === selectedNode.id).name = '잘못된 이전 응답';
    injection = withFingerprint(older); await refresh();
    await page.locator('.banner.error[role="alert"] > span').filter({hasText: '이전 순서의 응답을 받았습니다. 마지막으로 확인한 상태를 유지합니다.'}).waitFor();
    assert.equal(await page.locator('.rg-detail h3').textContent(), '독립 검수 최신 응답');
    const duplicate = structuredClone(newer);
    duplicate.workspace_graph.observed_at += 1;
    const duplicateSnapshot = duplicate.workspace_graph.snapshots.find(snapshot => snapshot.id === current.id);
    duplicateSnapshot.nodes.find(node => node.id === selectedNode.id).name = '중복 제거 응답';
    duplicateSnapshot.nodes.push(...structuredClone(duplicateSnapshot.nodes));
    duplicateSnapshot.edges.push(...structuredClone(duplicateSnapshot.edges));
    injection = withFingerprint(duplicate); await refresh();
    await page.locator('.rg-detail h3').filter({hasText: '중복 제거 응답'}).waitFor();
    assert.equal(await page.locator('.rg-list [data-rg-node]').count(), current.nodes.length);
    assert.equal(await page.locator('.rg-list [data-rg-edge]').count(), current.edges.length);
    const foreign = structuredClone(newer);
    foreign.workspace_graph.observed_at += 2;
    foreign.workspace_graph.snapshots.find(snapshot => snapshot.id === current.id).nodes[0].project_id = fixtures.other_project_id;
    injection = withFingerprint(foreign); await refresh();
    await page.locator('.banner.error[role="alert"] > span').filter({hasText: '이전 순서의 응답을 받았습니다. 마지막으로 확인한 상태를 유지합니다.'}).waitFor();
    assert.equal(await page.locator('.rg-detail h3').textContent(), '중복 제거 응답');
    await screenshot('stale-response-preserved');
    checks.push('Injected changed response preserves mobile detail scroll/expanded evidence; same-cursor older timestamp rejected, duplicate IDs deduplicated, foreign node binding rejected; selected newest detail retained');

    // First handoff inserts a details section before the source. Semantic identity,
    // rather than numeric DOM position, must retain the reader's evidence/focus.
    const beforeHandoff = structuredClone(newer);
    beforeHandoff.workspace_graph.observed_at += 3;
    const beforeRole = beforeHandoff.workspace_graph.snapshots.find(snapshot => snapshot.id === current.id).nodes.find(node => node.id === selectedNode.id);
    beforeRole.name = '최초 이관 전'; beforeRole.handoffs = [];
    injection = withFingerprint(beforeHandoff); await refresh();
    await page.locator('.rg-detail h3').filter({hasText: '최초 이관 전'}).waitFor();
    const sourceSummary = page.locator('.rg-detail summary[data-rg-detail-key="source"]');
    if (!await sourceSummary.evaluate(summary => summary.parentElement.open)) await sourceSummary.click();
    await sourceSummary.focus();
    assert.equal(await page.locator('.rg-detail summary[data-rg-detail-key="handoff"]').count(), 0);
    const afterHandoff = structuredClone(beforeHandoff);
    afterHandoff.workspace_graph.observed_at += 1;
    const afterRole = afterHandoff.workspace_graph.snapshots.find(snapshot => snapshot.id === current.id).nodes.find(node => node.id === selectedNode.id);
    afterRole.name = '최초 이관 수신';
    afterRole.handoffs = [{execution_id: 'independent-injected-next', previous_execution_id: 'independent-injected-first', generation: 2, role: 'developer'}];
    injection = withFingerprint(afterHandoff);
    // No click/explicit refresh: the existing five-second poll must deliver this
    // changed payload while keyboard focus remains on the evidence summary.
    await page.locator('.rg-detail h3').filter({hasText: '최초 이관 수신'}).waitFor({timeout: 15000});
    assert.ok(await sourceSummary.evaluate(summary => summary.parentElement.open), 'source evidence stays open when first handoff is inserted');
    assert.equal(await page.locator('.rg-detail summary[data-rg-detail-key="handoff"]').evaluate(summary => summary.parentElement.open), false, 'new handoff evidence must not inherit the source open state');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.rgDetailKey), 'source', 'natural polling preserves semantic evidence focus');
    await screenshot('first-handoff-source-focus');
    checks.push('Natural five-second poll inserts first handoff: source evidence stays open/focused and the new handoff section stays closed');
    assert.deepEqual(violations, []);
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, ['/api/login', '/api/password']);
    const result = {status: 'PASS', source: 'independently authored browser interactions; ephemeral real API plus explicit transport injections', browser: browser.version(), sandbox: true, checks, write_paths: writes, violations, page_errors: errors};
    await fs.writeFile(path.join(output, 'workspace-graph-independent-validation.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    await screenshot('failure').catch(() => {});
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
