// ═══════════════════════════════════════
// AITRPG Web UI - Frontend Logic
// ═══════════════════════════════════════

let isProcessing = false;
let selectedDestination = null;   // 待移动的目标location key
let currentMapData = null;        // 缓存的地图数据
let progressPollTimer = null;
let processingStatusCollapsed = true;
let endingPhase = null;           // null | "triggered" | "concluded"

const PROCESSING_STAGE_GROUPS = [
    {
        key: "rule",
        order: 1,
        label: "规则AI",
        stepKeys: ["rule_intent", "rule_adjudication", "rule_check"],
    },
    {
        key: "rhythm",
        order: 2,
        label: "节奏AI",
        stepKeys: ["rhythm"],
    },
    {
        key: "narrative",
        order: 3,
        label: "文案AI",
        stepKeys: ["narrative"],
    },
];

const PROCESSING_STEP_FALLBACK_MESSAGES = {
    rule_intent: "规则AI 解析意图中……",
    rule_adjudication: "规则AI 裁定动作中……",
    rule_check: "规则层 执行判定中……",
    rhythm: "节奏AI 掌控情况中……",
    narrative: "文案AI 生成描述中……",
};

// ─── 初始化 ───

document.addEventListener("DOMContentLoaded", () => {
    loadModules();
    setupInputHandlers();
    checkExistingSession();
});

function setupInputHandlers() {
    const input = document.getElementById("chat-input");
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendAction();
        }
    });
    // Auto-resize textarea
    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 120) + "px";
    });

    document.getElementById("btn-reset").addEventListener("click", resetGame);
}

// ─── 模组加载 ───

async function loadModules() {
    try {
        const resp = await fetch("/trpg/api/modules");
        const data = await resp.json();
        const list = document.getElementById("module-list");
        list.innerHTML = "";

        data.modules.forEach((mod, index) => {
            const card = document.createElement("div");
            card.className = "module-card";
            card.onclick = () => startGame(index);
            card.innerHTML = `
                <div class="module-card-name">${escapeHtml(mod.name)}</div>
                ${mod.module_type ? `<span class="module-card-type">${escapeHtml(mod.module_type)}</span>` : ""}
                <div class="module-card-desc">${escapeHtml(mod.description)}</div>
            `;
            list.appendChild(card);
        });
    } catch (err) {
        console.error("Failed to load modules:", err);
    }
}

// ─── 检查已有会话 ───

async function checkExistingSession() {
    try {
        const resp = await fetch("/trpg/api/state");
        const data = await resp.json();
        if (data.game_started) {
            showGameUI();
            // 恢复聊天消息
            if (data.chat_messages) {
                data.chat_messages.forEach((msg) => {
                    addMessage(msg.role, msg.content, false);
                });
                scrollToBottom();
            }
            // 恢复状态
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
            // 恢复地图
            if (data.map_data) {
                currentMapData = data.map_data;
                renderMap(data.map_data);
            }
            // 恢复结局阶段
            handleEndingPhase(data.ending_phase, data.game_over, data.ending_id);
        }
    } catch (err) {
        // 首次访问，正常显示模组选择
    }
}

// ─── 开始游戏 ───

async function startGame(moduleIndex) {
    let data = null;

    try {
        const resp = await fetch("/trpg/api/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ module_index: moduleIndex })
        });

        const responseText = await resp.text();
        try {
            data = responseText ? JSON.parse(responseText) : {};
        } catch (parseErr) {
            throw new Error(responseText || `HTTP ${resp.status}`);
        }

        if (!resp.ok || data.error) {
            alert(data.error || `启动游戏失败（HTTP ${resp.status}）`);
            return;
        }
    } catch (err) {
        console.error("Failed to start game:", err);
        alert(err && err.message ? err.message : "启动游戏失败，请刷新重试。");
        return;
    }

    try {
        const messages = document.getElementById("chat-messages");
        if (messages) {
            messages.innerHTML = "";
        }

        const titleEl = document.getElementById("game-title");
        if (titleEl) {
            titleEl.textContent = data.module_name || "AI驱动TRPG";
        }

        if (data.game_state) {
            updatePlayerStatus(data.game_state);
        }

        if (data.map_data) {
            currentMapData = data.map_data;
        } else {
            currentMapData = null;
        }

        clearProcessingStatus();

        showGameUI(() => {
            try {
                addMessage("assistant", data.opening || "");
            } catch (messageErr) {
                console.error("Failed to render opening message:", messageErr);
            }

            if (currentMapData) {
                try {
                    renderMap(currentMapData);
                } catch (mapErr) {
                    console.error("Failed to render map:", mapErr, currentMapData);
                }
            }
        });
    } catch (uiErr) {
        console.error("Failed to initialize game UI:", uiErr, data);
    }
}

