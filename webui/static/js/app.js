// ═══════════════════════════════════════
// AITRPG Web UI - Frontend Logic
// ═══════════════════════════════════════

let isProcessing = false;

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
        }
    } catch (err) {
        // 首次访问，正常显示模组选择
    }
}

// ─── 开始游戏 ───

async function startGame(moduleIndex) {
    try {
        const resp = await fetch("/trpg/api/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ module_index: moduleIndex })
        });
        const data = await resp.json();

        if (data.error) {
            alert(data.error);
            return;
        }

        // 切换到游戏界面
        showGameUI();

        // 设置标题
        document.getElementById("game-title").textContent = data.module_name || "AI驱动TRPG";

        // 显示开场白
        addMessage("assistant", data.opening);

        // 更新状态
        if (data.game_state) {
            updatePlayerStatus(data.game_state);
        }
    } catch (err) {
        console.error("Failed to start game:", err);
        alert("启动游戏失败，请刷新重试。");
    }
}

function showGameUI() {
    const overlay = document.getElementById("module-overlay");
    overlay.classList.add("fade-out");
    setTimeout(() => {
        overlay.style.display = "none";
        document.getElementById("game-container").classList.remove("hidden");
    }, 250);
}

// ─── 发送行动 ───

async function sendAction() {
    if (isProcessing) return;

    const input = document.getElementById("chat-input");
    const text = input.value.trim();
    if (!text) return;

    // 清空输入
    input.value = "";
    input.style.height = "auto";

    // 显示用户消息
    addMessage("user", text);

    // 显示 loading
    isProcessing = true;
    setInputEnabled(false);
    const loadingEl = addLoadingIndicator();

    try {
        const resp = await fetch("/trpg/api/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ input: text })
        });
        const data = await resp.json();

        // 移除 loading
        loadingEl.remove();

        if (data.error) {
            addMessage("assistant", "处理出错: " + data.error);
        } else {
            // 显示叙述
            addMessage("assistant", data.narrative);

            // 更新左侧面板
            updateRulePanel(data.rule_result);
            updateRhythmPanel(data.rhythm_result);

            // 更新右侧状态
            if (data.game_state) {
                updatePlayerStatus(data.game_state);
            }
        }
    } catch (err) {
        loadingEl.remove();
        addMessage("assistant", "网络错误，请重试。");
        console.error("Action failed:", err);
    } finally {
        isProcessing = false;
        setInputEnabled(true);
        document.getElementById("chat-input").focus();
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
                <div class="ai-field-value">无需检定</div>
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
            <div class="ai-field-value">${escapeHtml(result.skill || "N/A")}</div>
        </div>
        <div class="ai-field">
            <div class="ai-field-label">难度</div>
            <div class="ai-field-value">${escapeHtml(result.difficulty || "normal")}</div>
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

    const feasibleText = result.feasible !== false ? "可行" : "不可行";
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

    // Location
    const locationEl = document.getElementById("player-location");
    locationEl.innerHTML = `<span class="location-tag">${escapeHtml(state.current_location || "--")}</span>`;

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

// ─── 重置游戏 ───

async function resetGame() {
    if (!confirm("确定要重置游戏吗？所有进度将丢失。")) return;

    try {
        await fetch("/trpg/api/reset", { method: "POST" });

        // 清空聊天
        document.getElementById("chat-messages").innerHTML = "";

        // 重置面板
        document.getElementById("rule-panel").innerHTML = `<p class="placeholder-text">等待游戏行动...</p>`;
        document.getElementById("rhythm-panel").innerHTML = `<p class="placeholder-text">等待游戏行动...</p>`;

        // 回到模组选择
        document.getElementById("game-container").classList.add("hidden");
        const overlay = document.getElementById("module-overlay");
        overlay.style.display = "";
        overlay.classList.remove("fade-out");
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
