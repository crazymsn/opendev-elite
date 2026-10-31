async sourcePage => {
 const context=await sourcePage.context().browser().newContext(),page=await context.newPage();
 const origin='http://127.0.0.1:8881/',checks=[],errors=[];
 const check=(ok,label)=>{if(!ok)throw new Error(label);checks.push(label);};
 page.on('pageerror',e=>errors.push(e.message));
 const ready=async()=>{await page.locator('#workspace').waitFor({state:'visible'});await page.locator('#main .page-heading').waitFor();};
 const fill=async values=>{for(const [name,value] of Object.entries(values)){const el=page.locator('#action-form [name="'+name+'"]');if(await el.evaluate(e=>e.tagName)==='SELECT')await el.selectOption(value);else await el.fill(value);}};
 const submit=async ok=>{const wait=page.waitForResponse(r=>r.url().endsWith('/api/actions/qualify')&&r.request().method()==='POST');await page.locator('#dialog-submit').click();const r=await wait;check(r.ok()===ok,'selection submission '+(ok?'accepted':'rejected'));if(ok){await page.locator('#action-dialog').waitFor({state:'hidden'});await ready();}else await page.locator('#dialog-error').filter({hasText:/./}).waitFor();};
 const open=async()=>{await page.locator('#main [data-action=qualify]').first().click();await page.locator('#action-dialog[open]').waitFor();};
 try {
  await page.goto(origin+'#team-login');await page.locator('#account-fields:not([disabled])').waitFor();
  await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-submit').click();await ready();
  check(await page.evaluate(async()=>(await(await fetch('/api/ui-config')).json()).demo_mode===true),'synthetic fixture before all writes');
  for(const [lang,id] of [['zh-CN',401],['en',402]]) {
   await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();
   await page.goto(origin+'#criteria');await ready();
   check(await page.locator('.policy-scope section').count()===4,lang+' four use-case paths');
   check(await page.locator('[data-policy=hold] details').count()===6,lang+' six hold reasons');
   check(await page.locator('[data-policy=exclude] details').count()===8,lang+' eight exclusion reasons');
   for(const summary of await page.locator('[data-policy=exclude] summary').all())await summary.click();
   const text=await page.locator('#main').innerText();
   check(text.includes('90')&&text.includes('6–12'),lang+' maintenance window and longer context explained');
   check(lang!=='en'||!/[\u4e00-\u9fff]/.test(text),'English policy has no untranslated UI text');
   for(const width of [375,768,1024,1440]){await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' policy width '+width);}
   await page.screenshot({path:'output/playwright/selection-v2-'+lang+'-policy.png',fullPage:true});
   await page.locator('[data-policy=exclude]').screenshot({path:'output/playwright/selection-v2-'+lang+'-exclusions.png'});
   await page.goto(origin+'#projects/'+id+'/selection');await ready();await open();
   check(await page.locator('[name="evidence.reason_code"]').inputValue()==='evidence_missing',lang+' hold reason defaults safely');
   check(!(await page.locator('[name="evidence.use_case"]').evaluate(e=>e.required)),lang+' hold does not force invented use case');
   await fill({outcome:'include'});
   check(await page.locator('[name="evidence.reason_code"]').inputValue()==='none',lang+' reason follows include outcome');
   check(await page.locator('[name="evidence.use_case"]').evaluate(e=>e.required&&e.validity.valueMissing),lang+' use-case required to include');
   await fill({outcome:'hold',maintenance:'uncertain','evidence.reason_code':'maintenance_uncertain',rationale:'SYNTHETIC missing public support evidence; reassess on substantive fix','evidence.scope':'SYNTHETIC user tool','evidence.maintenance':'SYNTHETIC timestamp only','evidence.sources':'fixture:source','evidence.maintenance_kind':'timestamp_only'});
   for(const width of [375,768,1024,1440]){await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' assessment width '+width);}
   await page.keyboard.press('Tab');check(await page.evaluate(()=>document.querySelector('#action-dialog').contains(document.activeElement)),lang+' dialog keyboard focus');
   await submit(true);check((await page.locator('#main').innerText()).includes('maintenance_uncertain'),lang+' hold reason retained in history');
   await open();await fill({category:'agent-applications',scope_fit:'yes',maintenance:'active',outcome:'include',rationale:'SYNTHETIC stable tool with substantive response','evidence.scope':'SYNTHETIC application callable by an Agent','evidence.use_case':'SYNTHETIC Agent reads fixture and emits expected record','evidence.delivery':'SYNTHETIC documented fixture CLI','evidence.access_risk':'SYNTHETIC authorized fixture, no external data or cost','evidence.maintenance':'SYNTHETIC meaningful response to a specific issue','evidence.sources':'fixture:source','evidence.maintenance_kind':'timestamp_only','evidence.maintenance_date':new Date(Date.now()-240*86400000).toISOString().slice(0,10)});
   await submit(false);
   check((await page.locator('#dialog-error').innerText()).includes(lang==='en'?'timestamps':'时间戳'),lang+' substantive-maintenance rejection is localized');
   await fill({'evidence.maintenance_kind':'response'});await submit(true);
   const listed=await page.evaluate(async pid=>{const r=await(await fetch('/api/projects?selection=include&page_size=200')).json();return r.items.some(p=>p.id===pid);},id);
   check(listed,lang+' stable active project appears in included list');
   check((await page.locator('#main').innerText()).includes('SYNTHETIC authorized fixture'),lang+' access and risk evidence displayed');
   await open();await fill({outcome:'exclude',scope_fit:'yes',maintenance:'active',rationale:'SYNTHETIC contradicting status test','evidence.scope':'SYNTHETIC scope','evidence.maintenance':'SYNTHETIC commits','evidence.sources':'fixture:source','evidence.reason_code':'abandoned','evidence.maintenance_kind':'commit','evidence.maintenance_date':new Date().toISOString().slice(0,10)});await submit(false);
   await fill({maintenance:'inactive'});await submit(true);
   await open();await page.keyboard.press('Escape');check(!(await page.locator('#action-dialog').isVisible()),lang+' Escape closes assessment');
  }
  // A slow catalog response must not reopen a form after logout.
  let held;const seen=new Promise(resolve=>{held=resolve;});let release;
  await page.route('**/api/criteria',async route=>{held();await new Promise(resolve=>release=resolve);await route.continue();});
  await page.locator('#main [data-action=qualify]').first().click();await seen;
  await page.locator('#logout').click();await page.locator('#login-view').waitFor({state:'visible'});const received=page.waitForResponse(r=>r.url().endsWith('/api/criteria'));release();
  await received;
  check(!(await page.locator('#action-dialog').isVisible()),'delayed policy cannot reopen form after logout');
  check(errors.length===0,'no uncaught JavaScript errors');return {checks,errors,synthetic_only:true};
 } finally {await context.close();}
}