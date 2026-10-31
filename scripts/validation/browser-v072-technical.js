async sourcePage=>{
 const context=await sourcePage.context().browser().newContext(),page=await context.newPage(),checks=[],errors=[],posts=[];
 const origin='http://127.0.0.1:8884/';
 const check=(value,label)=>{if(!value)throw new Error(label);checks.push(label);};
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url().replace(origin,'/'));});
 const ready=async()=>{await page.locator('#workspace').waitFor({state:'visible'});await page.locator('#main[aria-busy=false]').waitFor();};
 const go=async id=>{await page.goto(origin+'#projects/'+id+'/evidence');await ready();};
 try{
  await page.goto(origin+'#team-login');await page.locator('#account-fields:not([disabled])').waitFor();await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-submit').click();await ready();
  check(await page.evaluate(async()=>(await(await fetch('/api/ui-config')).json()).demo_mode===true),'isolated synthetic fixture');
  for(const lang of ['zh-CN','en']){
   await page.locator('#workspace [data-language-select]').selectOption(lang);await page.waitForFunction(l=>document.documentElement.lang===l,lang);await ready();await go(711);
   const panel=page.locator('[data-technical-evidence]');
   check(await panel.locator('.technical-file').count()===6,lang+' six bounded file samples');
   check((await panel.innerText()).includes(lang==='en'?'not an approval':'尚不代表审核通过'),lang+' evidence is not approval');
   check((await panel.innerText()).includes(lang==='en'?'truncated':'目录树被截断'),lang+' tree truncation visible');
   check((await panel.innerText()).includes(lang==='en'?'upstream':'上游'),lang+' fork contribution reminder');
   await panel.locator('.technical-file summary').first().focus();await page.keyboard.press('Enter');
   check(await panel.locator('.technical-file[open]').count()===1,lang+' keyboard opens source sample');
   check(await panel.locator('.technical-code ol').first().locator('li').count()<=300,lang+' code line limit');
   check((await panel.locator('.technical-code').first().textContent()).length<=13000,lang+' bounded text preview');
   check(await panel.locator('[data-preview-truncated]').first().isVisible(),lang+' preview truncation visible');
   check((await panel.locator('.technical-file[open]').innerText()).includes(lang==='en'?'matches the captured blob SHA':'文件 SHA 一致'),lang+' checked file integrity');
   check((await panel.locator('.technical-file[open] a').getAttribute('href')).includes('/blob/'+'a'.repeat(40)+'/src/server.jl'),lang+' immutable source link');
   for(const section of ['commits','releases','discussions'])await panel.locator('[data-maintenance='+section+']>summary').click();
   check((await panel.locator('[data-maintenance=commits]').innerText()).includes(lang==='en'?'additional pages':'后续分页'),lang+' partial maintenance sample');
   check((await panel.locator('[data-maintenance=discussions]').innerText()).includes(lang==='en'?'do not prove a maintainer response':'不证明维护者已响应'),lang+' issue updates not response claims');
   check(await panel.locator('script,img,iframe,a[href^="javascript:"]').count()===0,lang+' untrusted content remains inert');
   check(await page.evaluate(()=>typeof window.evidenceXss==='undefined'),lang+' no evidence script execution');
   for(const width of [375,768,1024,1440]){await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),lang+' evidence width '+width);}
   await page.evaluate(()=>window.scrollTo({top:document.querySelector('[data-technical-evidence]').getBoundingClientRect().top+window.scrollY-85}));
   await page.screenshot({path:'output/playwright/v072-technical-'+lang+'.png'});
   await go(712);check((await page.locator('[data-technical-evidence]').innerText()).includes(lang==='en'?'Differs from the current commit':'与当前版本不一致'),lang+' stale snapshot warning');
   await page.locator('.technical-file>summary').click();check((await page.locator('.technical-file').innerText()).includes(lang==='en'?'not valid UTF-8':'不是有效的 UTF-8'),lang+' invalid legacy content state');
   await go(713);check((await page.locator('.technical-evidence').innerText()).includes(lang==='en'?'No evidence has been captured':'尚未采集到证据'),lang+' missing snapshot state');
  }
  await page.locator('#logout').click();await page.locator('#login-view').waitFor({state:'visible'});
  check(await page.locator('#main [data-technical-evidence]').count()===0,'logout clears evidence');
  check(posts.every(p=>['/auth/password-login','/auth/logout'].includes(p)),'no workflow writes');check(errors.length===0,'no uncaught JavaScript errors');
  return {checks,errors,synthetic_only:true,business_mutations:false,test_session_logged_out:true};
 }finally{await context.close();}
}
