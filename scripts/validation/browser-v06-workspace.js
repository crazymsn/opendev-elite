async page => {
  const origin = 'http://127.0.0.1:8877/';
  const checks = [], errors = [], overflow = [], untranslated = [];
  page.on('pageerror', error => errors.push(error.message));
  const check = (condition, message) => { if (!condition) throw new Error(message); checks.push(message); };
  const ready = async () => {
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('#main .page-heading').waitFor();
  };
  const go = async hash => { await page.goto(origin + '#' + hash); await ready(); };
  await go('projects');
  for (const language of ['zh-CN','en']) {
    await page.locator('#workspace [data-language-select]').selectOption(language);
    await page.waitForFunction(lang => document.documentElement.lang === lang, language);
    await ready();
    for (const route of ['overview','projects','criteria','projects/301','projects/301/selection','projects/301/reviews','projects/301/experiences','projects/301/contacts','projects/301/decisions','inbox','experiences','contacts','grants','followups','operations','audit','members','account']) {
      await go(route);
      for (const width of [375,768,1024,1440]) {
        await page.setViewportSize({width,height:1000});
        const excess = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
        if (excess > 1) overflow.push({language,route,width,excess});
      }
      if (language === 'en') {
        const labels = await page.locator('#workspace h1,#workspace h2,#workspace h3,#workspace button,#workspace label,#navigation a,#breadcrumb').allTextContents();
        const cjk = labels.map(text=>text.trim()).filter(text=>/[\u3400-\u9fff]/.test(text));
        if(cjk.length)untranslated.push({route,labels:cjk});
      }
      if (['overview','projects','criteria','projects/301','members'].includes(route)) {
        await page.screenshot({path:'output/playwright/v06-' + language + '-' + route.replaceAll('/','-') + '.png',fullPage:true});
      }
    }
    await go('projects/301');
    const original = await page.locator('.readme-original').innerText();
    check(original.includes('Original README') || original.includes('README 原文'), language + ' original README available');
    await page.locator('[data-translation-field=description]').click();
    check(await page.locator('#translation-dialog textarea[readonly]').inputValue() === 'An open source Agent runtime with reproducible tool execution.', language + ' source text preserved');
    await page.locator('#translation-dialog [name=content]').fill('取消编辑，不应存储 <script>example</script>');
    await page.locator('#translation-dialog [data-close-translation]').last().click();
    check(await page.locator('#translation-dialog textarea').count() === 0, language + ' dismissed translation cleared');
  }
  check(errors.length === 0, 'no uncaught JavaScript errors');
  return {checks,errors,overflow,untranslated,synthetic_only:true};
}
