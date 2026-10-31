async sourcePage=>{
 const context=await sourcePage.context().browser().newContext(),page=await context.newPage(),checks=[],errors=[],bad=[],posts=[];
 const origin='http://127.0.0.1:8882/';
 const check=(v,label)=>{if(!v)throw new Error(label);checks.push(label);};
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',r=>{if(r.status()>=400&&!r.url().endsWith('/auth/me'))bad.push([r.status(),r.url()]);});
 page.on('request',r=>{if(r.method()==='POST')posts.push(r.url().replace(origin,'/'));});
 const ready=async()=>{await page.locator('#workspace').waitFor({state:'visible'});await page.locator('#main[aria-busy=false]').waitFor();};
 const go=async route=>{await page.goto(origin+route);await ready();};
 try{
  await page.goto(origin+'#team-login');await page.locator('#account-fields:not([disabled])').waitFor();
  await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-submit').click();await ready();
  check(await page.evaluate(async()=>(await(await fetch('/api/ui-config')).json()).demo_mode===true),'synthetic fixture verified');
  for(const lang of ['zh-CN','en']){
   await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();
   await go('#developers');check(await page.locator('.developer-row').count()===15,lang+' bounded first page');
   const first=await page.locator('.developer-row .project-title').first().textContent();
   await page.locator('[data-do=page]').last().click();await ready();check(await page.locator('.developer-row').count()===4,lang+' second page');
   check(await page.locator('.developer-row .project-title').first().textContent()!==first,lang+' stable pagination');
   await page.locator('#developer-search [name=q]').fill('project-601');await page.locator('#developer-search [type=submit]').click();await ready();
   check(await page.locator('.developer-row').count()===1,lang+' search linked repository');
   check((await page.locator('.developer-counts').innerText()).includes('2'),lang+' cross-project aggregation');
   await page.locator('[data-clear-developers]').click();await ready();
   await page.locator('#developer-search [name=relationship]').selectOption('candidate');await page.locator('#developer-search [type=submit]').click();await ready();
   check(await page.locator('.developer-row').count()===1,lang+' candidate relationship filter');
   await page.locator('[data-clear-developers]').click();await ready();
   await page.locator('#developer-search [name=kind]').selectOption('Organization');await page.locator('#developer-search [type=submit]').click();await ready();
   check(await page.locator('.developer-row').count()===7,lang+' organizations separated from individuals');
   await page.locator('#developer-search [name=q]').fill('no-such-fixture');await page.locator('#developer-search [type=submit]').click();await ready();
   check(await page.locator('.developer-row').count()===0,lang+' empty search');
   await page.locator('[data-clear-developers]').click();await ready();
   check(await page.locator('#main script').count()===0,lang+' public bio safely rendered as text');
   check(!(await page.locator('#main').innerText()).includes('private-fixture@example.invalid'),lang+' email is not in directory');
   for(const width of [375,768,1024,1440]){await page.setViewportSize({width,height:900});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' directory width '+width);}
   await page.screenshot({path:'output/playwright/v070-'+lang+'-developers.png'});
   await page.locator('.developer-row .project-title').first().click();await ready();
   check(await page.locator('.developer-project').count()===2,lang+' two related projects');
   check(await page.locator('#main a[href^="javascript:"]').count()===0,lang+' unsafe public website not linked');
   check((await page.locator('#main').innerText()).includes(lang==='en'?'Contact declined':'已拒绝联系'),lang+' global refusal surfaced');
   await page.locator('#main a[href="#interviews/1"]').click();await ready();
   check((await page.locator('#main').innerText()).includes('SYNTHETIC roadmap evidence'),lang+' original interview is accessible');
   await go('#developers/601/interviews');check(await page.locator('.developer-project').count()===1,lang+' source-backed interview history');
   for(const kind of ['contacts','grants','followups']){await go('#developers/601/'+kind);await page.locator(`.developer-project a[href^="#${kind}/"]`).click();await ready();check(!(await page.locator('#main').innerText()).includes(lang==='en'?'Unable to load':'暂时无法读取'),lang+' '+kind+' source route');}
   await go('#developers/601');
   for(const width of [375,768,1024,1440]){await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' dossier width '+width);}
   await page.screenshot({path:'output/playwright/v070-'+lang+'-dossier.png',fullPage:true});
   if(lang==='en')check(!/[\u4e00-\u9fff]/.test(await page.locator('#main').innerText()),'English dossier has no untranslated labels');
   for(const [kind,target] of [['unassigned_projects','#projects/502'],['pending_selection','#projects/503/selection'],['awaiting_collection','#projects/504'],['pending_technical_reviews','#projects/505/reviews']]){
    await go('#inbox/'+kind);check(await page.locator('.inbox-item').count()>0,lang+' '+kind+' has work');
    await page.locator('.inbox-item a').first().click();await ready();check('#'+page.url().split('#')[1]===target,lang+' '+kind+' goes to right step');
   }
   await page.setViewportSize({width:1280,height:600});await page.locator('#navigation a[href="#account"]').focus();
   check(await page.locator('#navigation a[href="#account"]').evaluate(e=>{const a=e.getBoundingClientRect(),b=e.parentElement.getBoundingClientRect();return a.top>=b.top&&a.bottom<=b.bottom;}),lang+' bottom navigation keyboard accessible');
  }
  await page.locator('#logout').click();await page.locator('#login-view').waitFor({state:'visible'});
  for(const [role,kinds] of [['reviewer',['followups']],['operator',['contacts','interviews','followups']],['approver',['interviews','grants']],['disburser',['grants']],['auditor',['grants','followups']]]){
   await page.evaluate(async role=>{const r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:'synthetic-directory-'+role+'-fixture-only'})});if(!r.ok)throw new Error('fixture login failed');},role);
   await page.reload();await ready();
   await go('#developers/601');
   const tabs=await page.locator('#main .tabs a').evaluateAll(a=>a.map(e=>e.getAttribute('href').split('/').pop()));
   check(JSON.stringify(tabs)===JSON.stringify(['projects',...kinds]),role+' authorized sections only');
   for(const k of kinds)await go('#developers/601/'+k);
   if(role==='approver'){await page.locator('#main .tabs a[href="#developers/601/interviews"]').click();await ready();await page.locator('.developer-project a[href="#interviews/1"]').click();await ready();check((await page.locator('#main').innerText()).includes('SYNTHETIC roadmap evidence'),'approver can open source interview without contacts permission');}
   await page.locator('#logout').click();await page.locator('#login-view').waitFor({state:'visible'});
  }
  await page.evaluate(async()=>{await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:'synthetic-directory-operator-fixture-only'})});});await page.reload();await ready();await go('#developers');
  let seen,release;const arrived=new Promise(r=>seen=r);
  await page.route('**/api/developers/601?*',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});});
  await page.locator('.developer-row .project-title').first().click();await arrived;
  await page.locator('#logout').click();await page.locator('#login-view').waitFor({state:'visible'});
  const response=page.waitForResponse(r=>r.url().includes('/api/developers/601?'));release();await response;
  check(await page.locator('#main').innerText()==='','late dossier response cannot repopulate signed-out workspace');
  check(!posts.some(p=>p.startsWith('/api/')),'no business writes during browser validation');
  check(errors.length===0,'no uncaught JavaScript errors');check(bad.length===0,'no unexpected HTTP errors');
  return {checks,errors,bad,synthetic_only:true};
 }catch(e){throw new Error(e.message+'; URL='+page.url()+'; recent='+checks.slice(-3).join(' / ')+'; errors='+JSON.stringify(errors)+'; http='+JSON.stringify(bad));}finally{await context.close();}
}
