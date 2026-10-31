async sourcePage => {
  const browser=sourcePage.context().browser(), checks=[], errors=[], failures=[];
  const origin='http://127.0.0.1:8877/';
  const adminContext=await browser.newContext(), admin=await adminContext.newPage();
  const members=[], stamp=Date.now(), password='synthetic role validation password';
  const check=(v,label)=>{if(!v)throw new Error(label);checks.push(label);};
  const login=async(page,username,secret)=>{
    await page.goto(origin+'#team-login');
    await page.locator('#account-fields:not([disabled])').waitFor();
    await page.locator('#account-username').fill(username);
    await page.locator('#account-password').fill(secret);
    await page.locator('#account-submit').click();
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('#main .page-heading').waitFor();
  };
  const request=async(path,body)=>admin.evaluate(async({path,body})=>{
    const session=await(await fetch('/auth/me')).json();
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token,'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(body)});
    if(!r.ok)throw new Error('Fixture account request failed '+r.status);
    return r.json();
  },{path,body});
  try {
    await login(admin,'OpenDev','123456');
    check(await admin.evaluate(async()=> (await(await fetch('/api/ui-config')).json()).demo_mode===true),'dedicated synthetic environment');
    for(const role of ['reviewer','operator','approver','disburser','auditor']) {
      const account=await request('/api/accounts',{username:'role-'+role+'-'+stamp,display_name:'Synthetic '+role,password,roles:[role]});
      members.push(account);
      const context=await browser.newContext(), page=await context.newPage();
      page.on('pageerror',e=>errors.push({role,error:e.message}));
      page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)failures.push({role,url:r.url(),status:r.status()});});
      try {
        await login(page,account.username,password);
        for(const language of ['zh-CN','en']) {
          await page.locator('#workspace [data-language-select]').selectOption(language);
          await page.waitForFunction(lang=>document.documentElement.lang===lang,language);
          await page.locator('#main .page-heading').waitFor();
          const routes=['projects/101/selection','projects/101/reviews','projects/101/support'];
          if(['approver','disburser','auditor'].includes(role))routes.push('projects/101/decisions');
          if(role==='operator')routes.push('projects/101/contacts');
          for(const route of routes) {
            await page.goto(origin+'#'+route); await page.locator('#main .page-heading').waitFor();
            check(await page.locator('.tabs .active').getAttribute('href')==='#'+route,role+' '+language+' '+route+' available');
            check(await page.locator('.next-step-panel').innerText().then(t=>language==='en'?t.includes('Follow usage and milestones'):t.includes('按计划跟进')),role+' '+language+' issued grant follows schedule');
            if(route.endsWith('reviews'))check(await page.locator('.evidence-fields').count()>=2,role+' '+language+' structured reviews');
            if(route.endsWith('support'))check(await page.locator('#main table').count()===(role==='auditor'?2:1),role+' '+language+' support records follow permissions');
            for(const width of [375,768,1024,1440]) {
              await page.setViewportSize({width,height:1000});
              check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),role+' '+language+' '+route+' width '+width);
            }
            if(role==='auditor'&&route.endsWith('support'))await page.screenshot({path:'output/playwright/v06-'+language+'-support-auditor.png',fullPage:true});
            if(role==='reviewer'&&route.endsWith('reviews'))await page.screenshot({path:'output/playwright/v06-'+language+'-structured-reviews.png',fullPage:true});
          }
        }
      } finally {await context.close();}
    }
    check(errors.length===0,'no uncaught browser errors');
    check(failures.length===0,'no forbidden or failed API requests in permitted views');
    return {checks,errors,failures,synthetic_only:true};
  } finally {
    for(const account of members)await request('/api/accounts/'+account.id,{status:'disabled',roles:account.roles,auth_version:account.auth_version});
    await adminContext.close();
  }
}
