async sourcePage=>{
 const context=await sourcePage.context().browser().newContext(),page=await context.newPage(),checks=[],errors=[],posts=[];
 const origin='http://127.0.0.1:8765/';
 const check=(v,label)=>{if(!v)throw new Error(label);checks.push(label);};
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url().replace(origin,'/'));});
 const ready=async()=>{await page.locator('#workspace').waitFor({state:'visible'});await page.locator('#main[aria-busy=false]').waitFor();};
 try{
  await page.goto(origin+'#team-login');await page.locator('#account-fields:not([disabled])').waitFor();
  await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-submit').click();await ready();
  const facts=await page.evaluate(async()=>{
   const get=async path=>{const r=await fetch(path);if(!r.ok)throw new Error(path+' failed');return r.json();};
   const [schema,policy,ui,projects,people,inbox]=await Promise.all(['/openapi.json','/api/criteria','/api/ui-config','/api/projects?page_size=1','/api/developers?page_size=1','/api/inbox?kind=unassigned_projects'].map(get));
   return {version:schema.info.version,policy:policy.version,demo:ui.demo_mode,projects:projects.total,first_project:projects.items[0].id,developers_and_organizations:people.total,unassigned:inbox.total};
  });
  check(facts.version==='0.7.0'&&facts.policy==='sop-2026-09-v2'&&!facts.demo,'installed version and policy');
  check(facts.projects>=145&&facts.developers_and_organizations>=222,'original projects and collected GitHub accounts preserved');
  for(const lang of ['zh-CN','en']){
   await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();
   await page.goto(origin+'#criteria');await ready();check(await page.locator('[data-policy=exclude] details').count()===8,lang+' selection criteria retained');
   await page.goto(origin+'#developers');await ready();check(await page.locator('.developer-row').count()===15,lang+' real directory first page');
   for(const width of [375,768,1024,1440]){await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' real directory width '+width);}
   await page.screenshot({path:'output/playwright/v070-production-'+lang+'.png'});
   await page.locator('.developer-row .project-title').first().click();await ready();check(await page.locator('.developer-project').count()>0,lang+' source-backed real relationships');
   await page.goto(origin+'#inbox/unassigned_projects');await ready();check(await page.locator('.inbox-item').count()>0,lang+' early inbox connected');
   await page.locator('.inbox-item a').first().click();await ready();check(await page.locator('#main [data-action=assign]').count()===1,lang+' owner assignment entry available');
   await page.goto(origin+'#projects/'+facts.first_project+'/selection');await ready();await page.locator('#main [data-action=qualify]').first().click();await page.locator('#action-dialog[open]').waitFor();
   check(await page.locator('[name="evidence.maintenance_kind"]').count()===1&&await page.locator('[name="evidence.access_risk"]').count()===1,lang+' current evidence fields retained');await page.keyboard.press('Escape');
  }
  await page.locator('#logout').click();await page.locator('#login-view').waitFor({state:'visible'});
  check(posts.every(p=>['/auth/password-login','/auth/logout'].includes(p)),'no production business writes');check(errors.length===0,'no uncaught JavaScript errors');
  return {facts,checks,errors,business_mutations:false,test_session_logged_out:true};
 }finally{await context.close();}
}
