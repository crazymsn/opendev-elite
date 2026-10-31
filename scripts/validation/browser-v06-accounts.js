async sourcePage => {
  const adminContext = await sourcePage.context().browser().newContext();
  const page = await adminContext.newPage();
  const checks = [], errors = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const origin = 'http://127.0.0.1:8877/';
  const password = 'synthetic member password 12345';
  const memberName = 'synthetic-member-v06-' + Date.now();
  const changedPassword = 'synthetic changed password 67890';
  page.on('pageerror', error => errors.push(error.message));
  const context = await page.context().browser().newContext();
  const member = await context.newPage();
  member.on('pageerror', error => errors.push(error.message));
  const login = async (target, name, secret) => {
    await target.locator('#account-username').fill(name);
    await target.locator('#account-password').fill(secret);
    await target.locator('#account-submit').click();
  };
  const card = () => page.locator('.member-form').filter({hasText:memberName});
  const save = async body => {
    const response = page.waitForResponse(r => r.url().includes('/api/accounts/') && r.request().method() === 'POST');
    await body();
    check((await response).status() === 200, 'member update accepted');
    await page.locator('#toast').filter({hasText:'成员授权已保存'}).waitFor();
    await page.locator('#main .loading').waitFor({state:'hidden'});
  };
  try {
    await page.goto(origin+'#team-login');
    await page.locator('#account-fields:not([disabled])').waitFor();
    check(await page.locator('[data-auth-mode], #admin-setup-tab, #account-confirm').count() === 0, 'public registration and admin setup removed from DOM');
    for (const width of [375,768,1024,1440]) {
      await page.setViewportSize({width,height:1000});
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'login responsive '+width);
    }
    await page.locator('#team-login').screenshot({path:'output/playwright/v06-account-login.png'});
    await page.locator('#account-password').fill('sample');
    await page.locator('#password-visibility').click();
    check(await page.locator('#account-password').getAttribute('type') === 'text', 'password visibility toggle');
    await login(page,'OpenDev','wrong-password');
    await page.locator('#account-error').filter({hasText:'用户名或密码不正确'}).waitFor();
    check(await page.locator('#account-password').inputValue() === '', 'failed password cleared');
    check(await page.locator('#account-password').getAttribute('type') === 'password', 'password masked after submission');
    await login(page,'OpenDev','123456');
    await page.locator('#workspace').waitFor({state:'visible'});
    check(await page.locator('#identity-name').innerText() === 'OpenDev', 'default administrator login');
    check(await page.evaluate(async()=>{const r=await fetch('/api/ui-config');return(await r.json()).demo_mode===true;}),'synthetic environment confirmed before member mutation');
    await page.locator('#navigation a[href="#members"]').click();
    await page.locator('#open-member-create').click();
    const create = page.locator('#member-create-form');
    for (const width of [375,768,1024,1440]) {
      await page.setViewportSize({width,height:1000});
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'member creation responsive '+width);
    }
    await create.locator('[name=username]').fill(memberName);
    await create.locator('[name=display_name]').fill('测试成员 <img src=x>');
    await create.locator('[name=password]').fill(password);
    await create.locator('[name=confirm]').fill(changedPassword);
    await create.locator('button[type=submit]').click();
    await create.locator('.member-error').filter({hasText:'不一致'}).waitFor();
    check(true, 'member password confirmation');
    await create.locator('[name=password]').fill(password);
    await create.locator('[name=confirm]').fill(password);
    await create.locator('button[type=submit]').click();
    await create.locator('.member-error').filter({hasText:'至少选择'}).waitFor();
    check(true, 'member role required');
    await create.locator('[name=password]').fill(password);
    await create.locator('[name=confirm]').fill(password);
    await create.locator('[name=role][value=reviewer]').check();
    await create.locator('button[type=submit]').click();
    await card().waitFor();
    check(await card().locator('img').count() === 0, 'member display name escaped');
    check(!(await page.locator('#member-create-panel').isVisible()), 'creation form closes after success');
    await page.locator('#open-member-create').click();
    await create.locator('[name=password]').fill(password);
    await page.locator('#cancel-member-create').click();
    check(await create.locator('[name=password]').inputValue() === '', 'cancel clears initial password');
    await page.locator('#main').screenshot({path:'output/playwright/v06-member-management.png'});
    await member.goto(origin+'#team-login');
    await login(member,memberName,password);
    await member.locator('#workspace').waitFor({state:'visible'});
    check(await member.locator('#navigation a[href="#members"]').count() === 0, 'member cannot manage accounts');
    check(await member.evaluate(() => Object.keys(localStorage).every(key => key === 'opendev.language') && sessionStorage.length === 0), 'no password in web storage');
    await member.locator('a[href="#account"]').click();
    await member.locator('[name=current_password]').fill(password);
    await member.locator('[name=new_password]').fill(changedPassword);
    await member.locator('[name=confirm]').fill(changedPassword);
    await member.locator('#password-change-form button[type=submit]').click();
    await member.locator('#login-view').waitFor({state:'visible'});
    await login(member,memberName,changedPassword);
    await member.locator('#workspace').waitFor({state:'visible'});
    check(true, 'member changes password and signs in again');
    await page.locator('#refresh').click();
    await card().waitFor();
    await save(async () => {
      await card().locator('[name=status]').selectOption('disabled');
      await card().locator('button[type=submit]').click();
    });
    await member.locator('#refresh').click();
    await member.locator('#login-view').waitFor({state:'visible'});
    check(true, 'disabled member session revoked');
    await member.setViewportSize({width:375,height:900});
    await member.goto(origin+'#team-login');
    await member.locator('#team-login').screenshot({path:'output/playwright/v06-account-login-mobile.png'});
    check(errors.length === 0, 'no uncaught browser errors');
    return {version:'0.6-development',checks,errors,synthetic_only:true};
  } finally { await context.close(); await adminContext.close(); }
}
