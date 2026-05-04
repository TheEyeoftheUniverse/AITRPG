// ═══════════════════════════════════════
// AITRPG Web UI - Frontend Logic
// ═══════════════════════════════════════

let isProcessing = false;
let selectedDestination = null;   // 待移动的目标location key
let currentMapData = null;        // 缓存的地图数据
let progressPollTimer = null;
let processingStatusCollapsed = false;
let endingPhase = null;           // null | "triggered" | "concluded"
let currentAbortController = null; // 用于中断正在进行的fetch请求
let lastPlayerInput = "";          // 上次玩家输入（用于重试恢复）
let lastMoveDestination = null;    // 上次移动目标（用于重试恢复）
let availableModules = [];         // 可选模组列表缓存
let currentSaveSummary = null;     // 当前浏览器的显式恢复摘要
let latestRetryFrom = null;        // 后端建议的断点重试层
let canRetryCurrentTurn = false;   // 当前轮是否允许直接调用服务端重试
const MOBILE_LAYOUT_BREAKPOINT = 820;
let lastViewportMobile = null;

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

const PROCESSING_STAGE_GROUPS_MERGED = [
    {
        key: "rule",
        order: 1,
        label: "规则AI",
        stepKeys: ["rule_intent", "rule_adjudication", "rule_check"],
    },
    {
        key: "story",
        order: 2,
        label: "剧情AI",
        stepKeys: ["story"],
    },
];

function pickProcessingStageGroups(progress) {
    const steps = (progress && progress.steps) || [];
    if (steps.some((step) => step && step.key === "story")) {
        return PROCESSING_STAGE_GROUPS_MERGED;
    }
    if (steps.length === 0 && isMergeMode()) {
        return PROCESSING_STAGE_GROUPS_MERGED;
    }
    return PROCESSING_STAGE_GROUPS;
}

const PROCESSING_STEP_FALLBACK_MESSAGES = {
    rule_intent: "规则AI 解析意图中……",
    rule_adjudication: "规则AI 裁定动作中……",
    rule_check: "规则层 执行判定中……",
    rhythm: "节奏AI 掌控情况中……",
    narrative: "文案AI 生成描述中……",
    story: "剧情AI 生成节奏+叙述中……",
};

// ─── 初始化 ───

document.addEventListener("DOMContentLoaded", () => {
    applyStoredTheme();
    setupInputHandlers();
    syncMobileLayoutPanels(true);
    window.addEventListener("resize", () => syncMobileLayoutPanels());
    initializeModuleSelection();
    _updateApiButtonState();
    _updateMergeModeButton();
});

// ─── 主题切换 ───

function applyStoredTheme() {
    const stored = localStorage.getItem("aitrpg_theme");
    if (stored) {
        document.documentElement.setAttribute("data-theme", stored);
    }
    _updateThemeIcon();
}

function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme");
    const next = current === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("aitrpg_theme", next);
    _updateThemeIcon();
}

function _updateThemeIcon() {
    const btn = document.getElementById("btn-theme");
    if (!btn) return;
    const isDark = document.documentElement.getAttribute("data-theme") !== "light";
    btn.innerHTML = isDark
        ? '<span class="mdi mdi-weather-night"></span>'
        : '<span class="mdi mdi-white-balance-sunny"></span>';
    btn.title = isDark ? "切换到亮色主题" : "切换到暗色主题";
}

// ─── AI模式切换（三层 ↔ 合并双层） ───

const MERGE_MODE_LS_KEY = "aitrpg_merge_mode";

function isMergeMode() {
    return localStorage.getItem(MERGE_MODE_LS_KEY) === "true";
}

function toggleMergeMode() {
    const next = !isMergeMode();
    localStorage.setItem(MERGE_MODE_LS_KEY, String(next));
    _updateMergeModeButton();
}

function _updateMergeModeButton() {
    const btn = document.getElementById("btn-merge-mode");
    if (!btn) return;
    const on = isMergeMode();
    btn.innerHTML = on
        ? '<span class="mdi mdi-layers-outline"></span>'
        : '<span class="mdi mdi-layers-triple"></span>';
    btn.title = on
        ? "当前：双层模式（节奏+文案合并，响应更快）。点击切回三层模式"
        : "当前：三层模式（节奏和文案分开）。点击切换到双层模式";
    btn.classList.toggle("merge-mode-on", on);
}

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

function isMobileLayout() {
    return window.matchMedia(`(max-width: ${MOBILE_LAYOUT_BREAKPOINT}px)`).matches;
}

function updateMobilePanelBackdrop() {
    const backdrop = document.getElementById("mobile-panel-backdrop");
    if (!backdrop) return;

    if (!isMobileLayout()) {
        backdrop.classList.add("hidden");
        backdrop.classList.remove("active");
        return;
    }

    const leftPanel = document.getElementById("left-panel");
    const rightPanel = document.getElementById("right-panel");
    const hasOpenPanel = Boolean(
        (leftPanel && !leftPanel.classList.contains("collapsed")) ||
        (rightPanel && !rightPanel.classList.contains("collapsed"))
    );
    backdrop.classList.toggle("hidden", !hasOpenPanel);
    backdrop.classList.toggle("active", hasOpenPanel);
}

function closeMobilePanels() {
    if (!isMobileLayout()) return;

    const leftPanel = document.getElementById("left-panel");
    const rightPanel = document.getElementById("right-panel");
    const leftExpand = document.getElementById("left-expand");
    const rightExpand = document.getElementById("right-expand");

    if (leftPanel) leftPanel.classList.add("collapsed");
    if (rightPanel) rightPanel.classList.add("collapsed");
    if (leftExpand) leftExpand.classList.remove("hidden");
    if (rightExpand) rightExpand.classList.remove("hidden");
    updateMobilePanelBackdrop();
}

function syncMobileLayoutPanels(forceCollapse = false) {
    const mobile = isMobileLayout();
    const leftPanel = document.getElementById("left-panel");
    const rightPanel = document.getElementById("right-panel");
    const leftExpand = document.getElementById("left-expand");
    const rightExpand = document.getElementById("right-expand");

    if (!leftPanel || !rightPanel || !leftExpand || !rightExpand) {
        lastViewportMobile = mobile;
        return;
    }

    if (mobile && (forceCollapse || lastViewportMobile !== true)) {
        leftPanel.classList.add("collapsed");
        rightPanel.classList.add("collapsed");
        leftExpand.classList.remove("hidden");
        rightExpand.classList.remove("hidden");
    }

    updateMobilePanelBackdrop();
    lastViewportMobile = mobile;
}

// ─── 模组加载 ───

async function initializeModuleSelection() {
    await loadModules();
    await loadSaveSummary();
}

async function loadModules() {
    try {
        const resp = await fetch("/trpg/api/modules");
        const data = await resp.json();
        availableModules = Array.isArray(data.modules) ? data.modules : [];
        renderModuleCards();
    } catch (err) {
        console.error("Failed to load modules:", err);
    }
}

async function loadSaveSummary() {
    try {
        const resp = await fetch("/trpg/api/save-summary");
        const data = await resp.json();
        currentSaveSummary = data && data.has_save ? data.save : null;
        renderModuleCards();
    } catch (err) {
        console.error("Failed to load save summary:", err);
        currentSaveSummary = null;
        renderModuleCards();
    }
}

function renderModuleCards() {
    const list = document.getElementById("module-list");
    if (!list) return;
    list.innerHTML = "";

    availableModules.forEach((mod, index) => {
        const hasResume = Boolean(
            currentSaveSummary
            && Number(currentSaveSummary.module_index) === index
            && !currentSaveSummary.game_over
        );
        const card = document.createElement("div");
        card.className = `module-card${hasResume ? " module-card--has-save" : ""}`;

        const saveMeta = hasResume
            ? `
                <div class="module-card-save">
                    <div class="module-card-save-title">检测到中断存档</div>
                    <div class="module-card-save-meta">
                        第 ${Number(currentSaveSummary.round_count || 0)} 回合 · ${escapeHtml(currentSaveSummary.current_location_name || currentSaveSummary.current_location || "未知地点")}
                    </div>
                    ${currentSaveSummary.saved_at ? `<div class="module-card-save-time">保存于 ${escapeHtml(formatSaveTime(currentSaveSummary.saved_at))}</div>` : ""}
                </div>
            `
            : "";

        card.innerHTML = `
            <div class="module-card-name">${escapeHtml(mod.name)}</div>
            ${mod.module_type ? `<span class="module-card-type">${escapeHtml(mod.module_type)}</span>` : ""}
            <div class="module-card-desc">${escapeHtml(mod.description)}</div>
            ${saveMeta}
            <div class="module-card-actions">
                ${hasResume ? `<button class="module-card-btn module-card-btn--primary" type="button" data-role="resume" data-index="${index}">继续存档</button>` : ""}
                <button class="module-card-btn${hasResume ? " module-card-btn--secondary" : " module-card-btn--primary"}" type="button" data-role="start" data-index="${index}">
                    ${hasResume ? "开始新游戏" : "开始游戏"}
                </button>
            </div>
        `;

        const startBtn = card.querySelector('[data-role="start"]');
        if (startBtn) {
            startBtn.addEventListener("click", () => startGame(index));
        }
        const resumeBtn = card.querySelector('[data-role="resume"]');
        if (resumeBtn) {
            resumeBtn.addEventListener("click", () => resumeGame());
        }
        list.appendChild(card);
    });

    if (availableModules.length === 0) {
        list.innerHTML = `<div class="module-card"><div class="module-card-desc">未找到可用模组。</div></div>`;
    }
}

