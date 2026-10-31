async page => {
  if(!page.url().startsWith('http://127.0.0.1:8886/'))throw Error('Synthetic fixture required');
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.reload();
  const cases=[['overview','.stats'],['projects','.candidate-filters'],['criteria','.rubric-row'],['projects/801/selection','.quality-result']];
  for(const language of ['zh-CN','en']) {
    await page.locator('#workspace [data-language-select]').selectOption(language);
    await page.waitForFunction(lang=>document.documentElement.lang===lang,language);
    for(const width of [375,768,1024,1440]) {
      await page.setViewportSize({width,height:width===375?812:1000});
      for(const [route,ready] of cases) {
        await page.goto('http://127.0.0.1:8886/#'+route);
        await page.locator(ready).first().waitFor();
        if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error(`Overflow ${language} ${width} ${route}`);
        if((width===375||width===1440)&&['overview','criteria','projects'].includes(route))await page.screenshot({path:`output/playwright/v080-${route}-${language}-${width}.png`,fullPage:false});
      }
    }
  }
  await page.locator('#logout').click();
  await page.locator('#account-submit').waitFor();
  for(const language of ['zh-CN','en']) {
    await page.locator('#login-view [data-language-select]').selectOption(language);
    await page.waitForFunction(lang=>document.documentElement.lang===lang,language);
    await page.locator('#account-submit:not(:disabled)').waitFor();
    for(const width of [375,768,1440]) {
      await page.setViewportSize({width,height:width===375?812:1000});
      if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error(`Login overflow ${language} ${width}`);
      const bounds=await page.locator('#team-login').boundingBox();
      if(bounds.y<0||bounds.y+bounds.height>(width===375?812:1000))throw Error(`Login does not fit ${language} ${width}`);
      await page.screenshot({path:`output/playwright/v080-login-${language}-${width}.png`});
    }
  }
  await page.locator('#account-password').fill('synthetic-password');
  await page.locator('#password-visibility').click();
  if(await page.locator('#account-password').getAttribute('type')!=='text')throw Error('Password reveal failed');
  await page.locator('#password-visibility').click();
  if(await page.locator('#account-password').getAttribute('type')!=='password')throw Error('Password conceal failed');
  await page.locator('#account-password').fill('');
  if(errors.length)throw Error(errors.join('\n'));
}
