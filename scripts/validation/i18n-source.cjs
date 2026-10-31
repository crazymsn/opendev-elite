// Development helper: local Acorn path is explicit; never rewrites interpolated data.
const fs=require('fs'), path=require('path');
const acorn=require(process.argv[2]);
const write=process.argv.includes('--write');
const root=path.resolve(__dirname,'../..'), keys=new Set();
const chinese=/[\u3400-\u9fff]/;
function collect(text) {
  if (!chinese.test(text)) return;
  if (!/[<>]/.test(text)) keys.add(text.trim());
  for (const part of text.match(/[\u3400-\u9fff][^<>"'`\r\n]*/g)||[]) if(part.trim()) keys.add(part.trim());
}
for (const file of ['app.js','forms.js','accounts.js','landing.js']) {
  const full=path.join(root,'worker/web',file),source=fs.readFileSync(full,'utf8'),edits=[];
  const ast=acorn.parse(source,{ecmaVersion:'latest'});
  function walk(node,parent) {
    if (!node || typeof node!=='object') return;
    if(node.type==='Literal' && typeof node.value==='string' && chinese.test(node.value)) {
      const key=parent?.type==='Property' && parent.key===node && !parent.computed;
      const wrapped=parent?.type==='CallExpression' && parent.callee?.object?.property?.name==='OpenDevI18n';
      if(!key && !wrapped) {
        collect(node.value);
        edits.push([node.start,node.start,'globalThis.OpenDevI18n.t('],[node.end,node.end,')']);
      }
    }
    if(node.type==='TemplateLiteral' && parent?.type!=='TaggedTemplateExpression' && node.quasis.some(q=>chinese.test(q.value.cooked||''))) {
      node.quasis.forEach(q=>collect(q.value.cooked||''));
      edits.push([node.start,node.start,'globalThis.OpenDevI18n.html']);
    }
    for(const [key,value] of Object.entries(node)) {
      if(['start','end','loc'].includes(key))continue;
      if(Array.isArray(value))value.forEach(v=>walk(v,node));
      else if(value&&typeof value==='object')walk(value,node);
    }
  }
  walk(ast,null);
  if(write) {
    let next=source;
    for(const [start,end,text] of edits.sort((a,b)=>b[0]-a[0]))next=next.slice(0,start)+text+next.slice(end);
    fs.writeFileSync(full,next);
  }
}
const html=fs.readFileSync(path.join(root,'worker/web/index.html'),'utf8');
for(const text of html.split(/<[^>]+>/))collect(text);
for(const match of html.matchAll(/(?:placeholder|title|aria-label)="([^"]+)"/g))collect(match[1]);
fs.writeFileSync(path.join(root,'artifacts/validation/i18n-keys.json'),JSON.stringify([...keys].sort(),null,2));
console.log('Interface source phrases:',keys.size,'rewritten:',write);
