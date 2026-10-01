/* 예시 페이지 전용: 실제 로그인·API 호출 없이 화면과 모의 전환을 검사한다. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const base = process.env.BASE_URL;
const output = process.env.UI_OUTPUT || '/tmp/ai-company-ui-check/artifacts';
if (!base || !['localhost','127.0.0.1','[::1]'].includes(new URL(base).hostname)) throw new Error('격리된 로컬 예시 서버가 필요합니다.');
(async()=>{
  const browser = await chromium.launch({headless:true,chromiumSandbox:true,
    ...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),
    ...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],apiCalls=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/'))apiCalls.push(request.url());});
  await fs.mkdir(output,{recursive:true});
  try {
    await page.goto(base+'/design-preview/index.html');
    await page.locator('#preview-login').waitFor();
    await page.evaluate(()=>document.fonts.ready);
    for(const concept of ['a','b','c']) {
      await page.locator(`button[data-concept="${concept}"]`).click();
      await page.locator('button[data-screen="login"]').click();
      assert.equal(await page.getByRole('textbox',{name:'아이디',exact:true}).getAttribute('readonly'),'');
      await page.screenshot({path:`${output}/preview-${concept}-login-desktop.png`,fullPage:true});
      await page.locator('#preview-login button').click();
      await page.locator('.role-grid').waitFor();
      assert.equal(await page.locator('.role').count(),3);
      await page.screenshot({path:`${output}/preview-${concept}-workspace-desktop.png`,fullPage:true});
      for(const width of [800,390,320]) {
        await page.setViewportSize({width,height:900});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${concept}: ${width}px 작업 화면`);
      }
      await page.setViewportSize({width:390,height:844});
      await page.screenshot({path:`${output}/preview-${concept}-workspace-mobile.png`,fullPage:true});
      await page.locator('.workspace-nav button[data-view="manager"]').click();
      await page.locator('#message').fill('<img src=x onerror=alert(1)> 예시 메시지');
      await page.locator('#preview-message button').click();
      await page.getByText('<img src=x onerror=alert(1)> 예시 메시지',{exact:true}).last().waitFor();
      assert.equal(await page.locator('.chat-message img').count(),0,'예시 입력은 HTML로 해석하지 않음');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'모바일 대화 화면');
      await page.screenshot({path:`${output}/preview-${concept}-manager-mobile.png`,fullPage:true});
      await page.locator('.workspace-nav button[data-view="reports"]').click();
      await page.getByRole('heading',{name:'주간 일정 화면의 기본 구조'}).waitFor();
      await page.locator('.workspace-nav button[data-view="approvals"]').click();
      await page.getByRole('button',{name:'승인 내용 보기'}).click();
      await page.getByRole('status').getByText('예시 승인 화면입니다. 실제 승인이나 배포는 실행되지 않습니다.',{exact:true}).waitFor();
      await page.locator('button[data-screen="login"]').click();
      await page.screenshot({path:`${output}/preview-${concept}-login-mobile.png`,fullPage:true});
      for(const width of [320,600,800]) {
        await page.setViewportSize({width,height:900});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${concept}: ${width}px 로그인`);
      }
      await page.setViewportSize({width:1440,height:1000});
      await page.locator('#mobile-toggle').click();
      assert.ok((await page.locator('#stage').boundingBox()).width<=390);
      await page.locator('#mobile-toggle').click();
    }
    await page.reload();
    await page.locator('button[data-screen="workspace"]').click();
    await page.locator('.workspace-nav button[data-view="manager"]').click();
    assert.equal(await page.getByText('나 · 예시 입력',{exact:true}).count(),0,'새로고침 후 예시 입력은 남지 않음');
    assert.deepEqual(apiCalls,[],'실제 API 호출 없음');
    assert.deepEqual(errors,[],'브라우저 오류 없음');
    console.log(JSON.stringify({결과:'통과',시안:3,확인:['로그인→작업 화면','PM 예시 입력','보고서·승인','320~1440px','실제 API 호출 없음'],스크린샷:output}));
  } catch(error) {
    await page.screenshot({path:`${output}/preview-failure.png`,fullPage:true}).catch(()=>{});
    throw error;
  } finally { await browser.close(); }
})().catch(error=>{console.error(error.stack||error.message);process.exitCode=1;});
