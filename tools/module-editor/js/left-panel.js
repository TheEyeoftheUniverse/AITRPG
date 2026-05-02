/**
 * Editor.LeftPanel — 左栏: 模组元信息 + 地图组只读列表
 *
 * 内容:
 *   - 模组元信息只读卡片 (name, module_type, target_rounds, location 数等)
 *   - 地图组列表: 显示当前模组里有哪些 map_group, 每组多少节点. 让作者一眼看出
 *     分组情况. 名字不能在这里改, 想改去 JSON 里直接编辑 location.map_group.
 *
 * Phase 2 待加 (本期不做):
 *   - 模组结构树 (locations / objects / npcs / threat_entities), 点击节点跳到地图
 *   - 字段编辑 (description / exits / map_group inline rename)
 *   - 作者通过结构树新建/删除 location
 */
window.Editor = window.Editor || {};
window.Editor.LeftPanel = (function () {
    "use strict";

    const State = window.Editor.State;
    let _metaEl = null;

    function init() {
        _metaEl = document.getElementById("module-meta");
        if (!_metaEl) {
            console.error("[LeftPanel] module-meta element missing");
            return;
        }
        State.on("module:loaded", _renderMeta);
        State.on("location:changed", _renderMeta);   // visited count 会变
        State.on("map:position-changed", _renderMeta); // 已编辑数会变
        _renderMeta();
    }

    function _esc(s) {
        return String(s == null ? "" : s).replace(/[&<>]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
        });
    }

    /** 统计当前模组的 map_group 分布. 复用 MapCanvas 的派生逻辑保持一致性. */
    function _collectGroups(mod) {
        const MC = window.Editor.MapCanvas;
        if (!MC || !mod || !mod.locations) return [];
        const counts = new Map();
        for (const k in mod.locations) {
            if (!Object.prototype.hasOwnProperty.call(mod.locations, k)) continue;
            const g = MC.deriveMapGroup(mod.locations[k]);
            counts.set(g, (counts.get(g) || 0) + 1);
        }
        const list = Array.from(counts.entries()).map(function (e) {
            return { group: e[0], count: e[1] };
        });
        list.sort(function (a, b) { return MC.compareGroups(a.group, b.group); });
        return list;
    }

    function _renderMeta() {
        if (!_metaEl) return;
        const s = State.getState();
        const mod = s.module;
        if (!mod) {
            _metaEl.innerHTML = '<p class="placeholder-text">请先导入一个模组 JSON</p>';
            return;
        }

        const info = mod.module_info || {};
        const locCount = mod.locations ? Object.keys(mod.locations).length : 0;
        const npcCount = mod.npcs ? Object.keys(mod.npcs).length : 0;
        const threatCount = mod.threat_entities ? Object.keys(mod.threat_entities).length : 0;
        const microCount = mod.micro_scenes ? Object.keys(mod.micro_scenes).length : 0;

        // 统计有多少 location 已经手画了 map_position
        let painted = 0;
        if (mod.locations) {
            for (const k in mod.locations) {
                if (Object.prototype.hasOwnProperty.call(mod.locations, k)) {
                    const mp = (mod.locations[k] || {}).map_position;
                    if (mp && typeof mp === "object" && typeof mp.col === "number") painted++;
                }
            }
        }

        const visitedCount = s.runtime.visitedKeys.size;

        // 地图组列表
        const groups = _collectGroups(mod);
        let groupsHtml = '';
        if (groups.length > 0) {
            groupsHtml =
                '<hr class="meta-sep">' +
                '<div class="meta-section-title">地图组</div>' +
                '<div class="meta-groups">' +
                groups.map(function (g) {
                    return '<div class="meta-group-row">' +
                        '<span class="meta-group-name">' + _esc(g.group) + '</span>' +
                        '<span class="meta-group-count">' + g.count + ' 个</span>' +
                        '</div>';
                }).join('') +
                '</div>' +
                '<div class="meta-hint">想改组名? 编辑 location.map_group 字段; 没填会按 floor 自动派生</div>';
        }

        _metaEl.innerHTML =
            '<div class="meta-row"><span class="meta-key">模组名</span><span class="meta-val">' + _esc(info.name || "(未命名)") + '</span></div>' +
            (info.module_type ? '<div class="meta-row"><span class="meta-key">类型</span><span class="meta-val">' + _esc(info.module_type) + '</span></div>' : '') +
            (info.target_rounds != null ? '<div class="meta-row"><span class="meta-key">目标轮次</span><span class="meta-val">' + _esc(info.target_rounds) + '</span></div>' : '') +
            '<hr class="meta-sep">' +
            '<div class="meta-row"><span class="meta-key">locations</span><span class="meta-val">' + locCount + ' (已手画 ' + painted + ')</span></div>' +
            (npcCount ? '<div class="meta-row"><span class="meta-key">npcs</span><span class="meta-val">' + npcCount + '</span></div>' : '') +
            (threatCount ? '<div class="meta-row"><span class="meta-key">threat_entities</span><span class="meta-val">' + threatCount + '</span></div>' : '') +
            (microCount ? '<div class="meta-row"><span class="meta-key">micro_scenes</span><span class="meta-val">' + microCount + '</span></div>' : '') +
            groupsHtml +
            '<hr class="meta-sep">' +
            '<div class="meta-row"><span class="meta-key">本次走入</span><span class="meta-val">' + visitedCount + ' 个房间</span></div>' +
            (s.runtime.currentLocationKey ? '<div class="meta-row"><span class="meta-key">当前</span><span class="meta-val">' + _esc(s.runtime.currentLocationKey) + '</span></div>' : '') +
            '<div class="meta-hint">导出后可在主游戏里直接加载, 或拷回 modules/ 目录</div>';
    }

    return { init: init };
})();
