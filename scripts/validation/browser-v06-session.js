async page => {
  const origin='http://127.0.0.1:8877/';
  const checks=[],errors=[];
  const check=(condition,message)=>{if(!condition)throw new Error(message);checks.push(message);};
  const context=await page.context().browser().newContext();
  const target=await context.newPage();
  target.on('pageerror',error=>errors.push(error.message));
  try {
    for(const language of ['zh-CN','en']) {
      await target.goto(origin+'#team-login');
      await target.locator('#account-fields:not([disabled])').waitFor();
      await target.locator('#login-view [data-language-select]').selectOption(language);
      await target.waitForFunction(value=>document.documentElement.lang===value,language);
      await target.locator('#account-fields:not([disabled])').waitFor();
      check(await target.locator('#admin-setup-tab,#account-confirm,[data-auth-mode]').count()===0,language+' no setup or registration UI');
      check((await target.locator('#account-submit').innerText())===(language==='en'?'Sign in':'登录工作台'),language+' localized login button');
      await target.locator('#account-username').fill('OpenDev');
      await target.locator('#account-password').fill('incorrect-synthetic-password');
      await target.locator('#account-submit').click();
      await target.locator('#account-error').filter({hasText:language==='en'?'Incorrect':'用户名或密码不正确'}).waitFor();
      check(await target.locator('#account-password').inputValue()==='',language+' failed login clears password');
      await target.locator('#account-password').fill('123456');
      await target.locator('#account-submit').click();
      await target.locator('#workspace').waitFor({state:'visible'});
      check(await target.evaluate(async()=>{const r=await fetch('/api/ui-config');return(await r.json()).demo_mode===true;}),'synthetic environment confirmed');
      await target.goto(origin+'#projects/301');
      await target.locator('.dossier-description').waitFor();
      for(const width of [375,768,1024,1440]) {
        await target.setViewportSize({width,height:900});
        check(await target.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),language+' updated header fits '+width);
      }
      await target.screenshot({path:'output/playwright/v06-'+language+'-header-final.png',fullPage:true});
      const result=await target.evaluate(()=>{
        const data='项目代码：保持原文 <tag> & test';
        const value=OpenDevI18n.html(['登录工作台：',''],data);
        return {preserved:value.endsWith(data),language:localStorage.getItem('opendev.language'),keys:Object.keys(localStorage)};
      });
      check(result.preserved,language+' localization preserves runtime values');
      check(result.keys.every(key=>key==='opendev.language'),language+' only UI preference stored');
      await target.locator('[data-translation-field=description]').click();
      await target.locator('#translation-dialog [name=content]').fill('草稿应该在会话结束时清除');
      check(await target.evaluate(async()=>{
        const csrf=await(await fetch('/auth/csrf')).json();
        return(await fetch('/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrf_token},body:'{}'})).ok;
      }),language+' revoke this synthetic browser session');
      await target.locator('#translation-dialog button[type=submit]').click();
      await target.locator('#workspace').waitFor({state:'hidden'});
      check(await target.locator('#translation-dialog[open]').count()===0,language+' expired session closes editor');
      check(await target.locator('#translation-dialog textarea').count()===0,language+' expired session clears source and draft');
      check(await target.locator('#main').innerText()==='',language+' expired session clears workspace');
      check(await target.locator('#account-password').inputValue()==='',language+' expired session clears password');
      await target.reload();
      check(await target.locator('html').getAttribute('lang')===language,language+' preference survives reload');
      await target.locator('#account-fields:not([disabled])').waitFor();
      await target.locator('#team-login').screenshot({path:'output/playwright/v06-'+language+'-login-final.png'});
    }
    check(errors.length===0,'no uncaught browser errors');
    return{checks,errors,synthetic_only:true};
  }finally{await context.close();}
}
