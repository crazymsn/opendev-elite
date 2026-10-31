async sourcePage => {
  const browser=sourcePage.context().browser(), origin='http://127.0.0.1:8883/';
  const context=await browser.newContext(), page=await context.newPage();
  const financeContext=await browser.newContext(), finance=await financeContext.newPage();
  const checks=[],errors=[], records=[];
  const check=(v,label)=>{if(!v)throw new Error(label);checks.push(label);};
  page.on('pageerror',e=>errors.push(e.message));finance.on('pageerror',e=>errors.push(e.message));
  const ready=async target=>{await target.locator('#workspace').waitFor({state:'visible'});await target.locator('#main .page-heading').waitFor();};
  const go=async(target,route)=>{await target.goto(origin+'#'+route);await ready(target);};
  const fill=async(target,values)=>{
    for(const [name,value] of Object.entries(values)) {
      const input=target.locator('#action-form [name="'+name+'"]');
      if(typeof value==='boolean')await input.setChecked(value);
      else if(await input.evaluate(e=>e.tagName)==='SELECT')await input.selectOption(value);
      else await input.fill(String(value));
    }
  };
  const open=async(target,action)=>{await target.locator('#main [data-action="'+action+'"]').first().click();await target.locator('#action-dialog[open]').waitFor();};
  const submit=async(target,success=true)=>{
    const response=target.waitForResponse(r=>r.request().method()==='POST'&&(r.url().includes('/api/actions/')||r.url().includes('/api/credits/')));
    await target.locator('#dialog-submit').click();
    const r=await response,body=await r.json();
    if(success){check(r.ok(),'workflow write '+r.url().split('/').slice(-2).join('/'));await target.locator('#action-dialog').waitFor({state:'hidden'});await ready(target);}
    else {check(!r.ok(),'incomplete evidence rejected');await target.locator('#dialog-error').filter({hasText:/./}).waitFor();}
    return body;
  };
  const date=days=>new Date(Date.now()+days*86400000).toISOString().slice(0,10);
  try {
    await page.goto(origin+'#team-login');await page.locator('#account-fields:not([disabled])').waitFor();
    await page.locator('#account-username').fill('OpenDev');await page.locator('#account-password').fill('123456');await page.locator('#account-submit').click();await ready(page);
    check(await page.evaluate(async()=> (await(await fetch('/api/ui-config')).json()).demo_mode===true),'dedicated offline SOP fixture');
    await finance.goto(origin+'#team-login');await finance.locator('#account-fields:not([disabled])').waitFor();
    await finance.locator('#token-login-details').evaluate(e=>e.open=true);
    await finance.locator('#access-token').fill('offline-sop-disburser-fixture-only');await finance.locator('#login-form button').click();await ready(finance);
    for(const [language,pid] of [['zh-CN',401],['en',402]]) {
      for(const target of [page,finance]) {await target.locator('#workspace [data-language-select]').selectOption(language);await target.waitForFunction(l=>document.documentElement.lang===l,language);await ready(target);}
      await go(page,'projects/'+pid);
      await open(page,'assign');await fill(page,{owner:'SYNTHETIC browser reviewer',audience:'human'});await submit(page);
      await open(page,'qualify');await fill(page,{category:'ai-coding',scope_fit:'yes',maintenance:'uncertain',outcome:'include',rationale:'SYNTHETIC local fixture only','evidence.scope':'Fixture coding task','evidence.maintenance':'Fixture commits','evidence.sources':'fixture:repository','evidence.use_case':'SYNTHETIC: user completes a coding task','evidence.delivery':'SYNTHETIC: fixture CLI entry','evidence.access_risk':'SYNTHETIC: fixture permission; no external writes','evidence.maintenance_kind':'commit','evidence.maintenance_date':new Date().toISOString().slice(0,10)});
      await submit(page,false);
      await fill(page,{maintenance:'active'});await submit(page);
      await open(page,'review-one');await fill(page,{decision:'pass',rationale:'SYNTHETIC code inspection','evidence.structure':'fixture:source inspected','evidence.maintenance':'fixture:maintained commits','evidence.documentation':'fixture:documented setup'});
      for(const width of [375,768,1024,1440]) {await page.setViewportSize({width,height:1000});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),language+' technical form width '+width);}
      await page.screenshot({path:'output/playwright/selection-v2-sop-'+language+'-technical-form.png'});await submit(page);
      await open(page,'experience-create');await fill(page,{audience:'human','spec.scenario':'SYNTHETIC complete a fixture coding task','spec.success_criteria':'fixture output matches expectation','spec.environment':'offline browser fixture','spec.input':'fixture task','spec.budget.seconds':'60'});
      const experience=await submit(page);
      await go(page,'projects/'+pid+'/experiences');await open(page,'experience-result');
      await fill(page,{'result.success':'true','result.executor':'SYNTHETIC human tester','result.environment':'offline fixture','result.steps':'inspect fixture\ncompare result','result.actual_result':'SYNTHETIC expected result','result.artifacts':'fixture:experience-evidence'});await submit(page);
      await open(page,'review-two');await fill(page,{decision:'pass',rationale:'SYNTHETIC product judgement','evidence.real_problem':'fixture:developer task','evidence.usability':'fixture:completed scenario','evidence.differentiation':'fixture:comparison','evidence.potential':'fixture:next milestone'});
      check((await page.locator('[name="evidence.experience_ids"]').inputValue()).includes(experience.id),language+' real experience automatically linked');await submit(page);
      await go(page,'projects/'+pid+'/contacts');await open(page,'maintainer-confirm');await fill(page,{evidence:'SYNTHETIC maintainer relationship fixture'});await submit(page);
      await open(page,'contact-draft');await fill(page,{channel:'synthetic-only',draft:'SYNTHETIC DRAFT; NEVER SEND'});const contact=await submit(page);
      for(const status of ['sent','replied']) {await open(page,'contact-status');await fill(page,{status,evidence:'SYNTHETIC communication fixture; no real message'});await submit(page);}
      await open(page,'interview');await page.locator('[name=profile][value=engineer]').check();
      await fill(page,{'notes.users':'SYNTHETIC target users','notes.commitment':'SYNTHETIC continued work','notes.roadmap':'SYNTHETIC roadmap','notes.credit_need':'SYNTHETIC 1000 cents','notes.milestone':'SYNTHETIC next release','notes.consent':true});const interview=await submit(page);
      await go(page,'projects/'+pid+'/decisions');await open(page,'decision');
      await fill(page,{interview_id:interview.id,outcome:'approve',rationale:'SYNTHETIC approval fixture','terms.account':'fixture-'+pid+'@example.invalid','terms.amount':'1000','terms.event_slug':'offline-browser','terms.expires_at':date(90),'terms.review_dates':date(7),'terms.identity_evidence':'SYNTHETIC account verified in interview','terms.purpose':'SYNTHETIC inference task'});
      await page.screenshot({path:'output/playwright/selection-v2-sop-'+language+'-approval-form.png'});const decision=await submit(page);
      await go(finance,'projects/'+pid+'/support');
      check(await finance.locator('#main .evidence-fields').count()===0,language+' financial support view renders ledger');
      await finance.locator('[data-do=credits-plan]').click();await finance.locator('#confirm-credit').check();
      const invitation=await submit(finance);check(invitation.status==='awaiting_claim',language+' invitation is not receipt');
      for(const expected of ['awaiting_claim','issued']) {
        const response=finance.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/reconcile'));
        await finance.locator('[data-do=credits-reconcile]').click();
        const value=await(await response).json();check(value.status===expected,language+' reconciliation '+expected);await ready(finance);
      }
      await go(page,'projects/'+pid+'/support');
      check((await page.locator('.next-step-panel').innerText()).includes(language==='en'?'Follow usage':'按计划跟进'),language+' next step becomes follow-up');
      await open(page,'followup');await fill(page,{'result.usage':'SYNTHETIC 125 cents used','result.milestone':'SYNTHETIC milestone reached','result.evidence':'fixture:review','result.decision':'continue','result.next_action':'SYNTHETIC second review'});
      await submit(page,false);await fill(page,{'result.next_review_at':date(14)});await submit(page);
      await open(page,'followup');await fill(page,{'result.usage':'SYNTHETIC final usage','result.milestone':'SYNTHETIC cycle complete','result.evidence':'fixture:final','result.decision':'end','result.next_action':'SYNTHETIC close cycle'});await submit(page);
      check((await page.locator('.next-step-panel').innerText()).includes(language==='en'?'cycle is complete':'本轮赞助已结束'),language+' closed cycle shown');
      await page.screenshot({path:'output/playwright/selection-v2-sop-'+language+'-completed.png',fullPage:true});
      const detail=await page.evaluate(async id=>(await(await fetch('/api/projects/'+id)).json()),pid);
      check(detail.project.stage==='closed'&&detail.readiness.next_step==='cycle-closed',language+' server and UI agree');
      records.push({language,project_id:pid,experience:experience.id,contact:contact.id,interview:interview.id,grant:decision.grant_id});
    }
    check(errors.length===0,'no uncaught browser errors');
    return {checks,errors,records,synthetic_only:true,real_messages:0,real_credits_requests:0};
  } finally {await context.close();await financeContext.close();}
}
