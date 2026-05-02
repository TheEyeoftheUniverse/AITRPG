/**
 * Editor.ScenePanel — 中间下方的场景硬文本展示
 *
 * 数据全部来自 module.locations[currentLocationKey], 不调任何 LLM.
 * 展示字段约定:
 *   - 标题区: name + floor + atmosphere 数值
 *   - description (主区, 可包含 <system-echo> 等 inline 标签, 这里直接以纯文本展示, 让作者一眼看到原始)
 *   - objects[]
 *   - exits[] (显示名)
 *   - npc_present_description / threat_present_description (始终展示, 让作者自己核对触发条件)
 *   - first_entry_blocked.text (仅当 not visited)
 *   - npc_reactions (按 NPC 名分组, 展示 follow_arrival / knowledge)
 *   - 状态徽章: hidden / has_door / is_ending_location / has_butler 等
 */
window.Editor = window.Editor || {};
window.Editor.ScenePanel = (function () {
    "use strict";

    const State = window.Editor.State;
    let _root = null;

    function init(rootId) {
        _root = document.getElementById(rootId);
        if (!_root) {
            console.error("[ScenePanel] root not found:", rootId);
            return;
        }
        State.on("location:changed", _render);
        State.on("module:loaded", _render);
        _render();
    }

    function _esc(s) {
        return String(s == null ? "" : s).replace(/[&<>]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
        });
    }

    function _render() {
        if (!_root) return;
        const s = State.getState();
        const mod = s.module;
        const key = s.runtime.currentLocationKey;

        if (!mod || !mod.locations) {
            _root.innerHTML = '<p class="placeholder-text">点击地图上的房间走入, 这里会显示场景的硬文本 (description / objects / exits 等), 不调 LLM</p>';
            return;
        }
        if (!key || !mod.locations[key]) {
            _root.innerHTML = '<p class="placeholder-text">点击任一房间走入</p>';
            return;
        }

        const loc = mod.locations[key];
        const isVisited = s.runtime.visitedKeys.has(key);

        const badges = [];
        if (loc.hidden) badges.push('<span class="badge badge-warn">hidden</span>');
        if (loc.has_door) badges.push('<span class="badge">has_door</span>');
        if (loc.has_butler) badges.push('<span class="badge badge-danger">has_butler</span>');
        if (loc.is_ending_location) badges.push('<span class="badge badge-accent">ending: ' + _esc(loc.ending_id || "?") + '</span>');
        if (loc.npc_location) badges.push('<span class="badge">npc_location</span>');
        if (loc.sancheck) badges.push('<span class="badge badge-warn">sancheck: ' + _esc(loc.sancheck) + '</span>');

        const sections = [];

        // 标题
        sections.push(
            '<div class="scene-header">' +
                '<div class="scene-title">' + _esc(loc.name || key) + ' <span class="scene-key">[' + _esc(key) + ']</span></div>' +
                '<div class="scene-meta">' +
                    'floor: ' + _esc(loc.floor != null ? loc.floor : "?") +
                    (loc.atmosphere != null ? ' · atmosphere: ' + _esc(loc.atmosphere) : '') +
                '</div>' +
                (badges.length ? '<div class="scene-badges">' + badges.join("") + '</div>' : '') +
            '</div>'
        );

        // 描述
        if (loc.description) {
            sections.push(
                '<div class="scene-block">' +
                    '<div class="scene-block-title">描述</div>' +
                    '<pre class="scene-text">' + _esc(loc.description) + '</pre>' +
                '</div>'
            );
        }

        // 首次进入文本 (only if 还没 visited)
        if (!isVisited && loc.first_entry_blocked && loc.first_entry_blocked.text) {
            const mode = loc.first_entry_blocked.mode || "block";
            sections.push(
                '<div class="scene-block scene-block-warn">' +
                    '<div class="scene-block-title">首次进入触发文本 (mode: ' + _esc(mode) + ')</div>' +
                    '<pre class="scene-text">' + _esc(loc.first_entry_blocked.text) + '</pre>' +
                '</div>'
            );
        }

        // NPC 在场动态描述
        if (loc.npc_present_description) {
            sections.push(
                '<div class="scene-block">' +
                    '<div class="scene-block-title">npc_present_description (NPC 在场时追加)</div>' +
                    '<pre class="scene-text">' + _esc(loc.npc_present_description) + '</pre>' +
                '</div>'
            );
        }

        // 威胁在场动态描述
        const threatDesc = loc.threat_present_description || loc.entity_present_description;
        if (threatDesc) {
            sections.push(
                '<div class="scene-block scene-block-warn">' +
                    '<div class="scene-block-title">threat_present_description (威胁在场时追加)</div>' +
                    '<pre class="scene-text">' + _esc(threatDesc) + '</pre>' +
                '</div>'
            );
        }

        // 可见物品
        if (Array.isArray(loc.objects) && loc.objects.length > 0) {
            sections.push(
                '<div class="scene-block">' +
                    '<div class="scene-block-title">可见物品 (objects)</div>' +
                    '<ul class="scene-list">' +
                        loc.objects.map(function (o) { return '<li>' + _esc(o) + '</li>'; }).join("") +
                    '</ul>' +
                '</div>'
            );
        }

        // 出口 (按显示名, 跟模组里 exits 字段一致)
        if (Array.isArray(loc.exits) && loc.exits.length > 0) {
            sections.push(
                '<div class="scene-block">' +
                    '<div class="scene-block-title">出口 (exits)</div>' +
                    '<ul class="scene-list">' +
                        loc.exits.map(function (e) { return '<li>' + _esc(e) + '</li>'; }).join("") +
                    '</ul>' +
                '</div>'
            );
        }

        // NPC 到场反应
        if (loc.npc_reactions && typeof loc.npc_reactions === "object") {
            const npcKeys = Object.keys(loc.npc_reactions);
            if (npcKeys.length > 0) {
                const items = npcKeys.map(function (npcName) {
                    const r = loc.npc_reactions[npcName] || {};
                    const lines = [];
                    if (r.follow_arrival) lines.push('<div><span class="scene-sub">follow_arrival:</span> ' + _esc(r.follow_arrival) + '</div>');
                    if (r.knowledge) lines.push('<div><span class="scene-sub">knowledge:</span> ' + _esc(r.knowledge) + '</div>');
                    return '<div class="scene-npc-reaction"><div class="scene-npc-name">' + _esc(npcName) + '</div>' + lines.join("") + '</div>';
                });
                sections.push(
                    '<div class="scene-block">' +
                        '<div class="scene-block-title">NPC 到场反应 (npc_reactions)</div>' +
                        items.join("") +
                    '</div>'
                );
            }
        }

        // 隐藏条件
        if (loc.reveal_conditions) {
            sections.push(
                '<div class="scene-block scene-block-meta">' +
                    '<div class="scene-block-title">显现条件 (reveal_conditions)</div>' +
                    '<pre class="scene-text scene-text-mono">' + _esc(JSON.stringify(loc.reveal_conditions, null, 2)) + '</pre>' +
                '</div>'
            );
        }

        // hidden_name 提示
        if (loc.hidden_name) {
            sections.push(
                '<div class="scene-block scene-block-meta">' +
                    '<div class="scene-block-title">表名 (hidden_name)</div>' +
                    '<div class="scene-text">' + _esc(loc.hidden_name) + (loc.show_name_when_visible ? ' · show_name_when_visible: true' : '') + '</div>' +
                '</div>'
            );
        }

        _root.innerHTML = sections.join("");
    }

    return { init: init };
})();
