const WORKFLOW_EMPTY_TEXT = "等待下一次行动...";

const WORKFLOW_LABELS = {
    normalized_action: "行动解析",
    verb: "动作",
    target_kind: "目标类型",
    target_key: "目标键",
    raw_target_text: "原始目标文本",
    feasibility: "可行性",
    ok: "可执行",
    reason: "原因",
    check: "检定计划",
    required: "需要检定",
    skill: "技能",
    difficulty: "难度",
    check_type: "判定类型",
    success: "是否成功",
    critical_success: "大成功",
    critical_failure: "大失败",
    result_description: "结果描述",
    roll: "投骰",
    threshold: "目标值",
    player_skill: "技能阈值",
    hard_changes: "硬变化",
    feasible: "允许推进",
    hint: "叙述提示",
    stage_assessment: "阶段评估",
    world_changes: "世界变化",
    soft_world_changes: "软变化",
    location_context: "地点上下文",
    object_context: "物品上下文",
    npc_context: "NPC 上下文",
    npc_action_guide: "NPC 行动引导",
    focus_npc: "焦点 NPC",
    attitude: "态度",
    response_strategy: "回应策略",
    next_line_goal: "下一句目标",
    revealable_info: "可透露信息",
    should_open_door: "是否开门",
    atmosphere_guide: "氛围指引",
    runtime_state: "运行状态",
    location: "位置",
    memory: "记忆",
    player_facts: "玩家信息",
    evidence_seen: "已见证据",
    promises: "承诺",
    topics_discussed: "已讨论话题",
    pending_questions: "待确认问题",
    conversation_flags: "对话标记",
    last_impression: "最近印象",
    trust_level: "信任值",
    trust_shift: "信任变化",
    trust_threshold: "信任阈值",
    initial_attitude: "初始态度",
    current_state: "当前状态",
    key_info: "关键信息",
    first_appearance: "首次印象",
    can_take: "可拾取",
    requires: "前置条件",
    type: "类型",
    name: "名称",
    san_delta: "理智变化",
    hp_delta: "生命变化",
    inventory_add: "获得物品",
    inventory_remove: "失去物品",
    clues: "线索",
    npc_locations: "NPC 位置变化",
    npc_updates: "NPC 状态变化",
    source_round: "来源轮次",
};

const WORKFLOW_VALUE_LABELS = {
    auto_check: "自动判定",
    skill_check: "技能检定",
    none: "无",
    null: "无",
    true: "是",
    false: "否",
    allowed: "允许",
    blocked: "受阻",
    required: "需要",
    optional: "可选",
    take: "拾取",
    burn: "焚烧",
    destroy: "破坏",
    talk: "交谈",
    use: "使用",
    inspect: "调查",
    interact: "交互",
    npc: "NPC",
    object: "物品",
    location: "地点",
    unknown: "未知",
    easy: "简单",
    normal: "普通",
    hard: "困难",
    extreme: "极难",
    neutral: "中立",
    friendly: "友好",
    hostile: "敌对",
    "stable pacing": "节奏平稳",
    "stay alert but cooperate.": "保持警惕，但愿意合作。",
    "keep probing but share a small amount of information.": "继续试探，但可以透露少量信息。",
    "answer through the door and keep the player at distance.": "隔门回应，并与玩家保持距离。",
    "confirm the cooperation plan and share key information.": "确认合作方案，并透露关键信息。",
    "test the player's intent and maybe reveal one useful clue.": "测试玩家意图，并视情况透露一条有用线索。",
    "verify who the player is and whether they can be trusted.": "确认玩家身份，并判断其是否值得信任。",
    player_shared_verifiable_or_personal_information: "玩家提供了可验证或较私人的信息",
    identity: "身份",
    origin: "来历",
    goal: "目标",
    cooperation: "合作",
    evidence: "证据",
};

function workflowIsPlainObject(value) {
    return value && typeof value === "object" && !Array.isArray(value);
}

