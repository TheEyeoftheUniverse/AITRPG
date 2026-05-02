/**
 * Editor.RightPanel — 右栏: 调试器扩展位
 *
 * Phase 1 只放占位提示 + 一个简单的"当前节点信息"卡片 (跟左栏的元信息互补,
 * 这里聚焦在"当前选中/走入的那个节点"). 这样作者拖完一个节点能立刻看到坐标改了.
 *
 * Phase 2 待加 (已有需求, 这里只占位):
 *   - 加物品到玩家背包 (mock)
 *   - 加 flag (mock)
 *   - 测试某条 reveal_conditions / first_entry_blocked / object.requires 是否能命中
 *   - 模拟门锁 / 守卫 / 管家激活 (mock world_state 子集)
 *   - 切换调查员人设 (默认 vs 自定义)
 *
 * 扩展位: panel-extension-slot[data-extension-slot="right-tools"] 已在 HTML 预留.
 */
window.Editor = window.Editor || {};
window.Editor.RightPanel = (function () {
    "use strict";

    const State = window.Editor.State;
    let _slotEl = null;

    function init() {
        _slotEl = document.querySelector('[data-extension-slot="right-tools"]');
        if (!_slotEl) return;
        State.on("location:changed", _renderCurrentInfo);
        State.on("map:position-changed", _renderCurrentInfo);
        State.on("module:loaded", _renderCurrentInfo);
        _renderCurrentInfo();
    }

    function _esc(s) {
        return String(s == null ? "" : s).replace(/[&<>]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
        });
    }

    function _renderCurrentInfo() {
        if (!_slotEl) return;
        const s = State.getState();
        const mod = s.module;
        const key = s.runtime.currentLocationKey;
        if (!mod || !key) {
            _slotEl.innerHTML = '';
            return;
        }
        const loc = (mod.locations || {})[key];
        if (!loc) {
            _slotEl.innerHTML = '';
            return;
        }
        const mp = loc.map_position;
        const mpStr = (mp && typeof mp.col === "number")
            ? '{ col: ' + mp.col + ', row: ' + (typeof mp.row === "number" ? mp.row : 0) + ' }'
            : '(自动布局)';
        _slotEl.innerHTML =
            '<h3 class="panel-section-title">当前节点</h3>' +
            '<div class="panel-section-body">' +
                '<div class="meta-row"><span class="meta-key">key</span><span class="meta-val">' + _esc(key) + '</span></div>' +
                '<div class="meta-row"><span class="meta-key">name</span><span class="meta-val">' + _esc(loc.name || "?") + '</span></div>' +
                '<div class="meta-row"><span class="meta-key">floor</span><span class="meta-val">' + _esc(loc.floor != null ? loc.floor : "?") + '</span></div>' +
                '<div class="meta-row"><span class="meta-key">map_position</span><span class="meta-val">' + _esc(mpStr) + '</span></div>' +
                '<div class="meta-hint">拖动地图节点会更新这里的 map_position</div>' +
            '</div>';
    }

    return { init: init };
})();
