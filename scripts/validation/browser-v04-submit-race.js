async page => {
 const checks=[];const ok=(v,n)=>{if(!v)throw new Error(n);checks.push(n);};
 await page.goto('http://127.0.0.1:8874/');await page.locator('#login-view').waitFor({state:'visible'});
 await page.locator('#access-token').fill('synthetic-browser-test-only-reviewer-v04-00000000');await page.locator('#login-form button').click();await page.locator('#main .stats').waitFor();
 await page.goto('http://127.0.0.1:8874/#projects/301');await page.getByRole('button',{name:'分配负责人',exact:true}).click();
 let release,captured;const gate=new Promise(r=>release=r),request=new Promise(r=>captured=r);
 await page.route('**/api/actions/assign',async route=>{const response=await route.fetch();captured();await gate;await route.fulfill({response});});
 await page.locator('[name="owner"]').fill('synthetic-browser-reviewer');await page.locator('#dialog-submit').click();await request;
 await page.locator('#dialog-cancel').click();await page.getByRole('button',{name:'第一轮技术复核',exact:true}).click();
 const title=await page.locator('#dialog-title').innerText();release();await page.unrouteAll({behavior:'wait'});
 ok(await page.locator('#action-dialog').isVisible(),'late submit leaves new modal open');
 ok(await page.locator('#dialog-title').innerText()===title,'late submit does not replace modal');
 ok(await page.locator('#dialog-error').innerText()==='','late submit does not overwrite error');
 ok(!(await page.locator('#dialog-submit').isDisabled()),'new submit remains enabled');
 await page.locator('#dialog-cancel').click();await page.locator('#logout').click();await page.context().clearCookies();
 return {passed:checks.length,checks};
}
