async page => {
  if(!page.url().startsWith('http://127.0.0.1:8886/'))throw Error('Synthetic fixture required');
  await page.locator('#login-view [data-language-select]').selectOption('zh-CN');
  await page.waitForFunction(()=>document.documentElement.lang==='zh-CN');
  await page.locator('#account-username').fill('OpenDev');
  await page.locator('#account-password').fill('123456');
  await page.locator('#account-submit').click();
  await page.locator('#workspace').waitFor();
  await page.goto('http://127.0.0.1:8886/#projects/801/selection');
  await page.getByRole('button',{name:'更新入选评估',exact:true}).click();
  await page.locator('.quality-editor').last().waitFor();
  for(const width of [1440,375]) {
    await page.setViewportSize({width,height:width===375?812:1000});
    await page.locator('.quality-editor').last().scrollIntoViewIfNeeded();
    const dialog=await page.locator('#action-dialog').boundingBox(),head=await page.locator('.dialog-head').boundingBox(),foot=await page.locator('.dialog-footer').boundingBox();
    if(head.y<dialog.y-1||foot.y+foot.height>dialog.y+dialog.height+1)throw Error('Dialog controls scroll out of view');
    await page.screenshot({path:`output/playwright/v080-quality-form-final-${width}.png`});
  }
  await page.locator('#dialog-close').click();
  await page.setViewportSize({width:1440,height:1000});
}