function workflowIsEmpty(value) {
    if (value === null || value === undefined || value === "") return true;
    if (Array.isArray(value)) return value.length === 0;
    if (workflowIsPlainObject(value)) return Object.keys(value).length === 0;
    return false;
}

function workflowTranslateLabel(key) {
    return WORKFLOW_LABELS[key] || key;
}

function workflowTranslateScalar(value, key = "") {
    if (value === null || value === undefined || value === "") return "无";
    if (typeof value === "boolean") return value ? "是" : "否";

    if (typeof value === "string") {
        const trimmed = value.trim();
        if (!trimmed) return "无";

        const lowered = trimmed.toLowerCase();
        if (Object.prototype.hasOwnProperty.call(WORKFLOW_VALUE_LABELS, trimmed)) {
            return WORKFLOW_VALUE_LABELS[trimmed];
        }
        if (Object.prototype.hasOwnProperty.call(WORKFLOW_VALUE_LABELS, lowered)) {
            return WORKFLOW_VALUE_LABELS[lowered];
        }
        if ((key === "difficulty" || key === "attitude" || key === "target_kind" || key === "verb") &&
            Object.prototype.hasOwnProperty.call(WORKFLOW_VALUE_LABELS, lowered)) {
            return WORKFLOW_VALUE_LABELS[lowered];
        }
        return trimmed;
    }

    return value;
}

function workflowTranslateValue(value, key = "") {
    if (Array.isArray(value)) {
        return value.map((item) => workflowTranslateValue(item, key));
    }

    if (workflowIsPlainObject(value)) {
        const translated = {};
        Object.entries(value).forEach(([childKey, childValue]) => {
            translated[workflowTranslateLabel(childKey)] = workflowTranslateValue(childValue, childKey);
        });
        return translated;
    }

    return workflowTranslateScalar(value, key);
}

function workflowFormatValue(value, key = "") {
    const translated = workflowTranslateValue(value, key);
    if (Array.isArray(translated) || workflowIsPlainObject(translated)) {
        return JSON.stringify(translated, null, 2);
    }
    return String(translated);
}

function workflowField(label, value, extraClass = "") {
    return `
        <div class="ai-field">
            <div class="ai-field-label">${escapeHtml(label)}</div>
            <div class="ai-field-value ${extraClass}">${escapeHtml(workflowFormatValue(value))}</div>
        </div>
    `;
}

function workflowStructuredField(label, value, sourceKey = "") {
    if (workflowIsEmpty(value)) return "";
    return `
        <div class="ai-field">
            <div class="ai-field-label">${escapeHtml(label)}</div>
            <pre class="ai-structured-value">${escapeHtml(workflowFormatValue(value, sourceKey))}</pre>
        </div>
    `;
}

function workflowPill(label, value, tone = "") {
    if (workflowIsEmpty(value)) return "";
    return `
        <div class="workflow-pill ${tone}">
            <span class="workflow-pill-label">${escapeHtml(label)}</span>
            <span class="workflow-pill-value">${escapeHtml(workflowFormatValue(value))}</span>
        </div>
    `;
}

function workflowPillRow(pills) {
    const content = pills.filter(Boolean).join("");
    if (!content) return "";
    return `<div class="workflow-pill-row">${content}</div>`;
}

function workflowSection(title, body, tone = "") {
    if (!body) return "";
    return `
        <section class="workflow-card ${tone}">
            <div class="workflow-card-title">${escapeHtml(title)}</div>
            <div class="workflow-card-body">${body}</div>
        </section>
    `;
}

function workflowDescribeAction(rulePlan) {
    const normalizedAction = workflowIsPlainObject(rulePlan && rulePlan.normalized_action) ? rulePlan.normalized_action : {};
    const verb = workflowTranslateScalar(normalizedAction.verb, "verb");
    const targetKind = workflowTranslateScalar(normalizedAction.target_kind, "target_kind");
    const targetKey = normalizedAction.target_key || normalizedAction.raw_target_text;

    if (!targetKey && verb === "无") return "未解析出明确行动";
    if (!targetKey) return `${verb}`;
    if (targetKind === "无" || targetKind === "未知") return `${verb} ${targetKey}`;
    return `${verb} ${targetKind}「${targetKey}」`;
}