// ─── 开始游戏 ───

async function startGame(moduleIndex, forceNew = false) {
    let data = null;

    if (currentSaveSummary && !forceNew) {
        const confirmed = confirm("检测到未完成的断点存档。开始新游戏会覆盖当前断点，是否继续？");
        if (!confirmed) return;
        forceNew = true;
    }

    try {
        const characterCard = (typeof getCurrentCharacterCard === "function") ? getCurrentCharacterCard() : null;
        const resp = await fetch("/trpg/api/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                module_index: moduleIndex,
                force_new: forceNew,
                character_card: characterCard
            })
        });

        const responseText = await resp.text();
        try {
            data = responseText ? JSON.parse(responseText) : {};
        } catch (parseErr) {
            throw new Error(responseText || `HTTP ${resp.status}`);
        }

        if (!resp.ok || data.error) {
            if (data && data.requires_confirm && !forceNew) {
                const confirmed = confirm("检测到未完成的断点存档。开始新游戏会覆盖当前断点，是否继续？");
                if (confirmed) {
                    return startGame(moduleIndex, true);
                }
            }
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

        currentSaveSummary = null;
        if (data.game_state) {
            updatePlayerStatus(data.game_state);
        }

        if (data.map_data) {
            currentMapData = data.map_data;
        } else {
            currentMapData = null;
        }

        clearProcessingStatus();
        clearRetryState();

        showGameUI(async () => {
            try {
                // Phase 5: 开局硬 placeholder 骰点动画 (与行动骰点同管线)
                if (data.dice_rolls && data.dice_rolls.length > 0) {
                    for (const diceRoll of data.dice_rolls) {
                        await showDiceRollPanel(diceRoll, requestPushRoll);
                    }
                    await theatricalSleep(3000);
                }
                addMessage("assistant", data.opening || "");
                // 处理内联标记 (glitch/echo-text → span)
                const lastMsg = document.getElementById("chat-messages").lastElementChild;
                processInlineMarkers(lastMsg);
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

            // 开场白演出效果
            if (data.theatrical_effects && data.theatrical_effects.length) {
                setTimeout(() => processTheatricalEffects(data.theatrical_effects), 500);
            }
        });
    } catch (uiErr) {
        console.error("Failed to initialize game UI:", uiErr, data);
    }
}

async function resumeGame() {
    let data = null;
    try {
        const resp = await fetch("/trpg/api/resume", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
        });
        data = await resp.json();
        if (!resp.ok || data.error) {
            alert(data.error || `恢复存档失败（HTTP ${resp.status}）`);
            return;
        }
    } catch (err) {
        console.error("Failed to resume game:", err);
        alert(err && err.message ? err.message : "恢复存档失败，请刷新重试。");
        return;
    }

    try {
        const messages = document.getElementById("chat-messages");
        if (messages) {
            messages.innerHTML = "";
        }

        const titleEl = document.getElementById("game-title");
        if (titleEl) {
            const resumedModuleName = data.game_state
                && data.game_state.module_data
                && data.game_state.module_data.module_info
                ? data.game_state.module_data.module_info.name
                : "";
            titleEl.textContent = resumedModuleName
                || (currentSaveSummary && currentSaveSummary.module_name)
                || "AI驱动TRPG";
        }

        if (Array.isArray(data.chat_messages)) {
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
            if (data.last_workflow.telemetry) {
                renderProcessingStatus(data.last_workflow.telemetry, { forceCollapsed: true });
            }
        } else {
            clearProcessingStatus();
        }

        if (data.map_data) {
            currentMapData = data.map_data;
        } else {
            currentMapData = null;
        }

        clearRetryState();
        currentSaveSummary = null;

        showGameUI(() => {
            if (currentMapData) {
                try {
                    renderMap(currentMapData);
                } catch (mapErr) {
                    console.error("Failed to render resumed map:", mapErr, currentMapData);
                }
            }
        });

        handleEndingPhase(data.ending_phase, data.game_over, data.ending_id, data.ending_display);
    } catch (uiErr) {
        console.error("Failed to initialize resumed game UI:", uiErr, data);
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
        syncMobileLayoutPanels(true);
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

function formatAttemptSummary(attempt) {
    if (!attempt) return "";
    const model = String(attempt.model_display || attempt.provider_id || "").trim();
    const status = String(attempt.status || "").trim();
    const message = String(attempt.message || "").trim();
    if (status === "success") {
        return `${model} 成功`;
    }
    return `${model} ${message || status || "失败"}`;
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
    const modelDisplays = [...new Set(
        steps
            .map((step) => String(step.model_display || "").trim())
            .filter(Boolean)
    )];
    const fallbackUsed = steps.some((step) => Boolean(step.fallback_used));
    const selectedAttempt = steps.find((step) => Number(step.selected_attempt_index) > 0 && Number(step.candidate_count) > 0);
    const attemptCarrier = activeStep
        || [...steps].reverse().find((step) => Array.isArray(step.attempts) && step.attempts.length)
        || selectedAttempt
        || null;
    const attemptSummary = attemptCarrier && Array.isArray(attemptCarrier.attempts)
        ? attemptCarrier.attempts.map(formatAttemptSummary).filter(Boolean).join(" -> ")
        : "";

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

    if (attemptSummary && (fallbackUsed || status === "error")) {
        message = `${message} | ${attemptSummary}`;
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
        modelDisplay: modelDisplays.join(" | "),
        fallbackUsed,
        selectedAttemptIndex: selectedAttempt ? Number(selectedAttempt.selected_attempt_index) || 0 : 0,
        candidateCount: selectedAttempt ? Number(selectedAttempt.candidate_count) || 0 : 0,
        attemptSummary,
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
        return `${runningGroup.message}（${runningGroup.order}/${groups.length}） 已用时：${formatDurationMs(runningGroup.durationMs)}${totalTokenText}`;
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
        : group.status === "error"
        ? "danger"
        : "";
    const tokenSourceLabel = group.tokenSource === "mixed" ? "混合" : "";

    return `
        <div class="processing-step ${group.status}">
            <div class="processing-step-main">
                <div class="processing-step-title">${group.order}/${group.total} ${escapeHtml(group.label)}</div>
                <div class="processing-step-message">${escapeHtml(group.message)}</div>
            </div>
            <div class="processing-step-meta">
                <span class="processing-chip ${statusTone}">${statusLabel}</span>
                <span class="processing-chip">${escapeHtml(formatDurationMs(group.durationMs))}</span>
                ${group.modelDisplay ? `<span class="processing-chip">实际模型 ${escapeHtml(group.modelDisplay)}</span>` : ""}
                ${group.promptTokens ? `<span class="processing-chip">输入 ${group.promptTokens}</span>` : ""}
                ${group.completionTokens ? `<span class="processing-chip">输出 ${group.completionTokens}</span>` : ""}
                ${group.totalTokens ? `<span class="processing-chip">总计 ${group.totalTokens}</span>` : ""}
                ${tokenSourceLabel ? `<span class="processing-chip">${tokenSourceLabel}</span>` : ""}
            </div>
        </div>
    `;
}

function hideProcessingStatus() {
    // 待机态：保留面板可见，仅重置内容为闲置文案；折叠/展开跟随玩家此前的选择。
    const panel = document.getElementById("processing-status");
    if (!panel) return;
    panel.classList.remove("hidden");
    panel.classList.toggle("collapsed", processingStatusCollapsed);

    const badge = document.getElementById("processing-status-badge");
    if (badge) {
        badge.textContent = "待机";
        badge.className = "processing-status-badge";
    }
    const summary = document.getElementById("processing-status-summary");
    if (summary) {
        summary.textContent = "等待下一次行动...";
    }
    const steps = document.getElementById("processing-status-steps");
    if (steps) {
        steps.innerHTML = "";
    }
    const toggle = document.getElementById("processing-status-toggle");
    if (toggle) {
        toggle.classList.remove("hidden");
        toggle.setAttribute("aria-expanded", processingStatusCollapsed ? "false" : "true");
        toggle.title = processingStatusCollapsed ? "展开处理详情" : "收起处理详情";
    }
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

    const stageGroups = pickProcessingStageGroups(progress);
    const groups = stageGroups.map((groupDef) => {
        const summary = summarizeProcessingGroup(groupDef, progress);
        summary.total = stageGroups.length;
        return summary;
    });
    const status = progress.status || "running";

    // 折叠状态完全由玩家的点击控制；这里不再根据 running / error / completed
    // 自动覆盖玩家的选择，避免完成后自动折叠破坏"默认展开"的预期。
    // 仅显式 forceExpanded / forceCollapsed 选项可以由调用方主动改写状态。
    if (options.forceExpanded) {
        processingStatusCollapsed = false;
    } else if (options.forceCollapsed) {
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

    // 网络错误等没有单独步骤信息的场景：不再显示内嵌重试按钮，使用顶部栏重试按钮

    toggle.classList.remove("hidden");
    toggle.setAttribute("aria-expanded", processingStatusCollapsed ? "false" : "true");
    toggle.title = processingStatusCollapsed ? "展开处理详情" : "收起处理详情";

    panel.classList.remove("hidden");
    panel.classList.toggle("collapsed", processingStatusCollapsed);
}

function buildInitialProcessingState() {
    const merged = isMergeMode();
    const tailSteps = merged
        ? [
            { key: "story", status: "pending", duration_ms: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, token_source: null },
        ]
        : [
            { key: "rhythm", status: "pending", duration_ms: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, token_source: null },
            { key: "narrative", status: "pending", duration_ms: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, token_source: null },
        ];
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
            ...tailSteps,
        ],
    };
}

function setRetryState(retryFromHint, canRetry) {
    canRetryCurrentTurn = Boolean(canRetry);
    latestRetryFrom = canRetryCurrentTurn ? (retryFromHint || "rule") : null;
}

function clearRetryState() {
    setRetryState(null, false);
}

function applyPartialWorkflowResults(partialResults) {
    const pr = partialResults || {};
    if (pr.rule_plan || pr.rule_result || pr.hard_changes) {
        updateRulePanel(pr.rule_plan, pr.rule_result, pr.hard_changes);
    }
    if (pr.rhythm_result) {
        updateRhythmPanel(pr.rhythm_result);
    }
}

function restoreLastInputDraft() {
    const input = document.getElementById("chat-input");
    if (!input) return;

    if (lastPlayerInput || lastMoveDestination) {
        input.value = lastPlayerInput || "";
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 120) + "px";

        if (lastMoveDestination && currentMapData) {
            const reachable = new Set(currentMapData.reachable || []);
            if (reachable.has(lastMoveDestination)) {
                selectedDestination = lastMoveDestination;
                const loc = currentMapData.locations[lastMoveDestination];
                const locName = loc ? loc.display_name : lastMoveDestination;
                const indicator = document.getElementById("move-indicator");
                const indicatorText = document.getElementById("move-indicator-text");
                indicatorText.textContent = `即将移动到：${locName}`;
                indicator.classList.remove("hidden");
                renderMap(currentMapData);
            }
        }
    }
}

