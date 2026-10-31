async sourcePage => {
  const context=await sourcePage.context().browser().newContext(),page=await context.newPage();
  const origin='http://127.0.0.1:8765/',checks=[],errors=[],writes=[];
  const check=(v,label)=>{if(!v)throw new Error(label);checks.push(label);};
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST')writes.push(r.url().replace(origin,'/'));});
  const ready=async()=>{await page.locator('#workspace').waitFor({state:'visible'});await page.locator('#main .page-heading').waitFor();};
  try {
    await page.goto(origin+'#team-login');await page.locator('#account-fields:not([disabled])').waitFor();
    await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-submit').click();await ready();
    const facts=await page.evaluate(async()=>{
      const paths=['/openapi.json','/api/ui-config','/api/projects?page_size=200','/api/records/reviews?page_size=1','/api/accounts?page_size=1'];
      const [schema,config,projects,reviews,accounts]=await Promise.all(paths.map(async p=>(await(await fetch(p)).json())));
      return {version:schema.info.version,demo:config.demo_mode,projects:projects.total,chinese_descriptions:projects.items.filter(p=>p.description_zh).length,reviews:reviews.total,accounts:accounts.total};
    });
    check(facts.version==='0.6.0'&&facts.demo===false,'production v0.6.0');
    check(facts.projects>=145&&facts.chinese_descriptions>=145,'original project population and Chinese descriptions available');
    check(facts.reviews===0&&facts.accounts===1,'no synthetic reviews or test accounts in production');
    for(const lang of ['zh-CN','en']) {
      await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();
      for(const route of ['overview','projects','criteria','projects/1352188162','projects/1354769014','projects/1352188162/selection','projects/1352188162/support','members','account']) {
        await page.goto(origin+'#'+route);await ready();
        for(const width of [375,768,1024,1440]) {await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' '+route+' width '+width);}
        if(route==='projects/1354769014'&&lang==='zh-CN')check((await page.locator('.readme-reading').first().innerText()).includes('预览版'),'newly collected README Chinese summary visible');
        if(['overview','projects','criteria','projects/1354769014'].includes(route))await page.screenshot({path:'output/playwright/v060-production-'+lang+'-'+route.replaceAll('/','-')+'.png',fullPage:true});
      }
    }
    await page.evaluate(async()=>{const session=await(await fetch('/auth/me')).json();const r=await fetch('/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:'{}'});if(!r.ok)throw new Error('Test logout failed');});
    check(writes.every(p=>['/auth/password-login','/auth/logout'].includes(p)),'no production business mutations');
    check(errors.length===0,'no uncaught browser errors');
    return {facts,checks,errors,business_mutations:false,test_session_logged_out:true};
  } finally {await context.close();}
}