function workflowDescribeCheckPlan(checkPlan) {
    if (!workflowIsPlainObject(checkPlan) || checkPlan.required === false || !checkPlan.skill) {
        return "无需检定";
    }

    const skill = workflowTranslateScalar(checkPlan.skill, "skill");
    const difficulty = workflowTranslateScalar(checkPlan.difficulty || "normal", "difficulty");
    return `${skill} / ${difficulty}`;
}

function workflowExecutionBlock(ruleResult) {
    if (!ruleResult || (!ruleResult.check_type && !ruleResult.result_description)) {
        return workflowField("执行结果", "本轮未触发检定");
    }

    const isSuccess = ruleResult.success !== false;
    const resultClass = isSuccess ? "success" : "failure";
    const resultText = ruleResult.critical_success ? "大成功" :
        ruleResult.critical_failure ? "大失败" :
        isSuccess ? "成功" : "失败";

    let html = workflowField("判定类型", workflowTranslateScalar(ruleResult.check_type, "check_type"));
    if (ruleResult.skill) html += workflowField("技能", ruleResult.skill);
    if (ruleResult.difficulty) html += workflowField("难度", workflowTranslateScalar(ruleResult.difficulty, "difficulty"));
    if (ruleResult.roll !== undefined && ruleResult.roll !== null) {
        const threshold = ruleResult.threshold ?? ruleResult.player_skill ?? "?";
        html += workflowField("投骰", `${ruleResult.roll} / ${threshold}`);
    }
    html += workflowField("结果", ruleResult.result_description || resultText, resultClass);
    return html;
}

function workflowRulePlanSection(rulePlan, ruleResult, hardChanges) {
    const normalizedAction = workflowIsPlainObject(rulePlan && rulePlan.normalized_action) ? rulePlan.normalized_action : {};
    const feasibility = workflowIsPlainObject(rulePlan && rulePlan.feasibility) ? rulePlan.feasibility : {};
    const checkPlan = workflowIsPlainObject(rulePlan && rulePlan.check) ? rulePlan.check : {};
    const tone = feasibility.ok === false ? "danger" : "success";

    let body = "";
    body += workflowPillRow([
        workflowPill("行动", workflowDescribeAction(rulePlan)),
        workflowPill("可行性", feasibility.ok === false ? "受阻" : "可执行", tone),
        workflowPill("检定计划", workflowDescribeCheckPlan(checkPlan)),
    ]);
    body += workflowField("行动概览", workflowDescribeAction(rulePlan));
    body += workflowField("可行性", feasibility.ok === false ? "当前受阻" : "允许执行", tone);
    if (feasibility.reason) body += workflowField("受阻原因", feasibility.reason, "failure");
    body += workflowField("检定计划", workflowDescribeCheckPlan(checkPlan));
    if (!workflowIsEmpty(normalizedAction.raw_target_text)) {
        body += workflowField("原始目标", normalizedAction.raw_target_text);
    }
    if (!workflowIsEmpty(rulePlan && rulePlan.location_context)) {
        body += workflowStructuredField("地点上下文", rulePlan.location_context, "location_context");
    }
    if (!workflowIsEmpty(rulePlan && rulePlan.object_context)) {
        body += workflowStructuredField("物品上下文", rulePlan.object_context, "object_context");
    }
    if (!workflowIsEmpty(rulePlan && rulePlan.npc_context)) {
        body += workflowStructuredField("NPC 上下文", rulePlan.npc_context, "npc_context");
    }
    body += workflowStructuredField("完整规则规划", rulePlan, "rule_plan");

    let html = workflowSection("规则规划（rule_plan）", body, tone);

    let resultBody = workflowExecutionBlock(ruleResult);
    resultBody += workflowStructuredField("完整判定结果", ruleResult, "rule_result");
    html += workflowSection("判定结果（rule_result）", resultBody, ruleResult && ruleResult.success === false ? "danger" : "neutral");

    let changesBody = workflowField("变化概览", workflowIsEmpty(hardChanges) ? "本轮无硬变化" : "已生成硬变化");
    changesBody += workflowStructuredField("完整硬变化", hardChanges, "hard_changes");
    html += workflowSection("硬变化（hard_changes）", changesBody, workflowIsEmpty(hardChanges) ? "neutral" : "accent");

    return html;
}