function removeTrailingRetryableMessage() {
    const chatMessages = document.getElementById("chat-messages");
    if (!chatMessages || !chatMessages.lastElementChild) return;

    const lastMsg = chatMessages.lastElementChild;
    const bubble = lastMsg.querySelector(".message-bubble");
    if (bubble && (
        bubble.textContent.startsWith("处理出错:") ||
        bubble.textContent === "网络错误，请重试。" ||
        lastMsg.querySelector(".loading-dots")
    )) {
        lastMsg.remove();
    }
}

function handleActionErrorResponse(data) {
    const errorTelemetry = data.telemetry || {
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
    };
    if (errorTelemetry.status !== "error") {
        errorTelemetry.status = "error";
    }
    renderProcessingStatus(errorTelemetry);
    applyPartialWorkflowResults(data.partial_results);
    setRetryState(data.retry_from_hint, data.can_retry);
    addMessage("assistant", "处理出错: " + data.error);
}

async function handleActionSuccessResponse(data) {
    clearRetryState();
    lastPlayerInput = "";
    lastMoveDestination = null;

    if (data.dice_rolls && data.dice_rolls.length > 0) {
        for (const diceRoll of data.dice_rolls) {
            await showDiceRollPanel(diceRoll, requestPushRoll);
        }
        await theatricalSleep(3000);
    }

    addMessage("assistant", data.narrative);

    const lastNarrMsg = document.getElementById("chat-messages").lastElementChild;
    processInlineMarkers(lastNarrMsg);

    if (data.theatrical_effects && data.theatrical_effects.length) {
        await processTheatricalEffects(data.theatrical_effects);
    }

    updateRulePanel(data.rule_plan, data.rule_result, data.hard_changes);
    updateRhythmPanel(data.rhythm_result);

    if (data.game_state) {
        updatePlayerStatus(data.game_state);
    }

    if (data.map_data) {
        currentMapData = data.map_data;
        renderMap(data.map_data);
    }

    renderProcessingStatus(data.telemetry || null, { forceCollapsed: true });
    handleEndingPhase(data.ending_phase, data.game_over, data.ending_id, data.ending_display);
}

async function fetchAndRenderActionProgress() {
    try {
        const resp = await fetch("/trpg/api/progress", { cache: "no-store" });
        const data = await resp.json();
        const progress = data.progress || {};
        applyPartialWorkflowResults(data.partial_results);
        if (progress.status === "error" || progress.status === "running") {
            setRetryState(data.retry_from_hint, data.can_retry);
        }
        updateRetryButtonVisibility();
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

async function pollActionResult(loadingEl) {
    const POLL_INTERVAL = 1500;
    const TIMEOUT_MS = 300000; // 5 minutes
    const started = Date.now();

    while (Date.now() - started < TIMEOUT_MS) {
        await new Promise(r => setTimeout(r, POLL_INTERVAL));
        try {
            const resp = await fetch("/trpg/api/action/result", { cache: "no-store" });
            if (resp.status === 202) continue;  // still pending
            const data = await resp.json();
            loadingEl.remove();
            return data;
        } catch (err) {
            console.debug("pollActionResult error:", err);
        }
    }

    // Timeout
    loadingEl.remove();
    await fetchAndRenderActionProgress().catch(() => clearRetryState());
    addMessage("assistant", "等待超时，请检查网络后重试。");
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
    // 不重置 processingStatusCollapsed：玩家手动设置的折叠状态需要跨轮保留。
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

// ─── 中断并重试 ───

async function retryCurrentTurnFromStage(retryFrom) {
    if (!retryFrom || isProcessing) return;

    isProcessing = true;
    setInputEnabled(false);
    updateRetryButtonVisibility();
    removeTrailingRetryableMessage();
    const loadingEl = addLoadingIndicator();
    renderProcessingStatus(buildInitialProcessingState(), { forceExpanded: true });
    startProgressPolling();

    try {
        const retryBody = { retry_from: retryFrom };
        const customApi = getCustomApiPayload();
        if (customApi) retryBody.custom_api = customApi;
        retryBody.merge_mode = isMergeMode();
        const resp = await fetch("/trpg/api/retry", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(retryBody),
        });

        if (!resp.ok && resp.status !== 202) {
            const data = await resp.json();
            loadingEl.remove();
            handleActionErrorResponse(data);
            return;
        }

        const data = await pollActionResult(loadingEl);
        if (data === null) return;

        if (data.error) {
            handleActionErrorResponse(data);
        } else {
            await handleActionSuccessResponse(data);
        }
    } catch (err) {
        loadingEl.remove();

        await fetchAndRenderActionProgress().catch(() => clearRetryState());
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
        });
        addMessage("assistant", "网络错误，请重试。");
        console.error("Retry failed:", err);
    } finally {
        stopProgressPolling();
        isProcessing = false;
        updateRetryButtonVisibility();
        if (endingPhase !== "concluded" || (_lastEndingDisplay && _lastEndingDisplay.allow_epilogue)) {
            setInputEnabled(true);
            document.getElementById("chat-input").focus();
        }
    }
}