function showGameUI(onShown) {
    const overlay = document.getElementById("module-overlay");
    const gameContainer = document.getElementById("game-container");

    const finishShow = () => {
        if (overlay) {
            overlay.style.display = "none";
        }
        if (gameContainer) {
            gameContainer.classList.remove("hidden");
        }
        if (typeof onShown === "function") {
            requestAnimationFrame(() => onShown());
        }
    };

    if (!overlay) {
        finishShow();
        return;
    }

    overlay.classList.add("fade-out");
    setTimeout(finishShow, 250);
}

function formatDurationMs(durationMs) {
    const safeMs = Math.max(0, Number(durationMs) || 0);
    if (safeMs >= 10000) {
        return `${(safeMs / 1000).toFixed(0)}秒`;
    }
    return `${(safeMs / 1000).toFixed(1)}秒`;
}

function getProcessingStepMessage(step) {
    if (step && step.message) return step.message;
    if (step && step.key && PROCESSING_STEP_FALLBACK_MESSAGES[step.key]) {
        return PROCESSING_STEP_FALLBACK_MESSAGES[step.key];
    }
    return "AI 正在处理中……";
}

function summarizeProcessingGroup(groupDef, progress) {
    const stepsByKey = new Map(
        ((progress && progress.steps) || []).map((step) => [step.key, step || {}])
    );
    const steps = groupDef.stepKeys.map((key) => stepsByKey.get(key) || {
        key,
        status: "pending",
        duration_ms: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
        token_source: null,
        message: "",
    });

    const statuses = steps.map((step) => step.status || "pending");
    const activeStep = steps.find((step) => step.status === "running");
    const lastFinishedStep = [...steps].reverse().find((step) => step.status === "completed" || step.status === "skipped");
    const totalDurationMs = steps.reduce((sum, step) => sum + Math.max(0, Number(step.duration_ms) || 0), 0);
    const promptTokens = steps.reduce((sum, step) => sum + Math.max(0, Number(step.prompt_tokens) || 0), 0);
    const completionTokens = steps.reduce((sum, step) => sum + Math.max(0, Number(step.completion_tokens) || 0), 0);
    const totalTokens = steps.reduce((sum, step) => sum + Math.max(0, Number(step.total_tokens) || 0), 0);
    const tokenSources = new Set(steps.map((step) => step.token_source).filter(Boolean));

    let status = "pending";
    if (statuses.includes("error")) {
        status = "error";
    } else if (statuses.includes("running")) {
        status = "running";
    } else if (statuses.every((value) => value === "skipped")) {
        status = "skipped";
    } else if (statuses.some((value) => value === "completed") && statuses.some((value) => value === "pending")) {
        status = "running";
    } else if (statuses.every((value) => value === "completed" || value === "skipped")) {
        status = "completed";
    }

    let message = "";
    if (activeStep) {
        message = getProcessingStepMessage(activeStep);
    } else if (lastFinishedStep) {
        message = lastFinishedStep.message || `${groupDef.label} 已完成`;
    } else if (status === "pending") {
        message = `${groupDef.label} 等待执行`;
    } else if (status === "skipped") {
        message = `${groupDef.label} 本轮跳过`;
    } else {
        message = `${groupDef.label} 已完成`;
    }

    let tokenSource = null;
    if (tokenSources.size === 1) {
        tokenSource = [...tokenSources][0];
    } else if (tokenSources.size > 1) {
        tokenSource = "mixed";
    }

    return {
        key: groupDef.key,
        order: groupDef.order,
        label: groupDef.label,
        status,
        message,
        durationMs: totalDurationMs,
        promptTokens,
        completionTokens,
        totalTokens,
        tokenSource,
    };
}

function buildProcessingSummary(progress, groups) {
    const safeProgress = progress || {};
    const totalDuration = formatDurationMs(safeProgress.total_duration_ms || 0);
    const totalPromptTokens = Math.max(0, Number(safeProgress.summary && safeProgress.summary.prompt_tokens) || 0);
    const totalCompletionTokens = Math.max(0, Number(safeProgress.summary && safeProgress.summary.completion_tokens) || 0);
    const totalTokenText = (totalPromptTokens || totalCompletionTokens)
        ? ` 输入 ${totalPromptTokens} / 输出 ${totalCompletionTokens}`
        : "";

    if (safeProgress.status === "running") {
        const runningGroup = groups.find((group) => group.status === "running") || groups.find((group) => group.status === "pending") || groups[0];
        return `${runningGroup.message}（${runningGroup.order}/3） 已用时：${formatDurationMs(runningGroup.durationMs)}${totalTokenText}`;
    }
    if (safeProgress.status === "error") {
        return `本轮处理失败。总用时：${totalDuration}${totalTokenText}`;
    }
    if (safeProgress.status === "completed") {
        return `本轮 AI 处理完成。总用时：${totalDuration}${totalTokenText}`;
    }
    return "等待下一次行动...";
}