function workflowRhythmGuideSummary(result) {
    const guide = workflowIsPlainObject(result && result.npc_action_guide) ? result.npc_action_guide : {};
    return workflowPillRow([
        workflowPill("节奏结论", result && result.feasible !== false ? "可推进" : "受阻", result && result.feasible !== false ? "success" : "danger"),
        workflowPill("焦点 NPC", guide.focus_npc),
        workflowPill("下一句目标", guide.next_line_goal),
        workflowPill("是否开门", guide.should_open_door, guide.should_open_door ? "success" : ""),
    ]);
}

window.updateRulePanel = function updateRulePanel(rulePlan, ruleResult, hardChanges) {
    const panel = document.getElementById("rule-panel");
    if (!panel) return;
    if (!rulePlan && !ruleResult && !hardChanges) {
        panel.innerHTML = `<p class="placeholder-text">${WORKFLOW_EMPTY_TEXT}</p>`;
        return;
    }

    panel.innerHTML = workflowRulePlanSection(rulePlan || {}, ruleResult || {}, hardChanges || {}) ||
        `<p class="placeholder-text">${WORKFLOW_EMPTY_TEXT}</p>`;
};

window.updateRhythmPanel = function updateRhythmPanel(result) {
    const panel = document.getElementById("rhythm-panel");
    if (!panel) return;
    if (!result) {
        panel.innerHTML = `<p class="placeholder-text">${WORKFLOW_EMPTY_TEXT}</p>`;
        return;
    }

    const feasibleText = result.feasible !== false ? "允许推进" : "当前受阻";
    const feasibleClass = result.feasible !== false ? "success" : "danger";
    let body = "";
    body += workflowRhythmGuideSummary(result);
    body += workflowField("节奏结论", feasibleText, feasibleClass);
    if (result.hint) body += workflowField("叙述提示", result.hint);
    if (result.stage_assessment) body += workflowField("阶段评估", result.stage_assessment);
    body += workflowStructuredField("NPC 行动引导", result.npc_action_guide, "npc_action_guide");
    body += workflowStructuredField("软变化", result.soft_world_changes, "soft_world_changes");
    body += workflowStructuredField("合并后的世界变化", result.world_changes, "world_changes");
    body += workflowStructuredField("地点上下文", result.location_context, "location_context");
    body += workflowStructuredField("物品上下文", result.object_context, "object_context");
    body += workflowStructuredField("NPC 上下文", result.npc_context, "npc_context");
    body += workflowStructuredField("完整节奏结果", result, "rhythm_result");

    panel.innerHTML = workflowSection("节奏结果（rhythm_result）", body, feasibleClass) ||
        `<p class="placeholder-text">${WORKFLOW_EMPTY_TEXT}</p>`;
};

window.checkExistingSession = async function checkExistingSession() {
    try {
        const resp = await fetch("/trpg/api/state");
        const data = await resp.json();
        if (!data.game_started) return;

        showGameUI();
        if (data.chat_messages) {
            data.chat_messages.forEach((msg) => {
                addMessage(msg.role, msg.content, false);
            });
            scrollToBottom();
        }
        if (data.game_state) {
            updatePlayerStatus(data.game_state);
        }
        if (data.last_workflow) {
            updateRulePanel(
                data.last_workflow.rule_plan,
                data.last_workflow.rule_result,
                data.last_workflow.hard_changes
            );
            updateRhythmPanel(data.last_workflow.rhythm_result);
        }
        if (data.map_data) {
            currentMapData = data.map_data;
            renderMap(data.map_data);
        }
    } catch (err) {
        // 首次访问时保留模组选择界面
    }
};