async function abortAndRetry() {
    if (currentAbortController) {
        currentAbortController.abort();
        currentAbortController = null;
        clearRetryState();
        isProcessing = false;
        stopProgressPolling();
        removeTrailingRetryableMessage();
        restoreLastInputDraft();
        setInputEnabled(true);
        document.getElementById("chat-input").focus();
        renderProcessingStatus({ status: "error", message: "已中断，请重新发送", total_duration_ms: 0, summary: {}, steps: [] }, { forceCollapsed: true });
        updateRetryButtonVisibility();
        return;
    }

    if (canRetryCurrentTurn && latestRetryFrom) {
        await retryCurrentTurnFromStage(latestRetryFrom);
        return;
    }

    removeTrailingRetryableMessage();
    restoreLastInputDraft();
    setInputEnabled(true);
    document.getElementById("chat-input").focus();
    renderProcessingStatus({ status: "error", message: "请重新发送本轮行动", total_duration_ms: 0, summary: {}, steps: [] }, { forceCollapsed: true });
    updateRetryButtonVisibility();
}

function updateRetryButtonVisibility() {
    const btn = document.getElementById("btn-retry");
    if (!btn) return;
    const shouldShow = isProcessing || canRetryCurrentTurn || (lastPlayerInput || lastMoveDestination);
    btn.classList.toggle("hidden", !shouldShow);
    if (isProcessing) {
        btn.title = "中断当前处理";
    } else if (canRetryCurrentTurn && latestRetryFrom) {
        btn.title = `从${latestRetryFrom}层继续重试`;
    } else {
        btn.title = "恢复并重发本轮输入";
    }
}

window.abortAndRetry = abortAndRetry;

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

    // 保存本轮输入（用于重试恢复）
    lastPlayerInput = text;
    lastMoveDestination = moveTo;
    clearRetryState();

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
    updateRetryButtonVisibility();
    const loadingEl = addLoadingIndicator();
    renderProcessingStatus(buildInitialProcessingState(), { forceExpanded: true });
    startProgressPolling();

    // 创建 AbortController 以支持中断（用于取消初始 POST，后台任务不可中断）
    currentAbortController = new AbortController();

    try {
        const body = {};
        if (endingPhase === "triggered") {
            body.input = text || "[进入结局]";
            // Don't send move_to in ending phase
        } else {
            if (text) body.input = text;
            if (moveTo) body.move_to = moveTo;
        }
        const customApi = getCustomApiPayload();
        if (customApi) body.custom_api = customApi;
        body.merge_mode = isMergeMode();

        const resp = await fetch("/trpg/api/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: currentAbortController.signal,
        });

        if (!resp.ok && resp.status !== 202) {
            const data = await resp.json();
            loadingEl.remove();
            currentAbortController = null;
            handleActionErrorResponse(data);
            return;
        }

        // 202: 后台异步执行，轮询结果
        currentAbortController = null;
        const data = await pollActionResult(loadingEl);
        if (data === null) return;  // aborted or timeout handled inside pollActionResult

        if (data.error) {
            handleActionErrorResponse(data);
        } else {
            await handleActionSuccessResponse(data);
        }
    } catch (err) {
        loadingEl.remove();

        // 查询后端实际进度：若后端已执行到某步（如文案AI），则保留重试状态
        await fetchAndRenderActionProgress().catch(() => clearRetryState());
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
        });
        addMessage("assistant", "网络错误，请重试。");
        console.error("Action failed:", err);
    } finally {
        stopProgressPolling();
        isProcessing = false;
        updateRetryButtonVisibility();
        // Don't re-enable input if game is concluded (unless epilogue allowed)
        const _epilogueAllowed = _lastEndingDisplay && _lastEndingDisplay.allow_epilogue;
        if (endingPhase === "concluded" && !_epilogueAllowed) {
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
    const bubbleContent = role === "assistant"
        ? renderWhitelistedInlineHtml(content)
        : escapeHtml(content);
    msg.innerHTML = `
        <div class="message-avatar">
            <span class="mdi ${avatarIcon}"></span>
        </div>
        <div class="message-bubble">${bubbleContent}</div>
    `;
    container.appendChild(msg);
    scrollToBottom();
}

// ─── 骰子演出面板 ───

async function requestPushRoll(diceRoll) {
    const resp = await fetch("/trpg/api/push_roll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dice_roll: diceRoll }),
    });
    const data = await resp.json();
    if (!resp.ok || data.error) {
        throw new Error(data.error || "孤注一掷失败");
    }
    return data.dice_roll;
}