function renderProcessingGroup(group) {
    const statusLabel = {
        pending: "待开始",
        running: "进行中",
        completed: "已完成",
        skipped: "已跳过",
        error: "出错",
    }[group.status] || "待开始";

    const statusTone = group.status === "running"
        ? "active"
        : group.status === "completed"
        ? "success"
        : "";
    const tokenSourceLabel = group.tokenSource === "estimated"
        ? "估算"
        : group.tokenSource === "mixed"
        ? "混合"
        : "";

    return `
        <div class="processing-step ${group.status}">
            <div class="processing-step-main">
                <div class="processing-step-title">${group.order}/3 ${escapeHtml(group.label)}</div>
                <div class="processing-step-message">${escapeHtml(group.message)}</div>
            </div>
            <div class="processing-step-meta">
                <span class="processing-chip ${statusTone}">${statusLabel}</span>
                <span class="processing-chip">${escapeHtml(formatDurationMs(group.durationMs))}</span>
                ${group.promptTokens ? `<span class="processing-chip">输入 ${group.promptTokens}</span>` : ""}
                ${group.completionTokens ? `<span class="processing-chip">输出 ${group.completionTokens}</span>` : ""}
                ${group.totalTokens ? `<span class="processing-chip">总计 ${group.totalTokens}</span>` : ""}
                ${tokenSourceLabel ? `<span class="processing-chip">${tokenSourceLabel}</span>` : ""}
            </div>
        </div>
    `;
}

function hideProcessingStatus() {
    const panel = document.getElementById("processing-status");
    if (!panel) return;
    panel.classList.add("hidden");
    panel.classList.add("collapsed");
}

function renderProcessingStatus(progress, options = {}) {
    const panel = document.getElementById("processing-status");
    const badge = document.getElementById("processing-status-badge");
    const summary = document.getElementById("processing-status-summary");
    const steps = document.getElementById("processing-status-steps");
    const toggle = document.getElementById("processing-status-toggle");
    if (!panel || !badge || !summary || !steps || !toggle) return;

    if (!progress || !Object.keys(progress).length) {
        hideProcessingStatus();
        return;
    }

    const groups = PROCESSING_STAGE_GROUPS.map((groupDef) => summarizeProcessingGroup(groupDef, progress));
    const status = progress.status || "running";

    if (options.forceExpanded) {
        processingStatusCollapsed = false;
    } else if (options.forceCollapsed) {
        processingStatusCollapsed = true;
    } else if (status === "running") {
        processingStatusCollapsed = false;
    } else if (status === "completed" || status === "error") {
        processingStatusCollapsed = true;
    }

    badge.textContent = {
        running: "处理中",
        completed: "已完成",
        error: "出错",
        skipped: "已跳过",
    }[status] || "待机";
    badge.className = `processing-status-badge ${status}`;
    summary.textContent = buildProcessingSummary(progress, groups);
    steps.innerHTML = groups.map((group) => renderProcessingGroup(group)).join("");

    toggle.classList.remove("hidden");
    toggle.setAttribute("aria-expanded", processingStatusCollapsed ? "false" : "true");
    toggle.title = processingStatusCollapsed ? "展开处理详情" : "收起处理详情";

    panel.classList.remove("hidden");
    panel.classList.toggle("collapsed", processingStatusCollapsed);
}

function buildInitialProcessingState() {
    return {
        status: "running",
        message: "已提交本轮行动，等待 AI 开始处理",
        total_duration_ms: 0,
        summary: {
            prompt_tokens: 0,
            completion_tokens: 0,
            total_tokens: 0,
            token_source: null,
        },
        steps: [
            {
                key: "rule_intent",
                status: "running",
                message: "规则AI 解析意图中……",
                duration_ms: 0,
                prompt_tokens: 0,
                completion_tokens: 0,
                total_tokens: 0,
                token_source: null,
            },
            { key: "rule_adjudication", status: "pending", duration_ms: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, token_source: null },
            { key: "rule_check", status: "pending", duration_ms: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, token_source: null },
            { key: "rhythm", status: "pending", duration_ms: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, token_source: null },
            { key: "narrative", status: "pending", duration_ms: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, token_source: null },
        ],
    };
}

