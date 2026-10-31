async sourcePage=>{
 const context=await sourcePage.context().browser().newContext(),page=await context.newPage(),checks=[],errors=[],posts=[];
 const origin='http://127.0.0.1:8765/';
 const check=(value,label)=>{if(!value)throw new Error(label);checks.push(label);};
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url().replace(origin,'/'));});
 const ready=async()=>{await page.locator('#workspace').waitFor({state:'visible'});await page.locator('#main[aria-busy=false]').waitFor();};
 try{
  await page.goto(origin+'#team-login');await page.locator('#account-fields:not([disabled])').waitFor();await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-submit').click();await ready();
  const facts=await page.evaluate(async()=>{const get=async p=>await(await fetch(p)).json();return {version:(await get('/openapi.json')).info.version,demo:(await get('/api/ui-config')).demo_mode,assessments:(await get('/api/records/qualifications?page_size=1')).total};});
  check(facts.version==='0.7.2'&&!facts.demo,'installed production version');check(facts.assessments===0,'no generated qualifications');
  for(const lang of ['zh-CN','en']){
   await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();
   for(const id of [1271429585,1352750887,1309918135]){
    await page.goto(origin+'#projects/'+id+'/evidence');await ready();const panel=page.locator('[data-technical-evidence]');
    const n=await panel.locator('.technical-file').count();check(n>0&&n<=6,lang+' real snapshot '+id);
    await panel.locator('.technical-file summary').first().click();check(await panel.locator('.technical-code').first().isVisible(),lang+' real source preview '+id);
    check((await panel.innerText()).includes(lang==='en'?'not an approval':'尚不代表审核通过'),lang+' evidence boundary '+id);
   }
   for(const width of [375,768,1024,1440]){await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' real evidence width '+width);}
   await page.evaluate(()=>window.scrollTo({top:document.querySelector('[data-technical-evidence]').getBoundingClientRect().top+window.scrollY-85}));
   await page.screenshot({path:'output/playwright/v072-production-'+lang+'.png'});
  }
  await page.locator('#logout').click();await page.locator('#login-view').waitFor({state:'visible'});check(posts.every(p=>['/auth/password-login','/auth/logout'].includes(p)),'no production workflow writes');check(errors.length===0,'no uncaught JavaScript errors');
  return {facts,checks,errors,business_mutations:false,test_session_logged_out:true};
 }finally{await context.close();}
}
