async page => {
  const checks = [];
  const check = (value, name) => { if (!value) throw new Error(name); checks.push(name); };
  await page.goto('http://127.0.0.1:8874/');
  await page.locator('#login-view').waitFor({state:'visible'});
  check((await page.title()).startsWith('OpenDev Elite'), 'browser title uses OpenDev Elite');
  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({width, height:1000});
    check(await page.locator('.landing-nav .brand-name').innerText() === 'OpenDev Elite', 'landing brand '+width);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'landing fits '+width);
  }
  await page.screenshot({path:'output/playwright/v041-opendev-landing.png'});
  await page.locator('#access-token').fill('synthetic-browser-test-only-admin-v04-00000000');
  await page.locator('#login-form button').click();
  await page.locator('#workspace').waitFor({state:'visible'});
  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({width, height:1000});
    check(await page.locator('.sidebar .brand-name').innerText() === 'OpenDev Elite', 'workspace brand '+width);
    check(await page.evaluate(() => {
      const brand = document.querySelector('.sidebar .brand-name').getBoundingClientRect();
      const sidebar = document.querySelector('.sidebar').getBoundingClientRect();
      return brand.right <= sidebar.right && document.documentElement.scrollWidth <= innerWidth;
    }), 'workspace wordmark fits '+width);
  }
  await page.screenshot({path:'output/playwright/v041-opendev-workspace.png'});
  await page.locator('#logout').click();
  await page.setViewportSize({width:375,height:900});
  await page.locator('#login-view').waitFor({state:'visible'});
  await page.screenshot({path:'output/playwright/v041-opendev-mobile.png'});
  return {passed:checks.length, checks};
}
