async page => {
  await page.getByRole('textbox', {name:'账号', exact:true}).fill('OpenDev');
  await page.getByRole('textbox', {name:'密码', exact:true}).fill('wrong-password');
  await page.getByRole('button', {name:'登录工作台', exact:true}).click();
  await page.locator('#account-error').filter({hasText:/./}).waitFor();
  console.log('invalid login:',await page.locator('#account-error').innerText());
  await page.getByRole('textbox', {name:'密码', exact:true}).fill('123456');
  await page.getByRole('button', {name:'登录工作台', exact:true}).click();
  await page.getByRole('link', {name:'入选标准', exact:true}).waitFor();
  await page.getByRole('link', {name:'入选标准', exact:true}).click();
  await page.getByRole('heading', {name:/80|优质|入选标准/}).first().waitFor();
  console.log(await page.locator('#main').innerText());
}
