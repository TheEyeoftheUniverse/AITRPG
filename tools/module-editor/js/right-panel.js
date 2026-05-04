/**
 * Editor.RightPanel — 右栏: 调试器 + 节点视觉编辑器 (v3.4.0, 2026-05-04 map-node-visual-style)
 *
 * v3.4.0 新增:
 *   - 当前节点 (走入或选中的节点) 的视觉编辑器:
 *       icon (MDI 类名) / displayColor (圆环 accent) / badges 数组 / exits 每条的 style + 方向
 *   - 实时预览卡片 (跟主游戏 .map-node-card 同款 DOM 结构), 改一个字段立刻可见
 *   - 改动通过 State.setLocationVisual / setExitVisual 写回 module 数据, 后续 export 直接保留
 *
 * 设计原则:
 *   - 主画布 (map-canvas.js) 与玩家地图使用同款透明圆节点; 右侧预览卡片只作为小尺寸确认.
 *   - 所有改动 act on state.module 直接, 不复制. JSON.stringify 在 export 时完整保留.
 *   - icon picker 用 datalist 做半结构化 autocomplete (~50 个常用 MDI 名), 不限制只能选列表里的.
 */
window.Editor = window.Editor || {};
window.Editor.RightPanel = (function () {
    "use strict";

    const State = window.Editor.State;
    let _slotEl = null;

    // 常用 MDI 图标列表 (autocomplete 提示用; 作者可以输入列表外任意 mdi-* 字符串).
    // 选取以"跑团/老宅/地下城"为基线的高频图标 + 通用入口/陈设.
    const ICON_SUGGESTIONS = [
        "mdi-door", "mdi-door-open", "mdi-door-closed-lock",
        "mdi-stairs", "mdi-stairs-up", "mdi-stairs-down",
        "mdi-bed", "mdi-bed-empty", "mdi-bed-king",
        "mdi-home", "mdi-home-modern", "mdi-castle",
        "mdi-tree", "mdi-flower", "mdi-pine-tree", "mdi-cave",
        "mdi-bookshelf", "mdi-book-open-variant", "mdi-school",
        "mdi-pot-steam", "mdi-silverware-fork-knife", "mdi-fridge",
        "mdi-couch", "mdi-television-classic",
        "mdi-altar", "mdi-grave-stone", "mdi-coffin",
        "mdi-key", "mdi-key-variant", "mdi-lock", "mdi-lock-open-variant",
        "mdi-skull", "mdi-skull-crossbones", "mdi-ghost", "mdi-ghost-outline",
        "mdi-account", "mdi-account-multiple", "mdi-account-tie",
        "mdi-shield", "mdi-sword", "mdi-pistol", "mdi-bow-arrow",
        "mdi-flag", "mdi-flag-checkered", "mdi-target",
        "mdi-flash", "mdi-fire", "mdi-water",
        "mdi-eye", "mdi-eye-off",
        "mdi-help-circle"
    ];
    function init() {
        _slotEl = document.querySelector('[data-extension-slot="right-tools"]');
        if (!_slotEl) return;
        State.on("location:changed", _renderAll);
        State.on("ui:selection-changed", _renderAll);
        State.on("location:visual-changed", _renderAll);
        State.on("map:position-changed", _renderAll);
        State.on("module:loaded", _renderAll);
        _renderAll();
        _bindEvents();
    }

    function _esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    function _activeKey() {
        const s = State.getState();
        // 优先用 UI 选中, 其次走入的, 都没就空
        return s.ui.selectedNodeKey || s.runtime.currentLocationKey || null;
    }

    function _renderAll() {
        if (!_slotEl) return;
        const s = State.getState();
        const mod = s.module;
        const key = _activeKey();
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

        const previewHtml = _renderPreviewCard(loc);
        const visualFormHtml = _renderVisualForm(key, loc);
        const exitsFormHtml = _renderExitsForm(key, loc);

        _slotEl.innerHTML =
            '<datalist id="rp-icon-suggestions">' +
                ICON_SUGGESTIONS.map(function (n) { return '<option value="' + _esc(n) + '"></option>'; }).join("") +
            '</datalist>' +
            '<h3 class="panel-section-title">当前节点</h3>' +
            '<div class="panel-section-body">' +
                '<div class="meta-row"><span class="meta-key">key</span><span class="meta-val">' + _esc(key) + '</span></div>' +
                '<div class="meta-row"><span class="meta-key">name</span><span class="meta-val">' + _esc(loc.name || "?") + '</span></div>' +
                '<div class="meta-row"><span class="meta-key">floor</span><span class="meta-val">' + _esc(loc.floor != null ? loc.floor : "?") + '</span></div>' +
                '<div class="meta-row"><span class="meta-key">map_position</span><span class="meta-val">' + _esc(mpStr) + '</span></div>' +
                '<div class="meta-hint">拖动地图节点会更新 map_position</div>' +
            '</div>' +
            '<h3 class="panel-section-title">玩家视觉预览</h3>' +
            '<div class="panel-section-body rp-preview-wrap">' + previewHtml + '</div>' +
            '<h3 class="panel-section-title">节点视觉</h3>' +
            '<div class="panel-section-body" data-rp-form="visual" data-rp-key="' + _esc(key) + '">' + visualFormHtml + '</div>' +
            '<h3 class="panel-section-title">出口连线</h3>' +
            '<div class="panel-section-body" data-rp-form="exits" data-rp-key="' + _esc(key) + '">' + exitsFormHtml + '</div>';
    }

    // ---------- preview card ----------
    function _renderPreviewCard(loc) {
        const isCurrent = State.getState().runtime.currentLocationKey === _activeKey();
        const isVisited = State.getState().runtime.visitedKeys.has(_activeKey());
        const isFog = !isCurrent && !isVisited;
        const iconMdi = (typeof loc.icon === "string" && loc.icon.trim()) ? loc.icon.trim() : "mdi-door";
        const showQuestion = isFog;
        const bgColor = (typeof loc.displayColor === "string" && loc.displayColor.trim()) ? loc.displayColor.trim() : "#8b6f4e";
        const bgAlpha = 0;

        // 引擎自动徽章 + 模组 badges, merge & 分配角.
        const auto = [];
        // 编辑器没有真实 NPC/danger 计算; 这里给个示意"如果引擎检测为 danger / npc 会怎么样"的 toggle? 简化: 不模拟 auto, 只渲染 mod badges.
        const modBadges = Array.isArray(loc.badges) ? loc.badges.filter(function (b) {
            return b && typeof b === "object" && typeof b.icon === "string" && b.icon.trim();
        }) : [];
        const positions = ["tr", "tl", "br", "bl"];
        const used = new Set();
        const finalBadges = [];
        modBadges.forEach(function (b) {
            if (typeof b.position === "string" && positions.indexOf(b.position) >= 0 && !used.has(b.position)) {
                finalBadges.push(Object.assign({}, b));
                used.add(b.position);
            }
        });
        modBadges.forEach(function (b) {
            if (typeof b.position === "string" && positions.indexOf(b.position) >= 0) return;
            for (let i = 0; i < positions.length; i++) {
                if (!used.has(positions[i])) {
                    const out = Object.assign({}, b);
                    out.position = positions[i];
                    finalBadges.push(out);
                    used.add(positions[i]);
                    break;
                }
            }
        });

        // 整圆: 用一个 72x72 的 div 模拟 Cytoscape 的 ellipse 节点. background-color/alpha
        // 在内联 style 上, 让作者拖滑块时 "立刻看见效果".
        const circleStyle =
            "background-color: " + _esc(bgColor) + ";" +
            "opacity: " + bgAlpha + ";";
        const wrapStyle = "";
        const labelText = showQuestion ? "" : _esc(loc.name || "?");
        const cardClasses = "map-node-card rp-preview-card"
            + (isCurrent ? " is-current" : "")
            + (showQuestion ? " is-fog" : "");

        const badgeHtml = finalBadges.map(function (b) {
            const colorStyle = b.color ? ("color: " + _esc(b.color) + ";") : "";
            return '<span class="map-node-badge mdi ' + _esc(b.icon) + ' pos-' + _esc(b.position) + '" style="' + colorStyle + '"></span>';
        }).join("");

        return (
            '<div class="rp-preview-stage">' +
                '<div class="rp-preview-circle" style="' + circleStyle + '"></div>' +
                '<div class="' + cardClasses + '" style="' + wrapStyle + '--map-node-accent: ' + _esc(bgColor) + ';">' +
                    '<div class="map-node-icon-wrap">' +
                        (showQuestion
                            ? '<span class="map-node-q" style="font-size: 60px;">?</span>'
                            : '<span class="map-node-icon mdi ' + _esc(iconMdi) + '" style="font-size: 56px;"></span>'
                        ) +
                    '</div>' +
                    '<div class="map-node-label">' + labelText + '</div>' +
                    '<div class="map-node-badges">' + badgeHtml + '</div>' +
                '</div>' +
            '</div>' +
            '<div class="rp-preview-meta">' +
                '<span>' + (isCurrent ? '玩家位置' : (isFog ? '未探索' : '已探索')) + '</span> · ' +
                '<span>iconMdi: ' + _esc(iconMdi) + '</span> · ' +
                '<span>fill: off</span>' +
            '</div>'
        );
    }

    // ---------- visual form ----------
    function _renderVisualForm(key, loc) {
        const icon = (typeof loc.icon === "string") ? loc.icon : "";
        const color = (typeof loc.displayColor === "string") ? loc.displayColor : "";
        const colorForInput = /^#[0-9a-f]{6}$/i.test(color) ? color : "#8b6f4e";
        const badges = Array.isArray(loc.badges) ? loc.badges : [];

        const badgesHtml = badges.map(function (b, i) {
            const bIcon = (b && typeof b.icon === "string") ? b.icon : "";
            const bColor = (b && typeof b.color === "string") ? b.color : "";
            const bColorForInput = /^#[0-9a-f]{6}$/i.test(bColor) ? bColor : "#c49a3c";
            const bPos = (b && typeof b.position === "string") ? b.position : "";
            return (
                '<div class="rp-badge-row" data-badge-index="' + i + '">' +
                    '<input type="text" class="rp-input rp-badge-icon" list="rp-icon-suggestions" placeholder="mdi-skull" value="' + _esc(bIcon) + '" />' +
                    '<input type="color" class="rp-input rp-badge-color" value="' + _esc(bColorForInput) + '" title="徽章颜色" />' +
                    '<input type="text" class="rp-input rp-badge-color-text" placeholder="(可空 = 默认)" value="' + _esc(bColor) + '" />' +
                    '<select class="rp-input rp-badge-pos">' +
                        '<option value=""' + (bPos === "" ? " selected" : "") + '>自动</option>' +
                        '<option value="tr"' + (bPos === "tr" ? " selected" : "") + '>右上</option>' +
                        '<option value="tl"' + (bPos === "tl" ? " selected" : "") + '>左上</option>' +
                        '<option value="br"' + (bPos === "br" ? " selected" : "") + '>右下</option>' +
                        '<option value="bl"' + (bPos === "bl" ? " selected" : "") + '>左下</option>' +
                    '</select>' +
                    '<button class="rp-btn rp-badge-remove" type="button">删</button>' +
                '</div>'
            );
        }).join("");

        return (
            '<div class="rp-form-row">' +
                '<label class="rp-label">icon (MDI)</label>' +
                '<input type="text" class="rp-input rp-icon-input" list="rp-icon-suggestions" placeholder="mdi-door" value="' + _esc(icon) + '" />' +
                '<button class="rp-btn rp-icon-clear" type="button" title="清空 → 走默认 mdi-door">×</button>' +
            '</div>' +
            '<div class="rp-form-row">' +
                '<label class="rp-label">displayColor</label>' +
                '<input type="color" class="rp-input rp-color-input" value="' + _esc(colorForInput) + '" />' +
                '<input type="text" class="rp-input rp-color-text" placeholder="(空 = 默认 --accent)" value="' + _esc(color) + '" />' +
                '<button class="rp-btn rp-color-clear" type="button" title="清空 → 走默认 --accent">×</button>' +
            '</div>' +
            '<div class="rp-form-row rp-form-row-section">' +
                '<label class="rp-label">badges (角标徽章)</label>' +
                '<button class="rp-btn rp-badge-add" type="button">+ 加徽章</button>' +
            '</div>' +
            (badges.length ? '<div class="rp-badges-list">' + badgesHtml + '</div>' : '<div class="rp-empty-hint">无徽章; 引擎运行时仍会按 NPC/danger/micro 自动注入角标</div>')
        );
    }

    // ---------- exits form ----------
    function _renderExitsForm(key, loc) {
        const exits = Array.isArray(loc.exits) ? loc.exits : [];
        if (!exits.length) return '<div class="rp-empty-hint">本节点没有 exits</div>';
        return exits.map(function (raw, i) {
            let to = "";
            let style = "solid";
            let directed = false;
            let direction = "to";
            if (typeof raw === "string") {
                to = raw;
            } else if (raw && typeof raw === "object") {
                to = String(raw.to || "");
                if (typeof raw.style === "string") style = raw.style;
                if (typeof raw.directed === "boolean") directed = raw.directed;
                if (raw.direction === "from" || raw.direction === "to") direction = raw.direction;
            }
            const dirVisible = (style === "single-arrow" || directed);
            return (
                '<div class="rp-exit-row" data-exit-index="' + i + '">' +
                    '<div class="rp-exit-to">' + _esc(to || "?") + '</div>' +
                    '<div class="rp-form-row">' +
                        '<label class="rp-label">style</label>' +
                        '<select class="rp-input rp-exit-style">' +
                            '<option value="solid"' + (style === "solid" ? " selected" : "") + '>实线</option>' +
                            '<option value="dashed"' + (style === "dashed" ? " selected" : "") + '>虚线</option>' +
                            '<option value="double"' + (style === "double" ? " selected" : "") + '>双线</option>' +
                            '<option value="single-arrow"' + (style === "single-arrow" ? " selected" : "") + '>单向箭头</option>' +
                        '</select>' +
                    '</div>' +
                    '<div class="rp-form-row" style="' + (dirVisible ? "" : "display:none") + '">' +
                        '<label class="rp-label">单向方向</label>' +
                        '<select class="rp-input rp-exit-direction">' +
                            '<option value="to"' + (direction === "to" ? " selected" : "") + '>本节点 → 目标</option>' +
                            '<option value="from"' + (direction === "from" ? " selected" : "") + '>目标 → 本节点</option>' +
                        '</select>' +
                    '</div>' +
                '</div>'
            );
        }).join("");
    }

    // ---------- event delegation ----------
    function _bindEvents() {
        document.addEventListener("input", _onInput);
        document.addEventListener("change", _onInput);
        document.addEventListener("click", _onClick);
    }

    function _formContainer(target, name) {
        let el = target;
        while (el && el !== document.body) {
            if (el.dataset && el.dataset.rpForm === name) return el;
            el = el.parentElement;
        }
        return null;
    }

    function _onInput(evt) {
        const target = evt.target;
        if (!target || !target.classList) return;
        const visualForm = _formContainer(target, "visual");
        const exitsForm = _formContainer(target, "exits");

        if (visualForm) {
            const key = visualForm.dataset.rpKey;
            if (!key) return;
            if (target.classList.contains("rp-icon-input")) {
                State.setLocationVisual(key, { icon: target.value });
            } else if (target.classList.contains("rp-color-input")) {
                // 跟 rp-color-text 双向同步
                const tx = visualForm.querySelector(".rp-color-text");
                if (tx) tx.value = target.value;
                State.setLocationVisual(key, { displayColor: target.value });
            } else if (target.classList.contains("rp-color-text")) {
                State.setLocationVisual(key, { displayColor: target.value });
            } else if (target.classList.contains("rp-badge-icon")
                    || target.classList.contains("rp-badge-color")
                    || target.classList.contains("rp-badge-color-text")
                    || target.classList.contains("rp-badge-pos")) {
                _flushBadgesFromForm(visualForm, key);
                if (target.classList.contains("rp-badge-color")) {
                    const row = target.closest(".rp-badge-row");
                    const tx = row && row.querySelector(".rp-badge-color-text");
                    if (tx) tx.value = target.value;
                }
            }
        }

        if (exitsForm) {
            const key = exitsForm.dataset.rpKey;
            if (!key) return;
            const row = target.closest && target.closest(".rp-exit-row");
            if (!row) return;
            const idx = Number(row.dataset.exitIndex);
            if (target.classList.contains("rp-exit-style")) {
                const styleVal = target.value;
                const dirSel = row.querySelector(".rp-exit-direction");
                const direction = dirSel ? dirSel.value : "to";
                State.setExitVisual(key, idx, {
                    style: styleVal,
                    directed: (styleVal === "single-arrow"),
                    direction: direction
                });
            } else if (target.classList.contains("rp-exit-direction")) {
                const styleSel = row.querySelector(".rp-exit-style");
                const styleVal = styleSel ? styleSel.value : "solid";
                State.setExitVisual(key, idx, {
                    style: styleVal,
                    directed: (styleVal === "single-arrow"),
                    direction: target.value
                });
            }
        }
    }

    function _onClick(evt) {
        const target = evt.target;
        if (!target || !target.classList) return;
        const visualForm = _formContainer(target, "visual");
        if (!visualForm) return;
        const key = visualForm.dataset.rpKey;
        if (!key) return;

        if (target.classList.contains("rp-icon-clear")) {
            State.setLocationVisual(key, { icon: undefined });
        } else if (target.classList.contains("rp-color-clear")) {
            State.setLocationVisual(key, { displayColor: undefined });
        } else if (target.classList.contains("rp-badge-add")) {
            const s = State.getState();
            const loc = s.module && s.module.locations && s.module.locations[key];
            if (!loc) return;
            const cur = Array.isArray(loc.badges) ? loc.badges.slice() : [];
            cur.push({ icon: "mdi-help-circle" });
            State.setLocationVisual(key, { badges: cur });
        } else if (target.classList.contains("rp-badge-remove")) {
            const row = target.closest(".rp-badge-row");
            if (!row) return;
            const idx = Number(row.dataset.badgeIndex);
            const s = State.getState();
            const loc = s.module && s.module.locations && s.module.locations[key];
            if (!loc) return;
            const cur = Array.isArray(loc.badges) ? loc.badges.slice() : [];
            if (idx >= 0 && idx < cur.length) cur.splice(idx, 1);
            State.setLocationVisual(key, { badges: cur });
        }
    }

    function _flushBadgesFromForm(visualForm, key) {
        const rows = visualForm.querySelectorAll(".rp-badge-row");
        const badges = [];
        rows.forEach(function (row) {
            const ic = row.querySelector(".rp-badge-icon");
            const ct = row.querySelector(".rp-badge-color-text");
            const cc = row.querySelector(".rp-badge-color");
            const ps = row.querySelector(".rp-badge-pos");
            if (!ic || !ic.value.trim()) return;
            const b = { icon: ic.value.trim() };
            const colorTextVal = ct ? ct.value.trim() : "";
            const colorPickerVal = cc ? cc.value : "";
            // 文本框为空 → 走默认色 (不写 color); 否则文本优先于色板.
            if (colorTextVal) b.color = colorTextVal;
            else if (colorPickerVal && colorPickerVal !== "#000000") {
                /* 不写 — 默认 */
            }
            if (ps && ps.value) b.position = ps.value;
            badges.push(b);
        });
        State.setLocationVisual(key, { badges: badges });
    }

    return { init: init };
})();
