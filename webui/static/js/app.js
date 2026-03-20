// ═══════════════════════════════════════
// AITRPG Web UI - Frontend Logic
// ═══════════════════════════════════════

let isProcessing = false;
let selectedDestination = null;   // 待移动的目标location key
let currentMapData = null;        // 缓存的地图数据

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
            // 恢复地图
            if (data.map_data) {
                currentMapData = data.map_data;
                renderMap(data.map_data);
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

        let data = null;
        try {
            data = await resp.json();
        } catch (parseErr) {
            const errorText = await resp.text().catch(() => "");
            throw new Error(errorText || `HTTP ${resp.status}`);
        }

        if (!resp.ok || data.error) {
            alert(data.error || `启动游戏失败（HTTP ${resp.status}）`);
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

        // 初始化地图
        if (data.map_data) {
            currentMapData = data.map_data;
            renderMap(data.map_data);
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
    const moveTo = selectedDestination;

    // 需要有文字输入或移动目标
    if (!text && !moveTo) return;

    // 清空输入
    input.value = "";
    input.style.height = "auto";

    // 构建显示文本
    let displayText = text;
    if (moveTo && !text) {
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

    try {
        const body = {};
        if (text) body.input = text;
        if (moveTo) body.move_to = moveTo;

        const resp = await fetch("/trpg/api/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
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

            // 更新地图
            if (data.map_data) {
                currentMapData = data.map_data;
                renderMap(data.map_data);
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
    const svg = document.getElementById("map-svg");
    if (!svg) return;

    // 清空SVG（兼容所有浏览器）
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    if (!mapData || !mapData.locations) {
        console.warn("[Map] No map data or locations");
        return;
    }

    const locations = mapData.locations;
    const edges = mapData.edges || [];
    const currentLoc = mapData.current_location;
    const reachable = new Set(mapData.reachable || []);

    const keys = Object.keys(locations);
    if (keys.length === 0) {
        console.warn("[Map] Empty locations");
        return;
    }

    // 按floor分组，高楼层在上
    const floorGroups = {};
    for (const key of keys) {
        const floor = locations[key].floor;
        if (floor === undefined || floor === null) continue;
        if (!floorGroups[floor]) floorGroups[floor] = [];
        floorGroups[floor].push(key);
    }
    const floors = Object.keys(floorGroups).map(Number).sort((a, b) => b - a);

    // 构建邻接表（仅可见节点之间）
    const adj = {};
    for (const key of keys) adj[key] = [];
    for (const edge of edges) {
        if (locations[edge.from] && locations[edge.to]) {
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

    // 设置SVG尺寸 — 不设width/height属性，让CSS width:100%生效，viewBox控制内部坐标
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
        const loc = locations[key];
        const pos = nodePositions[key];
        if (!pos) continue;

        const isCurrent = key === currentLoc;
        const isReachable = reachable.has(key);
        const isVisited = loc.visited;
        const isSelected = key === selectedDestination;

        // 确定节点样式类
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

        // 截断过长的名字
        let displayName = loc.display_name || "?";
        if (displayName.length > 5) {
            displayName = displayName.substring(0, 4) + "…";
        }
        text.textContent = displayName;
        g.appendChild(text);

        // 点击事件
        if (isCurrent || (!isCurrent && isReachable)) {
            g.style.cursor = "pointer";
            g.addEventListener("click", () => onMapNodeClick(key));
        }

        svg.appendChild(g);
    }

    console.log(`[Map] Rendered ${keys.length} nodes, ${edges.length} edges`);
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
