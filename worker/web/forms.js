"use strict";
// Pure workflow field schemas shared by the management UI.
globalThis.SponsorshipForms = function (labels, esc) {
  const tx=(zh,en)=>globalThis.OpenDevI18n.language==='en'?en:zh;
  const actionNames = {
    qualify: globalThis.OpenDevI18n.t("项目入选评估"),
    assign: globalThis.OpenDevI18n.t("分配负责人"),
    review: globalThis.OpenDevI18n.t("审核"),
    "review-one": globalThis.OpenDevI18n.t("第一轮技术复核"),
    "review-two": globalThis.OpenDevI18n.t("第二轮体验复核"),
    "experience-create": globalThis.OpenDevI18n.t("创建真实体验"),
    "experience-result": globalThis.OpenDevI18n.t("记录人类体验"),
    "maintainer-confirm": globalThis.OpenDevI18n.t("确认维护关系"),
    "contact-draft": globalThis.OpenDevI18n.t("创建联系草稿"),
    "contact-status": globalThis.OpenDevI18n.t("更新联系记录"),
    interview: globalThis.OpenDevI18n.t("记录访谈"),
    decision: globalThis.OpenDevI18n.t("赞助决定"),
    followup: globalThis.OpenDevI18n.t("记录复盘"),
    'followup-schedule': tx('安排复盘','Schedule follow-up'),
    import: globalThis.OpenDevI18n.t("导入项目"),
    schedule: globalThis.OpenDevI18n.t("安排发现任务"),
  };
  const F = (
    name,
    label,
    type = "text",
    options = null,
    help = "",
    required = true,
  ) => ({ name, label, type, options, help, required });
  const choose = (...keys) => keys.map((k) => [k, labels[k] || k]);
  function fields(action, catalog) {
    const project = F("project_id", globalThis.OpenDevI18n.t("项目 ID"), "number");
    const sha = F("sha", globalThis.OpenDevI18n.t("当前 Commit SHA"));
    const review = [
      F("decision", globalThis.OpenDevI18n.t("审核结论"), "select", choose("hold", "pass", "reject")),
      F("rationale", globalThis.OpenDevI18n.t("结论与理由"), "textarea"),
    ];
    switch (action) {
      case "qualify": {
        if (!catalog?.assessment_fields) throw new Error(tx('入选标准未加载，请重试。','Selection policy is unavailable; retry.'));
        const pick = globalThis.OpenDevI18n.pick;
        const reasonOptions = [['none',tx('未发现排除项，证据满足入选要求','No exclusion found; selection evidence is complete')],
          ...catalog.hold_reasons.map(r=>[r.id,tx('暂缓 · ','Hold · ')+pick(r.title)]),
          ...catalog.exclusion_reasons.map(r=>[r.id,tx('排除 · ','Exclude · ')+pick(r.title)])];
        const result = [project, sha,
          F('category',tx('项目方向','Project category'),'select',Object.entries(catalog.categories).map(([key,label])=>[key,pick(label)])),
          F('scope_fit',tx('是否符合赞助方向','Scope fit'),'select',[['uncertain',tx('待确认','Uncertain')],['yes',tx('符合','Yes')],['no',tx('不符合','No')]]),
          F('maintenance',tx('持续维护情况','Maintenance'),'select',[['uncertain',tx('待确认','Uncertain')],['active',tx('持续维护','Active')],['inactive',tx('已停止维护','Inactive')]]),
          F('outcome',tx('入选结论','Selection outcome'),'select',[['hold',tx('暂缓，补充证据','Hold for evidence')],['include',tx('质量达标，进入技术复核','Quality selected; technical review next')],['exclude',tx('暂不入选','Exclude for now')]]),
          {...F('evidence.reason_code',tx('对应标准条款','Applicable policy reason'),'select',reasonOptions),reasonOutcomes:{none:'include',...Object.fromEntries(catalog.hold_reasons.map(r=>[r.id,'hold'])),...Object.fromEntries(catalog.exclusion_reasons.map(r=>[r.id,'exclude']))}},
          F('rationale',tx('判断理由与重评条件','Rationale and reassessment conditions'),'textarea',null,tx('区分事实与推断；暂缓或排除时写明缺项及何种变化可以重评。','Separate facts from inference. For hold/exclude, record missing evidence and what would permit reassessment.')),
          F('evidence.scope',tx('方向与使用场景依据','Scope and use-case evidence'),'textarea',null,tx('覆盖开发者、真实用户、Agent 基础设施与供 Agent 调用的应用。','Cover developers, real end users, Agent infrastructure and applications callable by Agents.')),
          ...catalog.assessment_fields.map(f=>({...F('evidence.'+f.name,pick(f.label),'textarea',null,pick(f.help),false),requiredForInclude:f.include_required})),
          F('evidence.maintenance_kind',tx('维护信号类型','Maintenance signal type'),'select',catalog.maintenance_kinds.map(k=>[k.id,pick(k)]),tx('选择实质提交、发布或具体响应；机器人活动和更新时间不能单独证明维护。','Select substantive changes, releases or specific responses. Bot activity and timestamps alone are insufficient.')),
          {...F('evidence.maintenance_date',tx('主要维护事实日期','Primary maintenance evidence date'),'date',null,tx('填写引用的实质提交、发布或响应日期；入选时须包含在过去一年内的实质维护日期列表中。','Date of the cited substantive change, release or response; inclusion requires this date in the list of maintenance dates within the past year.'),false),requiredForActive:true},
          F('evidence.maintenance',tx('持续维护证据','Maintenance evidence'),'textarea',null,tx('说明变更内容、解决的问题、响应者及项目节奏。90 天仅为建议检索窗口，稳定项目可回看一年。','Explain change substance, resolved issues, responder and project cadence. Ninety days is a search window only; stable projects may require a year of context.')),
          F('evidence.sources',tx('证据来源','Evidence sources'),'lines',null,tx('逐项引用链接、文件路径或记录；能对应到判断，不以 Stars 或热度代替证据。','Cite links, file paths or records tied to each judgment; popularity is not evidence.'))];
        const rubric=catalog.quality_rubric;
        result.push({...F('evidence.maintenance_dates',tx('实质维护日期','Substantive maintenance dates'),'lines',null,tx('至少两个不同日期，每行 YYYY-MM-DD，均在过去一年内。与维护来源和事实逐项对应。','At least two distinct dates in the past year, one YYYY-MM-DD per line. Match dates to sources and facts.'),false),requiredForInclude:true});
        for(const d of rubric.dimensions) {
          result.push({...F(`evidence.quality.${d.id}.score`,tx('质量评分','Quality score'),'score',[['',tx('请选择评分','Choose a score')],...rubric.scale.map((label,i)=>[String(i),pick(label)])],'',false),requiredForInclude:true,qualityWeight:d.weight});
          result.push({...F(`evidence.quality.${d.id}.sources`,tx('对应证据来源','Supporting sources'),'lines',null,d.id==='maintenance'?tx('至少两个不同来源，每行一条。','At least two distinct sources, one per line.'):tx('每行一条，指向提交、文件、Issue、用例或评测记录。','One source per line: commit, file, issue, use case or evaluation record.'),false),requiredForInclude:true});
        }
        for(const f of result) {
          const d=rubric.dimensions.find(d=>f.name===`evidence.${d.evidence_field}`||f.name.startsWith(`evidence.quality.${d.id}.`)||(d.id==='maintenance'&&f.name==='evidence.maintenance_dates'));
          if(d) f.qualityGroup={id:d.id,title:pick(d.title),help:pick(d.body),weight:d.weight};
        }
        return result;
      }
      case "assign":
        return [
          project,
          F("owner", globalThis.OpenDevI18n.t("负责人")),
          F("audience", globalThis.OpenDevI18n.t("目标用户"), "select", choose("human", "agent", "both")),
        ];
      case "review-one":
        return [
          project,
          sha,
          ...review,
          F(
            "evidence.structure",
            globalThis.OpenDevI18n.t("工程结构与代码质量"),
            "textarea",
            null,
            globalThis.OpenDevI18n.t("填写具体文件、结构观察和证据位置。"),
          ),
          F("evidence.maintenance", globalThis.OpenDevI18n.t("提交与持续维护"), "textarea"),
          F("evidence.documentation", globalThis.OpenDevI18n.t("文档与仓库规范"), "textarea"),
        ];
      case "review-two":
        return [
          project,
          sha,
          ...review,
          F("evidence.real_problem", globalThis.OpenDevI18n.t("解决的真实问题"), "textarea"),
          F("evidence.usability", globalThis.OpenDevI18n.t("实际使用结果"), "textarea"),
          F("evidence.differentiation", globalThis.OpenDevI18n.t("差异化"), "textarea"),
          F("evidence.potential", globalThis.OpenDevI18n.t("后续潜力"), "textarea"),
          F(
            "evidence.experience_ids",
            globalThis.OpenDevI18n.t("本次参考的体验 ID"),
            "lines",
            null,
            globalThis.OpenDevI18n.t("每行一个 ID。通过审核时须覆盖全部目标用户且体验成功。"),
          ),
        ];
      case "experience-create":
        return [
          project,
          F("audience", globalThis.OpenDevI18n.t("体验对象"), "select", choose("human", "agent")),
          F("spec.scenario", globalThis.OpenDevI18n.t("真实任务场景"), "textarea"),
          F("spec.success_criteria", globalThis.OpenDevI18n.t("成功标准"), "textarea"),
          F("spec.environment", globalThis.OpenDevI18n.t("执行环境")),
          F("spec.input", globalThis.OpenDevI18n.t("任务输入"), "textarea"),
          F("spec.budget.seconds", globalThis.OpenDevI18n.t("执行时间预算（秒）"), "number"),
        ];
      case "experience-result":
        return [
          F("id", globalThis.OpenDevI18n.t("体验 ID")),
          sha,
          F("result.success", globalThis.OpenDevI18n.t("执行结果"), "select", [
            ["false", globalThis.OpenDevI18n.t("失败")],
            ["true", globalThis.OpenDevI18n.t("成功")],
          ]),
          F(
            "result.failure_kind",
            globalThis.OpenDevI18n.t("失败分类"),
            "select",
            choose("environment", "product", "none"),
          ),
          F("result.executor", globalThis.OpenDevI18n.t("实际执行人")),
          F("result.environment", globalThis.OpenDevI18n.t("真实环境")),
          F(
            "result.steps",
            globalThis.OpenDevI18n.t("执行步骤"),
            "lines",
            null,
            globalThis.OpenDevI18n.t("每行一步，记录实际操作。"),
          ),
          F("result.actual_result", globalThis.OpenDevI18n.t("真实输出与结论"), "textarea"),
          F(
            "result.artifacts",
            globalThis.OpenDevI18n.t("证据文件 / 链接"),
            "lines",
            null,
            globalThis.OpenDevI18n.t("每行一项。成功时必须提供可复核证据。"),
            false,
          ),
        ];
      case "maintainer-confirm":
        return [
          project,
          F("maintainer_id", globalThis.OpenDevI18n.t("维护者 GitHub ID"), "number"),
          F("evidence", globalThis.OpenDevI18n.t("确认依据"), "textarea"),
        ];
      case "contact-draft":
        return [
          project,
          F("maintainer_id", globalThis.OpenDevI18n.t("维护者 GitHub ID"), "number"),
          F("channel", globalThis.OpenDevI18n.t("拟联系渠道")),
          F("draft", globalThis.OpenDevI18n.t("联系草稿"), "textarea", null, globalThis.OpenDevI18n.t("仅保存草稿，不发送消息。")),
        ];
      case "contact-status":
        return [
          F("id", globalThis.OpenDevI18n.t("联系记录 ID")),
          F(
            "status",
            globalThis.OpenDevI18n.t("记录已发生的状态"),
            "select",
            choose("sent", "replied", "closed", "declined"),
          ),
          F(
            "evidence",
            globalThis.OpenDevI18n.t("沟通证据与时间"),
            "textarea",
            null,
            globalThis.OpenDevI18n.t("填写已在外部渠道发生的事实。拒绝联系会阻断该维护者后续建联。"),
          ),
        ];
      case "interview":
        return [
          project,
          F("contact_id", globalThis.OpenDevI18n.t("已回复的联系记录 ID")),
          F(
            "profile",
            globalThis.OpenDevI18n.t("访谈画像"),
            "profile",
            choose("undetermined", "engineer", "entrepreneur"),
            tx('按访谈中的投入、规划与意愿判断，可兼具两类；资料不足选待判断。','Use interview evidence about commitment, plans and intent. Both profiles may apply; choose undetermined when evidence is insufficient.'),
          ),
          F("notes.users", globalThis.OpenDevI18n.t("目标用户与真实需求"), "textarea"),
          F("notes.commitment", globalThis.OpenDevI18n.t("投入情况")),
          F("notes.roadmap", globalThis.OpenDevI18n.t("产品规划"), "textarea"),
          F("notes.credit_need", globalThis.OpenDevI18n.t("Credits 需求")),
          F("notes.milestone", globalThis.OpenDevI18n.t("下一里程碑")),
          F("notes.consent", globalThis.OpenDevI18n.t("已获得访谈记录同意"), "checkbox"),
        ];
      case "decision":
        return [
          project,
          F("interview_id", globalThis.OpenDevI18n.t("同项目访谈 ID"), "number"),
          F(
            "outcome",
            globalThis.OpenDevI18n.t("赞助决定"),
            "select",
            choose("hold", "approve", "reject"),
          ),
          F("rationale", globalThis.OpenDevI18n.t("决定依据"), "textarea"),
          F(
            "terms.account",
            globalThis.OpenDevI18n.t("Credits 接收邮箱"),
            "email",
            null,
            globalThis.OpenDevI18n.t("批准时必填，并核实账号归属。"),
            false,
          ),
          F(
            "terms.amount",
            globalThis.OpenDevI18n.t("批准金额（美分）"),
            "number",
            null,
            globalThis.OpenDevI18n.t("100 cents = 1 USD；批准时必填正整数。"),
            false,
          ),
          F(
            "terms.event_slug",
            globalThis.OpenDevI18n.t("Credits 活动 slug"),
            "text",
            null,
            globalThis.OpenDevI18n.t("必须与部署活动一致。"),
            false,
          ),
          F(
            "terms.expires_at",
            globalThis.OpenDevI18n.t("活动到期日期"),
            "date",
            null,
            globalThis.OpenDevI18n.t("与 Credits 后台核实一致。"),
            false,
          ),
          F(
            "terms.review_dates",
            globalThis.OpenDevI18n.t("复盘日期"),
            "lines",
            null,
            tx("至少安排一次复盘。每行 YYYY-MM-DD，须为未来日期。", "Schedule at least one follow-up. One future YYYY-MM-DD date per line."),
            false,
          ),
          F(
            "terms.identity_evidence",
            globalThis.OpenDevI18n.t("账号归属依据"),
            "textarea",
            null,
            globalThis.OpenDevI18n.t("记录核实事实。"),
            false,
          ),
          F(
            "terms.purpose",
            globalThis.OpenDevI18n.t("支持用途"),
            "textarea",
            null,
            globalThis.OpenDevI18n.t("批准时必填。"),
            false,
          ),
        ];
      case "followup-schedule":
        return [F('grant_id',tx('赞助编号','Grant ID')),F('due_at',tx('复盘日期','Review date'),'date',null,
          tx('选择未来检查额度使用和里程碑的日期。','Choose a future date to review usage and milestones.'))];
      case "followup":
        return [
          F("id", globalThis.OpenDevI18n.t("复盘记录 ID")),
          F("result.usage", globalThis.OpenDevI18n.t("额度使用情况"), "textarea"),
          F("result.milestone", globalThis.OpenDevI18n.t("里程碑进展"), "textarea"),
          F("result.evidence", globalThis.OpenDevI18n.t("复盘证据"), "lines"),
          F(
            "result.decision",
            globalThis.OpenDevI18n.t("下一阶段结论"),
            "select",
            choose("continue", "adjust", "pause", "end"),
          ),
          F("result.next_action", globalThis.OpenDevI18n.t("下一步安排"), "textarea"),
          F("result.next_review_at", globalThis.OpenDevI18n.t("下一次复盘日期"), "date", null,
            globalThis.OpenDevI18n.t("继续、调整或暂停时填写未来日期；留空须已有未来待办。结束时无需填写。"), false),
        ];
      case "import":
        return [
          F(
            "repos",
            globalThis.OpenDevI18n.t("GitHub 仓库"),
            "lines",
            null,
            globalThis.OpenDevI18n.t("每行 owner/repo 或完整 GitHub 仓库链接；最多 100 个。"),
          ),
        ];
      case "schedule":
        return [
          F("source", globalThis.OpenDevI18n.t("采集来源"), "select", [["github_search", "GitHub Search"], ["github_trending", "GitHub Trending"]], globalThis.OpenDevI18n.t("Trending 会保存榜单名次；Search 保留原有查询规则。"), false),
          F("period", globalThis.OpenDevI18n.t("榜单周期"), "select", [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"]], "", false),
          F("language", globalThis.OpenDevI18n.t("语言"), "text", null, globalThis.OpenDevI18n.t("可选，例如 Python 或 TypeScript。"), false),
          F("max_projects", globalThis.OpenDevI18n.t("最多项目数"), "number", null, globalThis.OpenDevI18n.t("Trending 默认 25，最大 100。"), false),
          F(
            "start",
            globalThis.OpenDevI18n.t("起始日期"),
            "date",
            null,
            globalThis.OpenDevI18n.t("留空使用服务器的默认回看范围。"),
            false,
          ),
          F("end", globalThis.OpenDevI18n.t("结束日期"), "date", null, globalThis.OpenDevI18n.t("留空使用当前日期。"), false),
        ];
      default:
        return [];
    }
  }
  function valueAt(o, path) {
    return path.split(".").reduce((v, k) => v?.[k], o);
  }
  function setAt(o, path, value) {
    const keys = path.split(".");
    let t = o;
    keys.slice(0, -1).forEach((k) => (t = t[k] ??= {}));
    t[keys.at(-1)] = value;
  }
  function fieldHtml(f, defaults) {
    let v = valueAt(defaults, f.name) ?? "";
    if (Array.isArray(v)) v = v.join("\n");
    const id = "field-" + f.name.replaceAll(".", "-");
    const attr = `id="${id}" name="${esc(f.name)}" ${f.required ? "required" : ""}`;
    let input;
    if (f.type === "profile")
      return `<fieldset class="form-field wide profile-group"><legend>${esc(f.label)}</legend>${f.options.map(([key, text]) => `<label class="checkbox-label"><input type="checkbox" name="${esc(f.name)}" value="${esc(key)}" ${(v || "undetermined").split("\n").includes(key) ? "checked" : ""}>${esc(text)}</label>`).join("")}${f.help ? `<p class="field-help">${esc(f.help)}</p>` : ""}</fieldset>`;
    else if (f.type === "select" || f.type === "score")
      input = `<select ${attr}>${(f.options || []).map(([key, text]) => `<option value="${esc(key)}" ${String(v) === key ? "selected" : ""}>${esc(text)}</option>`).join("")}</select>`;
    else if (f.type === "textarea" || f.type === "lines")
      input = `<textarea ${attr} rows="3">${esc(v)}</textarea>`;
    else if (f.type === "checkbox")
      input = `<label class="checkbox-label"><input type="checkbox" ${attr} ${v === true ? "checked" : ""}>${esc(f.label)}</label>`;
    else
      input = `<input ${attr} type="${f.type}" value="${esc(v)}" ${f.type === "number" ? 'min="1" step="1"' : ""} autocomplete="off">`;
    return `<div class="form-field ${["textarea", "lines", "checkbox"].includes(f.type) ? "wide" : ""}">${f.type !== "checkbox" ? `<label for="${id}">${esc(f.label)}${f.requiredForInclude ? tx(' <span class="muted">（入选时必填）</span>',' <span class="muted">(required to include)</span>') : f.requiredForActive ? tx(' <span class="muted">（确认维护时必填）</span>',' <span class="muted">(required for active maintenance)</span>') : f.required ? "" : globalThis.OpenDevI18n.t(' <span class="muted">（可选）</span>')}</label>` : ""}${input}${f.help ? `<p class="field-help">${esc(f.help)}</p>` : ""}</div>`;
  }
  return { actionNames, fields, fieldHtml, setAt };
};
