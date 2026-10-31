"use strict";
globalThis.OpenDevAccounts = ({api, state, enter, showLogin, refresh, esc, toast}) => {
  const $ = s => document.querySelector(s);
  const roles = {reviewer:globalThis.OpenDevI18n.t('技术审核'),operator:globalThis.OpenDevI18n.t('项目运营'),approver:globalThis.OpenDevI18n.t('赞助审批'),disburser:globalThis.OpenDevI18n.t('Credits 执行'),auditor:globalThis.OpenDevI18n.t('质控审计'),admin:globalThis.OpenDevI18n.t('管理员')};
  const statuses = {pending:globalThis.OpenDevI18n.t('待审批'),active:globalThis.OpenDevI18n.t('已授权'),disabled:globalThis.OpenDevI18n.t('已停用'),rejected:globalThis.OpenDevI18n.t('未通过')};
  let options = null;
  const form = $('#account-login-form');
  function setBusy(value) {
    state.authBusy = value;
    $('#account-fields').disabled = value || !options;
    $('#login-form button').disabled = value;
    $('#account-submit').textContent = value ? globalThis.OpenDevI18n.t('正在登录…') : globalThis.OpenDevI18n.t('登录工作台');
    form.setAttribute('aria-busy', String(value));
  }
  function hidePassword() {
    $('#account-password').type = 'password';
    $('#password-visibility').textContent = globalThis.OpenDevI18n.t('显示');
    $('#password-visibility').setAttribute('aria-label', globalThis.OpenDevI18n.t('显示密码'));
    $('#password-visibility').setAttribute('aria-pressed', 'false');
    $('#caps-lock-hint').hidden = true;
  }
  function configure(value) {
    options = value;
    $('#account-form-description').textContent = globalThis.OpenDevI18n.t('欢迎回来，登录后继续团队协作。');
    hidePassword();
    setBusy(false);
  }
  $('#password-visibility').addEventListener('click', () => {
    const visible = $('#account-password').type === 'password';
    $('#account-password').type = visible ? 'text' : 'password';
    $('#password-visibility').textContent = visible ? globalThis.OpenDevI18n.t('隐藏') : globalThis.OpenDevI18n.t('显示');
    $('#password-visibility').setAttribute('aria-label', visible ? globalThis.OpenDevI18n.t('隐藏密码') : globalThis.OpenDevI18n.t('显示密码'));
    $('#password-visibility').setAttribute('aria-pressed', String(visible));
  });
  $('#account-password').addEventListener('keyup', event => { $('#caps-lock-hint').hidden = !event.getModifierState('CapsLock'); });
  form.addEventListener('input', () => { $('#account-error').textContent = ''; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (state.authBusy || !options) return;
    const body = {username:$('#account-username').value.trim(), password:$('#account-password').value};
    $('#account-error').textContent = ''; $('#account-notice').textContent = '';
    setBusy(true);
    try {
      const result = await api('/auth/password-login', body);
      form.reset();
      setBusy(false);
      await enter(result);
    } catch (error) { $('#account-error').textContent = error.message; }
    finally { $('#account-password').value = ''; hidePassword(); setBusy(false); }
  });
  async function membersPage() {
    if (!state.config?.capabilities?.manage_accounts) return globalThis.OpenDevI18n.t('<p class="notice">只有管理员可以管理成员。</p>');
    const response = await api('/api/accounts?page=' + state.page + '&page_size=25');
    const cards = response.items.map(member => globalThis.OpenDevI18n.html`<form class="member-form panel" data-member="${esc(member.id)}" data-version="${member.auth_version}">
      <div class="panel-head"><div><h3>${esc(member.display_name)}</h3><span class="muted small">${esc(member.username)}</span></div><span class="badge ${member.status === 'active' ? 'green' : 'amber'}">${statuses[member.status]}</span></div>
      <div class="panel-body"><fieldset class="member-role-options"><legend>授予角色</legend>${Object.entries(roles).map(([role,label]) => `<label class="checkbox-label"><input type="checkbox" name="role" value="${role}" ${member.roles.includes(role) ? 'checked' : ''}>${label}</label>`).join('')}</fieldset>
      <label>账号状态<select name="status">${Object.entries(statuses).map(([status,label]) => `<option value="${status}" ${member.status === status ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
      <details class="member-reset"><summary>重置此成员的密码</summary><label>新密码<input name="password" type="password" autocomplete="new-password" minlength="6" maxlength="128" placeholder="留空则保留原密码"></label><p class="muted small">至少 6 个字符。通过团队私下渠道告知本人，并请本人登录后修改。</p></details>
      <p class="member-error error-text" role="alert"></p><div class="record-actions"><button type="submit" class="button primary">保存授权</button></div></div></form>`).join('');
    const pages = Math.max(1,Math.ceil(response.total/25));
    return globalThis.OpenDevI18n.html`<div class="page-heading"><div><p class="eyebrow">TEAM ACCESS</p><h1>成员与权限</h1><p class="muted">在这里开通成员账号、分配角色和管理访问权限。</p></div><button type="button" id="open-member-create" class="button primary" aria-expanded="false" aria-controls="member-create-panel">新增成员</button></div>
      <section id="member-create-panel" class="panel" hidden><div class="panel-head"><div><h3>新增团队成员</h3><p class="muted small">填写账号资料并授予角色，创建后成员即可登录。</p></div></div><form id="member-create-form" class="panel-body">
      <div class="member-create-fields"><label>账号<input name="username" autocomplete="off" autocapitalize="none" spellcheck="false" required minlength="3" maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9_.@\\-]{2,63}" placeholder="3–64 位字母、数字或 _.@-"></label>
      <label>显示名称<input name="display_name" autocomplete="off" required maxlength="80" placeholder="成员姓名或团队称呼"></label>
      <label>初始密码<input name="password" type="password" autocomplete="new-password" required minlength="6" maxlength="128" placeholder="至少 6 个字符"></label>
      <label>确认密码<input name="confirm" type="password" autocomplete="new-password" required minlength="6" maxlength="128" placeholder="再次输入初始密码"></label></div>
      <fieldset class="member-role-options"><legend>成员角色（至少选择一项）</legend>${Object.entries(roles).map(([role,label]) => `<label class="checkbox-label"><input type="checkbox" name="role" value="${role}">${label}</label>`).join('')}</fieldset>
      <p class="muted small">创建后请将账号和初始密码告知本人，成员可在账号设置中修改密码。</p><p class="member-error error-text" role="alert"></p>
      <div class="record-actions"><button type="button" id="cancel-member-create" class="button">取消</button><button type="submit" class="button primary">创建成员</button></div></form></section>
      <div class="notice">按实际职责分配角色。同一笔赞助的审批人与执行人须由不同成员承担。</div>
      <div class="members-grid">${cards || globalThis.OpenDevI18n.t('<p class="muted">暂无成员，点击“新增成员”开通账号。</p>')}</div>
      <div class="record-actions"><button class="button" data-do="page" data-page="${Math.max(1,state.page-1)}" ${state.page <= 1 ? 'disabled' : ''}>上一页</button><span>第 ${state.page} / ${pages} 页 · 共 ${response.total} 人</span><button class="button" data-do="page" data-page="${state.page+1}" ${state.page >= pages ? 'disabled' : ''}>下一页</button></div>`;
  }
  async function accountPage() {
    await api('/api/me');
    return globalThis.OpenDevI18n.html`<div class="page-heading"><div><p class="eyebrow">YOUR ACCOUNT</p><h1>账号设置</h1><p class="muted">${esc(state.user.display_name || state.user.actor)}</p></div></div>` +
      (state.config?.capabilities?.password_change ? globalThis.OpenDevI18n.html`<form id="password-change-form" class="panel account-settings"><div class="panel-head"><h3>修改密码</h3></div><div class="panel-body">
        <label>当前密码<input name="current_password" type="password" autocomplete="current-password" required maxlength="128"></label>
        <label>新密码<input name="new_password" type="password" autocomplete="new-password" required minlength="6" maxlength="128"></label>
        <label>确认新密码<input name="confirm" type="password" autocomplete="new-password" required minlength="6" maxlength="128"></label>
        <p class="muted small">至少 6 个字符。保存后所有已登录设备需要重新登录。</p><p class="member-error error-text" role="alert"></p><button type="submit" class="button primary">修改密码并退出</button></div></form>` : globalThis.OpenDevI18n.t('<p class="notice">当前使用 Token 或团队单点登录，密码由对应身份系统管理。</p>'));
  }
  $('#main').addEventListener('click', event => {
    const trigger = event.target.closest('#open-member-create, #cancel-member-create');
    if (!trigger) return;
    const panel = $('#member-create-panel');
    const open = trigger.id === 'open-member-create' && panel.hidden;
    if ($('#member-create-form button[type=submit]').disabled) return;
    panel.hidden = !open;
    $('#open-member-create').setAttribute('aria-expanded', String(open));
    if (open) $('#member-create-form input').focus();
    else { $('#member-create-form').reset(); panel.querySelector('.member-error').textContent = ''; $('#open-member-create').focus(); }
  });
  $('#main').addEventListener('submit', async event => {
    const target = event.target;
    if (!target.matches('.member-form, #member-create-form, #password-change-form')) return;
    event.preventDefault();
    const button = target.querySelector('button[type=submit]');
    if (button.disabled) return;
    const currentUser = state.user, generation = state.generation;
    const current = () => currentUser === state.user && generation === state.generation;
    const errorBox = target.querySelector('.member-error');
    button.disabled = true; errorBox.textContent = '';
    try {
      if (target.id === 'member-create-form') {
        if (target.elements.password.value !== target.elements.confirm.value) throw new Error(globalThis.OpenDevI18n.t('两次输入的密码不一致。'));
        const selectedRoles = Array.from(target.querySelectorAll('[name=role]:checked'), input => input.value);
        if (!selectedRoles.length) throw new Error(globalThis.OpenDevI18n.t('请至少选择一个成员角色。'));
        await api('/api/accounts', {username:target.elements.username.value.trim(),display_name:target.elements.display_name.value.trim(),password:target.elements.password.value,roles:selectedRoles});
        if (!current()) return;
        toast(globalThis.OpenDevI18n.t('成员已创建，可使用账号和密码登录。'));
        state.page = 1;
        await refresh();
      } else if (target.id === 'password-change-form') {
        if (target.elements.new_password.value !== target.elements.confirm.value) throw new Error(globalThis.OpenDevI18n.t('两次输入的密码不一致。'));
        await api('/auth/password', {current_password:target.elements.current_password.value,new_password:target.elements.new_password.value});
        if (current()) showLogin(globalThis.OpenDevI18n.t('密码已修改，请用新密码登录。'));
      } else {
        const body = {auth_version:Number(target.dataset.version),status:target.elements.status.value,
          roles:Array.from(target.querySelectorAll('[name=role]:checked'),input=>input.value)};
        if (target.elements.password.value) body.password = target.elements.password.value;
        await api('/api/accounts/' + target.dataset.member, body);
        if (!current()) return;
        if (target.dataset.member === state.user.actor) showLogin(globalThis.OpenDevI18n.t('账号授权已更新，请重新登录。'));
        else { toast(globalThis.OpenDevI18n.t('成员授权已保存。')); await refresh(); }
      }
    } catch (error) { if (current()) errorBox.textContent = error.message; }
    finally { target.querySelectorAll('input[type=password]').forEach(input=>input.value=''); button.disabled = false; }
  });
  return {configure, setBusy, membersPage, accountPage};
};