async function fetchAndRenderActionProgress() {
    try {
        const resp = await fetch("/trpg/api/progress", { cache: "no-store" });
        const data = await resp.json();
        const progress = data.progress || {};
        if (progress && Object.keys(progress).length) {
            renderProcessingStatus(progress);
            if (progress.status !== "running" && !isProcessing) {
                stopProgressPolling();
            }
            return progress;
        }
    } catch (err) {
        console.debug("Progress polling failed:", err);
    }

    if (!isProcessing) {
        stopProgressPolling();
    }
    return null;
}

function startProgressPolling() {
    stopProgressPolling();
    fetchAndRenderActionProgress();
    progressPollTimer = window.setInterval(() => {
        fetchAndRenderActionProgress();
    }, 500);
}

function stopProgressPolling() {
    if (progressPollTimer) {
        window.clearInterval(progressPollTimer);
        progressPollTimer = null;
    }
}

function clearProcessingStatus() {
    stopProgressPolling();
    processingStatusCollapsed = true;
    hideProcessingStatus();
}

window.toggleProcessingStatus = function toggleProcessingStatus() {
    const panel = document.getElementById("processing-status");
    if (!panel || panel.classList.contains("hidden")) return;
    processingStatusCollapsed = !processingStatusCollapsed;
    panel.classList.toggle("collapsed", processingStatusCollapsed);

    const toggle = document.getElementById("processing-status-toggle");
    if (toggle) {
        toggle.setAttribute("aria-expanded", processingStatusCollapsed ? "false" : "true");
        toggle.title = processingStatusCollapsed ? "展开处理详情" : "收起处理详情";
    }
};

window.renderProcessingStatus = renderProcessingStatus;
window.fetchAndRenderActionProgress = fetchAndRenderActionProgress;
window.startProgressPolling = startProgressPolling;
window.stopProgressPolling = stopProgressPolling;
window.clearProcessingStatus = clearProcessingStatus;

// ─── 发送行动 ───

