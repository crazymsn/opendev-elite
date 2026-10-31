"use strict";
(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const parse = (v, fallback = {}) => {
    try {
      return typeof v === "string" ? JSON.parse(v) : (v ?? fallback);
    } catch {
      return fallback;
    }
  };
  const state = {
    user: null,
    config: null,
    csrf: "",
    route: "overview",
    page: 1,
    q: "",
    stage: "",
    category: "", selection: "", audience: "", contact_class: "", source: "", sort: "updated",
    generation: 0,
    dialogGeneration: 0,
    project: null,
    records: new Map(),
    dialog: null,
    authBusy: false,
  };
  const stages = {
    discovered: globalThis.OpenDevI18n.t("新发现"),
    technical_review: globalThis.OpenDevI18n.t("技术复核"),
    experience: globalThis.OpenDevI18n.t("产品体验"),
    ready_contact: globalThis.OpenDevI18n.t("待联系"),
    contacted: globalThis.OpenDevI18n.t("已联系"),
    interviewed: globalThis.OpenDevI18n.t("已访谈"),
    approved: globalThis.OpenDevI18n.t("待发放"),
    sponsored: globalThis.OpenDevI18n.t("赞助中"),
    closed: globalThis.OpenDevI18n.t("已结束"),
    hold: globalThis.OpenDevI18n.t("待观察"),
    rejected: globalThis.OpenDevI18n.t("未通过"),
    reject: globalThis.OpenDevI18n.t("未通过"),
  };
  const labels = {
    ...stages,
    pending: globalThis.OpenDevI18n.t("待处理"),
    completed: globalThis.OpenDevI18n.t("已完成"),
    queued: globalThis.OpenDevI18n.t("队列中"),
    running: globalThis.OpenDevI18n.t("运行中"),
    done: globalThis.OpenDevI18n.t("已完成"),
    dead: globalThis.OpenDevI18n.t("失败待处理"),
    partial: globalThis.OpenDevI18n.t("部分覆盖"),
    complete: globalThis.OpenDevI18n.t("完整"),
    pass: globalThis.OpenDevI18n.t("通过"),
    approve: globalThis.OpenDevI18n.t("批准"),
    human: globalThis.OpenDevI18n.t("面向人"),
    agent: globalThis.OpenDevI18n.t("面向 Agent"),
    both: globalThis.OpenDevI18n.t("人 + Agent"),
    unknown: globalThis.OpenDevI18n.t("待核对"),
    draft: globalThis.OpenDevI18n.t("草稿"),
    sent: globalThis.OpenDevI18n.t("已记录发送"),
    replied: globalThis.OpenDevI18n.t("已回复"),
    declined: globalThis.OpenDevI18n.t("拒绝联系"),
    failed: globalThis.OpenDevI18n.t("失败"),
    submitting: globalThis.OpenDevI18n.t("提交中"),
    awaiting_claim: globalThis.OpenDevI18n.t("待领取"),
    issued: globalThis.OpenDevI18n.t("已到账"),
    cancelled: globalThis.OpenDevI18n.t("已取消"),
    none: globalThis.OpenDevI18n.t("无"),
    product: globalThis.OpenDevI18n.t("产品问题"),
    environment: globalThis.OpenDevI18n.t("环境问题"),
    engineer: globalThis.OpenDevI18n.t("工程师"),
    entrepreneur: globalThis.OpenDevI18n.t("创业者"),
    undetermined: globalThis.OpenDevI18n.t("待判断"),
    continue: globalThis.OpenDevI18n.t("继续"),
    adjust: globalThis.OpenDevI18n.t("调整"),
    pause: globalThis.OpenDevI18n.t("暂停"),
    end: globalThis.OpenDevI18n.t("结束"),
  };
  const roleNames = {
    reviewer: globalThis.OpenDevI18n.t("技术审核"),
    operator: globalThis.OpenDevI18n.t("项目运营"),
    approver: globalThis.OpenDevI18n.t("赞助审批"),
    disburser: globalThis.OpenDevI18n.t("Credits 执行"),
    auditor: globalThis.OpenDevI18n.t("质控审计"),
    admin: globalThis.OpenDevI18n.t("管理员"),
    agent: globalThis.OpenDevI18n.t("Agent 执行器"),
  };
  const nav = [
    ["overview", globalThis.OpenDevI18n.t("工作概览"), "◫"],
    ["inbox", globalThis.OpenDevI18n.t("团队待办"), "inbox"],
    ["projects", globalThis.OpenDevI18n.t("候选项目"), "▦"],
    ["developers", globalThis.OpenDevI18n.language === "en" ? "Developers and teams" : "开发者与团队", "developers"],
    ["criteria", globalThis.OpenDevI18n.language === "en" ? "Selection criteria" : "入选标准", "criteria"],
    ["experiences", globalThis.OpenDevI18n.t("产品体验"), "◇", "experiences"],
    ["contacts", globalThis.OpenDevI18n.t("联系与访谈"), "↗", "contacts"],
    ["grants", globalThis.OpenDevI18n.t("赞助台账"), "◈", "grants"],
    ["followups", globalThis.OpenDevI18n.t("后续复盘"), "↻", "followups"],
    ["operations", globalThis.OpenDevI18n.t("采集与运行"), "≋", "jobs"],
    ["audit", globalThis.OpenDevI18n.t("操作审计"), "≡", "events"],
    ["members", globalThis.OpenDevI18n.t("成员与权限"), "members"],
    ["account", globalThis.OpenDevI18n.t("账号设置"), "account"],
  ];
  const can = (action) => (state.config?.actions || []).includes(action);
  const canRead = (table) => (state.config?.records || []).includes(table);
  const cap = (name) => Boolean(state.config?.capabilities?.[name]);
  const fmt = (v) =>
    v
      ? new Date(v).toLocaleString(globalThis.OpenDevI18n.locale, {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })
      : "—";
  const short = (v) => String(v || "—").slice(0, 9);
  const badge = (value) => {
    const color = [
      "pass",
      "complete",
      "issued",
      "sponsored",
      "completed",
      "done",
      "approve",
    ].includes(value)
      ? "green"
      : [
            "hold",
            "pending",
            "awaiting_claim",
            "partial",
            "unknown",
            "submitting",
            "queued",
          ].includes(value)
        ? "amber"
        : ["rejected", "reject", "failed", "dead", "declined"].includes(value)
          ? "red"
          : [
                "experience",
                "running",
                "ready_contact",
                "replied",
                "technical_review",
              ].includes(value)
            ? "blue"
            : "";
    return `<span class="badge ${color}">${esc(labels[value] || value || "—")}</span>`;
  };
  function toast(message, bad = false) {
    const e = $("#toast");
    e.textContent = message;
    e.classList.toggle("failure", bad);
    e.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (e.hidden = true), 6500);
  }
  function friendly(e) {
    const text = e.message || String(e);
    const known = {
      'Choose engineer and/or entrepreneur, or undetermined alone; duplicate profile tags are not allowed': globalThis.OpenDevI18n.language === 'en' ? 'Choose engineer and/or entrepreneur, or undetermined alone; duplicate profile tags are not allowed' : '可选择工程师、创业者或两者兼具；待判断须单独选择，不能重复。',
      "Selection evidence is incomplete under the current policy; reassess qualification": globalThis.OpenDevI18n.t("当前标准下入选证据不完整，请重新评估"),
      "Selection requires a list of verifiable evidence sources": globalThis.OpenDevI18n.t("证据来源须为非空、可核对的来源列表"),
      "Selection reason must match include, hold or exclude": globalThis.OpenDevI18n.t("对应条款必须与入选、暂缓或排除结论一致"),
      "Invalid maintenance evidence kind": globalThis.OpenDevI18n.t("无效的维护信号类型"),
      "Active maintenance requires meaningful changes, releases or responses, not timestamps alone": globalThis.OpenDevI18n.t("持续维护须有实质变更、发布或响应，不能仅凭时间戳"),
      "Maintenance evidence requires a valid observation date that is not in the future": globalThis.OpenDevI18n.t("维护事实日期须有效且不能晚于今天"),
      "Outside-scope exclusion requires a negative scope assessment": globalThis.OpenDevI18n.t("范围外排除必须先确认不符合方向"),
      "Abandonment exclusion requires confirmed inactive maintenance": globalThis.OpenDevI18n.t("停止维护排除必须先确认已停止维护"),
      "Selection requires use-case, delivery and access/risk evidence": globalThis.OpenDevI18n.t("入选须提供真实场景、评测入口及权限风险依据"),
      "Product-value exclusion requires a current second-round rejection": globalThis.OpenDevI18n.t("按产品价值排除前，须记录当前版本的第二轮未通过结论"),
      "Product-value exclusion requires linked completed product evidence; environment failure is insufficient": globalThis.OpenDevI18n.t("按产品价值排除须关联已完成的产品体验证据；环境失败不足以排除"),

      ...(globalThis.OpenDevI18n.language === 'zh-CN' ? {
        'Quality assessment must be an object': '质量评分格式无效，请按五个维度填写。',
        'Each quality dimension needs a score and evidence sources': '每个质量维度均须填写评分和证据来源。',
        'All five quality dimensions require an integer score, rationale and sources': '五个维度均须填写 0–4 分整数评分、事实依据和来源。',
        'Inclusion requires maintenance on two distinct dates within 365 days and two distinct sources': '入选须有过去一年内两个不同日期的实质维护，以及至少两个不同的维护证据来源。',
        'Quality threshold not met: at least 80/100 and every dimension at least 3/4; hold for improvement': '未达到质量门槛：总分须至少 80/100，每项至少 3/4 分。请暂缓并记录待改进事项。',
        'Record project scope and maintenance qualification before proceeding': '请先记录项目方向与持续维护的入选评估。',
        'Selection criteria changed; reassess project qualification': '入选标准已更新，请重新评估项目。',
        'Project commit or audience changed; reassess qualification': '项目版本或目标用户已变化，请更新入选评估。',
        'Project qualification expired; reassess current maintenance and scope': '入选评估已过期，请重新核对方向和维护情况。',
        'Project is on hold or outside the current selection criteria': '项目处于暂缓或未入选状态，请先核对入选结论。',
        'Follow-up scheduling requires an issued grant': '到账核对完成后才能安排复盘。',
        'Follow-up date must be in the future': '请选择未来的复盘日期。',
        'Review dates must be future ISO dates': '请至少填写一个未来复盘日期，格式为 YYYY-MM-DD。',
      } : {}),
      "Invalid credentials": globalThis.OpenDevI18n.t("访问凭证无效，请检查后重试。"),
      "Record not found": globalThis.OpenDevI18n.t("这条记录不存在或已不可用，请返回列表重新查看。"),
      "Confirmed, contactable maintainer required": globalThis.OpenDevI18n.t("请先确认实际维护者，并核对其联系意愿。"),
      "This maintainer already has an active contact record; reuse it": globalThis.OpenDevI18n.t("这位维护者已有进行中的联系记录，请沿用已有沟通。"),
      "Repository SHA changed; create a new experience after review": globalThis.OpenDevI18n.t("仓库版本已变化，请重新复核后创建体验任务。"),
      "First-round review is missing, expired or no longer passed": globalThis.OpenDevI18n.t("第一轮结论缺失、过期或未通过，请先完成当前版本复核。"),
      "Experience predates the current first-round review": globalThis.OpenDevI18n.t("体验早于当前第一轮审核，请重新安排真实体验。"),
      "Project audience no longer matches experience": globalThis.OpenDevI18n.t("项目目标用户已调整，请按当前受众重新安排体验。"),
      "Archived or disabled project requires reconsideration": globalThis.OpenDevI18n.t("仓库已归档或停用，请重新判断是否继续跟进。"),
      "Project evidence is expired or invalid; refresh before proceeding": globalThis.OpenDevI18n.t("项目证据已过期或无效，请刷新采集后继续。"),
      "Complete evidence snapshot required for the current commit": globalThis.OpenDevI18n.t("当前版本的证据不完整，请补齐采集后继续。"),
      "The current evidence snapshot is expired; refresh before proceeding": globalThis.OpenDevI18n.t("当前证据快照已过期，请刷新采集。"),
      "next_review_at is required when no future pending followup exists": globalThis.OpenDevI18n.t("目前没有未来复盘待办，请填写下一次复盘日期。"),
      "next_review_at must be a future ISO date": globalThis.OpenDevI18n.t("下一次复盘日期必须晚于今天。"),
      "Both current, unexpired review rounds must pass before contact or sponsorship":
        globalThis.OpenDevI18n.t("需要当前版本的两轮审核均通过，且结论未过期。"),
      "Assign owner and audience first":
        globalThis.OpenDevI18n.t("请先分配负责人和目标用户，再提交审核。"),
      "Round 1 must pass before a second-round experience":
        globalThis.OpenDevI18n.t("第一轮审核通过后，才能创建体验任务。"),
      "The approver cannot execute their own grant":
        globalThis.OpenDevI18n.t("审批人与 Credits 发放执行人必须不同。"),
      "Credits service is not configured":
        globalThis.OpenDevI18n.t("尚未配置 Credits 服务。请由管理员完成接入后重试。"),
      "This identity does not have the required role":
        globalThis.OpenDevI18n.t("当前身份没有执行该操作的权限。"),
      "A captured evidence snapshot is required":
        globalThis.OpenDevI18n.t("需要完整的当前版本证据快照，请先刷新采集。"),
    };
    return known[text] || globalThis.OpenDevI18n.t(text);
  }
  async function api(path, body, key) {
    const requestUser = state.user;
    const headers = {};
    const opts = { credentials: "same-origin", headers, cache: "no-store" };
    if (body !== undefined) {
      opts.method = "POST";
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = state.csrf;
      headers["Idempotency-Key"] = key || crypto.randomUUID();
      opts.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, opts);
    } catch {
      const e = new Error(
        globalThis.OpenDevI18n.t("连接中断。请检查服务状态；重试相同操作时会保留原请求标识。"),
      );
      e.uncertain = true;
      throw e;
    }
    const data = await response
      .json()
      .catch(() => ({ error: globalThis.OpenDevI18n.t("服务器返回了无法识别的响应。") }));
    if (!response.ok) {
      const e = new Error(
        typeof data.error === "string"
          ? friendly({message:data.error})
          : data.message || globalThis.OpenDevI18n.html`请求失败（${response.status}）`,
      );
      e.status = response.status;
      if (response.status === 401 && state.user === requestUser && !path.startsWith("/auth/"))
        showLogin(globalThis.OpenDevI18n.t("会话已过期，请重新登录。"));
      throw e;
    }
    return data;
  }
  function showLogin(message = "") {
    ++state.generation;
    ++state.dialogGeneration;
    state.user = null;
    state.csrf = "";
    state.project = null;
    state.config = null;
    state.dialog = null;
    state.records.clear();
    views.reset();
    developersUI.reset();
    $("#main").replaceChildren();
    $("#dialog-fields").replaceChildren();
    $("#dialog-result").replaceChildren();
    $("#dialog-error").textContent = "";
    $("#identity-name").textContent = "";
    $("#mobile-identity").textContent = "";
    $("#identity-role").textContent = "";
    $("#workspace").hidden = true;
    $("#login-view").hidden = false;
    if (location.hash === '#team-login') $("#team-login").focus();
    $("#login-error").textContent = message;
    $("#account-error").textContent = message;
    if ($("#action-dialog").open) $("#action-dialog").close();
    $("#access-token").value = "";
    $("#account-password").value = '';
    $("#toast").hidden = true;
    $("#toast").textContent = "";
    clearTimeout(toast.timer);
  }
  async function enter(user) {
    const generation = ++state.generation;
    state.user = user;
    state.csrf = user.csrf_token || (await api("/auth/csrf")).csrf_token;
    const config = await api("/api/ui-config");
    if (state.user !== user || generation !== state.generation) return;
    state.config = config;
    $("#login-view").hidden = true;
    $("#workspace").hidden = false;
    $("#identity-name").textContent = user.display_name || user.actor;
    $("#mobile-identity").textContent = user.display_name || user.actor;
    $("#identity-avatar").textContent = (user.display_name || user.actor).slice(0, 2).toUpperCase();
    $("#identity-role").textContent = user.roles
      .map((r) => roleNames[r] || r)
      .join(" / ");
    renderNav();
    await route();
  }
  function renderNav() {
    $("#navigation").innerHTML = nav
      .filter((n) => (n[0] !== "members" || cap("manage_accounts")) && (n[0] !== "inbox" || state.config.inbox_kinds?.length) && (!n[3] || canRead(n[3])))
      .map(
        (n, i) =>
          `${i === 0 ? `<div class="nav-section">${globalThis.OpenDevI18n.language==='en'?'DISCOVER & REVIEW':'发现与评测'}</div>` : n[0] === 'contacts' ? `<div class="nav-section">${globalThis.OpenDevI18n.language==='en'?'RELATIONSHIPS & SUPPORT':'建联与支持'}</div>` : n[0] === "operations" ? `<div class="nav-section">${globalThis.OpenDevI18n.language==='en'?'WORKSPACE':'工作空间'}</div>` : ""}<a href="#${n[0]}" class="nav-item ${state.route === n[0] ? "active" : ""}" ${state.route === n[0] ? 'aria-current="page"' : ""}><span class="nav-icon" aria-hidden="true">${globalThis.SponsorshipIcons(n[0])}</span>${n[1]}</a>`,
      )
      .join("");
  }
  const empty = (title, text, button = "") =>
    `<div class="empty"><div class="empty-mark" aria-hidden="true">◇</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${button}</div>`;
  const button = (text, action, data = "", primary = false) =>
    `<button class="button ${primary ? "primary" : ""}" data-do="${esc(action)}" ${data}>${esc(text)}</button>`;
  const heading = (title, description, actions = "") =>
    `<div class="page-heading"><div><h1>${esc(title)}</h1><p class="muted">${esc(description)}</p></div><div class="heading-actions">${actions}</div></div>`;
  const raw = (title, value) =>
    `<details><summary>${esc(title)}</summary><pre>${esc(JSON.stringify(parse(value, value), null, 2))}</pre></details>`;
  const external = (url, text) => {
    try {
      const u = new URL(url);
      return ["https:", "http:"].includes(u.protocol)
        ? `<a href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(text)} ↗</a>`
        : esc(text);
    } catch {
      return esc(text);
    }
  };
  function stat(label, value, note) {
    return `<div class="stat"><div class="stat-label">${esc(label)}<span aria-hidden="true">↗</span></div><div class="stat-number">${Number(value || 0).toLocaleString()}</div><div class="stat-note">${esc(note)}</div></div>`;
  }
  function total(rows) {
    return (rows || []).reduce((n, r) => n + r.count, 0);
  }
  function count(rows, key, value) {
    return (rows || [])
      .filter((r) => r[key] === value)
      .reduce((n, r) => n + r.count, 0);
  }
  function projectRows(rows) {
    const I=globalThis.OpenDevI18n,tx=(zh,en)=>I.language==='en'?en:zh;
    return rows.map(p=>{
      const description=(I.language==='zh-CN'?p.description_zh:p.description_en)||p.description||tx('等待补充项目描述','Description unavailable');
      const clue = {with_clue: tx('有线索','Contact clue'), without_clue: tx('无线索','No clue'), do_not_contact: tx('禁止联系','Do not contact')}[p.contact_class] || tx('待扫描','Not scanned');
      const ranking = p.ranking_rank ? `#${esc(String(p.ranking_rank))}` : '—';
      return `<tr><td class="repository-summary"><div class="project-cell"><span class="project-symbol">${esc(p.full_name.split('/').pop().slice(0,1).toUpperCase())}</span><div><a class="project-title" href="#projects/${p.id}">${esc(p.full_name)}</a><p class="project-description">${esc(description)}</p><div class="project-row-meta"><span>${esc(views.category(p.category))}</span><span>${esc(p.language||'—')}</span><span>☆ ${Number(p.stars||0).toLocaleString(I.locale)}</span><span class="mono">${esc(p.uid||'—')}</span></div></div></div></td><td>${views.selectionBadge(p.selection)}</td><td>${badge(p.stage)}<div class="audience-note">${esc(labels[p.audience]||p.audience)}</div></td><td data-label="${tx('榜单/线索','Rank / contact')}"><strong>${ranking}</strong><div class="audience-note">${esc(clue)} · ${Number(p.priority_score||0).toFixed(0)}</div></td><td data-label="${tx('负责人','Owner')}">${esc(p.owner||tx('未分配','Unassigned'))}</td><td class="muted" data-label="${tx('证据更新','Evidence updated')}">${fmt(p.refreshed_at)}</td><td><a class="row-open" href="#projects/${p.id}" aria-label="${tx('查看','View')} ${esc(p.full_name)}">↗</a></td></tr>`;
    }).join('');
  }
  function projectTable(rows) {
    const tx=(zh,en)=>globalThis.OpenDevI18n.language==='en'?en:zh;
    return `<div class="table-wrap repository-table"><table><thead><tr><th>${tx('项目与简介','Project and description')}</th><th>${tx('入选评估','Selection')}</th><th>${tx('阶段与受众','Stage and audience')}</th><th>${tx('榜单/线索','Rank / contact')}</th><th>${tx('负责人','Owner')}</th><th>${tx('证据更新','Evidence updated')}</th><th></th></tr></thead><tbody>${projectRows(rows)}</tbody></table></div>`;
  }
  const inboxNames = {
    unassigned_projects: globalThis.OpenDevI18n.language === 'en' ? 'Needs assignment' : '待分配负责人',
    awaiting_collection: globalThis.OpenDevI18n.language === 'en' ? 'Needs evidence refresh' : '采集证据待补齐',
    pending_selection: globalThis.OpenDevI18n.language === 'en' ? 'Needs selection assessment' : '待入选评估',
    pending_technical_reviews: globalThis.OpenDevI18n.language === 'en' ? 'Needs technical review' : '待第一轮技术复核',
    pending_experiences: globalThis.OpenDevI18n.t("待真实体验"), running_experiences: globalThis.OpenDevI18n.t("Agent 执行中"),
    stale_experiences: globalThis.OpenDevI18n.t("体验需更新"), ready_for_contact: globalThis.OpenDevI18n.t("可以联系"),
    blocked_contacts: globalThis.OpenDevI18n.t("联系条件待补齐"), due_followups: globalThis.OpenDevI18n.t("到期复盘"), dead_jobs: globalThis.OpenDevI18n.t("采集失败待处理"),
  };
  const inboxNotes = {
    unassigned_projects: globalThis.OpenDevI18n.language === 'en' ? 'Assign an owner and audience' : '指定负责人和目标受众',
    awaiting_collection: globalThis.OpenDevI18n.language === 'en' ? 'Refresh evidence for the current version' : '补齐当前版本可核对证据',
    pending_selection: globalThis.OpenDevI18n.language === 'en' ? 'Assess scope and maintenance evidence' : '核对场景、维护和入选依据',
    pending_technical_reviews: globalThis.OpenDevI18n.language === 'en' ? 'Review implementation and documentation' : '核查核心实现、工程与文档',
    pending_experiences: globalThis.OpenDevI18n.t("按当前版本与有效审核安排体验"), running_experiences: globalThis.OpenDevI18n.t("已有有效执行租约，等待结果"),
    stale_experiences: globalThis.OpenDevI18n.t("版本或审核变化，先更新体验条件"), ready_for_contact: globalThis.OpenDevI18n.t("两轮通过且有可联系维护者"),
    blocked_contacts: globalThis.OpenDevI18n.t("查看证据、维护者或联系限制"), due_followups: globalThis.OpenDevI18n.t("记录里程碑与下一次安排"), dead_jobs: globalThis.OpenDevI18n.t("核对失败原因后重新安排"),
  };
  async function inboxPage(kind) {
    const allowed = state.config.inbox_kinds || [];
    if (!allowed.length) return heading(globalThis.OpenDevI18n.t("团队待办"), globalThis.OpenDevI18n.t("当前角色没有待办读取权限。")) + empty(globalThis.OpenDevI18n.t("按你的角色继续工作"), globalThis.OpenDevI18n.t("可在对应的审批、赞助台账或审计页面查看记录。"));
    kind = kind || allowed[0];
    const data = await api(`/api/inbox?kind=${encodeURIComponent(kind)}&page=${state.page}&page_size=15`);
    const recordRoutes = { pending_experiences: "experiences", running_experiences: "experiences", stale_experiences: "experiences", due_followups: "followups", dead_jobs: "operations" };
    return heading(globalThis.OpenDevI18n.t("团队待办"), globalThis.OpenDevI18n.t("只看当前需要处理的工作，每一项都连接到对应项目或记录。")) +
      globalThis.OpenDevI18n.html`<nav class="inbox-tabs" aria-label="待办类别">${allowed.map(k => `<a href="#inbox/${k}" ${k === kind ? 'aria-current="page"' : ""}>${esc(inboxNames[k])}<span class="count">${data.counts[k] || 0}</span></a>`).join("")}</nav>` +
      globalThis.OpenDevI18n.html`<section class="panel"><div class="panel-head"><h3>${esc(inboxNames[kind])}</h3><span class="muted small">${data.total} 项</span></div>${data.items.length ? data.items.map(item => {
        const projectTabs = {unassigned_projects:'',awaiting_collection:'',pending_selection:'/selection',pending_technical_reviews:'/reviews'};
        const href = recordRoutes[kind] ? `#${recordRoutes[kind]}/${encodeURIComponent(item.id)}` : `#projects/${Number(item.project_id)}${projectTabs[kind] ?? '/contacts'}`;
        const title = globalThis.OpenDevI18n.language === 'en' ? item.title_en || item.title : item.title;
        const reason = globalThis.OpenDevI18n.language === 'en' ? item.reason_en || item.reason : item.reason;
        return globalThis.OpenDevI18n.html`<article class="inbox-item"><div><h3>${esc(title)}</h3><p>${esc(kind === 'dead_jobs' ? reason : friendly({ message: reason }))}</p></div><a class="button" href="${href}">查看并处理 →</a></article>`;
      }).join("") : empty(globalThis.OpenDevI18n.t("这一项已经处理妥当"), globalThis.OpenDevI18n.t("当前没有符合该类别的待办。新任务满足条件后会自动出现在这里。"))}${pager(data)}</section>`;
  }
  async function overview() {
    const [s, p] = await Promise.all([
      api("/api/status"),
      api("/api/projects?page_size=5"),
    ]);
    const aiCount = status => Number((s.ai_assessments || {})[status] || 0);
    const all = total(s.projects);
    const groups = [
      [globalThis.OpenDevI18n.t("技术复核"), ["discovered", "technical_review"]],
      [globalThis.OpenDevI18n.t("产品体验"), ["experience"]],
      [globalThis.OpenDevI18n.t("联系与访谈"), ["ready_contact", "contacted", "interviewed"]],
      [globalThis.OpenDevI18n.t("待发放"), ["approved"]],
      [globalThis.OpenDevI18n.t("赞助中"), ["sponsored"]],
    ];
    const values = groups.map(([_, keys]) =>
      keys.reduce((n, k) => n + count(s.projects, "stage", k), 0),
    );
    const max = Math.max(1, ...values);
    const bars = groups
      .map(
        ([name], i) =>
          `<div class="funnel-row"><span>${name}</span><progress class="stage-progress" value="${values[i]}" max="${max}" aria-label="${name}"></progress><span class="funnel-number">${values[i]}</span></div>`,
      )
      .join("");
    const todo = (state.config.inbox_kinds || []).map(kind => [inboxNames[kind], (s[kind] || []).length, inboxNotes[kind], kind]).filter(row=>row[1]>0);
    return (
      heading(
        globalThis.OpenDevI18n.t("工作概览"),
        globalThis.OpenDevI18n.t("发现有潜力的开发者，让每一步支持有据可循。"),
        cap("import") ? button(globalThis.OpenDevI18n.t("＋ 导入项目"), "import", "", true) : "",
      ) +
      `<div class="overview-context"><p>${globalThis.OpenDevI18n.language==='en'?'Focus on current evidence, clear ownership, and the next actionable step.':'围绕当前证据、明确负责人和可执行的下一步推进项目。'}</p><a href="#criteria">${globalThis.OpenDevI18n.language==='en'?'Selection criteria':'查看入选标准'} ↗</a></div>` +
      `<div class="stats">${stat(globalThis.OpenDevI18n.t("已采集项目"), s.collected_projects ?? 0, globalThis.OpenDevI18n.t("当前版本已有完整证据快照"))}${stat(globalThis.OpenDevI18n.t("已筛选项目"), s.screened_projects, globalThis.OpenDevI18n.t("已有最新入选评估"))}${stat(globalThis.OpenDevI18n.t("已入选项目"), s.selected_projects, globalThis.OpenDevI18n.t("评估结果为入选"))}${stat(globalThis.OpenDevI18n.t("已淘汰项目"), s.rejected_projects, globalThis.OpenDevI18n.t("评估结果为排除"))}${stat(globalThis.OpenDevI18n.t("待筛选项目"), s.pending_screening_projects, globalThis.OpenDevI18n.t("尚未完成入选评估"))}${stat(globalThis.OpenDevI18n.t("有联系方式线索"), s.contact_with_clue_projects, globalThis.OpenDevI18n.t("来自 README、主页或 Issue"))}${stat(globalThis.OpenDevI18n.t("无线索项目"), s.contact_without_clue_projects, globalThis.OpenDevI18n.t("后置或进入人工例外队列"))}${stat(globalThis.OpenDevI18n.t("榜单项目"), s.ranked_projects, globalThis.OpenDevI18n.t("保留榜单来源和排名证据"))}${stat(globalThis.OpenDevI18n.t("AI 已完成"), aiCount("completed"), globalThis.OpenDevI18n.t("规则评估完成，仍需人工复核"))}${stat(globalThis.OpenDevI18n.t("AI 失败"), aiCount("failed"), globalThis.OpenDevI18n.t("服务或 JSON 校验失败，需人工重试"))}${stat(globalThis.OpenDevI18n.t("AI 已过期"), aiCount("expired"), globalThis.OpenDevI18n.t("输入快照已变化，结果不再有效"))}</div>` +
      globalThis.OpenDevI18n.html`<div class="section-grid"><section class="panel"><div class="panel-head"><h3>项目推进</h3><span class="small muted">当前阶段分布</span></div><div class="panel-body"><div class="funnel">${bars}</div><p class="quiet-note">各阶段为当前项目数量，不代表历史转化率。待观察和已结束项目保留在项目列表中。</p></div></section><section class="panel"><div class="panel-head"><h3>团队待办</h3><a class="small" href="#inbox">查看全部 →</a></div><div class="panel-body">${todo.length ? todo.slice(0,6).map((x, i) => `<a class="todo" href="#inbox/${x[3]}"><span class="todo-index">0${i + 1}</span><div class="todo-text"><strong>${esc(x[0])}</strong><p>${esc(x[2])}</p></div><span class="count">${x[1]}</span><span class="todo-arrow" aria-hidden="true">→</span></a>`).join("") : globalThis.OpenDevI18n.t('<p class="quiet-note">当前身份按角色查看审批、台账或审计记录。</p>')}</div></section></div>` +
      globalThis.OpenDevI18n.html`<section class="panel"><div class="panel-head"><h3>候选项目</h3><a class="small" href="#projects">查看全部 →</a></div>${p.items.length ? projectTable(p.items) : empty(globalThis.OpenDevI18n.t("从一个值得关注的项目开始"), globalThis.OpenDevI18n.t("导入 GitHub 仓库，或由采集工作进程按配置发现项目。") + (cap("import") ? "" : globalThis.OpenDevI18n.t("请项目运营人员导入首批候选。")), cap("import") ? button(globalThis.OpenDevI18n.t("导入第一个项目"), "import") : "")}</section>`
    );
  }
  function pager(data) {
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    return globalThis.OpenDevI18n.html`<div class="pagination"><span>共 ${data.total} 条 · 第 ${data.page} / ${pages} 页</span><div class="inline-actions"><button class="button" data-do="page" data-page="${data.page - 1}" ${data.page <= 1 ? "disabled" : ""}>上一页</button><button class="button" data-do="page" data-page="${data.page + 1}" ${data.page >= pages ? "disabled" : ""}>下一页</button></div></div>`;
  }
  async function projects() {
    const tx=(zh,en)=>globalThis.OpenDevI18n.language==='en'?en:zh;
    const query=new URLSearchParams({page:state.page,page_size:15,q:state.q,stage:state.stage,category:state.category,selection:state.selection,audience:state.audience,contact_class:state.contact_class,source:state.source,sort:state.sort});
    const data=await api('/api/projects?'+query);
    const options=(values,current)=>values.map(([key,label])=>`<option value="${key}" ${current===key?'selected':''}>${esc(label)}</option>`).join('');
    return heading(tx('候选项目','Candidate projects'),tx('先确认范围与维护，再依据技术和真实体验决定是否推进。','Confirm scope and maintenance, then use technical evidence and real experience to decide what advances.'),cap('import')?button(tx('导入项目','Import projects'),'import','',true):'')+
      `<section class="panel candidate-panel"><form id="project-search" class="candidate-filters"><label class="candidate-search">${tx('搜索项目','Search projects')}<input name="q" type="search" value="${esc(state.q)}" placeholder="${tx('仓库名称、中文或英文简介','Repository name, Chinese or English description')}"></label>
      <label>${tx('入选状态','Selection status')}<select name="selection">${options([['',tx('全部状态','All statuses')],...['unassessed','include','hold','exclude','stale'].map(k=>[k,views.selection(k)])],state.selection)}</select></label>
      <label>${tx('项目方向','Category')}<select name="category">${options([['',tx('全部方向','All categories')],...Object.keys(views.categoryNames).map(k=>[k,views.category(k)])],state.category)}</select></label>
      <label>${tx('当前阶段','Stage')}<select name="stage">${options([['',tx('全部阶段','All stages')],...Object.entries(stages).filter(([k])=>k!=='reject')],state.stage)}</select></label>
      <label>${tx('目标用户','Audience')}<select name="audience">${options([['',tx('全部受众','All audiences')],...['unknown','human','agent','both'].map(k=>[k,labels[k]])],state.audience)}</select></label>
      <label>${tx('联系方式','Contact clue')}<select name="contact_class">${options([['',tx('全部线索状态','All contact states')],['with_clue',tx('有公开线索','Public clue')],['without_clue',tx('无线索','No clue')],['do_not_contact',tx('禁止联系','Do not contact')]],state.contact_class)}</select></label>
      <label>${tx('来源','Source')}<input name="source" type="search" value="${esc(state.source)}" placeholder="${tx('github_trending 等','github_trending, etc.')}" /></label>
      <label>${tx('排序','Sort')}<select name="sort">${options([['updated',tx('最近更新','Recently updated')],['priority',tx('运营优先级','Operations priority')],['name',tx('仓库名称','Repository name')],['stars',tx('Stars（仅供参考）','Stars (context only)')]],state.sort)}</select></label>
      <div class="filter-actions"><button class="button primary" type="submit">${tx('筛选','Apply filters')}</button><button type="button" class="button subtle" data-do="clear-project-filters">${tx('重置','Reset')}</button></div></form>
      <div class="collection-context"><span>${tx('共','Total')} <strong>${data.total}</strong> ${tx('个候选','candidates')}</span><a href="#criteria">${tx('入选标准与流程','Selection criteria and workflow')} ↗</a></div>
      ${data.items.length?projectTable(data.items):empty(tx('没有匹配的项目','No matching projects'),tx('调整筛选条件，或导入新的候选仓库。','Adjust your filters or import a candidate repository.'))}${pager(data)}</section>`;
  }
  function putRecord(table, r) {
    const key = `${table}:${r.id}`;
    state.records.set(key, r);
    return esc(key);
  }
  function recordAction(text, action, table, r) {
    return button(
      text,
      "action",
      `data-action="${action}" data-record="${putRecord(table, r)}"`,
    );
  }
  async function list(table, projectId = null, size = 15, page = projectId ? 1 : state.page) {
    if (!canRead(table))
      return { items: [], total: 0, page: 1, page_size: size };
    return api(
      `/api/records/${table}?page=${page}&page_size=${size}${projectId ? `&project_id=${projectId}` : ""}`,
    );
  }
  function recordTable(table, rows) {
    const configs = {
      experiences: {
        columns: [globalThis.OpenDevI18n.t("体验任务 / 项目"), globalThis.OpenDevI18n.t("目标用户"), globalThis.OpenDevI18n.t("结果"), globalThis.OpenDevI18n.t("创建时间"), ""],
        cells: (r) => {
          const spec = parse(r.spec);
          const result = parse(r.result);
          return [
            globalThis.OpenDevI18n.html`<strong>${esc(spec.scenario || short(r.id))}</strong><span class="project-description">${esc(r.id)} · <a href="#projects/${r.project_id}">项目 ${r.project_id}</a></span>`,
            badge(r.audience),
            r.status === "completed"
              ? badge(result.success ? "pass" : result.failure_kind)
              : badge(r.status),
            fmt(r.created_at),
            globalThis.OpenDevI18n.html`<div class="inline-actions"><button class="text-link" data-do="inspect" data-record="${putRecord(table, r)}">详情</button>${r.audience === "human" && r.status === "pending" && can("experience-result") ? globalThis.OpenDevI18n.html`<button class="text-link" data-do="action" data-action="experience-result" data-record="${putRecord(table, r)}">记录体验</button>` : ""}</div>`,
          ];
        },
      },
      contacts: {
        columns: [globalThis.OpenDevI18n.t("联系记录 / 项目"), globalThis.OpenDevI18n.t("渠道"), globalThis.OpenDevI18n.t("状态"), globalThis.OpenDevI18n.t("负责人"), ""],
        cells: (r) => [
          globalThis.OpenDevI18n.html`<a href="#projects/${r.project_id}">项目 ${r.project_id}</a><span class="project-description">${esc(short(r.id))} · 维护者 ${r.maintainer_id}</span>`,
          esc(r.channel),
          badge(r.status),
          esc(r.actor),
          globalThis.OpenDevI18n.html`<div class="inline-actions"><button class="text-link" data-do="inspect" data-record="${putRecord(table, r)}">详情</button>${can("contact-status") && ["draft", "sent", "replied"].includes(r.status) ? globalThis.OpenDevI18n.html`<button class="text-link" data-do="action" data-action="contact-status" data-record="${putRecord(table, r)}">更新记录</button>` : ""}${r.status === "replied" && can("interview") ? globalThis.OpenDevI18n.html`<button class="text-link" data-do="action" data-action="interview" data-record="${putRecord(table, r)}">访谈</button>` : ""}</div>`,
        ],
      },
      grants: {
        columns: [globalThis.OpenDevI18n.t("赞助编号"), globalThis.OpenDevI18n.t("审批编号"), globalThis.OpenDevI18n.t("发放状态"), globalThis.OpenDevI18n.t("更新时间"), ""],
        cells: (r) => [
          `<span class="mono">${esc(short(r.id))}</span>`,
          `<span class="mono">${esc(short(r.decision_id))}</span>`,
          badge(r.status),
          fmt(r.updated_at),
          globalThis.OpenDevI18n.html`<div class="inline-actions"><button class="text-link" data-do="inspect" data-record="${putRecord(table, r)}">台账</button>${cap("credits_plan") ? globalThis.OpenDevI18n.html`<button class="text-link" data-do="credits-plan" data-record="${putRecord(table, r)}">核对计划</button>` : ""}${cap("credits_reconcile") ? globalThis.OpenDevI18n.html`<button class="text-link" data-do="credits-reconcile" data-record="${putRecord(table, r)}">查询到账</button>` : ""}</div>`,
        ],
      },
      followups: {
        columns: [globalThis.OpenDevI18n.t("复盘日期"), globalThis.OpenDevI18n.t("赞助编号"), globalThis.OpenDevI18n.t("状态"), ""],
        cells: (r) => [
          esc(r.due_at),
          `<span class="mono">${esc(short(r.grant_id))}</span>`,
          badge(r.status),
          globalThis.OpenDevI18n.html`<div class="inline-actions"><button class="text-link" data-do="inspect" data-record="${putRecord(table, r)}">详情</button>${r.status === "pending" && can("followup") ? globalThis.OpenDevI18n.html`<button class="text-link" data-do="action" data-action="followup" data-record="${putRecord(table, r)}">记录复盘</button>` : ""}</div>`,
        ],
      },
      jobs: {
        columns: [globalThis.OpenDevI18n.t("任务"), globalThis.OpenDevI18n.t("状态"), globalThis.OpenDevI18n.t("尝试次数"), globalThis.OpenDevI18n.t("创建时间"), ""],
        cells: (r) => [
          `<strong>#${r.id}</strong> <span class="muted">${esc(r.kind)}</span>`,
          badge(r.state),
          r.attempts,
          fmt(r.created_at),
          globalThis.OpenDevI18n.html`<div class="inline-actions"><button class="text-link" data-do="inspect" data-record="${putRecord(table, r)}">详情</button>${r.state === "dead" && cap("schedule") ? globalThis.OpenDevI18n.html`<button class="text-link" data-do="retry" data-record="${putRecord(table, r)}">重试</button>` : ""}</div>`,
        ],
      },
      events: {
        columns: [globalThis.OpenDevI18n.t("事件"), globalThis.OpenDevI18n.t("操作人"), globalThis.OpenDevI18n.t("对象"), globalThis.OpenDevI18n.t("时间"), ""],
        cells: (r) => [
          esc(actionNames[r.action] || r.action),
          esc(r.actor),
          `<span class="mono">${esc(r.entity_type)} / ${esc(short(r.entity_id))}</span>`,
          fmt(r.created_at),
          globalThis.OpenDevI18n.html`<button class="text-link" data-do="inspect" data-record="${putRecord(table, r)}">查看证据</button>`,
        ],
      },
    };
    const c = configs[table];
    if (!c) return "";
    return `<div class="table-wrap"><table><thead><tr>${c.columns.map((t) => `<th>${t}</th>`).join("")}</tr></thead><tbody>${rows
      .map(
        (r) =>
          `<tr>${c
            .cells(r)
            .map((v, i) => `<td${i === 0 ? ' class="wrap"' : ""}>${v}</td>`)
            .join("")}</tr>`,
      )
      .join("")}</tbody></table></div>`;
  }
  async function recordPage(table, title, description, recordId = null) {
    const data = recordId ? { items: [(await api(`/api/records/${table}/${encodeURIComponent(recordId)}`)).item], total: 1, page: 1, page_size: 1 } : await list(table);
    let note = "";
    if (table === "experiences")
      note =
        globalThis.OpenDevI18n.t('<div class="notice">面向人的产品由团队真实体验；面向 Agent 的产品通过专用执行器提交结果。第二轮审核由负责人完成。</div>');
    if (table === "contacts")
      note =
        globalThis.OpenDevI18n.t('<div class="notice">这里保存联系草稿和已发生的沟通记录。发送消息由团队在对应渠道执行。</div>');
    if (table === "grants" && !state.config.credits_configured)
      note =
        globalThis.OpenDevI18n.t('<div class="notice warning">尚未连接 Credits 服务。可以查看审批台账，真实邀请与到账核对需要管理员完成配置。</div>');
    return (
      heading(title, description, recordId ? globalThis.OpenDevI18n.html`<a class="button" href="#${table}">查看全部记录</a>` : "") +
      note +
      globalThis.OpenDevI18n.html`<section class="panel"><div class="panel-head"><h3>${title}</h3><span class="muted small">${data.total} 条记录</span></div>${data.items.length ? recordTable(table, data.items) : empty(globalThis.OpenDevI18n.t("暂无记录"), globalThis.OpenDevI18n.t("项目达到对应流程阶段后，记录会出现在这里。"))}${pager(data)}</section>`
    );
  }
  async function operations() {
    const [s, j] = await Promise.all([api("/api/status"), list("jobs")]);
    const rate = s.github_rate_limit || {};
    const rateNotice = rate.backoff_active
      ? `<div class="notice warning">GitHub 当前处于退避窗口，队列会在配额恢复后继续；请求间隔由共享限流闸门统一控制。</div>`
      : `<div class="notice">发现任务使用服务器配置的检索规则。已采集项目只统计当前版本存在完整证据快照的项目；工作进程会按共享配额闸门继续处理队列。</div>`;
    return (
      heading(
        globalThis.OpenDevI18n.t("采集与运行"),
        globalThis.OpenDevI18n.t("查看任务进度与检索覆盖，处理需要人工关注的失败任务。"),
        cap("schedule") ? button(globalThis.OpenDevI18n.t("安排发现任务"), "schedule", "", true) : "",
      ) +
      globalThis.OpenDevI18n.html`<div class="stats">${stat(globalThis.OpenDevI18n.t("已采集项目"), Number(s.collected_projects||0), globalThis.OpenDevI18n.t("当前版本已有完整证据快照"))}${stat(globalThis.OpenDevI18n.t("待采集项目"), Number(s.collection_pending_projects||0), globalThis.OpenDevI18n.t("项目已进入工作空间但当前版本证据未完成"))}${stat(globalThis.OpenDevI18n.t("可领取"), Number(s.queue_ready||0), globalThis.OpenDevI18n.t("工作进程可立即消费"))}${stat(globalThis.OpenDevI18n.t("限流等待"), Number(s.queue_delayed||0), globalThis.OpenDevI18n.t("GitHub 限流或退避后才会领取"))}${stat(globalThis.OpenDevI18n.t("采集完成"), Number(s.collection_jobs_done||0), globalThis.OpenDevI18n.t("搜索、榜单和刷新任务累计完成"))}${stat(globalThis.OpenDevI18n.t("采集失败"), Number(s.collection_jobs_dead||0), globalThis.OpenDevI18n.t("查看原因后手动重试"))}${stat(globalThis.OpenDevI18n.t("运行中"), count(s.jobs, "state", "running"), globalThis.OpenDevI18n.t("具有有效执行租约的任务状态"))}</div>${rateNotice}<section class="panel"><div class="panel-head"><h3>采集任务</h3><span class="small muted">完整覆盖 ${count(s.coverage, "status", "complete")} · 部分覆盖 ${count(s.coverage, "status", "partial")}</span></div>${j.items.length ? recordTable("jobs", j.items) : empty(globalThis.OpenDevI18n.t("还没有采集任务"), globalThis.OpenDevI18n.t("安排发现任务，或从候选项目页面导入指定仓库。"))}${pager(j)}</section>`
    );
  }
  async function projectDetail(
    id,
    tab = "evidence",
    generation = state.generation,
    page = 1,
    secondaryPage = 1,
  ) {
    const d = await api(`/api/projects/${id}`);
    const p = d.project,
      m = parse(p.metadata),
      snap = d.snapshot,
      e = parse(snap?.evidence);
    const tabs = [
      ["evidence", globalThis.OpenDevI18n.t("仓库档案")],
      ["selection", globalThis.OpenDevI18n.language === "en" ? "Selection assessment" : "入选评估"],
      ["reviews", globalThis.OpenDevI18n.t("两轮审核")],
      ...(canRead("experiences") ? [["experiences", globalThis.OpenDevI18n.t("真实体验")]] : []),
      ...(canRead("contacts") ? [["contacts", globalThis.OpenDevI18n.t("联系与访谈")]] : []),
      ...(canRead("decisions") ? [["decisions", globalThis.OpenDevI18n.t("赞助审批")]] : []),
      ...(canRead("grants") || canRead("followups") ? [["support", globalThis.OpenDevI18n.t("发放与复盘")]] : []),
    ];
    if (!tabs.some((t) => t[0] === tab)) tab = "evidence";
    let content = "";
    if (tab === "evidence") {
      content = views.dossierView(d) + views.technicalView(d) + (snap?`<section class="panel"><div class="panel-body">${raw(globalThis.OpenDevI18n.t('展开完整技术证据'),e)}</div></section>`:'');
    }
    if (tab === "selection") content = views.assessmentView(d) + views.assessmentHistory(d, await list("qualifications", id, 25, page));
    if (tab === "reviews") content = views.reviewView(d, await list("reviews", id, 25, page));
    if (tab === "support") {
      const [grants, followups] = await Promise.all([list("grants", id, 15, page),list("followups", id, 15, secondaryPage)]);
      content = `<div class="notice">${globalThis.OpenDevI18n.language==="en"?"New reviews and grants require current evidence. Existing issuances still need reconciliation, and received grants still need follow-up.":"新评审与新赞助须满足当前证据要求；已发生的发放继续对账，已到账的赞助继续复盘。"}</div>`;
      if (canRead("grants")) content += globalThis.OpenDevI18n.html`<section class="panel"><div class="panel-head"><h3>赞助台账</h3></div>${grants.items.length?recordTable("grants",grants.items):empty(globalThis.OpenDevI18n.t("暂无记录"),globalThis.OpenDevI18n.t("项目达到对应流程阶段后，记录会出现在这里。"))}${views.historyPager(grants,n=>`#projects/${id}/support/${n}/${secondaryPage}`)}</section>`;
      if (canRead("followups")) content += globalThis.OpenDevI18n.html`<section class="panel"><div class="panel-head"><h3>后续复盘</h3></div>${followups.items.length?recordTable("followups",followups.items):empty(globalThis.OpenDevI18n.t("暂无记录"),globalThis.OpenDevI18n.t("项目达到对应流程阶段后，记录会出现在这里。"))}${views.historyPager(followups,n=>`#projects/${id}/support/${page}/${n}`)}</section>`;
    }
    if (tab === "experiences") {
      const rows = await list("experiences", id, 25, page);
      content = globalThis.OpenDevI18n.html`<div class="notice">审核通过需要覆盖项目的全部目标用户。环境缺失和产品失败需要如实记录。</div><section class="panel"><div class="panel-head"><h3>体验任务</h3>${can("experience-create") ? button(globalThis.OpenDevI18n.t("创建体验"), "action", 'data-action="experience-create"') : ""}</div>${rows.items.length ? recordTable("experiences", rows.items) : empty(globalThis.OpenDevI18n.t("还没有体验任务"), globalThis.OpenDevI18n.t("第一轮通过后，设计真实使用场景并创建体验任务。"))}${views.historyPager(rows,n=>`#projects/${id}/experiences/${n}`)}</section>`;
    }
    if (tab === "contacts") {
      const [cs, is] = await Promise.all([
        list("contacts", id, 25, page),
        list("interviews", id, 25, secondaryPage),
      ]);
      content = globalThis.OpenDevI18n.html`<section class="panel"><div class="panel-head"><h3>维护者确认</h3><span class="small muted">${(d.maintainers || []).length} 位候选</span></div>${(d.maintainers || []).map((r) => `<article class="record-card"><div class="record-top"><a class="project-title" href="#developers/${r.id}">${esc(r.login)}</a>${badge(r.confirmed ? "pass" : "pending")}</div><small>GitHub ID ${r.id} · ${esc(r.role)}${r.do_not_contact ? globalThis.OpenDevI18n.t(" · 已拒绝联系") : ""}</small><div class="record-actions">${!r.confirmed && can("maintainer-confirm") ? recordAction(globalThis.OpenDevI18n.t("确认维护关系"), "maintainer-confirm", "maintainers", { ...r, project_id: Number(id) }) : ""}${r.confirmed && !r.do_not_contact && can("contact-draft") ? recordAction(globalThis.OpenDevI18n.t("创建联系草稿"), "contact-draft", "maintainers", { ...r, project_id: Number(id) }) : ""}</div></article>`).join("") || empty(globalThis.OpenDevI18n.t("暂无维护者候选"), globalThis.OpenDevI18n.t("刷新仓库采集后可获取公开作者资料。"))}</section><section class="panel"><div class="panel-head"><h3>${globalThis.OpenDevI18n.t("已发现的公开联系方式")}</h3><span class="small muted">${(d.contact_points?.items || []).length} 条</span></div>${d.contact_points?.eligible ? ((d.contact_points.items || []).map((r) => `<article class="record-card"><div class="record-top"><strong>${esc(r.login || globalThis.OpenDevI18n.t("项目入口"))}</strong><span class="badge">${esc(r.channel)}</span></div><p>${r.channel === 'website' || r.channel === 'github' || r.channel === 'social' ? external(r.value, r.value) : `<a href="mailto:${esc(r.value)}">${esc(r.value)}</a>`}</p><small>${esc(r.source)} · ${esc(r.confidence)}${r.source_file ? ` · ${esc(r.source_file)} 第 ${r.source_row} 行` : ""}</small></article>`).join('') || empty(globalThis.OpenDevI18n.t("暂未发现联系方式"), globalThis.OpenDevI18n.t("只显示 GitHub 或项目公开页面，以及已导入 CSV 的线索。"))) : empty(globalThis.OpenDevI18n.t("项目尚未入选"), globalThis.OpenDevI18n.t("完成入选后才会自动整理项目作者的公开联系方式。"))}</section><section class="panel"><div class="panel-head"><h3>联系记录</h3></div>${cs.items.length ? recordTable("contacts", cs.items) : empty(globalThis.OpenDevI18n.t("尚无联系记录"), globalThis.OpenDevI18n.t("两轮通过并确认维护关系后，创建联系草稿。"))}${views.historyPager(cs,n=>`#projects/${id}/contacts/${n}/${secondaryPage}`)}</section><section class="panel"><div class="panel-head"><h3>访谈记录</h3></div>${
        is.items.map(r=>views.interviewView(r,can("decision")?recordAction(globalThis.OpenDevI18n.t("作出赞助决定"),"decision","interviews",r):"")).join("") || empty(globalThis.OpenDevI18n.t("尚无访谈记录"), globalThis.OpenDevI18n.t("获得维护者回复并完成访谈后，在联系记录中登记。"))
      }${views.historyPager(is,n=>`#projects/${id}/contacts/${page}/${n}`)}</section>`;
    }
    if (tab === "decisions") {
      const [ds, is] = await Promise.all([
        list("decisions", id, 25, page),
        list("interviews", id, 25, secondaryPage),
      ]);
      const interviewCards = is.items.map(r=>views.interviewView(r,can("decision")?recordAction(globalThis.OpenDevI18n.t("依据此访谈审批"),"decision","interviews",r):"")).join("");
      content = globalThis.OpenDevI18n.html`<section class="panel"><div class="panel-head"><h3>赞助审批</h3>${can("decision") ? button(globalThis.OpenDevI18n.t("新增决定"), "action", 'data-action="decision"') : ""}</div>${ds.items.map(r=>views.decisionView(r)).join("") || empty(globalThis.OpenDevI18n.t("暂无审批记录"), globalThis.OpenDevI18n.t("审批须有当前两轮结论及同项目访谈依据。"))}${views.historyPager(ds,n=>`#projects/${id}/decisions/${n}/${secondaryPage}`)}</section>`;
      if (canRead("interviews")) content += globalThis.OpenDevI18n.html`<section class="panel"><div class="panel-head"><h3>访谈记录</h3></div>${interviewCards||empty(globalThis.OpenDevI18n.t("尚无访谈记录"),globalThis.OpenDevI18n.t("获得维护者回复并完成访谈后，在联系记录中登记。"))}${views.historyPager(is,n=>`#projects/${id}/decisions/${page}/${n}`)}</section>`;
    }
    const actions = [
      ["assign", globalThis.OpenDevI18n.t("分配负责人")],
      ["qualify", globalThis.OpenDevI18n.t("项目入选评估")],
      ["review-one", globalThis.OpenDevI18n.t("第一轮技术复核")],
      ["experience-create", globalThis.OpenDevI18n.t("创建真实体验")],
      ["review-two", globalThis.OpenDevI18n.t("第二轮体验复核")],
    ]
      .filter(([a]) => can(a.startsWith("review-") ? "review" : a))
      .map(([a, t]) => button(t, "action", `data-action="${a}"`))
      .join("");
    if (generation === state.generation) state.project = d;
    return globalThis.OpenDevI18n.html`<div class="page-heading"><div><p class="eyebrow"><a href="#projects">候选项目</a> / PROJECT ${id}</p><div class="detail-title"><span class="project-symbol">${esc(p.full_name.split("/").pop().slice(0, 1).toUpperCase())}</span><h1>${esc(p.full_name)}</h1></div></div>${badge(p.stage)}</div><nav class="tabs" aria-label="项目详情">${tabs.map(([t, n]) => `<a class="tab ${t === tab ? "active" : ""}" ${t === tab ? 'aria-current="page"' : ""} href="#projects/${id}/${t}">${n}</a>`).join("")}</nav><div class="detail-grid"><div>${content}</div><aside>${views.progressView(d)}<section class="panel"><div class="panel-head"><h3>推进项目</h3></div><div class="panel-body action-stack">${actions || globalThis.OpenDevI18n.t('<p class="muted small">当前角色只读。可在对应页面处理授权范围内的工作。</p>')}</div></section><section class="panel"><div class="panel-head"><h3>筛选路径</h3></div><div class="panel-body">${[globalThis.OpenDevI18n.t("技术复核"), globalThis.OpenDevI18n.t("真实体验"), globalThis.OpenDevI18n.t("联系与访谈"), globalThis.OpenDevI18n.t("赞助审批"), globalThis.OpenDevI18n.t("发放与复盘")].map((s, i) => `<div class="progress-step"><span class="step-dot">${i + 1}</span>${s}</div>`).join("")}<p class="quiet-note">项目阶段由有效证据和审核记录推进。页面操作仍受服务端门槛校验。</p></div></section></aside></div>`;
  }
  async function route(reset = false) {
    if (!state.user || !state.config) return;
    const parts = location.hash.replace(/^#/, "").split("/");
    const name = parts[0] === 'interviews' || nav.some(n => n[0] === parts[0]) ? parts[0] : "overview";
    if (name !== state.route || reset) {
      state.page = 1;
      if (name !== "projects") {
        state.q = "";
        state.stage = "";
      }
    }
    state.route = name;
    state.project = null;
    state.records.clear();
    renderNav();
    $("#breadcrumb").textContent =
      nav.find((n) => n[0] === name)?.[1] || (name === 'interviews' ? globalThis.OpenDevI18n.t('访谈记录') : globalThis.OpenDevI18n.t("项目"));
    if ($("#action-dialog").open) closeDialog();
    const generation = ++state.generation;
    $("#main").setAttribute("aria-busy", "true");
    $("#main").innerHTML = globalThis.OpenDevI18n.t('<div class="loading">正在读取数据…</div>');
    try {
      let html;
      if (name === "members") html = await accountsUI.membersPage();
      else if (name === "account") html = await accountsUI.accountPage();
      else if (name === "criteria") html = await views.criteriaPage();
      else if (name === "developers") html = /^[1-9][0-9]{0,17}$/.test(parts[1]||'') ? await developersUI.detail(parts[1],parts[2]) : await developersUI.listing();
      else if (name === "interviews") {
        if (!canRead('interviews')) html = heading(globalThis.OpenDevI18n.t('访谈记录'),'') + empty(globalThis.OpenDevI18n.language==='en'?'Interview access is not available to this role':'当前角色没有访谈读取权限','');
        else {
          const record = await api(`/api/records/interviews/${encodeURIComponent(parts[1]||'')}`);
          html = heading(globalThis.OpenDevI18n.t('访谈记录'),'',`<a class="button" href="#projects/${record.item.project_id}">${globalThis.OpenDevI18n.language==='en'?'Source project':'来源项目'}</a>`) + views.interviewView(record.item);
        }
      }
      else if (name === "inbox") html = await inboxPage(parts[1]);
      else if (name === "projects")
        html =
          parts[1] && /^\d+$/.test(parts[1])
            ? await projectDetail(parts[1], parts[2], generation, /^[1-9][0-9]{0,4}$/.test(parts[3]||'')?Number(parts[3]):1, /^[1-9][0-9]{0,4}$/.test(parts[4]||'')?Number(parts[4]):1)
            : await projects();
      else if (name === "experiences")
        html = await recordPage(
          "experiences",
          globalThis.OpenDevI18n.t("产品体验"),
          globalThis.OpenDevI18n.t("把真实任务、执行结果和第二轮审核连接起来。"),
          parts[1],
        );
      else if (name === "contacts")
        html = await recordPage(
          "contacts",
          globalThis.OpenDevI18n.t("联系与访谈"),
          globalThis.OpenDevI18n.t("从两轮合格的候选出发，记录每一次有效沟通。"),
          parts[1],
        );
      else if (name === "grants")
        html = await recordPage(
          "grants",
          globalThis.OpenDevI18n.t("赞助台账"),
          globalThis.OpenDevI18n.t("审批、邀请、领取与到账分别记录，全程可追溯。"),
          parts[1],
        );
      else if (name === "followups")
        html = await recordPage(
          "followups",
          globalThis.OpenDevI18n.t("后续复盘"),
          globalThis.OpenDevI18n.t("持续了解额度使用和项目进展，记录下一阶段安排。"),
          parts[1],
        );
      else if (name === "operations") html = parts[1] ? await recordPage("jobs", globalThis.OpenDevI18n.t("采集任务"), globalThis.OpenDevI18n.t("当前失败任务的状态与执行记录。"), parts[1]) : await operations();
      else if (name === "audit")
        html = await recordPage(
          "events",
          globalThis.OpenDevI18n.t("操作审计"),
          globalThis.OpenDevI18n.t("按责任人回溯每一次判断和业务变更。"),
          parts[1],
        );
      else html = await overview();
      if (generation === state.generation) {
        $("#main").innerHTML =
          (state.config.demo_mode
            ? globalThis.OpenDevI18n.t('<div class="notice warning">演示工作空间 · 当前记录包含明确标记的合成测试数据，未发生真实联系或 Credits 发放。</div>')
            : "") + html;
        $("#connection").innerHTML = globalThis.OpenDevI18n.t('<span class="status-dot"></span> 已连接');
        $("#main").setAttribute("aria-busy", "false");
        $("#main").focus({ preventScroll: true });
      }
    } catch (e) {
      if (generation === state.generation) {
        $("#main").setAttribute("aria-busy", "false");
        $("#main").innerHTML = empty(
          globalThis.OpenDevI18n.t("暂时无法读取"),
          friendly(e),
          button(globalThis.OpenDevI18n.t("重新加载"), "reload"),
        );
        $("#main").focus({ preventScroll: true });
      }
    }
  }
  const { actionNames, fields, fieldHtml, setAt } = globalThis.SponsorshipForms(
    labels,
    esc,
  );
  function dialogRequest() {
    const marker = ++state.dialogGeneration;
    const user = state.user;
    const generation = state.generation;
    return () => Boolean(user) && state.user === user && state.generation === generation && state.dialogGeneration === marker;
  }
  function closeDialog() {
    ++state.dialogGeneration;
    state.dialog = null;
    $("#action-dialog").close();
  }
  function dialogBase(title, description) {
    $("#dialog-title").textContent = title;
    $("#dialog-description").textContent = description;
    $("#dialog-error").textContent = "";
    $("#dialog-result").hidden = true;
    $("#dialog-result").innerHTML = "";
    $("#dialog-fields").hidden = false;
    $("#dialog-submit").hidden = false;
    $("#dialog-submit").disabled = false;
    $("#dialog-submit").textContent = globalThis.OpenDevI18n.t("保存记录");
    $("#dialog-cancel").textContent = globalThis.OpenDevI18n.t("取消");
    if (!$("#action-dialog").open) $("#action-dialog").showModal();
  }
  async function actionDialog(action, record) {
    if (!state.user) return;
    const stillCurrent = dialogRequest();
    const p = state.project?.project;
    const defaults = {
      project_id: record?.project_id || p?.id,
      sha: record?.sha || p?.head_sha,
      owner: p?.owner || state.user.actor,
      audience:
        action === "assign"
          ? p?.audience === "unknown"
            ? "human"
            : p?.audience || "human"
          : p?.audience === "both" || p?.audience === "unknown"
            ? "human"
            : p?.audience || "human",
      result: { executor: state.user.actor },
      grant_id: action === 'followup-schedule' ? record?.grant_id || record?.id || state.project?.readiness?.followup_grant_id : undefined,
      spec: { budget: { seconds: 300 } },
    };
    if (record) {
      defaults.id = record.id;
      if (action === "maintainer-confirm" || action === "contact-draft")
        defaults.maintainer_id = record.id;
      if (action === "interview") defaults.contact_id = record.id;
      if (action === "decision") defaults.interview_id = record.id;
    }
    if (action === "contact-status" && record) {
      defaults.status =
        { draft: "sent", sent: "replied", replied: "closed" }[record.status] ||
        "closed";
    }
    if (action === "review-two" && p && canRead("experiences")) {
      const data = await list("experiences", p.id, 100);
      if (!stillCurrent()) return;
      defaults.evidence = {
        experience_ids: data.items
          .filter(
            (e) =>
              e.sha === p.head_sha &&
              e.status === "completed" &&
              parse(e.result).success === true,
          )
          .map((e) => e.id),
      };
    }
    const selectionCatalog = action === 'qualify' ? await api('/api/criteria') : null;
    if (!stillCurrent()) return;
    const schema = fields(action, selectionCatalog);
    if (action.startsWith("review-"))
      schema
        .filter((f) => f.name.startsWith("evidence."))
        .forEach((f) => (f.required = false));
    if (action === "contact-status" && record) {
      const transitions = {
        draft: ["sent", "closed", "declined"],
        sent: ["replied", "closed", "declined"],
        replied: ["closed", "declined"],
      };
      const options = transitions[record.status];
      if (!options) throw new Error(globalThis.OpenDevI18n.t("这条联系记录已经结束，不能再次更新状态。"));
      schema.find((f) => f.name === "status").options = options.map(key => [key, labels[key] || key]);
    }
    state.dialog = {
      action,
      schema,
      key: crypto.randomUUID(),
      bodyFingerprint: null,
    };
    dialogBase(
      actionNames[action] || action,
      action === "decision"
        ? globalThis.OpenDevI18n.t("批准后生成赞助台账；真实发放由另一位执行人核对计划后操作。")
        : action.startsWith("review-")
          ? globalThis.OpenDevI18n.t("请根据当前版本的实际证据做出判断。提交后保留操作人和依据。")
          : action === "import"
            ? globalThis.OpenDevI18n.t("导入后进入采集队列；工作进程完成证据采集后即可审核。")
            : globalThis.OpenDevI18n.t("请填写真实信息。服务端会再次检查流程门槛和当前身份权限。"),
    );
    if(action === 'qualify') {
      const pick=globalThis.OpenDevI18n.pick;
      $("#dialog-fields").innerHTML=schema.filter(f=>!f.qualityGroup).map(f=>fieldHtml(f,defaults)).join('')+
        `<div class="quality-live form-field wide" role="status" id="quality-live"></div>`+
        selectionCatalog.quality_rubric.dimensions.map(d=>`<fieldset class="quality-editor form-field wide"><legend>${esc(pick(d.title))}<span>${d.weight}%</span></legend><p class="field-help">${esc(pick(d.body))}</p><div class="form-grid">${schema.filter(f=>f.qualityGroup?.id===d.id).sort((a,b)=>Number(b.type==='score')-Number(a.type==='score')).map(f=>fieldHtml(f,defaults)).join('')}</div></fieldset>`).join('');
    } else $("#dialog-fields").innerHTML=schema.map(f=>fieldHtml(f,defaults)).join('');
    $("#dialog-submit").textContent =
      action === "import"
        ? globalThis.OpenDevI18n.t("导入并安排采集")
        : action === "schedule"
          ? globalThis.OpenDevI18n.t("安排任务")
          : globalThis.OpenDevI18n.t("保存记录");
    syncFormConstraints();
  }
  async function inspect(record) {
    if (!state.user || !record) return;
    const stillCurrent = dialogRequest();
    dialogBase(globalThis.OpenDevI18n.t("记录详情"), globalThis.OpenDevI18n.t("原始业务记录与证据，仅供有权限的团队成员查看。"));
    state.dialog = null;
    $("#dialog-fields").innerHTML =
      `<div class="form-field wide"><pre>${esc(JSON.stringify(record, null, 2))}</pre></div>`;
    $("#dialog-submit").hidden = true;
    $("#dialog-cancel").textContent = globalThis.OpenDevI18n.t("关闭");
    if (record?.audience === "agent") {
      const marker = Symbol();
      state.inspection = marker;
      const generation = state.generation;
      const uploaded = await api(`/api/experiences/${encodeURIComponent(record.id)}/artifacts`);
      if (!stillCurrent() || generation !== state.generation || state.inspection !== marker || state.dialog !== null || !$("#action-dialog").open) return;
      $("#dialog-fields").insertAdjacentHTML("afterbegin", globalThis.OpenDevI18n.html`<div class="form-field wide"><h3>可下载的执行证据</h3>${uploaded.items.length ? uploaded.items.map(file => globalThis.OpenDevI18n.html`<div class="record-card"><a href="/api/artifacts/${encodeURIComponent(file.id)}/download" download>${esc(file.path)} ↓</a><p class="field-help">${Number(file.size).toLocaleString()} 字节 · ${file.referenced ? globalThis.OpenDevI18n.t("已关联体验结果") : globalThis.OpenDevI18n.t("已上传，尚未关联结果")}</p><p class="mono wrap">SHA-256 ${esc(file.sha256)}</p></div>`).join("") : globalThis.OpenDevI18n.t('<p class="muted small">尚无已完成上传的文件。旧版或外部执行记录需要核实原始证据。</p>')}</div>`);
    }
  }
  function syncFormConstraints(changed) {
    const form = $("#action-form");
    const field = name => form.elements.namedItem(name);
    if (state.dialog?.action === 'qualify') {
      const outcome = field('outcome').value;
      const reasonSchema = state.dialog.schema.find(f=>f.reasonOutcomes);
      const reason = field('evidence.reason_code');
      for (const option of reason.options) {
        option.hidden = option.disabled = reasonSchema.reasonOutcomes[option.value] !== outcome;
      }
      if (reasonSchema.reasonOutcomes[reason.value] !== outcome) reason.value = [...reason.options].find(o=>!o.disabled).value;
      for (const f of state.dialog.schema) {
        if (f.requiredForInclude) field(f.name).required = outcome === 'include';
        if (f.requiredForActive) field(f.name).required = field('maintenance').value === 'active';
      }
      let total=0,filled=0,floors=true;
      for(const f of state.dialog.schema.filter(f=>f.type==='score')) {
        const value=field(f.name).value;
        if(value!=='') {total+=Number(value)*f.qualityWeight/4;filled++;if(Number(value)<3)floors=false;}
      }
      const en=globalThis.OpenDevI18n.language==='en';
      $('#quality-live').textContent=`${en?'Quality review':'质量评审'} · ${total}/100 · ${filled}/5 ${en?'scored':'项已评分'} · ${en?'Required: 80+, every dimension 3+; evidence checked on save.':'门槛：总分 ≥80，每项 ≥3；保存时核验完整证据。'}`;
      $('#quality-live').classList.toggle('quality-ready',filled===5&&floors&&total>=80);
    }
    if (state.dialog?.action === "experience-result") {
      const success = field("result.success"), failure = field("result.failure_kind");
      if (success && failure) {
        if (success.value === "true") failure.value = "none";
        else if (failure.value === "none") failure.value = "environment";
        failure.disabled = success.value === "true";
      }
    }
    if (state.dialog?.action === "decision") {
      const approve = field("outcome")?.value === "approve";
      for (const f of state.dialog.schema.filter(f => f.name.startsWith("terms."))) {
        const input = field(f.name);
        input.required = approve;
        input.disabled = !approve;
        input.closest(".form-field").classList.toggle("inactive-field", !approve);
      }
    }
    if (changed?.name === "profile" && changed.checked) {
      for (const input of form.querySelectorAll('[name="profile"]')) {
        if (input !== changed && (changed.value === "undetermined" || input.value === "undetermined")) input.checked = false;
      }
    }
    if (state.dialog?.action === "followup") {
      const next = field("result.next_review_at");
      if (next) {
        next.disabled = field("result.decision")?.value === "end";
        if (next.disabled) next.value = "";
      }
    }
  }
  async function submitDialog(event) {
    event.preventDefault();
    const d = state.dialog;
    if (!d) return;
    const user = state.user;
    const current = () => state.user === user && state.dialog === d && $("#action-dialog").open;
    const submit = $("#dialog-submit");
    submit.disabled = true;
    $("#dialog-error").textContent = "";
    try {
      let body = {},
        path;
      if (d.action === "credits-execute") {
        body = { execute: true };
        path = `/api/credits/${d.grant.id}/submit`;
        if (!$("#confirm-credit")?.checked)
          throw new Error(globalThis.OpenDevI18n.t("请核对计划并勾选确认。"));
      } else {
        for (const f of d.schema) {
          const field = $(`[name="${f.name}"]`, $("#action-form"));
          if (field.disabled && f.name !== "result.failure_kind") continue;
          let v = f.type === "checkbox" ? field.checked : field.value.trim();
          if (v === "" && !f.required) {
            if (f.type === "lines") setAt(body, f.name, []);
            continue;
          }
          if (f.type === "number" || f.type === "score") v = Number(v);
          if (f.type === "lines")
            v = v
              .split(/\r?\n/)
              .map((x) => x.trim())
              .filter(Boolean);
          if (f.type === "profile") {
            v = Array.from(
              $("#action-form").querySelectorAll(`[name="${f.name}"]:checked`),
            ).map((x) => x.value);
            if (!v.length) throw new Error(globalThis.OpenDevI18n.t("请选择至少一个访谈画像。"));
          }
          if (f.name === "result.success") v = v === "true";
          setAt(body, f.name, v);
        }
        let operation = d.action;
        if (operation === "review-one" || operation === "review-two") {
          body.round = operation === "review-one" ? 1 : 2;
          operation = "review";
          if (
            body.decision !== "pass" &&
            (!body.evidence || !Object.keys(body.evidence).length)
          )
            body.evidence = { observations: body.rationale };
        }
        if (operation === "decision" && body.outcome === "approve")
          body.terms.unit = "cents";
        if (operation === "decision" && body.outcome !== "approve")
          delete body.terms;
        path =
          operation === "import"
            ? "/api/import"
            : operation === "schedule"
              ? "/api/schedule"
              : `/api/actions/${operation}`;
      }
      const fingerprint = JSON.stringify(body);
      if (d.bodyFingerprint && fingerprint !== d.bodyFingerprint)
        d.key = crypto.randomUUID();
      d.bodyFingerprint = fingerprint;
      const result = await api(path, body, d.key);
      if (!current()) return;
      closeDialog();
      toast(
        d.action === "schedule" && Object.prototype.hasOwnProperty.call(result, "scheduled_count")
          ? `${globalThis.OpenDevI18n.t("已安排")} ${Number(result.scheduled_count || result.job_ids?.length || 0)} ${globalThis.OpenDevI18n.t("个发现任务，采集进程正在处理。")}`
          : result.message_sent === false
            ? globalThis.OpenDevI18n.t("记录已保存；系统没有发送消息。")
          : d.action === "credits-execute"
            ? globalThis.OpenDevI18n.t("邀请提交已记录，请查询领取与到账状态。")
            : globalThis.OpenDevI18n.t("已保存，工作台已更新。"),
      );
      await route();
    } catch (e) {
      if (!current()) return;
      $("#dialog-error").textContent = friendly(e);
      if (d.action === "credits-execute" && (e.uncertain || e.status === 409)) {
        d.uncertain = true;
        $("#dialog-error").textContent +=
          globalThis.OpenDevI18n.t(" 请关闭窗口并查询到账，勿再次创建邀请。");
      }
    } finally {
      if (current()) submit.disabled = Boolean(d.uncertain);
    }
  }
  async function creditsPlan(record) {
    if (!state.user || !record) return;
    const stillCurrent = dialogRequest();
    const plan = await api(`/api/credits/${record.id}/plan`, {});
    if (!stillCurrent()) return;
    dialogBase(
      globalThis.OpenDevI18n.t("核对 Credits 发放计划"),
      globalThis.OpenDevI18n.t("创建邀请不等于到账。请核对接收账号、金额和活动；审批人不能执行自己批准的赞助。"),
    );
    state.dialog = {
      action: "credits-execute",
      grant: record,
      key: crypto.randomUUID(),
    };
    $("#dialog-fields").innerHTML = views.creditsPlanView(plan);
    if (
      cap("credits_submit") &&
      state.config.allow_credit_writes &&
      ["pending", "failed"].includes(record.status)
    ) {
      $("#dialog-fields").insertAdjacentHTML(
        "beforeend",
        globalThis.OpenDevI18n.t('<div class="form-field wide"><label class="checkbox-label"><input id="confirm-credit" type="checkbox" required>我已核对计划，确认创建这笔已批准的邀请。</label></div>'),
      );
      $("#dialog-submit").textContent = globalThis.OpenDevI18n.t("确认创建邀请");
    } else {
      $("#dialog-submit").hidden = true;
      $("#dialog-fields").insertAdjacentHTML(
        "beforeend",
        globalThis.OpenDevI18n.t('<p class="form-field wide field-help">当前身份、赞助状态或服务配置不允许创建邀请。可关闭窗口后查询到账。</p>'),
      );
    }
  }
  async function click(event) {
    const el = event.target.closest("[data-do]");
    if (!el || el.disabled) return;
    const action = el.dataset.do;
    const record = state.records.get(el.dataset.record);
    const user = state.user;
    const generation = state.generation;
    try {
      if (action === "action") await actionDialog(el.dataset.action, record);
      else if (action === "import" || action === "schedule")
        await actionDialog(action);
      else if (action === "clear-project-filters") { state.q=state.stage=state.category=state.selection=state.audience=state.contact_class=state.source="";state.sort="updated";state.page=1;await route(); }
      else if (action === "reload") await route();
      else if (action === "page") {
        state.page = Number(el.dataset.page);
        await route();
      } else if (action === "inspect") await inspect(record);
      else if (action === "credits-plan") await creditsPlan(record);
      else if (action === "credits-reconcile") {
        el.disabled = true;
        const r = await api(`/api/credits/${record.id}/reconcile`, {});
        toast(globalThis.OpenDevI18n.html`对账完成：${labels[r.status] || r.status || globalThis.OpenDevI18n.t("记录已更新")}`);
        await route();
      } else if (action === "retry") {
        el.disabled = true;
        await api(`/api/jobs/${record.id}/retry`, {});
        toast(globalThis.OpenDevI18n.t("已重新安排任务。"));
        await route();
      } else if (action === "refresh-project") {
        el.disabled = true;
        await api("/api/import", { repos: [state.project.project.full_name] });
        toast(globalThis.OpenDevI18n.t("已安排刷新，采集完成后更新证据。"));
      } else if (action === "export-project") {
        const url = URL.createObjectURL(
          new Blob([JSON.stringify(state.project, null, 2)], {
            type: "application/json",
          }),
        );
        const a = document.createElement("a");
        a.href = url;
        a.download = `project-${state.project.project.id}.json`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (e) {
      if (state.user === user && generation === state.generation) toast(friendly(e), true);
    } finally {
      el.disabled = false;
    }
  }
  const accountsUI = globalThis.OpenDevAccounts({api, state, enter, showLogin, refresh: () => route(), esc, toast});
  const views = globalThis.OpenDevViews({api,esc,external,fmt,button,can,state,refresh:()=>route(),toast});
  const developersUI = globalThis.OpenDevDevelopers({api,esc,external,fmt,heading,empty,pager,badge,state,refresh:()=>route(),views});
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.authBusy) return;
    const btn = $("button", e.target);
    accountsUI.setBusy(true);
    $("#login-error").textContent = "";
    try {
      const token = $("#access-token").value.trim();
      const user = await api("/auth/login", { token });
      $("#access-token").value = "";
      await enter(user);
    } catch (err) {
      $("#login-error").textContent = friendly(err);
    } finally {
      accountsUI.setBusy(false);
    }
  });
  $("#logout").addEventListener("click", async () => {
    try {
      await api("/auth/logout", {});
      showLogin();
    } catch (e) {
      if (e.status === 401) showLogin(globalThis.OpenDevI18n.t("会话已结束，请重新登录。"));
      else toast(friendly(e), true);
    }
  });
  $("#mobile-logout").addEventListener("click", () => $("#logout").click());
  $("#refresh").addEventListener("click", () => route());
  $("#main").addEventListener("click", click);
  $("#action-form").addEventListener("submit", submitDialog);
  $("#action-form").addEventListener("change", (e) => {
    syncFormConstraints(e.target);
    if (
      e.target.name === "decision" &&
      state.dialog?.action.startsWith("review-")
    ) {
      state.dialog.schema
        .filter((f) => f.name.startsWith("evidence."))
        .forEach((f) => {
          const input = $(`[name="${f.name}"]`, $("#action-form"));
          input.required = e.target.value === "pass";
        });
    }
  });
  $("#dialog-close").addEventListener("click", closeDialog);
  $("#dialog-cancel").addEventListener("click", closeDialog);
  $("#action-dialog").addEventListener("cancel", () => {
    ++state.dialogGeneration;
    state.dialog = null;
  });
  $("#main").addEventListener("submit", (e) => {
    if (e.target.id === "project-search") {
      e.preventDefault();
      state.q = e.target.elements.q.value.trim();
      state.stage = e.target.elements.stage.value;
      for (const key of ["category","selection","audience","contact_class","source","sort"]) state[key]=e.target.elements[key].value;
      state.page = 1;
      route();
    }
  });
  window.addEventListener("hashchange", () => route(true));
  api("/auth/options")
    .then((o) => {
      accountsUI.configure(o);
      if (o.sso_enabled) $("#sso-login").hidden = false;
    })
    .catch(() => { $("#account-error").textContent = globalThis.OpenDevI18n.t("无法读取登录设置，请刷新页面重试。"); });
  api("/auth/me")
    .then(enter)
    .catch(() => showLogin());
})();
