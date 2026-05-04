/**
 * Editor.MapCanvas — Cytoscape 接管的编辑器地图
 *
 * 跟主游戏 webui/static/js/map_view.js 的关系:
 *   - 主游戏按 location.floor (数字) 分带渲染, 编辑器按 location.map_group (字符串)
 *     分组渲染. 没设 map_group 时编辑器从 floor 自动派生 ("1F" / "B1" / "GF" / 等).
 *   - 主游戏不允许拖拽; 编辑器允许作者完全自由拖拽, 不做任何吸附 / 边界 clamp /
 *     联动重排.
 *   - 编辑器渲染所有 location (含 hidden), 主游戏只渲染可见的.
 *
 * 自由编辑原则 (作者要求):
 *   - 任意位置: 拖到哪儿就停在哪儿, 不限 Y, 不吸附整数网格;
 *   - 不联动: 拖一个节点不会让其他节点跟着动;
 *   - map_position 直接存浮点 (col, row), 整数也是合法的特例;
 *   - 跨"楼层 / 区域"拖动: 节点视觉位置改变, 但 map_group 不会自动改写; 想换组
 *     得作者手动改 JSON. 这是有意为之 — 编辑器只忠实呈现作者填的字段.
 *   - 地图组只是视觉提示 (节点底部显示组名 + 同色描边), 不限制编辑.
 */