async function sendAction() {
    if (isProcessing) return;

    const input = document.getElementById("chat-input");
    const text = input.value.trim();
    const moveTo = selectedDestination;

    // In ending phase, allow sending even with empty text
    if (endingPhase === "triggered") {
        // Player can type additional text or just click send
    } else {
        // 需要有文字输入或移动目标
        if (!text && !moveTo) return;
    }

    // 清空输入
    input.value = "";
    input.style.height = "auto";

    // 构建显示文本
    let displayText = text;
    if (endingPhase === "triggered") {
        displayText = text ? `[进入结局] ${text}` : "[进入结局]";
    } else if (moveTo && !text) {
        const locData = currentMapData && currentMapData.locations[moveTo];
        const locName = locData ? locData.display_name : moveTo;
        displayText = `[移动到${locName}]`;
    } else if (moveTo && text) {
        const locData = currentMapData && currentMapData.locations[moveTo];
        const locName = locData ? locData.display_name : moveTo;
        displayText = `[移动到${locName}] ${text}`;
    }

    // 清除移动选择
    cancelMoveSelection();

    // 显示用户消息
    addMessage("user", displayText);

    // 显示 loading
    isProcessing = true;
    setInputEnabled(false);
    const loadingEl = addLoadingIndicator();
    renderProcessingStatus(buildInitialProcessingState(), { forceExpanded: true });
    startProgressPolling();

    try {
        const body = {};
        if (endingPhase === "triggered") {
            body.input = text || "[进入结局]";
            // Don't send move_to in ending phase
        } else {
            if (text) body.input = text;
            if (moveTo) body.move_to = moveTo;
        }

        const resp = await fetch("/trpg/api/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });
        const data = await resp.json();

        // 移除 loading
        loadingEl.remove();

        if (data.error) {
            renderProcessingStatus({
                status: "error",
                message: data.error,
                total_duration_ms: 0,
                summary: {
                    prompt_tokens: 0,
                    completion_tokens: 0,
                    total_tokens: 0,
                    token_source: null,
                },
                steps: [],
            }, { forceCollapsed: true });
            addMessage("assistant", "处理出错: " + data.error);
        } else {
            // 显示叙述
            addMessage("assistant", data.narrative);

            // 更新左侧面板
            updateRulePanel(data.rule_plan, data.rule_result, data.hard_changes);
            updateRhythmPanel(data.rhythm_result);

            // 更新右侧状态
            if (data.game_state) {
                updatePlayerStatus(data.game_state);
            }

            // 更新地图
            if (data.map_data) {
                currentMapData = data.map_data;
                renderMap(data.map_data);
            }

            renderProcessingStatus(data.telemetry || null, { forceCollapsed: true });

            // 处理结局阶段
            handleEndingPhase(data.ending_phase, data.game_over, data.ending_id);
        }
    } catch (err) {
        loadingEl.remove();
        renderProcessingStatus({
            status: "error",
            message: "网络错误",
            total_duration_ms: 0,
            summary: {
                prompt_tokens: 0,
                completion_tokens: 0,
                total_tokens: 0,
                token_source: null,
            },
            steps: [],
        }, { forceCollapsed: true });
        addMessage("assistant", "网络错误，请重试。");
        console.error("Action failed:", err);
    } finally {
        stopProgressPolling();
        isProcessing = false;
        // Don't re-enable input if game is concluded
        if (endingPhase === "concluded") {
            // Everything stays disabled
        } else {
            setInputEnabled(true);
            document.getElementById("chat-input").focus();
        }
    }
}

function setInputEnabled(enabled) {
    document.getElementById("chat-input").disabled = !enabled;
    document.getElementById("btn-send").disabled = !enabled;
}

// ─── 消息管理 ───

function addMessage(role, content, animate = true) {
    const container = document.getElementById("chat-messages");
    const msg = document.createElement("div");
    msg.className = `message ${role}`;
    if (!animate) msg.style.animation = "none";

    const avatarIcon = role === "user" ? "mdi-account" : "mdi-robot";
    msg.innerHTML = `
        <div class="message-avatar">
            <span class="mdi ${avatarIcon}"></span>
        </div>
        <div class="message-bubble">${escapeHtml(content)}</div>
    `;
    container.appendChild(msg);
    scrollToBottom();
}

function addLoadingIndicator() {
    const container = document.getElementById("chat-messages");
    const msg = document.createElement("div");
    msg.className = "message assistant";
    msg.innerHTML = `
        <div class="message-avatar">
            <span class="mdi mdi-robot"></span>
        </div>
        <div class="message-bubble">
            <div class="loading-dots">
                <span></span><span></span><span></span>
            </div>
        </div>
    `;
    container.appendChild(msg);
    scrollToBottom();
    return msg;
}

function scrollToBottom() {
    const container = document.getElementById("chat-messages");
    requestAnimationFrame(() => {
        container.scrollTop = container.scrollHeight;
    });
}

// ─── 左侧面板更新 ───

function updateRulePanel(result) {
    const panel = document.getElementById("rule-panel");
    if (!result || !result.check_type) {
        panel.innerHTML = `
            <div class="ai-field">
                <div class="ai-field-value">本轮未触发检定</div>
            </div>
        `;
        return;
    }

    const isSuccess = result.success;
    const resultClass = isSuccess ? "success" : "failure";
    const resultText = result.critical_success ? "大成功!" :
                       result.critical_failure ? "大失败!" :
                       isSuccess ? "成功" : "失败";

    panel.innerHTML = `
        <div class="ai-field">
            <div class="ai-field-label">技能</div>
            <div class="ai-field-value">${escapeHtml(result.skill || "无")}</div>
        </div>
        <div class="ai-field">
            <div class="ai-field-label">难度</div>
            <div class="ai-field-value">${escapeHtml(result.difficulty || "普通")}</div>
        </div>
        <div class="ai-field">
            <div class="ai-field-label">投骰</div>
            <div class="ai-field-value">${result.roll || "?"} / ${result.player_skill || "?"}</div>
        </div>
        <div class="ai-field">
            <div class="ai-field-label">结果</div>
            <div class="ai-field-value ${resultClass}">${resultText}</div>
        </div>
    `;
}

function updateRhythmPanel(result) {
    const panel = document.getElementById("rhythm-panel");
    if (!result) {
        panel.innerHTML = `<p class="placeholder-text">等待游戏行动...</p>`;
        return;
    }

    const feasibleText = result.feasible !== false ? "允许推进" : "当前受阻";
    const feasibleClass = result.feasible !== false ? "success" : "failure";

    let html = `
        <div class="ai-field">
            <div class="ai-field-label">行动判断</div>
            <div class="ai-field-value ${feasibleClass}">${feasibleText}</div>
        </div>
    `;

    if (result.hint) {
        html += `
        <div class="ai-field">
            <div class="ai-field-label">提示</div>
            <div class="ai-field-value">${escapeHtml(result.hint)}</div>
        </div>
        `;
    }

    if (result.stage_assessment) {
        html += `
        <div class="ai-field">
            <div class="ai-field-label">阶段评估</div>
            <div class="ai-field-value">${escapeHtml(result.stage_assessment)}</div>
        </div>
        `;
    }

    panel.innerHTML = html;
}

// ─── 右侧面板更新 ───

function updatePlayerStatus(state) {
    if (!state) return;

    const player = state.player || {};
    const world = state.world_state || {};

    // SAN
    const san = player.san || 0;
    const sanMax = 65;
    const sanPct = Math.max(0, Math.min(100, (san / sanMax) * 100));
    document.getElementById("san-bar").style.width = sanPct + "%";
    document.getElementById("san-value").textContent = `${san}/${sanMax}`;

    // HP
    const hp = player.hp || 0;
    const hpMax = 12;
    const hpPct = Math.max(0, Math.min(100, (hp / hpMax) * 100));
    document.getElementById("hp-bar").style.width = hpPct + "%";
    document.getElementById("hp-value").textContent = `${hp}/${hpMax}`;

    // Skills
    const skillsEl = document.getElementById("player-skills");
    const skills = player.skills || {};
    const skillEntries = Object.entries(skills);
    if (skillEntries.length > 0) {
        skillsEl.innerHTML = skillEntries.map(([name, value]) =>
            `<span class="item-tag">${escapeHtml(name)} ${escapeHtml(String(value))}</span>`
        ).join("");
    } else {
        skillsEl.innerHTML = `<span class="placeholder-text">暂无技能</span>`;
    }

    // Inventory
    const invEl = document.getElementById("player-inventory");
    const inventory = player.inventory || [];
    if (inventory.length > 0) {
        invEl.innerHTML = inventory.map(item =>
            `<span class="item-tag">${escapeHtml(item)}</span>`
        ).join("");
    } else {
        invEl.innerHTML = `<span class="placeholder-text">暂无物品</span>`;
    }

    // Clues
    const clueEl = document.getElementById("player-clues");
    const clues = world.clues_found || [];
    if (clues.length > 0) {
        clueEl.innerHTML = clues.map(clue =>
            `<span class="clue-tag">${escapeHtml(clue)}</span>`
        ).join("");
    } else {
        clueEl.innerHTML = `<span class="placeholder-text">暂未发现线索</span>`;
    }
}

// ─── 面板折叠 ───

function togglePanel(side) {
    const panel = document.getElementById(side + "-panel");
    const expandBtn = document.getElementById(side + "-expand");

    if (panel.classList.contains("collapsed")) {
        panel.classList.remove("collapsed");
        expandBtn.classList.add("hidden");
    } else {
        panel.classList.add("collapsed");
        expandBtn.classList.remove("hidden");
    }
}

function toggleStatusSection(sectionId) {
    const section = document.getElementById(sectionId);
    if (!section) return;
    section.classList.toggle("status-section-collapsed");
}

// ─── 重置游戏 ───

async function resetGame() {
    if (!confirm("确定要重置游戏吗？所有进度将丢失。")) return;

    try {
        await fetch("/trpg/api/reset", { method: "POST" });

        // 重置结局状态
        endingPhase = null;
        const input = document.getElementById("chat-input");
        input.disabled = false;
        input.placeholder = "输入你的行动...";
        document.getElementById("btn-send").disabled = false;

        // 隐藏结局相关UI
        document.getElementById("ending-indicator").classList.add("hidden");
        document.getElementById("ending-overlay").classList.add("hidden");

        // 重置地图样式
        const svg = document.getElementById("map-svg");
        if (svg) {
            svg.style.pointerEvents = "";
            svg.style.opacity = "";
        }

        // 清空聊天
        document.getElementById("chat-messages").innerHTML = "";

        // 重置面板
        document.getElementById("rule-panel").innerHTML = `<p class="placeholder-text">等待游戏行动...</p>`;
        document.getElementById("rhythm-panel").innerHTML = `<p class="placeholder-text">等待游戏行动...</p>`;

        // 重置地图
        selectedDestination = null;
        currentMapData = null;
        document.getElementById("map-svg").innerHTML = "";
        document.getElementById("move-indicator").classList.add("hidden");

        // 回到模组选择
        document.getElementById("game-container").classList.add("hidden");
        const overlay = document.getElementById("module-overlay");
        overlay.style.display = "";
        overlay.classList.remove("fade-out");
        clearProcessingStatus();
        loadModules();
    } catch (err) {
        console.error("Reset failed:", err);
    }
}

// ─── 工具函数 ───

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ─── 地图渲染与交互 ───

function renderMap(mapData) {
    try {
        const svg = document.getElementById("map-svg");
        if (!svg) return;

        // 清空SVG（兼容所有浏览器）
        while (svg.firstChild) svg.removeChild(svg.firstChild);

        if (!mapData || !mapData.locations || typeof mapData.locations !== "object") {
            console.warn("[Map] No map data or invalid locations:", mapData);
            return;
        }

        const locations = mapData.locations;
        const edges = Array.isArray(mapData.edges) ? mapData.edges : [];
        const currentLoc = mapData.current_location;
        const reachable = new Set(Array.isArray(mapData.reachable) ? mapData.reachable : []);
        const dangerLocations = new Set(Array.isArray(mapData.danger_locations) ? mapData.danger_locations : []);

        const keys = Object.keys(locations);
        if (keys.length === 0) {
            console.warn("[Map] Empty locations");
            return;
        }

        // 按floor分组，高楼层在上
        const floorGroups = {};
        for (const key of keys) {
            const loc = locations[key] || {};
            const floor = loc.floor;
            if (floor === undefined || floor === null) continue;
            if (!floorGroups[floor]) floorGroups[floor] = [];
            floorGroups[floor].push(key);
        }
        const floors = Object.keys(floorGroups).map(Number).sort((a, b) => b - a);

        // 构建邻接表（仅可见节点之间）
        const adj = {};
        for (const key of keys) adj[key] = [];
        for (const edge of edges) {
            if (edge && locations[edge.from] && locations[edge.to]) {
                adj[edge.from].push(edge.to);
                adj[edge.to].push(edge.from);
            }
        }

        // 布局参数
        const nodeW = 60;
        const nodeH = 28;
        const gapX = 16;
        const gapY = 56;
        const labelW = 28;
        const padX = 8;
        const padY = 12;
        const ns = "http://www.w3.org/2000/svg";

        // 每层内布局：找hub居中，其他左右排列
        const nodePositions = {};
        let yOffset = padY;

        for (const floor of floors) {
            const group = floorGroups[floor];
            if (!group || group.length === 0) continue;

            // 找连接数最多的节点作为hub
            let hubKey = group[0];
            let maxConn = 0;
            for (const key of group) {
                const conn = (adj[key] || []).length;
                if (conn > maxConn) {
                    maxConn = conn;
                    hubKey = key;
                }
            }

            // 排列：hub居中，其他按连接关系左右交替
            const ordered = [hubKey];
            const remaining = group.filter(k => k !== hubKey);
            const connected = remaining.filter(k => (adj[hubKey] || []).includes(k));
            const unconnected = remaining.filter(k => !(adj[hubKey] || []).includes(k));

            let left = true;
            for (const k of [...connected, ...unconnected]) {
                if (left) {
                    ordered.unshift(k);
                } else {
                    ordered.push(k);
                }
                left = !left;
            }

            const startX = labelW + padX;
            for (let i = 0; i < ordered.length; i++) {
                nodePositions[ordered[i]] = {
                    x: startX + i * (nodeW + gapX),
                    y: yOffset
                };
            }

            yOffset += nodeH + gapY;
        }

        // 计算SVG尺寸
        let maxX = 0;
        for (const pos of Object.values(nodePositions)) {
            const right = pos.x + nodeW + padX;
            if (right > maxX) maxX = right;
        }
        const svgW = Math.max(maxX, 200);
        const svgH = yOffset - gapY + nodeH + padY;

        // 设置SVG尺寸
        svg.setAttribute("viewBox", `0 0 ${svgW} ${svgH}`);
        svg.removeAttribute("width");
        svg.removeAttribute("height");

        // 绘制楼层标签
        for (const floor of floors) {
            const group = floorGroups[floor];
            const firstKey = group[0];
            const pos = nodePositions[firstKey];
            if (!pos) continue;

            const label = document.createElementNS(ns, "text");
            label.setAttribute("x", "4");
            label.setAttribute("y", String(pos.y + nodeH / 2));
            label.setAttribute("class", "map-floor-label");
            label.textContent = floor >= 1 ? `${floor}F` : `B${Math.abs(floor)}`;
            svg.appendChild(label);
        }

        // 绘制边
        for (const edge of edges) {
            if (!edge) continue;
            const fromPos = nodePositions[edge.from];
            const toPos = nodePositions[edge.to];
            if (!fromPos || !toPos) continue;

            const line = document.createElementNS(ns, "line");
            line.setAttribute("x1", String(fromPos.x + nodeW / 2));
            line.setAttribute("y1", String(fromPos.y + nodeH / 2));
            line.setAttribute("x2", String(toPos.x + nodeW / 2));
            line.setAttribute("y2", String(toPos.y + nodeH / 2));
            line.setAttribute("class", edge.locked ? "map-edge map-edge--locked" : "map-edge");
            svg.appendChild(line);
        }

        // 绘制节点
        for (const key of keys) {
            const loc = locations[key] || {};
            const pos = nodePositions[key];
            if (!pos) continue;

            const isCurrent = key === currentLoc;
            const isReachable = reachable.has(key);
            const isVisited = Boolean(loc.visited);
            const isSelected = key === selectedDestination;
            const isDanger = dangerLocations.has(key);

            let nodeClass = "map-node";
            if (isCurrent) {
                nodeClass += " map-node--current";
            } else if (!isReachable) {
                nodeClass += " map-node--locked";
            } else if (!isVisited) {
                nodeClass += " map-node--fog";
            } else {
                nodeClass += " map-node--visited";
            }
            if (isSelected) {
                nodeClass += " map-node--selected";
            }
            if (isDanger) {
                nodeClass += " map-node--danger";
            }

            const g = document.createElementNS(ns, "g");
            g.setAttribute("class", nodeClass);

            const rect = document.createElementNS(ns, "rect");
            rect.setAttribute("x", String(pos.x));
            rect.setAttribute("y", String(pos.y));
            rect.setAttribute("width", String(nodeW));
            rect.setAttribute("height", String(nodeH));
            rect.setAttribute("rx", "6");
            rect.setAttribute("ry", "6");
            g.appendChild(rect);

            const text = document.createElementNS(ns, "text");
            text.setAttribute("x", String(pos.x + nodeW / 2));
            text.setAttribute("y", String(pos.y + nodeH / 2));

            let displayName = typeof loc.display_name === "string" ? loc.display_name : "?";
            if (displayName.length > 5) {
                displayName = displayName.substring(0, 4) + "…";
            }
            text.textContent = displayName;
            g.appendChild(text);

            if (isCurrent || (!isCurrent && isReachable)) {
                g.style.cursor = "pointer";
                g.addEventListener("click", () => onMapNodeClick(key));
            }

            svg.appendChild(g);
        }

        console.log(`[Map] Rendered ${keys.length} nodes, ${edges.length} edges`);
    } catch (err) {
        console.error("[Map] Render failed:", err, mapData);
    }
}

function onMapNodeClick(locationKey) {
    if (!currentMapData) return;
    const reachable = new Set(currentMapData.reachable || []);

    // 点击当前位置 → 取消选择
    if (locationKey === currentMapData.current_location) {
        cancelMoveSelection();
        return;
    }

    // 点击不可达 → 无效果
    if (!reachable.has(locationKey)) return;

    // 设为选中目标
    selectedDestination = locationKey;

    // 更新移动提示
    const loc = currentMapData.locations[locationKey];
    const locName = loc ? loc.display_name : locationKey;
    const indicator = document.getElementById("move-indicator");
    const indicatorText = document.getElementById("move-indicator-text");
    indicatorText.textContent = `即将移动到：${locName}`;
    indicator.classList.remove("hidden");

    // 重新渲染地图以更新选中样式
    renderMap(currentMapData);
}

function cancelMoveSelection() {
    selectedDestination = null;
    const indicator = document.getElementById("move-indicator");
    indicator.classList.add("hidden");

    // 重新渲染地图以清除选中样式
    if (currentMapData) renderMap(currentMapData);
}

// ─── 结局阶段处理 ───

const ENDING_NAMES = {
    insane: "疯狂结局",
    escaped: "逃脱结局",
    getlost: "迷失结局",
    amnesia: "失忆结局",
};

function getEndingDisplayName(endingId) {
    return ENDING_NAMES[endingId] || "结局";
}

function handleEndingPhase(phase, gameOver, endingId) {
    endingPhase = phase || null;

    if (phase === "triggered") {
        showEndingIndicator(endingId);
        disableMapInteraction();
    } else if (phase === "concluded" || gameOver) {
        hideEndingIndicator();
        disableAllGameInput();
        showEndingOverlay(endingId);
    }
}

function showEndingIndicator(endingId) {
    const indicator = document.getElementById("ending-indicator");
    const indicatorText = document.getElementById("ending-indicator-text");
    const endingName = getEndingDisplayName(endingId);
    indicatorText.textContent = `即将进入：${endingName}`;
    indicator.classList.remove("hidden");

    // Hide move indicator if visible
    document.getElementById("move-indicator").classList.add("hidden");
    selectedDestination = null;
}

function hideEndingIndicator() {
    const indicator = document.getElementById("ending-indicator");
    indicator.classList.add("hidden");
}

function disableMapInteraction() {
    const svg = document.getElementById("map-svg");
    if (svg) {
        svg.style.pointerEvents = "none";
        svg.style.opacity = "0.5";
    }
}

function disableAllGameInput() {
    const input = document.getElementById("chat-input");
    input.disabled = true;
    input.value = "";
    input.placeholder = "游戏已结束";
    document.getElementById("btn-send").disabled = true;
    disableMapInteraction();
    cancelMoveSelection();
}

function showEndingOverlay(endingId) {
    const overlay = document.getElementById("ending-overlay");
    const title = document.getElementById("ending-overlay-title");
    const desc = document.getElementById("ending-overlay-desc");
    const endingName = getEndingDisplayName(endingId);

    title.textContent = endingName;
    desc.textContent = "你的冒险到此结束了。";
    overlay.classList.remove("hidden");
}

function dismissEndingOverlay() {
    document.getElementById("ending-overlay").classList.add("hidden");
}
