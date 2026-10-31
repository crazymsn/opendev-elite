async page => {
 const contrast=()=>{
  const rgb=v=>{const x=v.match(/[\d.]+/g);return x?x.slice(0,3).map(Number):[255,255,255];};
  const lum=a=>a.map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((s,x,i)=>s+x*[.2126,.7152,.0722][i],0);
  const fails=[];
  for(const e of document.querySelectorAll('body *')){
   if(!Array.from(e.childNodes).some(n=>n.nodeType===3&&n.textContent.trim())||!e.getClientRects().length||e.closest('[hidden],[aria-hidden=true],svg,button:disabled,.inactive-field'))continue;
   const c=getComputedStyle(e);if(c.visibility==='hidden')continue;
   let p=e,bg='rgb(255,255,255)';while(p){const color=getComputedStyle(p).backgroundColor;if(color!=='rgba(0, 0, 0, 0)'&&color!=='transparent'){bg=color;break;}p=p.parentElement;}
   const l1=lum(rgb(c.color)),l2=lum(rgb(bg)),ratio=(Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05);
   const target=parseFloat(c.fontSize)>=24||(parseFloat(c.fontSize)>=18.66&&parseInt(c.fontWeight)>=700)?3:4.5;
   if(ratio+0.02<target)fails.push({tag:e.tagName,cls:e.className,color:c.color,bg,ratio:Math.round(ratio*100)/100});
  }
  return fails;
 };
 await page.goto('http://127.0.0.1:8874/');await page.locator('#login-view').waitFor({state:'visible'});
 await page.setViewportSize({width:1440,height:1000});
 const landingContrast=await page.evaluate(contrast);
 await page.locator('.skip-link').focus();await page.keyboard.press('Enter');
 const skip=await page.evaluate(()=>document.activeElement.id);
 await page.locator('#access-token').fill('synthetic-browser-test-only-admin-v04-00000000');await page.locator('#login-form button').click();await page.locator('#main .stats').waitFor();
 const workspaceContrast=await page.evaluate(contrast);
 const targets=await page.evaluate(()=>Array.from(document.querySelectorAll('button,.nav-item,.button')).filter(e=>e.getClientRects().length&&!e.closest('[hidden]')).map(e=>({cls:e.className,h:e.getBoundingClientRect().height,w:e.getBoundingClientRect().width})).filter(r=>r.h<43.8));
 await page.emulateMedia({reducedMotion:'reduce'});const reduced=await page.evaluate(()=>getComputedStyle(document.documentElement).scrollBehavior);
 await page.locator('#logout').click();await page.context().clearCookies();
 if(landingContrast.length||workspaceContrast.length||targets.length||skip!=='welcome'||reduced!=='auto')throw new Error(JSON.stringify({landingContrast,workspaceContrast,targets,skip,reduced}));
 return {passed:true,landingContrast,workspaceContrast,targets,skip,reduced};
}
