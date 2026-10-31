async sourcePage => {
  const context=await sourcePage.context().browser().newContext(), page=await context.newPage();
  const origin='http://127.0.0.1:8878/', checks=[], errors=[], requests=[];
  const check=(v,label)=>{if(!v)throw new Error(label);checks.push(label);};
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()!=='GET')requests.push({method:r.method(),path:r.url().replace(origin,'/')});});
  try {
    await page.goto(origin+'#team-login');
    await page.locator('#account-fields:not([disabled])').waitFor();
    await page.locator('#token-login-details').evaluate(el=>el.open=true);
    await page.locator('#access-token').fill('synthetic-dossier-reader-local-copy-only');
    await page.locator('#login-form button').click();
    await page.locator('#workspace').waitFor({state:'visible'});
    const inventory=await page.evaluate(async()=>{
      const projects=await(await fetch('/api/projects?page_size=200')).json();
      const result={total:projects.total,descriptions:0,summaries:[],missingReadme:0,failures:[]};
      for(const project of projects.items) {
        const r=await fetch('/api/projects/'+project.id); if(!r.ok)throw new Error('Dossier fetch failed');
        const d=await r.json(), f=d.dossier.fields;
        if(f.description.languages['zh-CN'].status==='current'&&/[\u3400-\u9fff]/.test(f.description.languages['zh-CN'].translation.content))result.descriptions++;
        else result.failures.push({id:project.id,field:'description'});
        if(!f.readme.source.text)result.missingReadme++;
        else if(f.readme_summary.languages['zh-CN'].status==='current')result.summaries.push({id:project.id,name:project.full_name,excerpt:f.readme.source.truncated});
        else result.failures.push({id:project.id,field:'summary'});
      }
      return result;
    });
    check(inventory.total===145&&inventory.descriptions===145,'145 current Chinese descriptions served');
    check(inventory.summaries.length===14&&inventory.missingReadme===131,'14 captured README summaries; 131 README sources pending');
    check(inventory.failures.length===0,'all available translated fields are current');
    for(const language of ['zh-CN','en']) {
      await page.locator('#workspace [data-language-select]').selectOption(language);
      await page.waitForFunction(lang=>document.documentElement.lang===lang,language);
      for(const item of inventory.summaries) {
        await page.goto(origin+'#projects/'+item.id);
        await page.locator('.dossier-description').waitFor();
        if(language==='zh-CN')check(await page.locator('.reading-label').allTextContents().then(v=>v.some(t=>t.includes('中文阅读摘要'))),item.name+' Chinese summary visible');
        if(item.excerpt)check(await page.locator('.translation-status').allTextContents().then(v=>v.some(t=>language==='en'?t.includes('captured README excerpt'):t.includes('采集节选'))),language+' excerpt explicitly labeled');
        check(await page.locator('.readme-original').count()===1,language+' '+item.name+' original available');
        for(const width of [375,768,1024,1440]) {
          await page.setViewportSize({width,height:1000});
          check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),language+' '+item.name+' width '+width);
        }
        if(item.name==='tamnd/molla'||item.name==='containers/ramalama')await page.screenshot({path:'output/playwright/v06-dossier-'+language+'-'+item.id+'.png',fullPage:true});
      }
      await page.goto(origin+'#projects');await page.locator('.repository-table').waitFor();
      check(await page.locator('.repository-table tbody tr').count()>0,language+' actual candidate list rendered');
      await page.screenshot({path:'output/playwright/v06-real-candidates-'+language+'.png',fullPage:true});
    }
    check(errors.length===0,'no uncaught browser errors');
    check(requests.every(r=>r.path==='/auth/login'),'no dossier or business writes during QA');
    return {checks,errors,inventory,working_copy_only:true,production_modified:false};
  } finally {await context.close();}
}