async function showDiceRollPanel(diceRoll, onPushRequest = null) {
    return new Promise((resolve) => {
        const container = document.getElementById("chat-messages");
        const panel = document.createElement("div");
        panel.className = "dice-roll-panel";

        const isSancheck = diceRoll.type === "sancheck";
        panel.classList.add(isSancheck ? "dice-roll--sancheck" : "dice-roll--skill");

        panel.innerHTML = `
            <div class="dice-roll-header">${escapeHtml(diceRoll.label)}</div>
            <div class="dice-roll-body">
                <div class="dice-roll-threshold">目标值: \u2264 ${diceRoll.threshold}</div>
                <div class="dice-roll-number">--</div>
                <button class="dice-roll-btn">投掷</button>
            </div>
        `;
        container.appendChild(panel);
        scrollToBottom();

        const numberEl = panel.querySelector(".dice-roll-number");
        const btnEl = panel.querySelector(".dice-roll-btn");
        const bodyEl = panel.querySelector(".dice-roll-body");

        // 闪烁动画：数字在 1-100 之间快速变化
        let flickerInterval = setInterval(() => {
            numberEl.textContent = Math.floor(Math.random() * 100) + 1;
        }, 80);

        btnEl.addEventListener("click", async () => {
            clearInterval(flickerInterval);
            numberEl.textContent = diceRoll.roll === null || diceRoll.roll === undefined ? "--" : diceRoll.roll;
            btnEl.disabled = true;
            btnEl.textContent = "已投掷";

            // 显示结果
            const resultEl = document.createElement("div");
            resultEl.className = "dice-roll-result " + (diceRoll.success ? "dice-success" : "dice-failure");

            let resultText = diceRoll.success ? "成功" : "失败";
            if (diceRoll.critical_success) resultText = "大成功！";
            if (diceRoll.critical_failure) resultText = "大失败！";
            if (isSancheck && diceRoll.san_loss) {
                resultText += ` (SAN ${diceRoll.san_loss})`;
            }
            resultEl.textContent = resultText;
            bodyEl.appendChild(resultEl);
            scrollToBottom();

            if (!diceRoll.success && diceRoll.pushable && typeof onPushRequest === "function") {
                const pushActions = document.createElement("div");
                pushActions.className = "dice-push-actions";
                pushActions.innerHTML = `
                    <div class="dice-push-warning">可以孤注一掷，但再次失败会带来额外代价。</div>
                    <button class="dice-push-btn">孤注一掷</button>
                    <button class="dice-push-skip-btn">放弃</button>
                `;
                bodyEl.appendChild(pushActions);
                scrollToBottom();

                const pushBtn = pushActions.querySelector(".dice-push-btn");
                const skipBtn = pushActions.querySelector(".dice-push-skip-btn");

                skipBtn.addEventListener("click", () => {
                    pushBtn.disabled = true;
                    skipBtn.disabled = true;
                    resolve();
                }, { once: true });

                pushBtn.addEventListener("click", async () => {
                    pushBtn.disabled = true;
                    skipBtn.disabled = true;
                    pushBtn.textContent = "重骰中...";
                    try {
                        const pushedRoll = await onPushRequest(diceRoll);
                        pushActions.remove();
                        await showDiceRollPanel(pushedRoll, onPushRequest);
                    } catch (err) {
                        const errorEl = document.createElement("div");
                        errorEl.className = "dice-push-error";
                        errorEl.textContent = err.message || "孤注一掷失败";
                        pushActions.appendChild(errorEl);
                    }
                    resolve();
                }, { once: true });
                return;
            }

            resolve();
        }, { once: true });
    });
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

    // SAN — 优先读 player.san_max（自定义卡注入），缺省 fallback 99
    const san = Number(player.san) || 0;
    const sanMax = Number(player.san_max) || 99;
    const sanPct = Math.max(0, Math.min(100, (san / Math.max(1, sanMax)) * 100));
    document.getElementById("san-bar").style.width = sanPct + "%";
    document.getElementById("san-value").textContent = `${san}/${sanMax}`;

    // HP — 优先读 player.hp_max，缺省 12
    const hp = Number(player.hp) || 0;
    const hpMax = Number(player.hp_max) || 12;
    const hpPct = Math.max(0, Math.min(100, (hp / Math.max(1, hpMax)) * 100));
    document.getElementById("hp-bar").style.width = hpPct + "%";
    document.getElementById("hp-value").textContent = `${hp}/${hpMax}`;

    // MP — 来自卡的 mp_max；fallback 时显示 0/0
    const mp = Number(player.mp) || 0;
    const mpMax = Number(player.mp_max) || 0;
    const mpBarEl = document.getElementById("mp-bar");
    const mpValueEl = document.getElementById("mp-value");
    if (mpBarEl && mpValueEl) {
        const mpPct = Math.max(0, Math.min(100, (mp / Math.max(1, mpMax || 1)) * 100));
        mpBarEl.style.width = (mpMax > 0 ? mpPct : 0) + "%";
        mpValueEl.textContent = `${mp}/${mpMax}`;
    }

    // 元数据：姓名、职业、幸运
    const luckEl = document.getElementById("player-luck-value");
    if (luckEl) luckEl.textContent = String(Number(player.luck) || 0);

    const nameRow = document.getElementById("player-name-row");
    const nameVal = document.getElementById("player-name-value");
    if (nameRow && nameVal) {
        const nm = player.name || "";
        if (nm && nm !== "调查员") {
            nameRow.classList.remove("hidden");
            nameVal.textContent = nm;
        } else {
            nameRow.classList.add("hidden");
        }
    }
    const profRow = document.getElementById("player-profession-row");
    const profVal = document.getElementById("player-profession-value");
    if (profRow && profVal) {
        // 优先读后端提供的中文 profession_name; 否则退回英文 key
        const profDisplay = (player.profession_name || player.profession || "").trim();
        if (profDisplay) {
            profRow.classList.remove("hidden");
            profVal.textContent = profDisplay;
        } else {
            profRow.classList.add("hidden");
        }
    }

    // 8 属性 + LUCK 网格（仅在卡注入时显示）
    const attrsGrid = document.getElementById("player-attrs-grid");
    if (attrsGrid) {
        const attrs = player.attributes || {};
        // COC7 八属性中文名 (英文是行业通用 key 故双语呈现)
        const ATTR_LABEL_ZH = {
            STR: "力量", CON: "体质", SIZ: "体型",
            DEX: "敏捷", APP: "外貌", INT: "智力",
            POW: "意志", EDU: "教育"
        };
        const order = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU"];
        const present = order.filter(k => attrs[k] != null);
        if (present.length > 0) {
            attrsGrid.classList.remove("hidden");
            attrsGrid.innerHTML = "";
            for (const k of order) {
                if (attrs[k] == null) continue;
                const cell = document.createElement("div");
                cell.className = "player-attr-cell";
                const lbl = document.createElement("span");
                lbl.className = "player-attr-label";
                lbl.textContent = `${ATTR_LABEL_ZH[k] || k}(${k})`;
                const val = document.createElement("span");
                val.className = "player-attr-value";
                val.textContent = String(attrs[k]);
                cell.appendChild(lbl);
                cell.appendChild(val);
                attrsGrid.appendChild(cell);
            }
        } else {
            attrsGrid.classList.add("hidden");
            attrsGrid.innerHTML = "";
        }
    }

    // 调查员生平：性别 / 居住地 / 出生地（紧凑显示在属性区块底部）
    const lifeRow = document.getElementById("player-life-row");
    if (lifeRow) {
        const sex = (player.sex || "").trim();
        const residence = (player.residence || "").trim();
        const birthplace = (player.birthplace || "").trim();
        const parts = [];
        if (sex) parts.push(`性别 ${sex}`);
        if (residence) parts.push(`现居 ${residence}`);
        if (birthplace) parts.push(`生于 ${birthplace}`);
        if (parts.length > 0) {
            lifeRow.classList.remove("hidden");
            lifeRow.textContent = parts.join("　·　");
        } else {
            lifeRow.classList.add("hidden");
            lifeRow.textContent = "";
        }
    }

    // 双点数池 used/total（仅在卡注入且 occupation_total > 0 时显示）
    const poolsRow = document.getElementById("player-pools-row");
    if (poolsRow) {
        const sp = player.skill_pools || {};
        const occT = Number(sp.occupation_total) || 0;
        const occU = Number(sp.occupation_used) || 0;
        const intT = Number(sp.interest_total) || 0;
        const intU = Number(sp.interest_used) || 0;
        if (occT > 0 || intT > 0) {
            poolsRow.classList.remove("hidden");
            poolsRow.innerHTML = "";
            const occBadge = document.createElement("span");
            occBadge.className = "pool-badge" + (occU < occT ? " pool-badge--unfilled" : "");
            occBadge.textContent = `职业点 ${occU}/${occT}`;
            const intBadge = document.createElement("span");
            intBadge.className = "pool-badge" + (intU < intT ? " pool-badge--unfilled" : "");
            intBadge.textContent = `兴趣点 ${intU}/${intT}`;
            poolsRow.appendChild(occBadge);
            poolsRow.appendChild(intBadge);
        } else {
            poolsRow.classList.add("hidden");
            poolsRow.innerHTML = "";
        }
    }

    // Skills — 改为按数值降序的 chip 排版（更易读，分高低）
    const skillsEl = document.getElementById("player-skills");
    const skills = player.skills || {};
    const skillEntries = Object.entries(skills)
        .filter(([_, v]) => typeof v === "number")
        .sort((a, b) => b[1] - a[1]);
    if (skillEntries.length > 0) {
        skillsEl.innerHTML = skillEntries.map(([name, value]) => {
            const v = Number(value) || 0;
            // 高数值高亮：≥60 强、≥40 中、其它弱
            let cls = "skill-chip";
            if (v >= 60) cls += " skill-chip--high";
            else if (v >= 40) cls += " skill-chip--mid";
            return `<span class="${cls}"><span class="skill-chip-name">${escapeHtml(name)}</span><span class="skill-chip-value">${v}</span></span>`;
        }).join("");
    } else {
        skillsEl.innerHTML = `<span class="placeholder-text">暂无技能</span>`;
    }

    // 调查员背景（COC7 6 项；任一非空则显示 section）
    const bgSection = document.getElementById("background-section");
    const bgEl = document.getElementById("player-background");
    if (bgSection && bgEl) {
        const bg = player.background || {};
        const bgLabels = {
            personal_description: "个人描述",
            ideology_beliefs: "思想/信念",
            significant_people: "重要之人",
            meaningful_locations: "意义非凡之地",
            treasured_possessions: "宝贵之物",
            traits: "特质"
        };
        const order = ["personal_description", "ideology_beliefs", "significant_people",
                       "meaningful_locations", "treasured_possessions", "traits"];
        const nonEmpty = order.filter(k => (bg[k] || "").trim().length > 0);
        if (nonEmpty.length > 0) {
            bgSection.classList.remove("hidden");
            bgEl.innerHTML = "";
            for (const k of order) {
                const text = (bg[k] || "").trim();
                if (!text) continue;
                const item = document.createElement("div");
                item.className = "player-bg-item";
                const label = document.createElement("div");
                label.className = "player-bg-label";
                label.textContent = bgLabels[k];
                const val = document.createElement("div");
                val.className = "player-bg-value";
                val.textContent = text;
                item.appendChild(label);
                item.appendChild(val);
                bgEl.appendChild(item);
            }
        } else {
            bgSection.classList.add("hidden");
            bgEl.innerHTML = `<span class="placeholder-text">未配置背景</span>`;
        }
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
    const otherSide = side === "left" ? "right" : "left";
    const otherPanel = document.getElementById(otherSide + "-panel");
    const otherExpandBtn = document.getElementById(otherSide + "-expand");

    if (panel.classList.contains("collapsed")) {
        panel.classList.remove("collapsed");
        expandBtn.classList.add("hidden");
        if (isMobileLayout() && otherPanel && !otherPanel.classList.contains("collapsed")) {
            otherPanel.classList.add("collapsed");
            if (otherExpandBtn) {
                otherExpandBtn.classList.remove("hidden");
            }
        }
    } else {
        panel.classList.add("collapsed");
        expandBtn.classList.remove("hidden");
    }
    updateMobilePanelBackdrop();
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
        lastPlayerInput = "";
        lastMoveDestination = null;
        clearRetryState();
        if (currentAbortController) {
            currentAbortController.abort();
            currentAbortController = null;
        }
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
        // 新地图: 重新启用 + 清画布
        if (window.MapView && window.MapView.isEnabled() && window.MapView._getCy()) {
            window.MapView.enable();
            window.MapView.clear();
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
        currentSaveSummary = null;
        closeMobilePanels();
        clearProcessingStatus();
        updateRetryButtonVisibility();
        initializeModuleSelection();
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

function formatSaveTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }
    return date.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function renderWhitelistedInlineHtml(text) {
    const escaped = escapeHtml(text);
    return escaped.replace(
        /&lt;(\/?)(b|strong|i|em|s|del)&gt;/gi,
        (_, closingSlash, tagName) => `<${closingSlash}${String(tagName || "").toLowerCase()}>`
    );
}

// ─── 演出效果系统 ───

function theatricalSleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// 将内联标记 (%%GLITCH:n%% / %%ECHO:n%%) 转换为可动画的 span
function processInlineMarkers(msgElement) {
    const bubble = msgElement.querySelector(".message-bubble");
    if (!bubble) return;
    let html = bubble.innerHTML;
    html = html.replace(/%%GLITCH:(\d+)%%([\s\S]*?)%%\/GLITCH%%/g,
        '<span class="glitch-inline" data-inline-id="$1">$2</span>');
    html = html.replace(/%%ECHO:(\d+)%%([\s\S]*?)%%\/ECHO%%/g,
        '<span class="echo-inline" data-inline-id="$1">$2</span>');
    bubble.innerHTML = html;
}

async function processTheatricalEffects(effects) {
    if (!effects || !effects.length) return;
    let prevType = null;
    let i = 0;
    while (i < effects.length) {
        const effect = effects[i];

        // 连续 map_corrupt 批量执行：收集后一次性闪烁+替换
        if (effect.type === "map_corrupt") {
            const batch = [];
            while (i < effects.length && effects[i].type === "map_corrupt") {
                batch.push(effects[i]);
                i++;
            }
            const delay = getTheatricalDelay(prevType, batch[0]);
            await theatricalSleep(delay);
            await effectMapCorruptBatch(batch);
            prevType = "map_corrupt";
            continue;
        }

        // 连续 paragraph 之间只等 500ms，其他情况等 800ms
        const delay = getTheatricalDelay(prevType, effect);
        await theatricalSleep(delay);
        switch (effect.type) {
            case "paragraph":
                await effectParagraph(effect.content);
                break;
            case "system_echo":
                await effectSystemEcho(effect.content);
                break;
            case "inject_input":
                await effectInjectInput(effect.content);
                break;
            case "glitch":
                await effectGlitch(effect.inline_id, effect.content);
                break;
            case "echo_text":
                await effectEchoText(effect.inline_id, effect.phases);
                break;
        }
        prevType = effect.type;
        i++;
    }
}

// 1. paragraph — 额外独立消息
function getTheatricalDelay(prevType, effect) {
    if (Number.isFinite(effect?.delay_ms) && effect.delay_ms >= 0) {
        return effect.delay_ms;
    }
    return (prevType === "paragraph" && effect?.type === "paragraph") ? 500 : 800;
}

// 1. paragraph 鈥?棰濆鐙珛娑堟伅
async function effectParagraph(content) {
    if (!content) return;
    addMessage("assistant", content);
    // 处理段落内嵌套的内联标记 (glitch/echo-text → span)
    const lastMsg = document.getElementById("chat-messages").lastElementChild;
    processInlineMarkers(lastMsg);
    await theatricalSleep(100);
}

// 2. system-echo — 伪系统消息 (红色)
async function effectSystemEcho(content) {
    if (!content) return;
    const container = document.getElementById("chat-messages");
    const msg = document.createElement("div");
    msg.className = "message system-echo";
    msg.innerHTML = `<div class="system-echo-bubble">${renderWhitelistedInlineHtml(content)}</div>`;
    container.appendChild(msg);
    scrollToBottom();
    await theatricalSleep(300);
}

// 3. inject-input — 幽灵打字
async function effectInjectInput(content) {
    if (!content) return;
    const input = document.getElementById("chat-input");
    for (let i = 0; i < content.length; i++) {
        input.value += content[i];
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 120) + "px";
        await theatricalSleep(50 + Math.random() * 80);
    }
}

// 4. glitch — 原文内联乱码闪烁
async function effectGlitch(inlineId, content) {
    const span = document.querySelector(`.glitch-inline[data-inline-id="${inlineId}"]`);
    if (!span || !content) return;

    span.classList.add("glitching");
    const glitchChars = "█▓▒░╫╬╪╩╦╠╣╚╗╔║═─│┤┐└┘┌├";
    const cycles = 6;
    for (let c = 0; c < cycles; c++) {
        let corrupted = "";
        for (let i = 0; i < content.length; i++) {
            if (Math.random() < 0.3) {
                corrupted += glitchChars[Math.floor(Math.random() * glitchChars.length)];
            } else {
                corrupted += content[i];
            }
        }
        span.textContent = corrupted;
        await theatricalSleep(100 + Math.random() * 100);
    }
    span.textContent = content;
    span.classList.remove("glitching");
    await theatricalSleep(200);
}

// 5. echo-text — 原文内联渐进展示
async function effectEchoText(inlineId, phases) {
    const span = document.querySelector(`.echo-inline[data-inline-id="${inlineId}"]`);
    if (!span || !phases || !phases.length) return;

    for (let i = 0; i < phases.length; i++) {
        span.classList.add("echo-fading");
        await theatricalSleep(250);
        span.textContent = phases[i];
        span.classList.remove("echo-fading");
        if (i < phases.length - 1) {
            await theatricalSleep(1500);
        }
    }
    await theatricalSleep(300);
}

// 6. map-corrupt — 地图节点批量污染（一次闪烁，全部替换）
async function effectMapCorruptBatch(batch) {
    if (!currentMapData || !currentMapData.locations) return;
    // 新地图: 闪烁容器 + 改名后 MapView.render 重画
    if (window.MapView && window.MapView.isEnabled() && window.MapView._getCy()) {
        window.MapView.flashCorrupt();
        await theatricalSleep(350);
        for (const effect of batch) {
            if (currentMapData.locations[effect.target]) {
                currentMapData.locations[effect.target].display_name = effect.content;
            }
        }
        renderMap(currentMapData);
        await theatricalSleep(300);
        return;
    }
    // 旧地图: 闪 #map-svg
    const svg = document.getElementById("map-svg");
    if (svg) {
        svg.classList.add("map-corrupt-flash");
        await theatricalSleep(350);
        svg.classList.remove("map-corrupt-flash");
    }
    for (const effect of batch) {
        if (currentMapData.locations[effect.target]) {
            currentMapData.locations[effect.target].display_name = effect.content;
        }
    }
    renderMap(currentMapData);
    await theatricalSleep(300);
}

// ─── 地图渲染与交互 ───

function renderMap(mapData) {
    // v3.1.0: feature flag 接入 Cytoscape.js 新地图; 失败/未启用回退到下方旧 SVG 逻辑
    if (window.MapView && window.MapView.isEnabled()) {
        try {
            if (!window.MapView._getCy()) {
                window.MapView.init();
                // 把 cytoscape tap 事件桥接到现有的 onMapNodeClick (空白处 tap 会传 null)
                window.MapView.setOnNodeTap((key) => {
                    if (key) onMapNodeClick(key);
                });
            }
            if (window.MapView.render(mapData)) {
                renderSceneNpcs(mapData);
                return;
            }
        } catch (e) {
            console.error("[MapView] render failed, fall back to legacy:", e);
        }
    }
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
        const npcLocations = new Set(Array.isArray(mapData.npc_locations) ? mapData.npc_locations : []);

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
        const gapY = 32;
        const labelW = 28;
        const padX = 8;
        const padY = 10;
        const ns = "http://www.w3.org/2000/svg";

        // 每层内布局：
        // 1. 常规房间先按既有拓扑粗排
        // 2. 带可见微场景的父节点整体推到本层右侧
        // 3. 微场景节点固定挂在父节点右边，避免挤乱原始地图结构
        const nodePositions = {};
        let yOffset = padY;

        for (const floor of floors) {
            const group = floorGroups[floor];
            if (!group || group.length === 0) continue;

            const primaryNodes = group.filter((key) => !locations[key]?.is_micro_scene);
            const microNodes = group.filter((key) => Boolean(locations[key]?.is_micro_scene));
            if (primaryNodes.length === 0) continue;

            const microChildrenByParent = new Map();
            for (const microKey of microNodes) {
                const parentKey = String(locations[microKey]?.parent_location || "").trim();
                if (!parentKey || !primaryNodes.includes(parentKey)) continue;
                if (!microChildrenByParent.has(parentKey)) {
                    microChildrenByParent.set(parentKey, []);
                }
                microChildrenByParent.get(parentKey).push(microKey);
            }

            const parentsWithMicroScenes = new Set(microChildrenByParent.keys());

            // 找连接数最多的节点作为hub
            let hubKey = primaryNodes[0];
            let maxConn = 0;
            for (const key of primaryNodes) {
                const conn = (adj[key] || []).filter((neighbor) => !locations[neighbor]?.is_micro_scene).length;
                if (conn > maxConn) {
                    maxConn = conn;
                    hubKey = key;
                }
            }

            // 排列：hub居中，其他按连接关系左右交替
            const ordered = [hubKey];
            const remaining = primaryNodes.filter(k => k !== hubKey);
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

            // 带微场景的父节点在本层优先排到右边，给右侧扩展留槽位。
            const regularNodes = ordered.filter((key) => !parentsWithMicroScenes.has(key));
            const expandableParents = ordered.filter((key) => parentsWithMicroScenes.has(key));
            const orderedPrimary = [...regularNodes, ...expandableParents];

            let cursorX = labelW + padX;
            for (const key of orderedPrimary) {
                nodePositions[key] = {
                    x: cursorX,
                    y: yOffset
                };
                cursorX += nodeW + gapX;

                const childNodes = microChildrenByParent.get(key) || [];
                for (const childKey of childNodes) {
                    nodePositions[childKey] = {
                        x: cursorX,
                        y: yOffset
                    };
                    cursorX += nodeW + gapX;
                }
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
            const firstKey = group.find((key) => nodePositions[key]) || group[0];
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
            const isNpc = npcLocations.has(key);

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
            if (isNpc) {
                nodeClass += " map-node--npc";
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

        // 计算 fit-contain：SVG 默认缩放到既不超过容器宽也不超过容器高 (取小者)
        // 让玩家进场就能一眼看到全部地图节点而无需滚动条；玩家可用 +/- 主动放大缩小
        const container = document.getElementById("game-map");
        if (container) {
            const cw = Math.max(0, container.clientWidth - 16);
            const ch = Math.max(0, container.clientHeight - 16);
            if (cw > 0 && ch > 0 && svgW > 0 && svgH > 0) {
                const fitScale = Math.min(cw / svgW, ch / svgH);
                // 不主动放大超过 1:1；同时不允许低于 0.55 防止地图被压缩到看不清
                window._mapBaseScale = Math.max(0.55, Math.min(fitScale, 1));
            } else {
                window._mapBaseScale = 1;
            }
        } else {
            window._mapBaseScale = 1;
        }
        if (typeof window._mapZoom !== "number") window._mapZoom = 1.0;
        applyMapZoom(window._mapZoom);
        attachMapPanHandlers();

        console.log(`[Map] Rendered ${keys.length} nodes, ${edges.length} edges`);
    } catch (err) {
        console.error("[Map] Render failed:", err, mapData);
    }

    renderSceneNpcs(mapData);
}

function renderSceneNpcs(mapData) {
    const container = document.getElementById("scene-npc-list");
    if (!container) return;

    const npcs = (mapData && Array.isArray(mapData.current_scene_npcs))
        ? mapData.current_scene_npcs
        : [];

    if (npcs.length === 0) {
        container.innerHTML = `<p class="placeholder-text">当前场景暂无可见的NPC</p>`;
        return;
    }

    const cards = npcs.map((npc) => {
        const cardClasses = ["scene-npc-card"];
        if (npc.is_threat) cardClasses.push("scene-npc-card--threat");
        if (npc.presence === "cross_wall") cardClasses.push("scene-npc-card--cross-wall");
        if (!npc.name_revealed) cardClasses.push("scene-npc-card--unrevealed");

        const tags = [];
        if (npc.is_threat) {
            tags.push(`<span class="scene-npc-card-tag scene-npc-card-tag--threat">威胁</span>`);
        }
        if (npc.presence === "cross_wall") {
            tags.push(`<span class="scene-npc-card-tag scene-npc-card-tag--cross-wall">隔墙交流</span>`);
        } else {
            tags.push(`<span class="scene-npc-card-tag">同场景</span>`);
        }

        const displayName = escapeHtml(String(npc.display_name || "陌生身影"));
        const fromHtml = (npc.presence === "cross_wall" && npc.from_room)
            ? `<div class="scene-npc-card-from">来自：${escapeHtml(String(npc.from_room))}</div>`
            : "";

        return `
            <div class="${cardClasses.join(" ")}">
                <div class="scene-npc-card-row">
                    <span class="scene-npc-card-name">${displayName}</span>
                    ${tags.join("")}
                </div>
                ${fromHtml}
            </div>
        `;
    });

    container.innerHTML = cards.join("");
}

function onMapNodeClick(locationKey) {
    if (!currentMapData) return;
    const reachable = new Set(currentMapData.reachable || []);

    // 点击当前位置 → 取消选择
    if (locationKey === currentMapData.current_location) {
        cancelMoveSelection();
        return;
    }

    // 点击不可达 → W4: 给玩家解释原因, 不再静默.
    // v3.2.1: 新地图 (Cytoscape) 已经在 map_view.js::_bindInteractions 的 tap 监听里源头拦截了
    // 不可达节点的选中流程 (改成显示 tooltip), 这里这一段实际只服务旧 SVG fallback. 但保留它
    // 作为 SVG 模式下的"看清不可达原因"提示, 不重复给新地图弹 move-indicator.
    if (!reachable.has(locationKey)) {
        const loc = currentMapData.locations[locationKey] || {};
        const reasonMap = {
            locked_door: "门锁着，先想办法解锁",
            pursuer_lock: "被追逐者激活，只能逐格移动到相邻场景",
            blocked: "暂时无法直接到达",
            needs_path: "需先到达中间的房间",
        };
        const reason = reasonMap[loc.unreachable_reason] || (loc.visited ? "暂时无法直接到达" : "尚未发现完整路径");
        const locName = loc.display_name || locationKey;
        const indicator = document.getElementById("move-indicator");
        const indicatorText = document.getElementById("move-indicator-text");
        indicatorText.textContent = `${locName}：${reason}`;
        indicator.classList.remove("hidden");
        // 2.5s 后自动消失 (不影响选中流程)
        clearTimeout(window._mapReasonTimer);
        window._mapReasonTimer = setTimeout(() => {
            if (!selectedDestination) {
                indicator.classList.add("hidden");
            }
        }, 2500);
        return;
    }

    // 设为选中目标
    selectedDestination = locationKey;
    clearTimeout(window._mapReasonTimer);

    // 更新移动提示 (W4: 文案明确"待发送")
    const loc = currentMapData.locations[locationKey];
    const locName = loc ? loc.display_name : locationKey;
    const indicator = document.getElementById("move-indicator");
    const indicatorText = document.getElementById("move-indicator-text");
    indicatorText.textContent = `已选：${locName} · 在聊天框输入任意行动并发送即可移动`;
    indicator.classList.remove("hidden");

    // 新地图: setSelectedTarget 会触发路径高亮; 旧地图: 重渲染
    if (window.MapView && window.MapView.isEnabled() && window.MapView._getCy()) {
        window.MapView.setSelectedTarget(locationKey);
    } else {
        renderMap(currentMapData);
    }
}

function cancelMoveSelection() {
    selectedDestination = null;
    const indicator = document.getElementById("move-indicator");
    indicator.classList.add("hidden");

    // 新地图: 清选中 (会自动清路径); 旧地图: 重渲染
    if (window.MapView && window.MapView.isEnabled() && window.MapView._getCy()) {
        window.MapView.setSelectedTarget(null);
    } else if (currentMapData) {
        renderMap(currentMapData);
    }
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

let _lastEndingDisplay = {};

function handleEndingPhase(phase, gameOver, endingId, endingDisplay) {
    endingPhase = phase || null;
    if (endingDisplay) _lastEndingDisplay = endingDisplay;

    if (phase === "triggered") {
        showEndingIndicator(endingId);
        disableMapInteraction();
    } else if (phase === "concluded" || gameOver) {
        hideEndingIndicator();
        const allowEpilogue = endingDisplay && endingDisplay.allow_epilogue;
        if (allowEpilogue) {
            // 允许后日谈：禁用地图但保留聊天输入
            disableMapInteraction();
            cancelMoveSelection();
            const input = document.getElementById("chat-input");
            input.placeholder = "后日谈：继续输入，回味这段旅程...";
        } else {
            disableAllGameInput();
        }
        showEndingOverlay(endingId);
    }
}

function showEndingIndicator(endingId) {
    const indicator = document.getElementById("ending-indicator");
    const indicatorText = document.getElementById("ending-indicator-text");
    const endingName = (_lastEndingDisplay && _lastEndingDisplay.display_name) || ENDING_NAMES[endingId] || "结局";
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
    // 新地图分支
    if (window.MapView && window.MapView.isEnabled() && window.MapView._getCy()) {
        window.MapView.disable();
        return;
    }
    const svg = document.getElementById("map-svg");
    if (svg) {
        svg.style.pointerEvents = "none";
        svg.style.opacity = "0.5";
    }
}

// ─── 地图缩放控件 ───
function applyMapZoom(zoom) {
    const svg = document.getElementById("map-svg");
    if (!svg || !svg.viewBox || !svg.viewBox.baseVal) return;
    const base = window._mapBaseScale || 1;
    const total = Math.max(0.1, base * zoom);
    const w = parseFloat(svg.viewBox.baseVal.width) || 200;
    const h = parseFloat(svg.viewBox.baseVal.height) || 120;
    svg.style.width = (w * total) + "px";
    svg.style.height = (h * total) + "px";
    window._mapZoom = zoom;
}

function mapZoomIn() {
    if (window.MapView && window.MapView.isEnabled() && window.MapView._getCy()) {
        return window.MapView.zoomIn();
    }
    const next = Math.min((window._mapZoom || 1) * 1.2, 3);
    applyMapZoom(next);
}

function mapZoomOut() {
    if (window.MapView && window.MapView.isEnabled() && window.MapView._getCy()) {
        return window.MapView.zoomOut();
    }
    const next = Math.max((window._mapZoom || 1) / 1.2, 0.4);
    applyMapZoom(next);
}

function mapZoomReset() {
    if (window.MapView && window.MapView.isEnabled() && window.MapView._getCy()) {
        return window.MapView.zoomReset();
    }
    applyMapZoom(1);
}

// ─── 地图拖拽（按住空白处拖动 .map-container scrollLeft/Top） ───
let _mapPanState = null;
let _mapPanInstalled = false;
let _mapJustDragged = false;

function attachMapPanHandlers() {
    if (_mapPanInstalled) return;
    const container = document.getElementById("game-map");
    if (!container) return;
    _mapPanInstalled = true;

    container.addEventListener("mousedown", (e) => {
        // 左键 + 起点不在节点交互元素上时才进入 pan 模式
        if (e.button !== 0) return;
        // 起点在节点(rect/text/g)上时让节点点击优先处理
        const path = e.composedPath ? e.composedPath() : [];
        const onNode = path.some(el =>
            el.classList && (el.classList.contains("map-node") || el.tagName === "g"
                && el.parentNode && el.parentNode.id === "map-svg" && el.classList.contains("map-node"))
        );
        if (onNode) return;
        _mapPanState = {
            startX: e.clientX,
            startY: e.clientY,
            scrollLeft: container.scrollLeft,
            scrollTop: container.scrollTop,
            moved: false,
        };
        container.classList.add("map-panning");
        e.preventDefault();
    });

    container.addEventListener("mousemove", (e) => {
        if (!_mapPanState) return;
        const dx = e.clientX - _mapPanState.startX;
        const dy = e.clientY - _mapPanState.startY;
        if (Math.abs(dx) + Math.abs(dy) > 4) _mapPanState.moved = true;
        container.scrollLeft = _mapPanState.scrollLeft - dx;
        container.scrollTop = _mapPanState.scrollTop - dy;
    });

    const endPan = () => {
        if (!_mapPanState) return;
        _mapJustDragged = _mapPanState.moved;
        _mapPanState = null;
        container.classList.remove("map-panning");
        if (_mapJustDragged) {
            // 吞掉本次 mouseup 后冒泡到 svg/node 的 click
            setTimeout(() => { _mapJustDragged = false; }, 80);
        }
    };
    container.addEventListener("mouseup", endPan);
    container.addEventListener("mouseleave", endPan);

    // 拖动后立刻发生的 click 吞掉，防止误触发节点跳转
    container.addEventListener("click", (e) => {
        if (_mapJustDragged) {
            e.stopPropagation();
            e.preventDefault();
            _mapJustDragged = false;
        }
    }, true);
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
    const endingName = (_lastEndingDisplay && _lastEndingDisplay.display_name) || ENDING_NAMES[endingId] || "结局";
    const overlayText = (_lastEndingDisplay && _lastEndingDisplay.overlay_text) || "你的冒险到此结束了。";

    title.textContent = endingName;
    desc.textContent = overlayText;
    overlay.classList.remove("hidden");
}

function dismissEndingOverlay() {
    document.getElementById("ending-overlay").classList.add("hidden");
}

// ─── 自定义API配置弹窗 ───

const API_CONFIG_LS_KEY = "aitrpg_custom_api";
const API_LAYERS = ["rule", "rhythm", "narrative"];

// 当前编辑中的草稿（未保存），结构: { unified: bool, rule:{...}, rhythm:{...}, narrative:{...} }
let _apiConfigDraft = null;
let _apiCurrentTab = "rule";

function _loadApiConfigFromStorage() {
    try {
        const raw = localStorage.getItem(API_CONFIG_LS_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch { return null; }
}

function _saveApiConfigToStorage(cfg) {
    localStorage.setItem(API_CONFIG_LS_KEY, JSON.stringify(cfg));
}

function getCustomApiPayload() {
    const cfg = _loadApiConfigFromStorage();
    if (!cfg) return null;
    const isValid = (o) => o && o.base_url && o.api_key && o.model;
    if (cfg.unified) {
        const src = cfg.unified_data;
        if (!isValid(src)) return null;
        return { rule: src, rhythm: src, narrative: src };
    }
    const out = {};
    for (const layer of API_LAYERS) {
        if (isValid(cfg[layer])) out[layer] = cfg[layer];
    }
    return Object.keys(out).length ? out : null;
}

function _updateApiButtonState() {
    const btn = document.getElementById("btn-custom-api");
    if (!btn) return;
    const payload = getCustomApiPayload();
    btn.classList.toggle("api-active", !!payload);
}

function openApiConfigModal() {
    const stored = _loadApiConfigFromStorage() || { unified: false };
    _apiConfigDraft = JSON.parse(JSON.stringify(stored));
    if (!_apiConfigDraft.unified_data) _apiConfigDraft.unified_data = { base_url: "", api_key: "", model: "" };
    for (const layer of API_LAYERS) {
        if (!_apiConfigDraft[layer]) _apiConfigDraft[layer] = { base_url: "", api_key: "", model: "" };
    }
    _apiCurrentTab = "rule";
    _renderApiModal();
    document.getElementById("api-config-modal").classList.remove("hidden");
}

function closeApiConfigModal() {
    document.getElementById("api-config-modal").classList.add("hidden");
    _apiConfigDraft = null;
}

function _renderApiModal() {
    const unified = !!_apiConfigDraft.unified;
    document.getElementById("api-unified-toggle").checked = unified;
    document.getElementById("api-tabs-row").style.display = unified ? "none" : "";

    const src = unified ? _apiConfigDraft.unified_data : _apiConfigDraft[_apiCurrentTab];
    document.getElementById("api-input-base-url").value = src.base_url || "";
    document.getElementById("api-input-api-key").value = src.api_key || "";
    document.getElementById("api-input-model").value = src.model || "";

    // 更新标签页高亮和已配置标记
    document.querySelectorAll(".api-config-tab").forEach(btn => {
        const layer = btn.dataset.layer;
        btn.classList.toggle("active", layer === _apiCurrentTab);
        const d = _apiConfigDraft[layer];
        btn.classList.toggle("configured", !!(d && d.base_url && d.api_key && d.model));
    });
}

function _flushCurrentLayerTosDraft() {
    if (!_apiConfigDraft) return;
    const target = _apiConfigDraft.unified ? _apiConfigDraft.unified_data : _apiConfigDraft[_apiCurrentTab];
    target.base_url = document.getElementById("api-input-base-url").value.trim();
    target.api_key = document.getElementById("api-input-api-key").value.trim();
    target.model = document.getElementById("api-input-model").value.trim();
}

function switchApiTab(layer) {
    _flushCurrentLayerTosDraft();
    _apiCurrentTab = layer;
    _renderApiModal();
}

function onApiUnifiedToggle() {
    _flushCurrentLayerTosDraft();
    _apiConfigDraft.unified = document.getElementById("api-unified-toggle").checked;
    if (_apiConfigDraft.unified) {
        // 统一模式：把当前层数据复制为unified_data起点
        const cur = _apiConfigDraft[_apiCurrentTab];
        if (cur.base_url || cur.api_key || cur.model) {
            _apiConfigDraft.unified_data = { ...cur };
        }
    }
    _renderApiModal();
}

function clearCurrentApiLayer() {
    if (!_apiConfigDraft) return;
    const target = _apiConfigDraft.unified ? _apiConfigDraft.unified_data : _apiConfigDraft[_apiCurrentTab];
    target.base_url = "";
    target.api_key = "";
    target.model = "";
    _renderApiModal();
}

function saveApiConfig() {
    _flushCurrentLayerTosDraft();
    _saveApiConfigToStorage(_apiConfigDraft);
    _updateApiButtonState();
    closeApiConfigModal();
}
