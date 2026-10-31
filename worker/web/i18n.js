"use strict";
(() => {
  let language='zh-CN';
  try { if(localStorage.getItem('opendev.language')==='en') language='en'; } catch {}
  const dictionary=globalThis.OpenDevEnglish||{};
  const keys=Object.keys(dictionary).filter(Boolean).sort((a,b)=>b.length-a.length);
  const regex=new RegExp(keys.map(key=>key.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|'),'g');
  const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function fragment(text,html=false) {
    if(language!=='en' || !keys.length) return text;
    return String(text).replace(regex,key=>html?escape(dictionary[key]):dictionary[key]);
  }
  function t(text) {
    if(language!=='en') return text;
    return Object.hasOwn(dictionary,text)?dictionary[text]:fragment(text,/[<>]/.test(text));
  }
  function html(strings,...values) {
    const markup=strings.some(s=>/[<>]/.test(s));
    // Only source-code literals are translated. Runtime values, code, and user
    // evidence retain their exact content and existing escaping behavior.
    return strings.map((part,index)=>fragment(part,markup)+(index<values.length?values[index]:'')).join('');
  }
  function localizeStatic() {
    document.documentElement.lang=language;
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    const nodes=[];
    while(walker.nextNode())nodes.push(walker.currentNode);
    for(const node of nodes) {
      if(node.parentElement?.closest('script,style,textarea,[data-no-i18n]')) continue;
      node.textContent=fragment(node.textContent);
    }
    document.querySelectorAll('[title],[placeholder],[aria-label]').forEach(element=>{
      if(element.closest('[data-no-i18n]'))return;
      for(const attribute of ['title','placeholder','aria-label'])if(element.hasAttribute(attribute))element.setAttribute(attribute,fragment(element.getAttribute(attribute)));
    });
    document.title=t(document.title);
    document.querySelectorAll('[data-language-select]').forEach(select=>{
      select.value=language;
      select.setAttribute('aria-label',language==='en'?'Interface language':'界面语言');
      select.addEventListener('change',()=>{
        const next=select.value==='en'?'en':'zh-CN';
        try { localStorage.setItem('opendev.language',next); } catch { return; }
        location.reload();
      });
    });
  }
  globalThis.OpenDevI18n={language,locale:language==='en'?'en-US':'zh-CN',t,html,fragment,
    pick:value=>value?.[language]??value?.['zh-CN']??'',localizeStatic};
  localizeStatic();
})();
