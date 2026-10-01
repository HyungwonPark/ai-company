/* Independent UX review scenarios. Synthetic resources only; no operating API. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');
const web = path.resolve(__dirname, '../../src/ai_company/web/workspace-preview');
const output = process.env.UI_OUTPUT || '/tmp/ai-company-workspace-independent';
const origin = 'http://127.0.0.1:47994';
const storageKey = 'ai-company:workspace-design:v1';

(async () => {
  await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({
    headless: true, chromiumSandbox: true,
    ...(process.env.CHROME_CHANNEL ? {channel: process.env.CHROME_CHANNEL} : {}),
    ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {}),
  });
  const evidence = {source: 'independently authored synthetic browser interactions', sandbox: true, checks: [], violations: [], page_errors: []};
  let context;
  async function fresh() {
    if (context) await context.close();
    context = await browser.newContext({viewport: {width: 320, height: 844}, serviceWorkers: 'block'});
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      const file = path.resolve(web, '.' + (url.pathname === '/' ? '/index.html' : url.pathname));
      if (url.origin !== origin || request.method() !== 'GET' || url.pathname.startsWith('/api/') || !file.startsWith(web + path.sep)) {
        evidence.violations.push({method: request.method(), url: request.url()});
        await route.abort(); return;
      }
      try {
        await route.fulfill({body: await fs.readFile(file), contentType: ({'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'})[path.extname(file)] || 'application/octet-stream'});
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        await route.fulfill({status: 404, body: ''});
      }
    });
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    page.on('pageerror', error => evidence.page_errors.push(error.message));
    await page.goto(origin);
    return page;
  }
  const action = (page, name) => page.locator(`[data-action="${name}"]`).first();
  const state = page => page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), storageKey);
  async function settled(page) {
    await page.waitForFunction(key => JSON.parse(sessionStorage.getItem(key)).ui.pending == null, storageKey);
  }
  async function scenario(page, value) {
    await page.locator('.lab-options summary').click();
    await page.locator('#lab-scenario').selectOption(value);
    await page.locator('.lab-options summary').click();
  }
  async function shot(page, name) {
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path: path.join(output, `independent-${name}.png`), fullPage: true});
  }
  try {
    let page = await fresh();
    await page.locator('button[data-concept="c"]').click();
    await action(page, 'projects').click();
    await action(page, 'new').click();
    await page.locator('#lab-new-name').fill('독립 검수 프로젝트');
    await page.locator('#lab-new-goal').fill('역할의 진행과 다음 행동을 쉽게 확인하고 싶어요.');
    await page.locator('#lab-new-form button[type="submit"]').click();
    await page.getByRole('heading', {name: '독립 검수 프로젝트', exact: true}).waitFor();
    const inbox = page.locator('.inbox-list');
    assert.ok(!(await inbox.textContent()).includes('결정 필요'));
    assert.ok(!(await inbox.textContent()).includes('검사 대기'));
    await inbox.locator('[data-tab="approval"]').click();
    await page.getByRole('heading', {name: '요청이 없어요'}).waitFor();
    assert.equal(await page.locator('[data-action="review-decision"]').count(), 0);
    await inbox.locator('[data-tab="team"]').click();
    await page.getByRole('heading', {name: '아직 시작하지 않았어요'}).waitFor();
    assert.equal(await page.locator('[data-action="artifact"]').count(), 0);
    await shot(page, 'c-new-project');
    evidence.checks.push('C mobile: new project has no borrowed approval, wait reason or artifact');

    page = await fresh();
    await page.locator('#lab-message').fill('알 수 없는 상태는 별도로 모아 주세요.');
    await page.locator('#lab-message-form button[type="submit"]').click();
    await action(page, 'save-spec').waitFor();
    await action(page, 'save-spec').click();
    await action(page, 'request-plan').waitFor();
    assert.equal((await state(page)).model.projects[0].spec.version, 1);
    await scenario(page, 'error');
    await page.locator('#lab-message').fill('입력이 바뀌지 않는 조건도 명확히 해 주세요.');
    await page.locator('#lab-message-form button[type="submit"]').click();
    await page.getByText('저장 결과 확인 필요', {exact: true}).waitFor();
    const unresolved = (await state(page)).ui.pending;
    await page.reload();
    assert.deepEqual((await state(page)).ui.pending, unresolved);
    for (const denied of ['readonly', 'offline']) {
      await scenario(page, denied);
      await action(page, 'recover').click();
      await page.locator('#lab-notice').filter({hasText: denied === 'readonly' ? '권한' : '연결'}).waitFor();
      assert.deepEqual((await state(page)).ui.pending, unresolved, `${denied} cannot discard an unknown committed request`);
      assert.equal((await state(page)).model.projects[0].messages.length, 2);
    }
    await scenario(page, 'normal');
    await action(page, 'recover').click(); await settled(page);
    assert.equal((await state(page)).model.projects[0].messages.length, 2);
    await action(page, 'save-spec').click();
    await action(page, 'request-plan').waitFor();
    assert.equal((await state(page)).model.projects[0].spec.version, 2);
    assert.equal((await state(page)).model.projects[0].runs.length, 0);
    await action(page, 'request-plan').click();
    await action(page, 'review-plan').waitFor();
    const plan = (await state(page)).model.projects[0].plan;
    assert.equal((await state(page)).model.projects[0].runs.length, 0);
    await action(page, 'review-plan').click();
    await page.locator('#lab-confirm-reviewed').check();
    await page.locator('#lab-confirm-form').evaluate(form => { form.dataset.digest = 'wrong'; });
    await page.locator('#lab-confirm-form button[type="submit"]').click();
    await page.locator('.inline-error').filter({hasText: '바뀌었습니다'}).waitFor();
    assert.equal((await state(page)).model.projects[0].runs.length, 0);
    await page.locator('#lab-confirm-form').evaluate((form, digest) => { form.dataset.digest = digest; }, plan.digest);
    await page.locator('#lab-confirm-form button[type="submit"]').click();
    await page.locator('#lab-dialog').waitFor({state: 'hidden'});
    const confirmed = (await state(page)).model.projects[0];
    assert.equal(confirmed.runs.length, 1);
    assert.equal(confirmed.runs[0].execution_spec.version, 2);
    assert.equal(confirmed.runs[0].plan_digest, plan.digest);
    await page.reload();
    assert.equal((await state(page)).model.events.filter(event => event.kind === 'plan_confirmed').length, 1);
    await page.locator('.tabs [data-tab="report"]').click();
    await page.getByRole('heading', {name: '계획 확정, 실제 실행 전'}).waitFor();
    assert.ok(!(await page.locator('.reading').textContent()).includes('초안 전달 완료'));
    await action(page, 'projects').click();
    assert.ok((await page.locator('[data-project="fixture-pilot"]').textContent()).includes('진행 확인'));
    assert.equal((await state(page)).model.projects[1].runs.length, 0);
    await shot(page, 'project-next-action');
    evidence.checks.push('Lost PM reply after v1: reload, read-only/offline retry retain original request, recover once, v2 saved without execution');
    evidence.checks.push('Changed plan digest rejected; direct reviewed confirmation creates exactly one v2 run; report and project next action reflect new run');

    page = await fresh();
    await page.locator('button[data-concept="c"]').click();
    await page.locator('.document summary').click();
    assert.ok((await page.locator('.document').textContent()).includes('Do not deploy or merge.'));
    // Simulate a changed display subject; the model's immutable approval copy must reject it.
    await page.evaluate(() => { window.WorkspacePreviewData.approval.subject_digest = 'd'.repeat(64); });
    await page.locator('[data-action="review-decision"][data-decision="approve"]').click();
    await page.locator('#lab-decision-reviewed').check();
    await page.locator('#lab-decision-form button[type="submit"]').click();
    await page.locator('.inline-error').filter({hasText: '승인 대상이 달라졌습니다'}).waitFor();
    assert.equal((await state(page)).model.decisions.length, 0);
    await page.evaluate(() => { window.WorkspacePreviewData.approval.subject_digest = 'b'.repeat(64); });
    await page.locator('#lab-decision-form button[type="submit"]').click();
    await page.getByText('예시 선택: 수용', {exact: true}).waitFor();
    assert.equal((await state(page)).model.decisions[0].subject_digest, 'b'.repeat(64));
    assert.equal(await page.evaluate(() => window.WorkspacePreviewData.approval.status), 'pending');
    assert.equal((await state(page)).model.projects[0].runs.length, 0);
    await shot(page, 'candidate-bound-choice');
    evidence.checks.push('Candidate display-subject mismatch rejected; original source accessible; fixture choice changes neither pending source nor plan execution');
    assert.deepEqual(evidence.violations, []);
    assert.deepEqual(evidence.page_errors, []);
    evidence.browser = browser.version();
    await fs.writeFile(path.join(output, 'workspace-independent-validation.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
