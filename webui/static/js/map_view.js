/**
 * map_view.js — Cytoscape.js 接管的地图视图模块 (v3.4.0)
 *
 * v3.4.0 视觉重构 (map-node-visual-style, 2026-05-04):
 *   - 节点本体改为完全透明的 Cytoscape 圆形 (background-opacity 默认 0, border 默认 0).
 *     连线两端永远落在圆心几何上, 不再因节点尺寸不一致出现脱节感.
 *   - 大尺寸 MDI 图标水印 + 大号节点名 + 角标徽章, 全部由独立的 DOM overlay 层渲染,
 *     与 Cytoscape canvas 同步 pan/zoom (rAF 节流). MDI 类名直接从 loc.icon 读取
 *     (默认 mdi-door); 未探索节点只显示 "?", 不显示房间名.
 *   - 玩家所在节点不再整圆染色, 改用 DOM/CSS 圆环呼吸; NPC / 危险 / 微场景
 *     等其他语义全部移到右上 / 右下角标徽章.
 *   - 模组作者可在 location 数据里写 icon (MDI 类名) / displayColor (hex) / displayAlpha
 *     (0..1) / badges 数组 来覆盖默认; 模组作者写的 badges 与引擎自动注入的 NPC/danger
 *     徽章一起 merge 进 4 个角.
 *   - 连线支持 style 字段: solid (默认) / dashed / double / single-arrow; directed=true 加箭头.
 *
 * v3.3.0 视觉前置 (部分保留):
 *   - hover 动画 & 不可达节点半透明
 *
 * v3.2.0 同步编辑器 (保留):
 *   - 节点不再可拖拽 (autoungrabify)
 *   - map_position 支持浮点 (col, row), 不再强制整数网格
 *   - 按 map_group (字符串) 分带渲染
 *
 * 数据契约: render(mapData) 接收
 *   { locations: { key: { display_name, floor, visited, map_position?, map_group?,
 *                         is_micro_scene?, parent_location?,
 *                         icon?, displayColor?, displayAlpha?, badges? } },
 *     edges: [{ from, to, locked, style?, directed? }],
 *     current_location: str,
 *     reachable: [keys],
 *     danger_locations: [keys],
 *     npc_locations: [keys] }
 *
 * 全局命名: window.MapView (单例)
 */
