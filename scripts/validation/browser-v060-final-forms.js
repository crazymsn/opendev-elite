async sourcePage => {
  const context=await sourcePage.context().browser().newContext(),page=await context.newPage(), checks=[],errors=[];
  const check=(v,label)=>{if(!v)throw new Error(label);checks.push(label);};
  page.on('pageerror',e=>errors.push(e.message));
  const ready=async()=>{await page.locator('#workspace').waitFor({state:'visible'});await page.locator('#main .page-heading').waitFor();};
  const sizes=async label=>{for(const width of [375,768,1024,1440]) {await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),label+' width '+width);}};
  try {
    await page.goto('http://127.0.0.1:8877/#team-login');await page.locator('#account-fields:not([disabled])').waitFor();
    await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-password').press('Enter');await ready();
    check(await page.evaluate(async()=>(await(await fetch('/api/ui-config')).json()).demo_mode===true),'synthetic environment confirmed');
    for(const [lang,id] of [['zh-CN',499],['en',498]]) {
      await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();
      await page.goto('http://127.0.0.1:8877/#projects/'+id);await ready();
      for(const action of ['assign','qualify','review-one','experience-create','review-two']) {
        await page.locator('#main [data-action="'+action+'"]').first().click();await page.locator('#action-dialog[open]').waitFor();
        await sizes(lang+' '+action);await page.keyboard.press('Tab');
        check(await page.evaluate(()=>document.querySelector('#action-dialog').contains(document.activeElement)),lang+' '+action+' keyboard focus stays in dialog');
        await page.keyboard.press('Escape');check(!(await page.locator('#action-dialog').isVisible()),lang+' '+action+' Escape closes');
      }
      await page.locator('.next-step-panel [data-action=followup-schedule]').click();await page.locator('#action-dialog[open]').waitFor();await sizes(lang+' schedule follow-up');
      check((await page.locator('[name=grant_id]').inputValue()).length===32,lang+' grant prefilled');
      await page.locator('[name=due_at]').fill(new Date(Date.now()+100*86400000).toISOString().slice(0,10));
      await page.screenshot({path:'output/playwright/v060-'+lang+'-schedule-followup.png'});
      const response=page.waitForResponse(r=>r.url().endsWith('/api/actions/followup-schedule')&&r.request().method()==='POST');
      await page.locator('#dialog-submit').click();check((await response).ok(),lang+' scheduled missing follow-up');await page.locator('#action-dialog').waitFor({state:'hidden'});await ready();
      check(await page.locator('.next-step-panel').innerText().then(t=>lang==='en'?t.includes('Follow usage'):t.includes('按计划')),lang+' schedule updates next step');
    }
    await page.goto('http://127.0.0.1:8879/#team-login');await page.locator('#account-fields:not([disabled])').waitFor();
    await page.locator('#token-login-details').evaluate(e=>e.open=true);await page.locator('#access-token').fill('offline-sop-disburser-fixture-only');await page.locator('#login-form button').click();await ready();
    for(const lang of ['zh-CN','en']) {
      await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();
      await page.goto('http://127.0.0.1:8879/#projects/401/support');await ready();
      await page.locator('[data-do=credits-plan]').click();await page.locator('#action-dialog[open]').waitFor();
      await sizes(lang+' Credits plan');
      check((await page.locator('#dialog-fields .evidence-fields').innerText()).includes('10.00 USD'),lang+' exact amount shown in readable plan');
      check(!(await page.locator('#dialog-fields details').getAttribute('open')),lang+' API payload collapsed by default');
      await page.screenshot({path:'output/playwright/v060-'+lang+'-credits-plan.png'});await page.keyboard.press('Escape');
    }
    check(errors.length===0,'no uncaught JavaScript errors');return {checks,errors,synthetic_only:true};
  } finally {await context.close();}
}
