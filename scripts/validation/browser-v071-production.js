async sourcePage=>{
 const context=await sourcePage.context().browser().newContext(),page=await context.newPage(),checks=[],errors=[],posts=[];
 const origin='http://127.0.0.1:8765/';
 const check=(v,label)=>{if(!v)throw new Error(label);checks.push(label);};
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url().replace(origin,'/'));});
 const ready=async()=>{await page.locator('#workspace').waitFor({state:'visible'});await page.locator('#main .page-heading').waitFor();};
 try{
  await page.goto(origin+'#team-login');await page.locator('#account-fields:not([disabled])').waitFor();await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-submit').click();await ready();
  const facts=await page.evaluate(async()=>{const get=async p=>await(await fetch(p)).json();const [schema,policy,ui,projects,assessments]=await Promise.all(['/openapi.json','/api/criteria','/api/ui-config','/api/projects?page_size=1','/api/records/qualifications?page_size=1'].map(get));return {version:schema.info.version,policy:policy.version,demo:ui.demo_mode,projects:projects.total,first_project:projects.items[0].id,assessments:assessments.total};});
  check(facts.version==='0.7.1'&&facts.policy==='sop-2026-09-v2'&&!facts.demo,'live version and policy upgraded');check(facts.projects>=145&&facts.assessments===0,'original candidates retained; no automatic assessments');
  for(const lang of ['zh-CN','en']){
   await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();await page.goto(origin+'#criteria');await ready();
   check(await page.locator('[data-policy=exclude] details').count()===8,lang+' live exclusion rules available');
   for(const width of [375,768,1024,1440]){await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' live criteria width '+width);}
   await page.screenshot({path:'output/playwright/v071-criteria-production-'+lang+'.png'});
   await page.goto(origin+'#projects/'+facts.first_project+'/selection');await ready();await page.locator('#main [data-action=qualify]').first().click();await page.locator('#action-dialog[open]').waitFor();
   check(await page.locator('[name="evidence.access_risk"]').count()===1,lang+' live new assessment evidence field');await page.keyboard.press('Escape');
  }
  await page.locator('#logout').click();await page.locator('#login-view').waitFor({state:'visible'});
  check(posts.every(p=>['/auth/password-login','/auth/logout'].includes(p)),'no production business writes');check(errors.length===0,'no uncaught JavaScript errors');
  return {facts,checks,errors,business_mutations:false,test_session_logged_out:true};
 }finally{await context.close();}
}