(function () {
    "use strict";

    const FLAG_KEY = "aitrpg_new_map";
    const CONTAINER_ID = "map-canvas";
    const LEGACY_SVG_ID = "map-svg";
    const OVERLAY_ID = "map-node-overlay";

    let _cy = null;
    let _enabled = false;
    let _lastLayoutKey = "";   // 用于布局缓存 (相同图形不重排)
    let _selectedTarget = null; // 选中目标 key (移动指示用)
    let _onNodeTap = null;     // 外部注入的 tap 回调 (app.js 提供)
    let _tooltipEl = null;     // 浮动 tooltip 元素
    let _tooltipTimer = null;
    let _lastMapData = null;   // 缓存最近一次 mapData, tooltip 拼文案用
    let _anchorKey = null;     // 本会话首次见到的 current_location, 作为稳定 BFS 根; 玩家走动时房间不再左右乱跳
    let _overlayEl = null;     // DOM overlay 容器 (v3.4.0)
    let _overlayPending = false; // rAF 节流标记 (v3.4.0)

    // ===== v3.4.0: 图标系统 (MDI 字体, 不再用 SVG dataURI) =====
    // 节点未声明 icon 时的默认图标. 模组作者可在 loc.icon 写任意 mdi-* 类名覆盖.
    // 未访问节点 (s-fog) 不调图标, 圆心位置渲染 "?" 字符替代, 不进入此 fallback.
    const _ICON_DEFAULT_MDI = "mdi-door";

    // 引擎自动注入的语义徽章 (NPC / 危险 / 微场景). 模组 badges 数组里的徽章会与它们 merge.
    // 对应 CSS 变量: --success / --danger / --warning (见 webui/static/css/style.css).
    const _AUTO_BADGE_NPC = { icon: "mdi-account", color: "var(--success)" };
    const _AUTO_BADGE_DANGER = { icon: "mdi-skull", color: "var(--danger)" };
    const _AUTO_BADGE_MICRO = { icon: "mdi-flash", color: "var(--warning)" };

    // 角标位置分配顺序 (同时存在多个徽章时依次填入).
    const _BADGE_POSITIONS = ["tr", "tl", "br", "bl"];

    function isEnabled() {
        // v3.1.0 默认启用新地图 (Cytoscape.js); 若用户显式设 "0" 则切回旧 SVG 渲染。
        // 这样升级后开箱即用; 万一新版异常, 玩家可在 console 里 localStorage.setItem('aitrpg_new_map','0') 应急回退。
        try {
            const v = localStorage.getItem(FLAG_KEY);
            if (v === "0") return false;
            return true;
        } catch (e) {
            return false;
        }
    }

    function _swapContainerMode(useNewMap) {
        const wrap = document.getElementById("game-map");
        const canvas = document.getElementById(CONTAINER_ID);
        const legacySvg = document.getElementById(LEGACY_SVG_ID);
        if (!wrap || !canvas) return;
        if (useNewMap) {
            wrap.classList.add("map-container--cy");
            canvas.classList.remove("hidden");
            if (legacySvg) legacySvg.style.display = "none";
        } else {
            wrap.classList.remove("map-container--cy");
            canvas.classList.add("hidden");
            if (legacySvg) legacySvg.style.display = "";
        }
    }

    /**
     * v3.4.0 cytoscape 样式表 — 节点外形完全透明的圆形, 视觉细节交给 DOM overlay.
     *
     * 设计要点:
     *  - Cytoscape 节点只承担圆形点击区域和连线锚点职责, 不再画任何可见填充/边框/hover overlay.
     *  - current / selected / fog / locked 等视觉态都交给 DOM overlay class + CSS.
     *  - 边按 data("style") 决定虚线/双线/箭头.
     */
    function _buildStyle() {
        return [
            // ===== 节点基础: 完全透明圆形 =====
            {
                selector: "node",
                style: {
                    "label": "",  // label 改由 DOM overlay 渲染
                    "shape": "ellipse",
                    "width": 104,
                    "height": 104,
                    "background-color": "#8b6f4e",
                    "background-opacity": 0,
                    "border-width": 0,
                    "border-color": "#5a4e3e",
                    "border-opacity": 0,
                    "overlay-opacity": 0,
                    "transition-property": "opacity",
                    "transition-duration": "180ms"
                }
            },
            // visited (已访问) — 完全透明, 视觉全部交给 DOM overlay.
            {
                selector: "node.s-visited",
                style: {}
            },
            // fog (未探索) — 透明圆, 只在 DOM overlay 显示问号.
            {
                selector: "node.s-fog",
                style: {}
            },
            // locked (无法到达但有视野) — Cytoscape 层只降低 hitbox 透明度, 可见状态交给 DOM overlay.
            {
                selector: "node.s-locked",
                style: {
                    "opacity": 0.7
                }
            },
            // current (玩家所在位置) — 不再整圆染色; DOM overlay 画圆环呼吸.
            {
                selector: "node.s-current",
                style: {}
            },
            // selected (玩家点击选中的目标) — DOM overlay 画圆环, Cytoscape 层保持透明.
            {
                selector: "node.s-selected",
                style: {}
            },
            // danger / npc — class 保留作 DOM overlay 角标 hook, 但 Cytoscape 层不再染色.
            { selector: "node.s-danger", style: {} },
            { selector: "node.s-npc", style: {} },
            // micro_scene (微场景 / 硬导向入口) — 保持透明圆形 hitbox, 语义由角标表达.
            {
                selector: "node.s-micro",
                style: {}
            },
            // 路径高亮 (BFS 路径上的节点) — 节点本体不画边框, 路径主要靠边高亮.
            {
                selector: "node.s-path",
                style: {}
            },
            // ===== hover 动效 (PC 端鼠标悬停) — 禁用 Cytoscape 矩形 overlay =====
            {
                selector: "node:hover",
                style: {
                    "overlay-opacity": 0,
                    "overlay-color": "#c49a3c"
                }
            },
            // ===== 边基础 =====
            {
                selector: "edge",
                style: {
                    "width": 2,
                    "line-color": "#5a4e3e",
                    "curve-style": "bezier",
                    "target-arrow-shape": "none",
                    "opacity": 0.7,
                    "transition-property": "line-color, width, opacity",
                    "transition-duration": "180ms"
                }
            },
            // 锁住的边 (虚线 + 暖红)
            {
                selector: "edge.s-locked",
                style: {
                    "line-color": "#8a4838",
                    "line-style": "dashed",
                    "width": 1.5,
                    "opacity": 0.55
                }
            },
            // 路径高亮的边
            {
                selector: "edge.s-path",
                style: {
                    "line-color": "#a4a04a",
                    "width": 3,
                    "opacity": 1
                }
            },
            // v3.4.0: 模组作者声明的边样式
            {
                selector: "edge.s-edge-dashed",
                style: { "line-style": "dashed" }
            },
            {
                selector: "edge.s-edge-double",
                // Cytoscape 没有原生 double 样式, 用 line-fill linear-gradient 做明暗双线视觉.
                // 主线占两侧, 中央留一条颜色更浅的轨道, 看起来像两条平行线.
                style: {
                    "width": 5,
                    "line-fill": "linear-gradient",
                    "line-gradient-stop-colors": "#5a4e3e #d8c8a8 #5a4e3e",
                    "line-gradient-stop-positions": "30 50 70"
                }
            },
            {
                selector: "edge.s-edge-arrow",
                style: {
                    "target-arrow-shape": "triangle",
                    "target-arrow-color": "#a89070",
                    "arrow-scale": 1.4
                }
            }
        ];
    }

    function _layoutKeyFromElements(elements) {
        // 用 nodes id 集合 + edges from-to 集合 哈希; 同结构跳过 layout
        const nodeIds = elements.filter(e => e.group === "nodes").map(e => e.data.id).sort();
        const edgeIds = elements.filter(e => e.group === "edges").map(e => e.data.source + "-" + e.data.target).sort();
        return nodeIds.join("|") + "::" + edgeIds.join("|");
    }

    // 跟编辑器 map-canvas.js 的 deriveMapGroup 保持一致: 优先 loc.map_group 字符串,
    // 没填的话按 floor 数字派生. 主游戏没法 import 编辑器代码, 复制一份逻辑.
    function _deriveMapGroup(loc) {
        if (!loc) return "1F";
        const explicit = loc.map_group;
        if (typeof explicit === "string" && explicit.trim()) return explicit.trim();
        const f = loc.floor;
        if (typeof f !== "number" || !isFinite(f)) return "1F";
        if (f === 0) return "GF";
        if (f < 0) return "B" + Math.abs(f);
        return f + "F";
    }

    // 把 group 字符串转成排序键. 数字楼层降序在上, 自定义字符串组按字母升序在下.
    function _groupSortKey(group) {
        const s = String(group || "").trim();
        let m = s.match(/^(-?\d+)F$/i);
        if (m) return { numeric: true, value: Number(m[1]) };
        m = s.match(/^B(\d+)$/i);
        if (m) return { numeric: true, value: -Number(m[1]) };
        if (s.toUpperCase() === "GF") return { numeric: true, value: 0 };
        return { numeric: false, value: s };
    }

    function _compareGroups(a, b) {
        const ka = _groupSortKey(a), kb = _groupSortKey(b);
        if (ka.numeric && kb.numeric) return kb.value - ka.value;
        if (ka.numeric && !kb.numeric) return -1;
        if (!ka.numeric && kb.numeric) return 1;
        return String(ka.value).localeCompare(String(kb.value));
    }

    function _buildElements(mapData) {
        const elements = [];
        const locations = mapData.locations || {};
        const edges = Array.isArray(mapData.edges) ? mapData.edges : [];
        const currentLoc = mapData.current_location;
        const reachable = new Set(Array.isArray(mapData.reachable) ? mapData.reachable : []);
        const dangerLocs = new Set(Array.isArray(mapData.danger_locations) ? mapData.danger_locations : []);
        const npcLocs = new Set(Array.isArray(mapData.npc_locations) ? mapData.npc_locations : []);

        for (const key of Object.keys(locations)) {
            const loc = locations[key] || {};
            const isCurrent = key === currentLoc;
            const isReachable = reachable.has(key);
            const isVisited = Boolean(loc.visited);
            const isMicro = Boolean(loc.is_micro_scene);
            const isDanger = dangerLocs.has(key);
            const isNpc = npcLocs.has(key);
            // 未访问节点无论是否当前可达, 都只显示问号; 不再显示默认门图标.
            const isFog = !isCurrent && !isVisited;

            const classes = [];
            if (isCurrent) classes.push("s-current");
            else if (!isReachable) classes.push("s-locked");
            else if (isVisited) classes.push("s-visited");
            else classes.push("s-fog");
            if (isDanger) classes.push("s-danger");
            if (isNpc) classes.push("s-npc");
            if (isMicro) classes.push("s-micro");

            // v3.4.0: MDI 类名 — 模组 loc.icon 优先, 缺省 mdi-door (visited) 或 "?" 字符 (fog).
            // fog 节点的 iconMdi 留空, 让 overlay 渲染 "?" 字符替代图标.
            let iconMdi = "";
            let showQuestion = false;
            if (isFog) {
                showQuestion = true;
            } else if (typeof loc.icon === "string" && loc.icon.trim()) {
                iconMdi = loc.icon.trim();
            } else {
                iconMdi = _ICON_DEFAULT_MDI;
            }

            // v3.5.0: 节点本体永远完全透明. displayColor 只作为圆环/角标的 accent 颜色,
            // displayAlpha 保留在数据里兼容旧模组, 但不再用于给节点整圆上色.
            let bgColor = "#8b6f4e"; // --accent fallback
            if (typeof loc.displayColor === "string" && loc.displayColor.trim()) {
                bgColor = loc.displayColor.trim();
            }
            const bgAlpha = 0;

            // v3.4.0: 角标徽章 — 引擎自动注入 (NPC/danger/micro) + 模组 badges 数组合并.
            // 引擎注入的徽章先排在前面, 占据右上 → 左上 → 右下 → 左下 顺序; 模组 badges 接在后面.
            // 模组徽章如果显式声明 position 字段就尊重, 否则按剩余角分配.
            const autoBadges = [];
            if (isDanger) autoBadges.push(_AUTO_BADGE_DANGER);
            if (isNpc) autoBadges.push(_AUTO_BADGE_NPC);
            if (isMicro) autoBadges.push(_AUTO_BADGE_MICRO);
            const modBadges = Array.isArray(loc.badges) ? loc.badges.filter(function (b) {
                return b && typeof b === "object" && typeof b.icon === "string" && b.icon.trim();
            }) : [];
            // 合并并分配 position. 引擎徽章不指定 position, 走自动分配; 模组徽章自带 position 的优先用自带.
            const usedPositions = new Set();
            const finalBadges = [];
            const allBadges = autoBadges.concat(modBadges);
            // 第一遍: 把显式 position 的徽章放下
            for (let i = 0; i < allBadges.length; i++) {
                const b = allBadges[i];
                if (typeof b.position === "string" && _BADGE_POSITIONS.indexOf(b.position) >= 0 && !usedPositions.has(b.position)) {
                    finalBadges.push({ icon: b.icon, color: b.color || "", position: b.position });
                    usedPositions.add(b.position);
                }
            }
            // 第二遍: 没显式 position 的按剩余角顺序分配
            for (let i = 0; i < allBadges.length; i++) {
                const b = allBadges[i];
                if (typeof b.position === "string" && _BADGE_POSITIONS.indexOf(b.position) >= 0) continue;
                let assigned = null;
                for (let p = 0; p < _BADGE_POSITIONS.length; p++) {
                    const cand = _BADGE_POSITIONS[p];
                    if (!usedPositions.has(cand)) { assigned = cand; break; }
                }
                if (assigned) {
                    finalBadges.push({ icon: b.icon, color: b.color || "", position: assigned });
                    usedPositions.add(assigned);
                }
                // 多于 4 个的徽章本轮先简单截断
            }

            elements.push({
                group: "nodes",
                data: {
                    id: key,
                    label: typeof loc.display_name === "string" ? loc.display_name : "?",
                    floor: loc.floor != null ? Number(loc.floor) : 1,
                    mapGroup: _deriveMapGroup(loc),
                    isCurrent: isCurrent,
                    isReachable: isReachable,
                    isVisited: isVisited,
                    isMicro: isMicro,
                    isDanger: isDanger,
                    isNpc: isNpc,
                    isFog: isFog,
                    parentLocation: loc.parent_location || null,
                    unreachableReason: loc.unreachable_reason || null,
                    iconMdi: iconMdi,
                    showQuestion: showQuestion,
                    bgColor: bgColor,
                    bgAlpha: bgAlpha,
                    badges: finalBadges,
                    // 模组作者手画坐标; 后端透传为 float, 编辑器允许浮点拖拽 (不强制整数吸附).
                    mapPosition: (loc.map_position && typeof loc.map_position === "object" && typeof loc.map_position.col === "number")
                        ? { col: Number(loc.map_position.col), row: Number(loc.map_position.row || 0) }
                        : null
                },
                classes: classes.join(" ")
            });
        }

        for (const e of edges) {
            if (!e || !locations[e.from] || !locations[e.to]) continue;
            // v3.4.0: 边样式 class — locked 仍走 s-locked; mod 的 style 加 s-edge-* class
            const edgeClasses = [];
            if (e.locked) edgeClasses.push("s-locked");
            const edgeStyle = (typeof e.style === "string") ? e.style : "solid";
            if (edgeStyle === "dashed") edgeClasses.push("s-edge-dashed");
            else if (edgeStyle === "double") edgeClasses.push("s-edge-double");
            else if (edgeStyle === "single-arrow" || e.directed) edgeClasses.push("s-edge-arrow");
            elements.push({
                group: "edges",
                data: {
                    id: e.from + "->" + e.to + (edgeStyle !== "solid" ? "::" + edgeStyle : ""),
                    source: e.from,
                    target: e.to,
                    locked: Boolean(e.locked),
                    edgeStyle: edgeStyle,
                    directed: Boolean(e.directed)
                },
                classes: edgeClasses.join(" ")
            });
        }
        return elements;
    }

    // ===== v3.4.0: DOM overlay 渲染 =====
    //
    // 节点本体在 Cytoscape 里只是一个透明圆形 (背景 + 边框 + shadow). 真正的"大图标 +
    // 50% 透明 label + 角标徽章"通过一个绝对定位的兄弟 DOM 层渲染, 与 Cytoscape canvas
    // 共用容器的 origin. 每次 render / pan / zoom / 节点增删 都会触发一次 _scheduleOverlayRender,
    // 经 requestAnimationFrame 节流, 避免高频拖动时掉帧.

    function _ensureOverlayEl() {
        if (_overlayEl && document.body.contains(_overlayEl)) return _overlayEl;
        const container = document.getElementById(CONTAINER_ID);
        if (!container) return null;
        // 把 overlay 作为 Cytoscape 容器 (#map-canvas) 的子节点, 这样 node.renderedPosition()
        // 返回的 viewport 像素坐标可以直接用 (overlay 的 origin == cy container origin).
        // pointer-events: none 让 click 仍打到 Cytoscape canvas.
        let el = container.querySelector(":scope > #" + OVERLAY_ID);
        if (!el) {
            el = document.createElement("div");
            el.id = OVERLAY_ID;
            el.className = "map-node-overlay";
            container.appendChild(el);
        }
        _overlayEl = el;
        return el;
    }

    function _removeOverlayEl() {
        if (_overlayEl && _overlayEl.parentNode) {
            _overlayEl.parentNode.removeChild(_overlayEl);
        }
        _overlayEl = null;
    }

    function _scheduleOverlayRender() {
        if (_overlayPending) return;
        _overlayPending = true;
        requestAnimationFrame(function () {
            _overlayPending = false;
            _renderOverlay();
        });
    }

    function _renderOverlay() {
        if (!_cy) return;
        const overlay = _ensureOverlayEl();
        if (!overlay) return;
        // viewport zoom — overlay 内的图标/字号要跟着缩放.
        const zoom = _cy.zoom();

        const nodes = _cy.nodes();
        // 维持一个 id -> existing-card 映射, 这次没出现的就移除.
        const seen = new Set();
        nodes.forEach(function (node) {
            const id = node.id();
            seen.add(id);
            const data = node.data();
            const pos = node.renderedPosition();
            const w = node.renderedWidth();
            const h = node.renderedHeight();

            let card = overlay.querySelector('[data-node-id="' + _cssEscape(id) + '"]');
            if (!card) {
                card = document.createElement("div");
                card.className = "map-node-card";
                card.setAttribute("data-node-id", id);
                card.innerHTML =
                    '<div class="map-node-icon-wrap">' +
                        '<span class="map-node-icon"></span>' +
                        '<span class="map-node-q">?</span>' +
                    '</div>' +
                    '<div class="map-node-label"></div>' +
                    '<div class="map-node-badges"></div>';
                overlay.appendChild(card);
            }
            // Cytoscape hitbox 故意比可见 card 大: 边线锚在 hitbox 外缘, 与圆环/card 留出呼吸距离.
            const visualW = Math.min(w, Math.max(54, Math.round((data.isMicro ? 68 : 76) * zoom)));
            const visualH = Math.min(h, Math.max(54, Math.round((data.isMicro ? 68 : 76) * zoom)));
            card.style.left = (pos.x - visualW / 2) + "px";
            card.style.top = (pos.y - visualH / 2) + "px";
            card.style.width = visualW + "px";
            card.style.height = visualH + "px";
            const cardClasses = ["map-node-card"];
            if (data.isCurrent) cardClasses.push("is-current");
            if (data.isFog) cardClasses.push("is-fog");
            if (data.isReachable === false && !data.isCurrent) cardClasses.push("is-locked");
            if (node.hasClass("s-selected")) cardClasses.push("is-selected");
            if (node.hasClass("s-path")) cardClasses.push("is-path");
            if (data.isMicro) cardClasses.push("is-micro");
            card.className = cardClasses.join(" ");
            card.style.setProperty("--map-node-accent", data.bgColor || "#8b6f4e");
            // 字号 / 图标尺寸跟随 zoom: 图标是主视觉, label 按文本长度自动缩小.
            const iconPx = Math.max(36, Math.round((data.isFog ? 68 : 60) * zoom));
            const labelText = data.isFog ? "" : (data.label || "");
            const labelLen = Array.from(labelText).length;
            let labelBase = 12;
            if (labelLen > 8) labelBase = 8.5;
            else if (labelLen > 6) labelBase = 9.5;
            else if (labelLen > 4) labelBase = 10.5;
            const labelPx = Math.max(8, Math.round(labelBase * zoom));
            const badgePx = Math.max(8, Math.round(12 * zoom));
            card.style.fontSize = labelPx + "px";

            // ----- icon / question -----
            const iconEl = card.querySelector(".map-node-icon");
            const qEl = card.querySelector(".map-node-q");
            if (data.showQuestion) {
                iconEl.className = "map-node-icon";
                iconEl.style.fontSize = iconPx + "px";
                iconEl.style.display = "none";
                qEl.style.display = "";
                qEl.style.fontSize = iconPx + "px";
            } else {
                iconEl.className = "map-node-icon mdi " + (data.iconMdi || _ICON_DEFAULT_MDI);
                iconEl.style.fontSize = iconPx + "px";
                iconEl.style.display = "";
                qEl.style.display = "none";
            }
            // current 节点的 icon 在金色 tint 上要稍稍亮一些, 让玩家更明显.
            if (data.isCurrent) {
                iconEl.classList.add("is-current");
                qEl.classList.add("is-current");
            } else {
                iconEl.classList.remove("is-current");
                qEl.classList.remove("is-current");
            }

            // ----- label -----
            const labelEl = card.querySelector(".map-node-label");
            labelEl.textContent = labelText;
            labelEl.style.fontSize = labelPx + "px";

            // ----- badges -----
            const badgesEl = card.querySelector(".map-node-badges");
            const desiredBadges = Array.isArray(data.badges) ? data.badges : [];
            // 简单做: 全清后重建. 角标数量 ≤ 4, 重绘开销可忽略.
            badgesEl.innerHTML = "";
            for (let i = 0; i < desiredBadges.length; i++) {
                const b = desiredBadges[i];
                if (!b || typeof b.icon !== "string") continue;
                const span = document.createElement("span");
                span.className = "map-node-badge mdi " + b.icon + " pos-" + (b.position || "tr");
                span.style.fontSize = badgePx + "px";
                if (b.color) span.style.color = b.color;
                badgesEl.appendChild(span);
            }
        });

        // 清理本帧未出现的节点 card (例如玩家走入场景导致旧节点被移除).
        const cards = overlay.querySelectorAll(".map-node-card");
        for (let i = 0; i < cards.length; i++) {
            const id = cards[i].getAttribute("data-node-id");
            if (!seen.has(id)) {
                cards[i].parentNode.removeChild(cards[i]);
            }
        }
    }

    function _cssEscape(s) {
        // 节点 id 可能含中文 / 特殊字符. 给 querySelector 的属性选择器值做最小化转义.
        if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(s);
        return String(s).replace(/(["\\\]\[])/g, "\\$1");
    }


    function _runLayout(force) {
        if (!_cy) return;
        const nodes = _cy.nodes();
        if (nodes.length === 0) return;

        // === Group-band preset 布局 (v3.2.0) ===
        //
        // 跟编辑器 tools/module-editor/js/map-canvas.js 的 _runLayout 算法对齐:
        //   * 按 mapGroup (字符串, 比如 "1F" / "B1" / "中央大街") 分组, 替代旧的 floor 分组
        //   * 数字派生的组 ("nF" / "Bn" / "GF") 按楼层数降序排, 自定义字符串组按字母升序放在后面
        //   * 同组内显式 map_position 优先, 没标的按 BFS 距离从左到右补
        //   * map_position 现在是浮点 (作者完全自由拖拽, 不再吸附整数), 这里 Math.ceil 兜底
        //     下一组 yOffset 推进, 防止浮点 col/row 把组间垂直留白挤没.
        //   * preset 布局确定性, 不会因节点多失败.
        const ROW_HEIGHT = 78;
        const COL_WIDTH = 108;
        const PAD_LEFT = 56;
        const PAD_TOP = 36;
        const GROUP_GAP = 32;   // 不同 map_group 之间额外的垂直留白 (跟编辑器一致)

        // 1) 按 mapGroup 分组
        const byGroup = new Map();
        nodes.forEach(function (node) {
            const g = String(node.data("mapGroup") || "1F");
            if (!byGroup.has(g)) byGroup.set(g, []);
            byGroup.get(g).push(node);
        });

        // 2) BFS 排序: 用本会话首次见到的房间 (_anchorKey) 当稳定的根; 用 live current_location 当根
        //    会让"现在所在的房间"永远抢到 col 0, 玩家走一步房间整排横移, 体验非常反直觉.
        const visitOrder = new Map();
        let bfsRootKey = null;
        if (_anchorKey && _cy.getElementById(_anchorKey).length) {
            bfsRootKey = _anchorKey;
        } else if (_lastMapData && _lastMapData.current_location && _cy.getElementById(_lastMapData.current_location).length) {
            // 异常兜底: anchor 还没拿到或被移除时, 用 current 临时当根 (本次布局保持自洽)
            bfsRootKey = _lastMapData.current_location;
        }
        if (bfsRootKey) {
            const root = _cy.getElementById(bfsRootKey);
            if (root && root.length) {
                let counter = 0;
                _cy.elements().bfs({
                    roots: root,
                    visit: function (v) { visitOrder.set(v.id(), counter++); },
                    directed: false
                });
            }
        }

        // 3) 组排序 (跟编辑器一致): 数字楼层降序在上, 自定义字符串组按字母升序在下
        const sortedGroups = Array.from(byGroup.keys()).sort(_compareGroups);

        // 4) 计算每个节点 (x, y); 每组先放显式 map_position, 再把没标的按 BFS 顺序补到右边
        const positions = {};
        let yOffset = PAD_TOP;
        sortedGroups.forEach(function (group) {
            const groupNodes = byGroup.get(group).slice();

            // 4a) 拆成 explicit (有 mapPosition) / auto (没标的)
            const explicit = [];
            const auto = [];
            groupNodes.forEach(function (node) {
                const mp = node.data("mapPosition");
                if (mp && typeof mp.col === "number") {
                    explicit.push({ node: node, col: Number(mp.col), row: Number(mp.row || 0) });
                } else {
                    auto.push(node);
                }
            });

            // 4b) auto 部分按 BFS 顺序排
            auto.sort(function (a, b) {
                const ao = visitOrder.has(a.id()) ? visitOrder.get(a.id()) : 1e9;
                const bo = visitOrder.has(b.id()) ? visitOrder.get(b.id()) : 1e9;
                if (ao !== bo) return ao - bo;
                return String(a.id()).localeCompare(String(b.id()));
            });

            // 4c) auto 起始 col = ceil(已有显式 col 最大值) + 1, 没显式就从 0 开始.
            //     ceil 是为了兼容浮点 col (比如 explicit max=3.7 时 auto 从 5 起步, 不重叠)
            let nextAutoCol = 0;
            if (explicit.length > 0) {
                let maxExplicitCol = -Infinity;
                explicit.forEach(function (e) { if (e.col > maxExplicitCol) maxExplicitCol = e.col; });
                nextAutoCol = Math.ceil(maxExplicitCol) + 1;
            }

            // 4d) 落坐标
            explicit.forEach(function (e) {
                positions[e.node.id()] = {
                    x: PAD_LEFT + e.col * COL_WIDTH,
                    y: yOffset + e.row * ROW_HEIGHT
                };
            });
            auto.forEach(function (node) {
                positions[node.id()] = {
                    x: PAD_LEFT + nextAutoCol * COL_WIDTH,
                    y: yOffset
                };
                nextAutoCol += 1;
            });

            // 4e) 这组用了多少 sub-row → 决定下一组 yOffset 推多远.
            //     用 ceil(maxRow)+1 兜底浮点 row, 上方负 row 不挤占下层空间.
            //     再加 GROUP_GAP 让不同地图组视觉上有可识别的间距.
            let minRow = 0;
            let maxRow = 0;
            explicit.forEach(function (e) {
                if (e.row < minRow) minRow = e.row;
                if (e.row > maxRow) maxRow = e.row;
            });
            yOffset += (Math.ceil(maxRow) + 1) * ROW_HEIGHT + GROUP_GAP;
        });

        // 5) 套用坐标. 注意: render() 每次都会 remove+add, 节点位置会被重置, 因此这里
        //    必须每次都跑 (放弃旧的 _lastLayoutKey 缓存路径——之前那个分支会让节点
        //    全部叠在原点 (0,0), 多层结构正好踩中这个 bug).
        _lastLayoutKey = "group-band";
        const layout = _cy.layout({
            name: "preset",
            positions: positions,
            fit: true,
            padding: 18,
            animate: false
        });
        layout.run();
    }

    function init() {
        if (!isEnabled()) {
            _enabled = false;
            return false;
        }
        if (typeof cytoscape !== "function") {
            console.error("[MapView] cytoscape lib not loaded; falling back to legacy renderMap");
            _enabled = false;
            return false;
        }
        // 注册 dagre 扩展
        if (typeof cytoscapeDagre !== "undefined") {
            try { cytoscape.use(cytoscapeDagre); } catch (e) {}
        }
        _swapContainerMode(true);
        const container = document.getElementById(CONTAINER_ID);
        if (!container) {
            console.error("[MapView] #" + CONTAINER_ID + " not found");
            return false;
        }
        if (_cy) {
            try { _cy.destroy(); } catch (e) {}
            _cy = null;
        }
        _cy = cytoscape({
            container: container,
            elements: [],
            style: _buildStyle(),
            wheelSensitivity: 0.3,
            minZoom: 0.3,
            maxZoom: 3.0,
            // v3.2.0: 玩家不能拖动地图节点 — 只能 tap 走入. 编辑模组用单独的编辑器
            // (/trpg/module-editor/), 不要让玩家在游戏里误改地图.
            autoungrabify: true,
            autounselectify: false,   // 仍允许 tap 触发 :selected 事件用于路径高亮
        });
        _bindInteractions();
        _enabled = true;
        _lastLayoutKey = "";
        // v3.4.0: 创建 overlay 容器, 让 _renderOverlay 有地方挂.
        _ensureOverlayEl();
        console.log("[MapView] initialized (v3.4.0)");
        return true;
    }

    function destroy() {
        if (_cy) {
            try { _cy.destroy(); } catch (e) {}
            _cy = null;
        }
        _removeOverlayEl();
        _swapContainerMode(false);
        _enabled = false;
        _lastLayoutKey = "";
        _selectedTarget = null;
        _anchorKey = null;
    }

    /**
     * 渲染 mapData. v3.3.0 增加四向箭头的生命周期管理.
     */
    function render(mapData) {
        if (!_enabled || !_cy) return false;
        if (!mapData || !mapData.locations) {
            console.warn("[MapView] render() no mapData/locations");
            return false;
        }
        _lastMapData = mapData;
        // 锁定 anchor: 用本会话首次出现的 current_location, 之后玩家移动时 layout 不会再因
        // BFS 根换位而把整排房间往右推. 仅当 anchor 还没设过, 且当前给的位置确实在 mapData
        // 中时才赋值 (防止异常状态初始化到一个不存在的 key).
        if (!_anchorKey && mapData.current_location && mapData.locations[mapData.current_location]) {
            _anchorKey = mapData.current_location;
        }
        const elements = _buildElements(mapData);
        // 全量替换 (render 总是 remove+add)
        _cy.batch(function () {
            _cy.elements().remove();
            _cy.add(elements);
        });
        _runLayout(false);
        // 选中目标态恢复
        if (_selectedTarget && _cy.getElementById(_selectedTarget).length > 0) {
            _cy.getElementById(_selectedTarget).addClass("s-selected");
        }
        // v3.5.0: 当前所在位置由 DOM overlay 圆环呼吸提示, 不再创建 Cytoscape 三角节点.
        // v3.4.0: 同步 DOM overlay (大图标 + label + 角标)
        _scheduleOverlayRender();
        console.log("[MapView] rendered " + elements.filter(e => e.group === "nodes").length + " nodes, " +
            elements.filter(e => e.group === "edges").length + " edges");
        return true;
    }

    function disable() {
        if (!_cy) return;
        _cy.userPanningEnabled(false);
        _cy.userZoomingEnabled(false);
        const wrap = document.getElementById("game-map");
        if (wrap) wrap.classList.add("map-disabled");
    }

    function enable() {
        if (!_cy) return;
        _cy.userPanningEnabled(true);
        _cy.userZoomingEnabled(true);
        const wrap = document.getElementById("game-map");
        if (wrap) wrap.classList.remove("map-disabled");
    }

    function flashCorrupt() {
        const canvas = document.getElementById(CONTAINER_ID);
        if (!canvas) return;
        canvas.classList.add("map-corrupt-flash");
        setTimeout(function () {
            canvas.classList.remove("map-corrupt-flash");
        }, 350);
    }

    function clear() {
        if (_cy) _cy.elements().remove();
        _selectedTarget = null;
        _lastLayoutKey = "";
        _lastMapData = null;
        _anchorKey = null;
        _hideTooltip();
        // v3.4.0: 清空 overlay (节点都没了, 角标也该撤)
        if (_overlayEl) _overlayEl.innerHTML = "";
    }

    function setSelectedTarget(key) {
        _selectedTarget = key;
        if (!_cy) return;
        _cy.nodes().removeClass("s-selected");
        _cy.elements().removeClass("s-path");
        if (key) {
            const node = _cy.getElementById(key);
            if (node && node.length) node.addClass("s-selected");
            _highlightPath(key);
        }
        _scheduleOverlayRender();
    }

    /**
     * 用 BFS 算从 current_location 到 target 的最短路径, 高亮路径上的节点+边 (.s-path)
     */
    function _highlightPath(targetKey) {
        if (!_cy || !_lastMapData) return;
        const currentKey = _lastMapData.current_location;
        if (!currentKey || currentKey === targetKey) return;
        const root = _cy.getElementById(currentKey);
        const target = _cy.getElementById(targetKey);
        if (!root.length || !target.length) return;
        const bfs = _cy.elements().bfs({
            roots: root,
            visit: function (v) { return v.id() === targetKey; },
            directed: false
        });
        if (bfs && bfs.path && bfs.path.length > 0) {
            bfs.path.forEach(function (el) {
                // 别给当前/目标本身加 path (它们已有 selected/current 高亮)
                if (el.isNode() && (el.id() === currentKey || el.id() === targetKey)) return;
                el.addClass("s-path");
            });
        }
    }

    // ===== Tooltip =====

    function _ensureTooltipEl() {
        if (_tooltipEl) return _tooltipEl;
        _tooltipEl = document.createElement("div");
        _tooltipEl.className = "map-tooltip hidden";
        _tooltipEl.setAttribute("role", "tooltip");
        document.body.appendChild(_tooltipEl);
        return _tooltipEl;
    }

    function _statusTextZh(node) {
        const d = node.data();
        if (d.isCurrent) return "当前位置";
        if (d.isMicro) return "微场景入口";
        if (!d.isReachable) {
            // W4: 用后端 unreachable_reason 给精确说法
            const reason = d.unreachableReason;
            if (reason === "locked_door") return "门锁着，先想办法解锁";
            if (reason === "pursuer_lock") return "被追逐者激活，只能逐格移动到相邻场景";
            if (reason === "blocked") return "暂时无法直接到达";
            if (reason === "needs_path") return "需先到达中间的房间";
            if (d.isVisited) return "暂时无法直接到达";
            return "尚未发现的路径";
        }
        if (d.isReachable && !d.isVisited) return "邻接，可前往探索";
        if (d.isReachable && d.isVisited) return "已探索，可返回";
        return "";
    }

    function _tooltipHtml(node) {
        const d = node.data();
        const tags = [];
        if (d.isDanger) tags.push('<span class="map-tooltip-tag map-tooltip-tag--danger">危险</span>');
        if (d.isNpc) tags.push('<span class="map-tooltip-tag map-tooltip-tag--npc">有 NPC</span>');
        if (d.isMicro) tags.push('<span class="map-tooltip-tag map-tooltip-tag--micro">微场景</span>');
        const labelText = String(d.label || "?");
        const floorText = (d.floor != null) ? ("F" + d.floor) : "";
        const status = _statusTextZh(node);
        const tagHtml = tags.length ? '<div class="map-tooltip-tags">' + tags.join("") + "</div>" : "";
        return (
            '<div class="map-tooltip-name">' + _escapeHtml(labelText) + (floorText ? ' <span class="map-tooltip-floor">' + floorText + '</span>' : '') + '</div>' +
            (status ? '<div class="map-tooltip-status">' + _escapeHtml(status) + '</div>' : '') +
            tagHtml
        );
    }

    function _escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
        });
    }

    function _showTooltip(node, evt) {
        const el = _ensureTooltipEl();
        el.innerHTML = _tooltipHtml(node);
        el.classList.remove("hidden");
        // 定位: 用渲染坐标 + 容器 offset
        const cyContainer = document.getElementById(CONTAINER_ID);
        if (!cyContainer) return;
        const rect = cyContainer.getBoundingClientRect();
        const pos = node.renderedPosition();
        const left = rect.left + pos.x + 18 + window.scrollX;
        const top = rect.top + pos.y - 16 + window.scrollY;
        el.style.left = left + "px";
        el.style.top = top + "px";
    }

    function _hideTooltip() {
        if (_tooltipEl) _tooltipEl.classList.add("hidden");
        if (_tooltipTimer) { clearTimeout(_tooltipTimer); _tooltipTimer = null; }
    }

    // ===== 交互绑定 =====

    function _bindInteractions() {
        if (!_cy) return;
        // 单击 / 触屏 tap 节点.
        // 设计原则 (v3.2.1): "出现移动提示" 严格等价于 "能去". 不可达节点 tap 不触发选中,
        // 而是就地弹个 tooltip 让玩家看清原因 (照顾手机端没有 hover). 这样玩家不会被
        // 输入栏上方的提示误导以为能去.
        _cy.on("tap", "node", function (evt) {
            const node = evt.target;
            const data = node.data();
            // 不可达 + 不是当前位置 → 只显示 tooltip, 不走选中流程
            if (data && data.isReachable === false && !data.isCurrent) {
                _showTooltip(node, evt);
                if (_tooltipTimer) clearTimeout(_tooltipTimer);
                _tooltipTimer = setTimeout(_hideTooltip, 1800);
                return;
            }
            if (typeof _onNodeTap === "function") {
                _onNodeTap(node.id(), data);
            }
        });
        // 点击空白处 → 取消选择
        _cy.on("tap", function (evt) {
            if (evt.target === _cy) {
                if (typeof _onNodeTap === "function") _onNodeTap(null, null);
            }
        });
        // PC hover tooltip
        _cy.on("mouseover", "node", function (evt) {
            _showTooltip(evt.target, evt);
        });
        _cy.on("mouseout", "node", function () {
            _hideTooltip();
        });
        // 触屏 long-press → 显示 tooltip 1.5s
        _cy.on("taphold", "node", function (evt) {
            _showTooltip(evt.target, evt);
            if (_tooltipTimer) clearTimeout(_tooltipTimer);
            _tooltipTimer = setTimeout(_hideTooltip, 1500);
        });
        // 拖动 / 缩放时关掉 tooltip
        _cy.on("pan zoom", _hideTooltip);
        // v3.4.0: 拖动 / 缩放 / 节点变化时同步 DOM overlay 位置 (rAF 节流)
        _cy.on("pan zoom render layoutstop add remove data", _scheduleOverlayRender);
    }

    function setOnNodeTap(fn) {
        _onNodeTap = (typeof fn === "function") ? fn : null;
    }

    // ===== Zoom / Pan API (替换旧 mapZoomIn/Out/Reset) =====

    function zoomIn() {
        if (!_cy) return;
        _cy.animate({ zoom: Math.min(_cy.zoom() * 1.2, 3) }, { duration: 150 });
    }
    function zoomOut() {
        if (!_cy) return;
        _cy.animate({ zoom: Math.max(_cy.zoom() / 1.2, 0.3) }, { duration: 150 });
    }
    function zoomReset() {
        if (!_cy) return;
        _cy.animate({ fit: { eles: _cy.elements(), padding: 24 } }, { duration: 200 });
    }

    // 暴露
    window.MapView = {
        init: init,
        destroy: destroy,
        render: render,
        disable: disable,
        enable: enable,
        clear: clear,
        flashCorrupt: flashCorrupt,
        setSelectedTarget: setSelectedTarget,
        setOnNodeTap: setOnNodeTap,
        zoomIn: zoomIn,
        zoomOut: zoomOut,
        zoomReset: zoomReset,
        isEnabled: isEnabled,
        // v3.4.0: 给模组编辑器复用同一套 overlay 渲染. (W3 用)
        // 编辑器侧也用 Cytoscape 实例, 把它的 cy + container 元素喂给 _renderOverlayFor.
        _getCy: function () { return _cy; },
        _scheduleOverlayRender: _scheduleOverlayRender,
        _renderOverlay: _renderOverlay,
        _ICON_DEFAULT_MDI: _ICON_DEFAULT_MDI,
        _AUTO_BADGE_NPC: _AUTO_BADGE_NPC,
        _AUTO_BADGE_DANGER: _AUTO_BADGE_DANGER,
        _AUTO_BADGE_MICRO: _AUTO_BADGE_MICRO,
    };
})();
