"use strict";
globalThis.OpenDevDevelopers = ({api,esc,external,fmt,heading,empty,pager,badge,state,refresh,views}) => {
  const tx=(zh,en)=>globalThis.OpenDevI18n.language==='en'?en:zh;
  const filters={q:'',kind:'',relationship:''};
  const names={contacts:()=>tx('联系记录','Contacts'),interviews:()=>tx('访谈与画像','Interviews and profiles'),grants:()=>tx('赞助台账','Grants'),followups:()=>tx('后续复盘','Follow-ups')};
  const kindName=k=>k==='Organization'?tx('组织','Organization'):k==='User'?tx('个人账号','Individual account'):tx('类型待确认','Type unconfirmed');
  const profileText=value=>{
    let items=[];try{items=JSON.parse(value||'[]');}catch{}
    const labels={engineer:tx('工程师','Engineer'),entrepreneur:tx('创业者','Entrepreneur'),undetermined:tx('待判断','Undetermined')};
    return Array.isArray(items)?items.map(v=>labels[v]||tx('待核对','Unverified')).join(' · '):tx('待核对','Unverified');
  };
  const source=(kind,id)=>`#${kind}/${encodeURIComponent(id)}`;
  const reset=()=>Object.assign(filters,{q:'',kind:'',relationship:''});
  const options=(rows,value)=>rows.map(([k,v])=>`<option value="${esc(k)}" ${k===value?'selected':''}>${esc(v)}</option>`).join('');
  document.querySelector('#main').addEventListener('submit',e=>{
    if(e.target.id!=='developer-search')return;
    e.preventDefault();for(const k of Object.keys(filters))filters[k]=e.target.elements[k].value.trim();
    state.page=1;refresh();
  });
  document.querySelector('#main').addEventListener('click',e=>{
    if(!e.target.closest('[data-clear-developers]'))return;
    reset();state.page=1;refresh();
  });
  async function listing(){
    const data=await api('/api/developers?'+new URLSearchParams({...filters,page:state.page,page_size:15}));
    return heading(tx('开发者与团队','Developers and teams'),tx('从真实项目贡献出发，积累可追溯的人才与团队档案。','Build a source-backed directory from contributions across projects.'))+
      `<div class="notice">${tx('候选贡献者须核实维护关系。访谈画像来自当次交流；公开简介不代表已验证的能力或创业意愿。','Candidate contributions require relationship verification. Profiles reflect a particular interview; public bios do not establish verified skills or entrepreneurial intent.')}</div>`+
      `<form id="developer-search" class="developer-filters"><label>${tx('搜索账号、姓名或仓库','Search login, name or repository')}<input name="q" maxlength="200" value="${esc(filters.q)}" type="search"></label><label>${tx('账号类型','Account type')}<select name="kind">${options([['',tx('全部类型','All types')],['User',kindName('User')],['Organization',kindName('Organization')]],filters.kind)}</select></label><label>${tx('项目关系','Project relationship')}<select name="relationship">${options([['',tx('全部关系','All relationships')],['candidate',tx('有待确认关系','Has candidate relationships')],['confirmed',tx('有已确认关系','Has confirmed relationships')]],filters.relationship)}</select></label><div class="inline-actions"><button class="button primary" type="submit">${tx('筛选','Apply filters')}</button><button type="button" class="button" data-clear-developers>${tx('重置','Reset')}</button></div></form>`+
      `<section class="panel"><div class="panel-head"><h3>${tx('GitHub 开发者与组织','GitHub developers and organizations')}</h3><span class="muted small">${data.total} ${tx('个账号','accounts')}</span></div><div class="developer-list">${data.items.map(r=>`<article class="developer-row"><div class="developer-identity"><span class="project-symbol" aria-hidden="true">${esc(r.login.slice(0,1).toUpperCase())}</span><div><a class="project-title" href="#developers/${r.id}">${esc(r.login)}</a><p class="small muted">${esc(r.name||kindName(r.kind))} · ${esc(kindName(r.kind))}</p><p class="developer-bio">${esc(r.bio||tx('暂无公开简介','No public bio available'))}</p></div></div><div class="developer-counts"><strong>${r.project_count} ${tx('个关联项目','linked projects')}</strong><span class="small muted">${r.confirmed_count} ${tx('条已确认关系','confirmed relationships')}</span><a class="text-link" href="#developers/${r.id}">${tx('查看档案','View dossier')} →</a></div></article>`).join('')||empty(tx('没有匹配的账号','No matching accounts'),tx('调整筛选条件；完成作者采集后，新候选会显示在这里。','Adjust the filters. New candidates appear after author collection.'))}</div>${pager(data)}</section>`;
  }
  async function detail(id,requested){
    const mode=requested||'projects';
    const data=await api(`/api/developers/${id}?page=${mode==='projects'?state.page:1}&page_size=15`),r=data.entity;
    const allowed=['projects',...data.history_kinds];
    if(!allowed.includes(mode))return heading(tx('没有此记录的读取权限','This record is unavailable'),tx('可从档案查看当前角色有权读取的信息。','Open this dossier to see records available to your role.'),`<a class="button" href="#developers/${id}">${tx('返回档案','Back to dossier')}</a>`);
    const latest=data.latest_interview;
    const profile=latest?`<section class="panel"><div class="panel-head"><h3>${tx('最近一次访谈画像','Latest interview profile')}</h3></div><div class="panel-body"><p><strong>${esc(profileText(latest.profile))}</strong></p><p class="small muted">${esc(latest.full_name)} · ${fmt(latest.created_at)} · ${esc(latest.actor)}</p><p class="quiet-note">${tx('保留访谈当时的判断；后续意愿与角色变化需要新的访谈依据。','Preserves the assessment at that interview. Changes in intent or role need new interview evidence.')}</p><a class="text-link" href="${source('interviews',latest.id)}">${tx('查看原始访谈','View source interview')} →</a></div></section>`:
      data.history_kinds.includes('interviews')?`<p class="notice">${tx('尚无访谈画像；先完成两轮筛选与有效联系，再记录真实访谈。','No interview profile yet. Complete both review rounds and valid contact before recording a real interview.')}</p>`:'';
    let content;
    if(mode==='projects')content=`<section class="panel"><div class="panel-head"><h3>${tx('关联项目','Linked projects')}</h3><span class="small muted">${data.projects.total} ${tx('个项目','projects')}</span></div>${data.projects.items.map(p=>`<article class="developer-project"><div><a class="project-title" href="#projects/${p.id}">${esc(p.full_name)}</a><p class="small muted">${esc(p.relationship.role==='owner_candidate'?tx('仓库所有者候选','Repository owner candidate'):p.relationship.role==='contributor_candidate'?tx('贡献者候选','Contributor candidate'):p.relationship.role==='maintainer'?tx('维护者','Maintainer'):p.relationship.role)} · ${p.relationship.confirmed?tx('关系已确认','Relationship confirmed'):tx('关系待确认','Relationship unconfirmed')}</p></div><div class="inline-actions">${views.selectionBadge(p.selection)}${badge(p.stage)}<a class="button" href="#projects/${p.id}/selection">${tx('入选评估','Selection')}</a></div></article>`).join('')||empty(tx('暂无关联项目','No linked projects'),'')}${pager(data.projects)}</section>`;
    else{
      const history=await api(`/api/developers/${id}/records/${mode}?page=${state.page}&page_size=15`);
      content=`<section class="panel"><div class="panel-head"><h3>${names[mode]()}</h3><span class="muted small">${history.total} ${tx('条记录','records')}</span></div>${history.items.map(item=>`<article class="developer-project"><div><a class="project-title" href="#projects/${item.project_id}">${esc(item.full_name)}</a><p class="small muted">${esc(item.actor||'')} · ${item.due_at?esc(item.due_at):fmt(item.created_at||item.updated_at)}</p>${mode==='interviews'?`<p>${esc(profileText(item.profile))}</p>`:badge(item.status)}</div><a class="button" href="${source(mode,item.id)}">${tx('查看原始记录','View source record')} →</a></article>`).join('')||empty(tx('暂无记录','No records yet'),tx('完成对应流程后，真实记录会在此汇总。','Records appear here after the corresponding workflow steps.'))}${pager(history)}</section>`;
    }
    return `<p class="eyebrow"><a href="#developers">${tx('开发者与团队','Developers and teams')}</a> / GITHUB ${r.id}</p>`+heading(r.login,`${kindName(r.kind)}${r.name?' · '+r.name:''}`,external(r.url,tx('GitHub 主页','GitHub profile')))+
      `<section class="panel"><div class="panel-body"><p class="user-prose">${esc(r.bio||tx('暂无公开简介','No public bio available'))}</p>${r.company?`<p>${tx('公开自述机构','Self-reported affiliation')}：${esc(r.company)}</p>`:''}${r.blog?`<p>${external(r.blog,tx('公开网站','Public website'))}</p>`:''}<p class="quiet-note">${tx('以上资料来自 GitHub 公开账号；机构、简介均为账号自述。','These details come from the public GitHub account. Affiliation and bio are self-reported.')}</p></div></section>`+
      (data.contact_preference==='declined'?`<p class="notice warning">${tx('已拒绝联系：该限制适用于此账号关联的全部项目。','Contact declined: this restriction applies across all linked projects.')}</p>`:'')+profile+
      `<nav class="tabs" aria-label="${tx('档案内容','Dossier sections')}">${allowed.map(k=>`<a class="tab" href="#developers/${id}/${k}" ${k===mode?'aria-current="page"':''}>${k==='projects'?tx('关联项目','Linked projects'):names[k]()}</a>`).join('')}</nav>`+content;
  }
  return {listing,detail,reset};
};
