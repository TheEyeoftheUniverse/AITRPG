/**
 * map_view.js — Cytoscape.js 接管的地图视图模块 (v3.2.0)
 *
 * v3.2.0 同步编辑器:
 *   - 节点不再可拖拽 (autoungrabify) — 玩家只能 tap 走入, 不能改地图位置
 *   - map_position 支持浮点 (col, row), 不再强制整数网格
 *   - 按 map_group (字符串) 分带渲染, 没填 map_group 时按 floor 数字派生 ("1F"/"B1"/"GF")
 *
 * 数据契约: render(mapData) 接收
 *   { locations: { key: { display_name, floor, visited, map_position?, map_group?,
 *                         is_micro_scene?, parent_location? } },
 *     edges: [{ from, to, locked }],
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

    let _cy = null;
    let _enabled = false;
    let _lastLayoutKey = "";   // 用于布局缓存 (相同图形不重排)
    let _selectedTarget = null; // 选中目标 key (移动指示用)
    let _onNodeTap = null;     // 外部注入的 tap 回调 (app.js 提供)
    let _tooltipEl = null;     // 浮动 tooltip 元素
    let _tooltipTimer = null;
    let _lastMapData = null;   // 缓存最近一次 mapData, tooltip 拼文案用
    let _anchorKey = null;     // 本会话首次见到的 current_location, 作为稳定 BFS 根; 玩家走动时房间不再左右乱跳

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
     * 七态视觉 + 楼层着色的 cytoscape style 表
     * 设计原则: 每态既有颜色差异, 也有形状/边框/图标差异 (色弱友好, 不仅靠颜色)
     */
    function _buildStyle() {
        return [
            // 节点基础
            {
                selector: "node",
                style: {
                    "label": "data(label)",
                    "color": "#f3ecd8",
                    "font-size": "11px",
                    "font-weight": 600,
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-wrap": "wrap",
                    "text-max-width": "84px",
                    "background-color": "#3a3530",
                    "border-width": 1.5,
                    "border-color": "#7a6a52",
                    "shape": "round-rectangle",
                    "width": 92,
                    "height": 36,
                    "transition-property": "background-color, border-color, border-width",
                    "transition-duration": "180ms"
                }
            },
            // visited (已访问) — 偏绿: "去过, 安全". 跟金色 current 拉开色相, 一眼能区分
            {
                selector: "node.s-visited",
                style: {
                    "background-color": "#324a36",
                    "border-color": "#7aae6c",
                    "border-width": 2,
                    "color": "#dcefcc"
                }
            },
            // fog (未探索, 邻居可见但未到过) — 冷蓝: "未知前线". 旧版偏红, 容易和 danger 混
            {
                selector: "node.s-fog",
                style: {
                    "background-color": "#243440",
                    "border-color": "#5e8aa6",
                    "border-style": "dashed",
                    "border-width": 2,
                    "color": "#b8cee0"
                }
            },
            // locked (无法到达, 但在视野中) — 中性灰: "门锁着 / 路被堵". 不再用红色, 把红色让给 danger
            {
                selector: "node.s-locked",
                style: {
                    "background-color": "#2a2724",
                    "border-color": "#5a5448",
                    "border-style": "dotted",
                    "border-width": 2,
                    "color": "#8a8478"
                }
            },
            // current (当前所在位置) — 金色椭圆 + 大发光圈: 跟绿色 visited 在色相和形状两个维度上区分
            {
                selector: "node.s-current",
                style: {
                    "background-color": "#6a4f2a",
                    "border-color": "#ffd166",
                    "border-width": 4,
                    "shape": "ellipse",
                    "color": "#fff8e0",
                    "font-weight": 700,
                    "font-size": "12px",
                    "shadow-blur": 18,
                    "shadow-color": "#ffd166",
                    "shadow-opacity": 0.85
                }
            },
            // selected (玩家点击选中的目标)
            {
                selector: "node.s-selected",
                style: {
                    "border-color": "#ffe18a",
                    "border-width": 4,
                    "shadow-blur": 16,
                    "shadow-color": "#ffe18a",
                    "shadow-opacity": 0.7
                }
            },
            // danger (管家/威胁存在) — 整个调色板里唯一使用强烈红色的状态, 不会和 locked / fog 混
            {
                selector: "node.s-danger",
                style: {
                    "border-color": "#ee5d3e",
                    "border-width": 3,
                    "background-color": "#4a1d18",
                    "color": "#ffcab8"
                }
            },
            // npc (有 NPC 在此) — 冷蓝叠加状态, 比 fog 略亮以便区分
            {
                selector: "node.s-npc",
                style: {
                    "background-color": "#2c4258",
                    "border-color": "#88b3d6"
                }
            },
            // micro_scene (微场景 / 硬导向入口) — 形状区分
            {
                selector: "node.s-micro",
                style: {
                    "shape": "diamond",
                    "background-color": "#3a304a",
                    "border-color": "#9a7ac4",
                    "width": 80,
                    "height": 50
                }
            },
            // 路径高亮 (W3 给路径节点加 .s-path)
            {
                selector: "node.s-path",
                style: {
                    "background-color": "#4a5a3a",
                    "border-color": "#b8d484"
                }
            },

            // 边基础
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
            // 锁住的边 (虚线 + 红)
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
                    "line-color": "#b8d484",
                    "width": 3,
                    "opacity": 1
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

            const classes = [];
            if (isCurrent) classes.push("s-current");
            else if (!isReachable) classes.push("s-locked");
            else if (isVisited) classes.push("s-visited");
            else classes.push("s-fog");
            if (isDanger) classes.push("s-danger");
            if (isNpc) classes.push("s-npc");
            if (isMicro) classes.push("s-micro");

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
                    parentLocation: loc.parent_location || null,
                    unreachableReason: loc.unreachable_reason || null,
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
            elements.push({
                group: "edges",
                data: {
                    id: e.from + "->" + e.to,
                    source: e.from,
                    target: e.to,
                    locked: Boolean(e.locked)
                },
                classes: e.locked ? "s-locked" : ""
            });
        }
        return elements;
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
        console.log("[MapView] initialized (W3)");
        return true;
    }

    function destroy() {
        if (_cy) {
            try { _cy.destroy(); } catch (e) {}
            _cy = null;
        }
        _swapContainerMode(false);
        _enabled = false;
        _lastLayoutKey = "";
        _selectedTarget = null;
        _anchorKey = null;
    }

    /**
     * 渲染 mapData. W2 实现完整数据→图映射 + 七态视觉 + dagre 布局.
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
        // 增量更新: 取出当前 cy 中的 ids, 跟新 elements 比对; 全量替换实现简单, 后续可优化
        _cy.batch(function () {
            _cy.elements().remove();
            _cy.add(elements);
        });
        _runLayout(false);
        // 选中目标态恢复
        if (_selectedTarget && _cy.getElementById(_selectedTarget).length > 0) {
            _cy.getElementById(_selectedTarget).addClass("s-selected");
        }
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
        _getCy: function () { return _cy; },
    };
})();
