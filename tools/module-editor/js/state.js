/**
 * Editor.State — 单一数据源 + 简单 pub-sub
 *
 * 设计原则:
 *   1. 所有可变数据集中在 state 对象里, 各 panel 不持有自己的副本
 *   2. 写操作走 actions (loadModule / moveTo / ...), 不允许从外部直接 mutate state
 *   3. 状态变化通过 emit 事件广播, panel 各自订阅自己关心的事件
 *
 * 扩展位: 未来加新 action / 新事件不需要改这个文件以外的人; 加 reducer 时也只是
 *         在这里多写一个 setter, 不影响订阅者.
 */
window.Editor = window.Editor || {};
window.Editor.State = (function () {
    "use strict";

    // === 内部 state ===
    const state = {
        // 完整模组 JSON. 拖拽改的就是这块.
        module: null,

        // 编辑器会话内的"运行时", 不写回模组
        runtime: {
            currentLocationKey: null,    // 当前 simulated 位置, null = 还没走入任何房间
            visitedKeys: new Set()       // 本次会话点开过的房间
        },

        // 纯 UI 状态
        ui: {
            anchorKey: null,             // 布局 BFS 根, 跟主游戏地图一致
            selectedNodeKey: null        // 当前 UI 选中节点 (跟 currentLocationKey 区分: 选中 ≠ 走入)
        }
    };

    // === Pub-sub ===
    const subscribers = new Map();   // event -> Set<callback>

    function on(event, callback) {
        if (!subscribers.has(event)) subscribers.set(event, new Set());
        subscribers.get(event).add(callback);
        return function unsubscribe() {
            const s = subscribers.get(event);
            if (s) s.delete(callback);
        };
    }

    function emit(event, payload) {
        const s = subscribers.get(event);
        if (!s) return;
        s.forEach(function (cb) {
            try { cb(payload); } catch (e) { console.error("[State] subscriber error for", event, e); }
        });
    }

    function getState() {
        // 返回 state 引用本身. 调用方约定为只读, 不直接 mutate.
        // (没有用 deep freeze 是为了避免每次拿都付出克隆代价; Phase 2 加复杂业务逻辑时再考虑)
        return state;
    }

    // === Actions ===

    /**
     * 加载完整模组 JSON, 替换当前 module 状态, 清空运行时.
     * 触发: module:loaded
     */
    function loadModule(moduleJson) {
        state.module = moduleJson;
        state.runtime.currentLocationKey = null;
        state.runtime.visitedKeys = new Set();
        state.ui.anchorKey = null;
        state.ui.selectedNodeKey = null;
        emit("module:loaded", { module: moduleJson });
    }

    /**
     * "走入"某个 location. 不调任何 LLM, 仅更新 currentLocationKey + visitedKeys.
     * 触发: location:changed
     */
    function moveTo(locationKey) {
        if (!state.module || !state.module.locations || !state.module.locations[locationKey]) {
            console.warn("[State] moveTo: unknown location", locationKey);
            return;
        }
        state.runtime.currentLocationKey = locationKey;
        state.runtime.visitedKeys.add(locationKey);
        // anchor 沿用主游戏逻辑: 首次走入的位置当稳定 BFS 根
        if (!state.ui.anchorKey) {
            state.ui.anchorKey = locationKey;
        }
        emit("location:changed", { key: locationKey });
    }

    /**
     * 拖拽地图节点松手时调用, 更新 module.locations[key].map_position.
     * (col, row) 现在允许浮点 — 编辑器完全放开了吸附 / 整数对齐, 作者拖到哪儿
     * 就存到哪儿. 整数也是浮点的特例, 旧模组的 (col:0, row:0) 整数照常工作.
     * 触发: map:position-changed
     */
    function setMapPosition(locationKey, col, row) {
        if (!state.module || !state.module.locations || !state.module.locations[locationKey]) return;
        const loc = state.module.locations[locationKey];
        loc.map_position = { col: Number(col), row: Number(row) };
        emit("map:position-changed", { key: locationKey, position: loc.map_position });
    }

    /**
     * 选中节点 (UI hover/click 但不一定走入)
     * 触发: ui:selection-changed
     */
    function selectNode(locationKey) {
        state.ui.selectedNodeKey = locationKey;
        emit("ui:selection-changed", { key: locationKey });
    }

    /**
     * 写一个 location 的视觉字段 (icon / displayColor / displayAlpha / badges).
     * patch 里某键为 undefined 表示"清空, 让它走默认"; null 也按清空处理 (对应序列化时这个键不出现).
     * 整数 / 浮点 alpha 自动 clamp 到 [0, 1]. badges 必须是数组, 否则忽略.
     * 触发: location:visual-changed (供右栏 / 主画布 (将来) 监听)
     */
    function setLocationVisual(locationKey, patch) {
        if (!state.module || !state.module.locations || !state.module.locations[locationKey]) return;
        const loc = state.module.locations[locationKey];
        if (!patch || typeof patch !== "object") return;
        if ("icon" in patch) {
            const v = patch.icon;
            if (typeof v === "string" && v.trim()) loc.icon = v.trim();
            else delete loc.icon;
        }
        if ("displayColor" in patch) {
            const v = patch.displayColor;
            if (typeof v === "string" && v.trim()) loc.displayColor = v.trim();
            else delete loc.displayColor;
        }
        if ("displayAlpha" in patch) {
            const v = patch.displayAlpha;
            if (typeof v === "number" && isFinite(v)) {
                loc.displayAlpha = Math.max(0, Math.min(1, v));
            } else {
                delete loc.displayAlpha;
            }
        }
        if ("badges" in patch) {
            const v = patch.badges;
            if (Array.isArray(v) && v.length > 0) {
                loc.badges = v.map(function (b) {
                    if (!b || typeof b !== "object") return null;
                    if (typeof b.icon !== "string" || !b.icon.trim()) return null;
                    const out = { icon: b.icon.trim() };
                    if (typeof b.color === "string" && b.color.trim()) out.color = b.color.trim();
                    if (typeof b.position === "string" && ["tr", "tl", "br", "bl"].indexOf(b.position) >= 0) out.position = b.position;
                    return out;
                }).filter(Boolean);
                if (!loc.badges.length) delete loc.badges;
            } else {
                delete loc.badges;
            }
        }
        emit("location:visual-changed", { key: locationKey });
    }

    /**
     * 写一条 exit 的连线视觉字段. 旧字符串形式按需自动升级为 dict.
     *   patch: { style?, directed?, direction? }
     *   如果 patch 把 style 设回 "solid" 且没有 directed/direction 覆盖, 这条 exit 会被
     *   反向规整化回字符串形式 (保留 round-trip 的最小磁盘形态).
     * 触发: location:visual-changed (复用同一个事件, payload.exitIndex 区分)
     */
    function setExitVisual(locationKey, exitIndex, patch) {
        if (!state.module || !state.module.locations || !state.module.locations[locationKey]) return;
        const loc = state.module.locations[locationKey];
        if (!Array.isArray(loc.exits) || exitIndex < 0 || exitIndex >= loc.exits.length) return;
        if (!patch || typeof patch !== "object") return;
        let entry = loc.exits[exitIndex];
        // 升级为 dict
        if (typeof entry === "string") {
            entry = { to: entry };
        } else {
            entry = Object.assign({}, entry);
            if (typeof entry.to !== "string" || !entry.to) {
                // 不完整的 exit 不动
                return;
            }
        }
        if ("style" in patch) {
            const v = patch.style;
            if (typeof v === "string" && ["solid", "dashed", "double", "single-arrow"].indexOf(v) >= 0) {
                entry.style = v;
            } else {
                delete entry.style;
            }
        }
        if ("directed" in patch) {
            entry.directed = !!patch.directed;
            if (!entry.directed) delete entry.directed;
        }
        if ("direction" in patch) {
            const v = patch.direction;
            if (typeof v === "string" && (v === "to" || v === "from")) entry.direction = v;
            else delete entry.direction;
        }
        // 保持 semantic 一致性: single-arrow 总是 directed.
        if (entry.style === "single-arrow") {
            entry.directed = true;
        }
        // 清理: 非 single-arrow → 删 directed / direction 默认
        if (entry.style !== "single-arrow") {
            delete entry.directed;
            delete entry.direction;
        }
        // 清理: style=solid 是默认值, 剥掉
        if (entry.style === "solid") delete entry.style;
        // 清理: direction=to 时剥掉(默认值)
        if (entry.direction === "to") delete entry.direction;
        // 简化: 只剩 to 时回退为字符串
        const keys = Object.keys(entry).filter(function (k) { return k !== "to"; });
        if (keys.length === 0) {
            loc.exits[exitIndex] = entry.to;
        } else {
            loc.exits[exitIndex] = entry;
        }
        emit("location:visual-changed", { key: locationKey, exitIndex: exitIndex });
    }

    /** 清空所有状态, 回到刚打开页面的形态. */
    function reset() {
        state.module = null;
        state.runtime.currentLocationKey = null;
        state.runtime.visitedKeys = new Set();
        state.ui.anchorKey = null;
        state.ui.selectedNodeKey = null;
        emit("module:loaded", { module: null });   // 复用 module:loaded, 各 panel 把空状态当"清空"处理
    }

    // === 暴露 ===
    return {
        getState: getState,
        on: on,
        emit: emit,                 // 暴露给将来的扩展, 比如自定义事件
        loadModule: loadModule,
        moveTo: moveTo,
        setMapPosition: setMapPosition,
        selectNode: selectNode,
        setLocationVisual: setLocationVisual,
        setExitVisual: setExitVisual,
        reset: reset
    };
})();