window.Editor = window.Editor || {};
window.Editor.MapCanvas = (function () {
    "use strict";

    const State = window.Editor.State;

    // === 布局常量 ===
    const ROW_HEIGHT = 78;
    const COL_WIDTH = 108;
    const PAD_LEFT = 56;
    const PAD_TOP = 36;
    const GROUP_GAP = 32;   // 不同 map_group 之间额外的垂直留白
    const OVERLAY_ID = "editor-map-node-overlay";
    const ICON_DEFAULT_MDI = "mdi-door";
    const BADGE_POSITIONS = ["tr", "tl", "br", "bl"];

    // === 模块状态 ===
    let _cy = null;
    let _overlayEl = null;
    let _overlayPending = false;
    // group (字符串) -> { yOffsetRef } — 仅用于 dragfree 时把视觉 (x, y) 反推回相对该组基线的 (col, row).
    // 不再保留 dragMinY/dragMaxY 之类的硬限制, 因为已经放开拖拽.
    let _groupBands = new Map();
    let _suspendDragHandlers = false;

    // ─── 公共: 从 location 数据派生 map_group ───
    // 优先级: location.map_group (字符串, 作者自定义) > floor 数字自动派生 > 默认 "1F"
    function deriveMapGroup(loc) {
        if (!loc) return "1F";
        const explicit = loc.map_group;
        if (typeof explicit === "string" && explicit.trim()) return explicit.trim();
        const f = loc.floor;
        if (typeof f !== "number" || !isFinite(f)) return "1F";
        if (f === 0) return "GF";
        if (f < 0) return "B" + Math.abs(f);
        return f + "F";
    }

    // 把 group 字符串转成排序键: 能识别成数字楼层的按楼层数, 否则按字母. 楼层高的排在上方.
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
        // 数字组按楼层数降序在前; 自定义字符串组按字母升序在后
        if (ka.numeric && kb.numeric) return kb.value - ka.value;
        if (ka.numeric && !kb.numeric) return -1;
        if (!ka.numeric && kb.numeric) return 1;
        return String(ka.value).localeCompare(String(kb.value));
    }

    function _locationAccent(loc) {
        if (loc && typeof loc.displayColor === "string" && loc.displayColor.trim()) {
            return loc.displayColor.trim();
        }
        return "#8b6f4e";
    }

    function _locationIcon(loc, fallback) {
        if (loc && typeof loc.icon === "string" && loc.icon.trim()) return loc.icon.trim();
        return fallback || ICON_DEFAULT_MDI;
    }

    function _normalizeBadges(badges, extraBadges) {
        const raw = [];
        if (Array.isArray(extraBadges)) raw.push.apply(raw, extraBadges);
        if (Array.isArray(badges)) {
            badges.forEach(function (b) {
                if (b && typeof b === "object" && typeof b.icon === "string" && b.icon.trim()) raw.push(b);
            });
        }
        const used = new Set();
        const out = [];
        raw.forEach(function (b) {
            if (typeof b.position === "string" && BADGE_POSITIONS.indexOf(b.position) >= 0 && !used.has(b.position)) {
                out.push({ icon: b.icon.trim(), color: b.color || "", position: b.position });
                used.add(b.position);
            }
        });
        raw.forEach(function (b) {
            if (typeof b.position === "string" && BADGE_POSITIONS.indexOf(b.position) >= 0) return;
            for (let i = 0; i < BADGE_POSITIONS.length; i++) {
                const p = BADGE_POSITIONS[i];
                if (!used.has(p)) {
                    out.push({ icon: b.icon.trim(), color: b.color || "", position: p });
                    used.add(p);
                    break;
                }
            }
        });
        return out;
    }

    function _buildStyle() {
        return [
            {
                selector: "node",
                style: {
                    "label": "",
                    "shape": "ellipse",
                    "width": 104,
                    "height": 104,
                    "background-color": "#8b6f4e",
                    "background-opacity": 0,
                    "border-width": 0,
                    "border-opacity": 0,
                    "overlay-opacity": 0
                }
            },
            // 已访问 — 视觉交给 DOM overlay
            {
                selector: "node.s-visited",
                style: {}
            },
            // 未访问 fog — 只显示问号 overlay
            {
                selector: "node.s-fog",
                style: {}
            },
            // 当前位置 — DOM overlay 圆环呼吸
            {
                selector: "node.s-current",
                style: {}
            },
            // 隐藏房间 — 整体虚化, 可见提示由 overlay opacity 体现
            {
                selector: "node.s-editor-hidden",
                style: {
                    "opacity": 0.45
                }
            },
            // 微场景 — 保持圆形 hitbox, 用角标表达
            {
                selector: "node.s-micro",
                style: {}
            },
            // 拖拽中
            {
                selector: "node:grabbed",
                style: {
                    "overlay-opacity": 0
                }
            },
            // 边
            {
                selector: "edge",
                style: {
                    "width": 2,
                    "line-color": "#5a4e3e",
                    "curve-style": "bezier",
                    "target-arrow-shape": "none",
                    "opacity": 0.7
                }
            },
            {
                selector: "edge.s-locked",
                style: {
                    "line-color": "#8a4838",
                    "line-style": "dashed",
                    "width": 1.5,
                    "opacity": 0.55
                }
            }
        ];
    }

    /** 初始化 Cytoscape (只调一次) */
    function init(containerId) {
        const container = document.getElementById(containerId);
        if (!container) {
            console.error("[MapCanvas] container not found:", containerId);
            return;
        }
        _cy = cytoscape({
            container: container,
            elements: [],
            style: _buildStyle(),
            wheelSensitivity: 0.3,
            minZoom: 0.3,
            maxZoom: 3.0,
            boxSelectionEnabled: false
        });
        _ensureOverlayEl(container);
        _bindInteractions();
        _cy.on("pan zoom render layoutstop add remove data position", _scheduleOverlayRender);

        // 模组变 → 重建; 走入变 → 改高亮.
        // map:position-changed 故意不重排: 拖一个节点不应该让其他节点跟着动.
        State.on("module:loaded", function () { _rerenderAll(); });
        State.on("location:changed", function () { _refreshNodeStates(); });
        State.on("location:visual-changed", function () { _rerenderAll(); });
    }

    function _rerenderAll() {
        if (!_cy) return;
        _cy.batch(function () {
            _cy.elements().remove();
            const elements = _buildElements();
            _cy.add(elements);
            _refreshNodeStates();
        });
        _runLayout();
        _cy.fit(_cy.elements(), 24);
        _scheduleOverlayRender();
    }

    /** 从完整 module JSON 构造 cytoscape elements (所有 location, 含 hidden) */
    function _buildElements() {
        const s = State.getState();
        const mod = s.module;
        if (!mod || !mod.locations) return [];

        const locations = mod.locations;
        const microScenes = (mod.micro_scenes && typeof mod.micro_scenes === "object") ? mod.micro_scenes : {};

        // 模组里 exits 填的是显示名, 需要建 displayName -> key 映射
        const nameToKey = {};
        for (const k in locations) {
            if (!Object.prototype.hasOwnProperty.call(locations, k)) continue;
            const loc = locations[k];
            if (loc && typeof loc.name === "string") nameToKey[loc.name] = k;
        }

        const elements = [];

        for (const key in locations) {
            if (!Object.prototype.hasOwnProperty.call(locations, key)) continue;
            const loc = locations[key] || {};
            // map_position 现在允许浮点 (作者自由拖拽), 但兼容旧整数
            const mp = (loc.map_position && typeof loc.map_position === "object" && typeof loc.map_position.col === "number")
                ? { col: Number(loc.map_position.col), row: Number(loc.map_position.row || 0) }
                : null;
            elements.push({
                group: "nodes",
                data: {
                    id: key,
                    label: loc.name || key,
                    mapGroup: deriveMapGroup(loc),
                    isHidden: Boolean(loc.hidden),
                    isMicro: false,
                    iconMdi: _locationIcon(loc, ICON_DEFAULT_MDI),
                    bgColor: _locationAccent(loc),
                    badges: _normalizeBadges(loc.badges, []),
                    mapPosition: mp
                }
            });
        }

        // 微场景节点 (用 parent 的 mapGroup)
        for (const microId in microScenes) {
            if (!Object.prototype.hasOwnProperty.call(microScenes, microId)) continue;
            const cfg = microScenes[microId] || {};
            const parentKey = String(cfg.parent_location || "").trim();
            if (!parentKey || !locations[parentKey]) continue;
            const microBadges = [{ icon: "mdi-flash", color: "#b08a3e" }];
            elements.push({
                group: "nodes",
                data: {
                    id: microId,
                    label: cfg.display_name || cfg.name || microId,
                    mapGroup: deriveMapGroup(locations[parentKey]),
                    isHidden: false,
                    isMicro: true,
                    parentLocation: parentKey,
                    iconMdi: _locationIcon(cfg, "mdi-flash"),
                    bgColor: _locationAccent(cfg),
                    badges: _normalizeBadges(cfg.badges, microBadges),
                    mapPosition: null
                }
            });
        }

        // 边: 从每个 location 的 exits 反查
        const seen = new Set();
        for (const key in locations) {
            if (!Object.prototype.hasOwnProperty.call(locations, key)) continue;
            const exits = Array.isArray(locations[key].exits) ? locations[key].exits : [];
            for (const exitName of exits) {
                const neighborKey = nameToKey[exitName];
                if (!neighborKey || !locations[neighborKey]) continue;
                const pair = [key, neighborKey].sort().join("::");
                if (seen.has(pair)) continue;
                seen.add(pair);
                elements.push({
                    group: "edges",
                    data: { id: key + "->" + neighborKey, source: key, target: neighborKey }
                });
            }
        }

        // 微场景的边: parent → micro
        for (const microId in microScenes) {
            if (!Object.prototype.hasOwnProperty.call(microScenes, microId)) continue;
            const parentKey = String(microScenes[microId].parent_location || "").trim();
            if (!parentKey || !locations[parentKey]) continue;
            elements.push({
                group: "edges",
                data: { id: parentKey + "->" + microId, source: parentKey, target: microId }
            });
        }

        return elements;
    }

    /** 初始布局: 给作者一个起点. 之后所有拖拽都不再触发任何重排. */
    function _runLayout() {
        if (!_cy) return;
        const nodes = _cy.nodes();
        if (nodes.length === 0) return;

        const s = State.getState();

        // 1) 按 mapGroup (字符串) 分组
        const byGroup = new Map();
        nodes.forEach(function (node) {
            const g = String(node.data("mapGroup") || "1F");
            if (!byGroup.has(g)) byGroup.set(g, []);
            byGroup.get(g).push(node);
        });

        // 2) BFS 序作为 auto 节点的兜底排序依据
        const visitOrder = new Map();
        let bfsRootKey = s.ui.anchorKey;
        if (!bfsRootKey || !_cy.getElementById(bfsRootKey).length) {
            const first = nodes[0];
            if (first) bfsRootKey = first.id();
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

        // 3) 组排序: 数字楼层降序在上, 自定义字符串组按字母升序在下
        const sortedGroups = Array.from(byGroup.keys()).sort(_compareGroups);

        // 4) 计算每组节点位置, 同时记录每组的 yOffsetRef (= 该组 row=0 对应的 y)
        const positions = {};
        _groupBands = new Map();
        let yOffset = PAD_TOP;

        sortedGroups.forEach(function (group) {
            const groupNodes = byGroup.get(group).slice();
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
            auto.sort(function (a, b) {
                const ao = visitOrder.has(a.id()) ? visitOrder.get(a.id()) : 1e9;
                const bo = visitOrder.has(b.id()) ? visitOrder.get(b.id()) : 1e9;
                if (ao !== bo) return ao - bo;
                return String(a.id()).localeCompare(String(b.id()));
            });
            let nextAutoCol = 0;
            if (explicit.length > 0) {
                let maxCol = -Infinity;
                explicit.forEach(function (e) { if (e.col > maxCol) maxCol = e.col; });
                nextAutoCol = Math.ceil(maxCol) + 1;
            }
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

            // 这组的 row 跨度 (含负 row)
            let minRow = 0, maxRow = 0;
            explicit.forEach(function (e) {
                if (e.row < minRow) minRow = e.row;
                if (e.row > maxRow) maxRow = e.row;
            });

            _groupBands.set(group, { yOffsetRef: yOffset });

            // 推下一组的 yOffset: maxRow+1 行 + 组间留白
            yOffset += (maxRow + 1) * ROW_HEIGHT + GROUP_GAP;
        });

        const layout = _cy.layout({
            name: "preset",
            positions: positions,
            fit: false,
            padding: 18,
            animate: false
        });
        layout.run();
        _scheduleOverlayRender();
    }

    function _ensureOverlayEl(container) {
        const host = container || document.getElementById("map-canvas");
        if (!host) return null;
        if (_overlayEl && host.contains(_overlayEl)) return _overlayEl;
        let el = host.querySelector(":scope > #" + OVERLAY_ID);
        if (!el) {
            el = document.createElement("div");
            el.id = OVERLAY_ID;
            el.className = "map-node-overlay editor-map-node-overlay";
            host.appendChild(el);
        }
        _overlayEl = el;
        return el;
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
        const s = State.getState();
        const currentKey = s.runtime.currentLocationKey;
        const visited = s.runtime.visitedKeys;
        const zoom = _cy.zoom();
        const seen = new Set();
        _cy.nodes().forEach(function (node) {
            const id = node.id();
            const data = node.data();
            seen.add(id);
            const isCurrent = id === currentKey;
            const isVisited = visited.has(id);
            const isFog = !isCurrent && !isVisited;
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

            const cls = ["map-node-card"];
            if (isCurrent) cls.push("is-current");
            if (isFog) cls.push("is-fog");
            if (node.hasClass("s-editor-hidden")) cls.push("is-locked");
            if (node.hasClass("s-micro")) cls.push("is-micro");
            card.className = cls.join(" ");
            const visualW = Math.min(w, Math.max(54, Math.round((data.isMicro ? 68 : 76) * zoom)));
            const visualH = Math.min(h, Math.max(54, Math.round((data.isMicro ? 68 : 76) * zoom)));
            card.style.left = (pos.x - visualW / 2) + "px";
            card.style.top = (pos.y - visualH / 2) + "px";
            card.style.width = visualW + "px";
            card.style.height = visualH + "px";
            card.style.setProperty("--map-node-accent", data.bgColor || "#8b6f4e");

            const iconPx = Math.max(36, Math.round((isFog ? 68 : 60) * zoom));
            const labelText = isFog ? "" : (data.label || "");
            const labelLen = Array.from(labelText).length;
            let labelBase = 12;
            if (labelLen > 8) labelBase = 8.5;
            else if (labelLen > 6) labelBase = 9.5;
            else if (labelLen > 4) labelBase = 10.5;
            const labelPx = Math.max(8, Math.round(labelBase * zoom));
            const badgePx = Math.max(8, Math.round(12 * zoom));
            const iconEl = card.querySelector(".map-node-icon");
            const qEl = card.querySelector(".map-node-q");
            if (isFog) {
                iconEl.className = "map-node-icon";
                iconEl.style.display = "none";
                qEl.style.display = "";
                qEl.style.fontSize = iconPx + "px";
            } else {
                iconEl.className = "map-node-icon mdi " + (data.iconMdi || ICON_DEFAULT_MDI);
                iconEl.style.display = "";
                iconEl.style.fontSize = iconPx + "px";
                qEl.style.display = "none";
            }
            if (isCurrent) {
                iconEl.classList.add("is-current");
                qEl.classList.add("is-current");
            } else {
                iconEl.classList.remove("is-current");
                qEl.classList.remove("is-current");
            }

            const labelEl = card.querySelector(".map-node-label");
            labelEl.textContent = labelText;
            labelEl.style.fontSize = labelPx + "px";

            const badgesEl = card.querySelector(".map-node-badges");
            badgesEl.innerHTML = "";
            const badges = Array.isArray(data.badges) ? data.badges : [];
            badges.forEach(function (b) {
                if (!b || typeof b.icon !== "string") return;
                const span = document.createElement("span");
                span.className = "map-node-badge mdi " + b.icon + " pos-" + (b.position || "tr");
                span.style.fontSize = badgePx + "px";
                if (b.color) span.style.color = b.color;
                badgesEl.appendChild(span);
            });
        });

        const cards = overlay.querySelectorAll(".map-node-card");
        for (let i = 0; i < cards.length; i++) {
            const id = cards[i].getAttribute("data-node-id");
            if (!seen.has(id)) cards[i].parentNode.removeChild(cards[i]);
        }
    }

    function _cssEscape(s) {
        if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(s);
        return String(s).replace(/(["\\\]\[])/g, "\\$1");
    }

    /** 仅根据当前 state 更新节点的视觉态 class, 不重新跑布局 */
    function _refreshNodeStates() {
        if (!_cy) return;
        const s = State.getState();
        const currentKey = s.runtime.currentLocationKey;
        const visited = s.runtime.visitedKeys;
        _cy.nodes().forEach(function (node) {
            node.removeClass("s-current s-visited s-editor-hidden s-micro");
            const key = node.id();
            if (key === currentKey) {
                node.addClass("s-current");
            } else if (visited.has(key)) {
                node.addClass("s-visited");
            }
            if (node.data("isHidden")) node.addClass("s-editor-hidden");
            if (node.data("isMicro")) node.addClass("s-micro");
        });
        _scheduleOverlayRender();
    }

    function _bindInteractions() {
        if (!_cy) return;

        // 单击节点 → 走入
        _cy.on("tap", "node", function (evt) {
            if (_suspendDragHandlers) return;
            const node = evt.target;
            State.moveTo(node.id());
        });

        // 拖拽: 完全自由, 不限 Y, 不吸附, 不重排, 不联动. 松手时把当前 (x, y) 反推回相对所属
        // map_group 基线的浮点 (col, row), 直接写入 location.map_position. 整数是浮点的特例.
        _cy.on("dragfree", "node", function (evt) {
            const node = evt.target;
            if (node.data("isMicro")) return;   // 微场景目前不支持手画
            const band = _groupBands.get(String(node.data("mapGroup") || "1F"));
            const yOffsetRef = band ? band.yOffsetRef : PAD_TOP;
            const pos = node.position();
            // 浮点存储, 不 round — 完全放开吸附
            const col = (pos.x - PAD_LEFT) / COL_WIDTH;
            const row = (pos.y - yOffsetRef) / ROW_HEIGHT;

            _suspendDragHandlers = true;
            try {
                State.setMapPosition(node.id(), col, row);
            } finally {
                _suspendDragHandlers = false;
            }
            _scheduleOverlayRender();
        });
    }

    function fit() {
        if (_cy) {
            _cy.fit(_cy.elements(), 24);
            _scheduleOverlayRender();
        }
    }

    return {
        init: init,
        fit: fit,
        deriveMapGroup: deriveMapGroup,   // 给 left-panel 列表用
        compareGroups: _compareGroups,    // 给 left-panel 列表排序用
        _getCy: function () { return _cy; }
    };
})();
