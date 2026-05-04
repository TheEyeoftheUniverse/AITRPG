// 调查员卡（COC7）前端交互
// 与后端契约：POST /trpg/api/character-card/validate 是单一权威校验真源；
// 前端只做镜像校验（即时反馈）。所有玩家自定义文本展示用 textContent，禁止 innerHTML。

(function () {
    "use strict";

    var LS_KEY = "the_call_of_ai_player_card_v2";
    var LS_KEY_LEGACY = "the_call_of_ai_player_card_v1";

    var ATTRIBUTE_KEYS = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU", "LUCK"];
    var ATTRIBUTE_RANGES = {
        STR: [15, 90], CON: [15, 90], DEX: [15, 90],
        APP: [15, 90], POW: [15, 90], LUCK: [15, 90],
        SIZ: [40, 90], INT: [40, 90], EDU: [40, 90]
    };
    var ATTRIBUTE_LABEL = {
        STR: "STR 力量", CON: "CON 体质", SIZ: "SIZ 体型",
        DEX: "DEX 敏捷", APP: "APP 外貌", INT: "INT 智力",
        POW: "POW 意志", EDU: "EDU 教育", LUCK: "LUCK 幸运"
    };
    var BACKGROUND_FIELDS = [
        ["personal_description", "个人描述"],
        ["ideology_beliefs", "思想/信念"],
        ["significant_people", "重要之人"],
        ["meaningful_locations", "意义非凡之地"],
        ["treasured_possessions", "宝贵之物"],
        ["traits", "特质"]
    ];
    var SKILL_HARD_CAP = 90;
    var CTHULHU_MYTHOS = "克苏鲁神话";
    var CREDIT_RATING = "信用评级";
    // v3.2.0: background 字段按字段独立 cap (个人描述 400 字, 其他 5 项各 40 字, 共 ≈600 字)。
    // 与后端 game_state/character_card.py 的 MAX_BACKGROUND_FIELD_LENS 对齐。
    var BG_FIELD_CAPS = {
        personal_description: 400,
        ideology_beliefs:      40,
        significant_people:    40,
        meaningful_locations:  40,
        treasured_possessions: 40,
        traits:                40
    };
    var NAME_CAP = 20;
    var INV_ITEM_CAP = 20;
    var INV_COUNT_CAP = 10;

    var _professions = [];
    var _skillsBase = {};

    // 工具：解析公式型基础值（"DEX/2"、"EDU"）
    function resolveSkillBase(baseVal, attrs) {
        if (typeof baseVal === "number") return baseVal | 0;
        if (typeof baseVal !== "string") return 0;
        var s = baseVal.trim();
        if (Object.prototype.hasOwnProperty.call(attrs, s)) return (attrs[s] | 0);
        if (s.indexOf("/") >= 0) {
            var parts = s.split("/");
            var attr = parts[0].trim();
            var div = parseInt(parts[1], 10);
            if (Object.prototype.hasOwnProperty.call(attrs, attr) && div) {
                return Math.floor(attrs[attr] / div);
            }
        }
        return 0;
    }

    function calcDerived(attrs) {
        var con = attrs.CON | 0, siz = attrs.SIZ | 0, pow = attrs.POW | 0, luck = attrs.LUCK | 0;
        return {
            hp_max: Math.floor((con + siz) / 10),
            san_start: pow,
            san_current: pow,
            san_max: 99,
            mp_max: Math.floor(pow / 5),
            luck: luck
        };
    }

    function calcSkillPools(attrs, profession) {
        var formula = (profession && profession.occupation_skill_pool_formula) || {};
        var primary = formula.primary || "EDU";
        var sec = formula.secondary_choice || [];
        var mul = (formula.multiplier | 0) || 2;
        var primVal = attrs[primary] | 0;
        var secMax = 0;
        for (var i = 0; i < sec.length; i++) {
            var v = attrs[sec[i]] | 0;
            if (v > secMax) secMax = v;
        }
        return {
            occupation_total: primVal * mul + secMax * mul,
            interest_total: (attrs.INT | 0) * 2
        };
    }

    // 与服务端 sanitize 等效的客户端镜像，仅用于显示和导出前清洗
    function sanitizeText(s, maxLen) {
        if (typeof s !== "string") return "";
        var out = s.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
        out = out.replace(/<[^>]+>/g, "");
        out = out.trim();
        if (out.length > maxLen) out = out.substring(0, maxLen);
        return out;
    }

    function fetchJSON(url, options) {
        return fetch(url, options).then(function (r) {
            return r.json().then(function (j) {
                return { ok: r.ok, status: r.status, body: j };
            });
        });
    }

    // === 加载 / 持久化 ===

    function loadFromLocalStorage() {
        try {
            // legacy v1 升级提示
            var legacy = localStorage.getItem(LS_KEY_LEGACY);
            if (legacy) {
                alert("检测到旧版本（v1）的调查员卡数据。COC7 v2 结构不兼容，已清除旧数据，请重新配置。");
                localStorage.removeItem(LS_KEY_LEGACY);
            }
            var raw = localStorage.getItem(LS_KEY);
            if (!raw) return null;
            var parsed = JSON.parse(raw);
            if (parsed && parsed.version === 2) return parsed;
            return null;
        } catch (e) {
            return null;
        }
    }

    function saveToLocalStorage(card) {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify(card));
            return true;
        } catch (e) {
            return false;
        }
    }

    function clearLocalStorage() {
        localStorage.removeItem(LS_KEY);
    }

    // === 模态打开/关闭 ===

    function openCharacterCardModal() {
        document.getElementById("character-card-modal").classList.remove("hidden");
        bootstrap();
    }

    function closeCharacterCardModal() {
        document.getElementById("character-card-modal").classList.add("hidden");
    }

    var _bootstrapped = false;

    function bootstrap() {
        if (_bootstrapped) {
            renderAll();
            return;
        }
        Promise.all([
            fetchJSON("/trpg/api/character-card/professions"),
            fetchJSON("/trpg/api/character-card/skills-base")
        ]).then(function (results) {
            _professions = (results[0].body && results[0].body.professions) || [];
            _skillsBase = (results[1].body && results[1].body.skills_base) || {};
            _bootstrapped = true;
            renderAll();
        }).catch(function (e) {
            showError("加载调查员配置数据失败：" + (e && e.message));
        });
    }

    // === 渲染 ===

    function emptyCard() {
        var profKey = _professions.length ? _professions[0].key : "";
        var attrs = {};
        ATTRIBUTE_KEYS.forEach(function (k) { attrs[k] = ATTRIBUTE_RANGES[k][0]; });
        var skills = {};
        Object.keys(_skillsBase).forEach(function (sk) {
            skills[sk] = resolveSkillBase(_skillsBase[sk], attrs);
        });
        skills[CTHULHU_MYTHOS] = 0;
        return {
            version: 2,
            name: "",
            age: 25,
            sex: "",
            residence: "",
            birthplace: "",
            era: "modern",
            era_custom: "",
            profession: profKey,
            attributes: attrs,
            derived: calcDerived(attrs),
            skill_pools: { occupation_total: 0, interest_total: 0, occupation_used: 0, interest_used: 0 },
            skills: skills,
            background: BACKGROUND_FIELDS.reduce(function (acc, f) { acc[f[0]] = ""; return acc; }, {}),
            inventory: []
        };
    }

    function getCard() {
        if (!window._ccCard) {
            window._ccCard = loadFromLocalStorage() || emptyCard();
        }
        return window._ccCard;
    }

    function setCard(card) {
        window._ccCard = card;
        renderAll();
    }

    function renderAll() {
        var card = getCard();
        renderBasic(card);
        renderProfessionInfo(card);
        renderAttributes(card);
        renderDerived(card);
        renderPools(card);
        renderSkills(card);
        renderBackground(card);
        renderInventory(card);
        validateAndUpdate();
    }

    // 解析当前 card 对应的"激活 profession"信息：
    //   1) profession 命中内置 → 直接返回内置 dict
    //   2) profession 为字符串但不在内置 → 用 card.profession_custom 构造一个临时 dict
    //   3) 都没有 → null
    function getActiveProfession(card) {
        if (!card) return null;
        var key = card.profession || "";
        if (key) {
            var built = _professions.find(function (x) { return x.key === key; });
            if (built) return built;
        }
        var custom = card.profession_custom;
        if (custom && typeof custom === "object") {
            return {
                key: key || "(自定义)",
                name: custom.name || key || "自定义职业",
                description: custom.description || "",
                credit_rating: custom.credit_rating || [0, 99],
                skill_choices: Array.isArray(custom.skill_choices) ? custom.skill_choices.slice() : [],
                occupation_skill_pool_formula: custom.occupation_skill_pool_formula || { primary: "EDU", secondary_choice: [], multiplier: 4 },
                _is_custom: true,
            };
        }
        return null;
    }

    function renderBasic(card) {
        var nameEl = document.getElementById("cc-name");
        var ageEl = document.getElementById("cc-age");
        var profEl = document.getElementById("cc-profession");
        var sexEl = document.getElementById("cc-sex");
        var residenceEl = document.getElementById("cc-residence");
        var birthEl = document.getElementById("cc-birthplace");
        var eraEl = document.getElementById("cc-era");
        var eraCustomEl = document.getElementById("cc-era-custom");
        var eraCustomWrap = document.getElementById("cc-era-custom-wrap");

        nameEl.value = card.name || "";
        document.getElementById("cc-name-len").textContent = String((card.name || "").length);

        ageEl.value = card.age || 25;
        if (sexEl) sexEl.value = card.sex || "";
        if (residenceEl) residenceEl.value = card.residence || "";
        if (birthEl) birthEl.value = card.birthplace || "";

        // 年代项: 后端允许 null/空/modern/1920s/custom; 在 select 上把 null/空 一律映射成 ""。
        if (eraEl) {
            var eraVal = (card.era === "modern" || card.era === "1920s" || card.era === "custom") ? card.era : "";
            eraEl.value = eraVal;
            if (eraCustomWrap) eraCustomWrap.style.display = (eraVal === "custom") ? "" : "none";
            if (eraCustomEl) eraCustomEl.value = card.era_custom || "";
        }

        // 渲染职业 select：内置 6 项 + 自定义 option（仅当 card.profession 不在内置时插入）
        var key = card.profession || "";
        var hasBuiltin = !!_professions.find(function (x) { return x.key === key; });
        // 只在数量变化或卡上来一个新自定义时才重建
        var expectedCount = _professions.length + (key && !hasBuiltin ? 1 : 0);
        if (profEl.options.length !== expectedCount || (key && !hasBuiltin && !profEl.querySelector("option[value='" + CSS.escape(key) + "']"))) {
            profEl.innerHTML = "";
            _professions.forEach(function (p) {
                var opt = document.createElement("option");
                opt.value = p.key;
                opt.textContent = p.name + "（" + p.key + "）";
                profEl.appendChild(opt);
            });
            if (key && !hasBuiltin) {
                var custom = card.profession_custom || {};
                var opt = document.createElement("option");
                opt.value = key;
                opt.textContent = (custom.name || key) + "（自定义）";
                opt.dataset.custom = "1";
                profEl.appendChild(opt);
            }
        }
        profEl.value = key || (_professions[0] && _professions[0].key) || "";
    }

    function renderProfessionInfo(card) {
        var info = document.getElementById("cc-profession-info");
        var p = getActiveProfession(card);
        if (!p) { info.textContent = ""; return; }
        var formula = p.occupation_skill_pool_formula || {};
        var sec = (formula.secondary_choice || []).join("/");
        var lines = [];
        if (p._is_custom) lines.push("【自定义职业】" + (p.name || "(未命名)"));
        lines.push("职业说明：" + (p.description || ""));
        lines.push("信用评级范围：" + (p.credit_rating ? p.credit_rating[0] + "-" + p.credit_rating[1] : "未指定"));
        lines.push("职业点公式：" + (formula.primary || "EDU") + "×" + (formula.multiplier || 2) +
            (sec ? " + max(" + sec + ")×" + (formula.multiplier || 2) : ""));
        lines.push("职业技能：" + ((p.skill_choices || []).join("、") || "（无）"));
        // textContent 自动转义 — 安全
        info.textContent = lines.join("\n");
    }

    function renderAttributes(card) {
        var wrap = document.getElementById("cc-attrs");
        wrap.innerHTML = "";
        ATTRIBUTE_KEYS.forEach(function (k) {
            var range = ATTRIBUTE_RANGES[k];
            var cell = document.createElement("div");
            cell.className = "cc-attr-cell";
            var label = document.createElement("label");
            label.textContent = ATTRIBUTE_LABEL[k] + " (" + range[0] + "-" + range[1] + ")";
            cell.appendChild(label);
            var inp = document.createElement("input");
            inp.type = "number";
            inp.min = range[0];
            inp.max = range[1];
            inp.value = card.attributes[k];
            inp.dataset.attrKey = k;
            inp.addEventListener("input", onAttributeChange);
            cell.appendChild(inp);
            wrap.appendChild(cell);
        });
    }

    function renderDerived(card) {
        var d = calcDerived(card.attributes);
        card.derived = d;
        var el = document.getElementById("cc-derived");
        el.innerHTML = "";
        function tag(label, val) {
            var s = document.createElement("span");
            var l = document.createTextNode(label + "：");
            var b = document.createElement("b");
            b.textContent = String(val);
            s.appendChild(l);
            s.appendChild(b);
            return s;
        }
        el.appendChild(tag("HP（最大）", d.hp_max));
        el.appendChild(tag("SAN（起始）", d.san_start));
        el.appendChild(tag("MP（最大）", d.mp_max));
        el.appendChild(tag("LUCK", d.luck));
    }

    function renderPools(card) {
        var p = getActiveProfession(card);
        var pools = p ? calcSkillPools(card.attributes, p) : { occupation_total: 0, interest_total: 0 };
        card.skill_pools = card.skill_pools || {};
        card.skill_pools.occupation_total = pools.occupation_total;
        card.skill_pools.interest_total = pools.interest_total;
        document.getElementById("cc-pool-occ-total").textContent = pools.occupation_total;
        document.getElementById("cc-pool-int-total").textContent = pools.interest_total;
    }

    function renderSkills(card) {
        var tbody = document.getElementById("cc-skills-body");
        tbody.innerHTML = "";
        var p = getActiveProfession(card);
        var skillChoices = (p && p.skill_choices) || [];
        var allowOccPoint = function (sk) {
            return sk === CREDIT_RATING || skillChoices.indexOf(sk) >= 0;
        };

        // 把 card.skills 里的 invested 拆回 +职业点 / +兴趣点 列。
        // 与后端 validate_card 占用计费一致：信用评级全归职业点；
        // skill_choices 内技能优先吃职业点池, 溢出归兴趣点；其它技能全归兴趣点。
        // v3: 自定义技能 (不在 _skillsBase 内) 一并渲染, base=0, 行为与内置技能一致。
        var pools = (p && card.attributes) ? calcSkillPools(card.attributes, p) : { occupation_total: 0, interest_total: 0 };
        var occRemaining = pools.occupation_total | 0;

        var builtinKeys = Object.keys(_skillsBase);
        var customKeys = Object.keys(card.skills || {}).filter(function (sk) {
            return !(sk in _skillsBase);
        });
        var orderedKeys = builtinKeys.concat(customKeys);

        orderedKeys.forEach(function (sk) {
            var isCustom = !(sk in _skillsBase);
            var base = isCustom ? 0 : resolveSkillBase(_skillsBase[sk], card.attributes);
            var total = card.skills[sk] != null ? card.skills[sk] : base;
            var invested = total - base;
            if (invested < 0) invested = 0;

            var occInvested = 0, intInvested = 0;
            if (sk === CTHULHU_MYTHOS) {
                occInvested = 0;
                intInvested = 0;
            } else if (sk === CREDIT_RATING) {
                occInvested = invested;
                occRemaining -= occInvested;
                if (occRemaining < 0) occRemaining = 0;
            } else if (allowOccPoint(sk)) {
                occInvested = Math.min(invested, Math.max(0, occRemaining));
                intInvested = invested - occInvested;
                occRemaining -= occInvested;
            } else {
                intInvested = invested;
            }

            var tr = document.createElement("tr");
            var tdName = document.createElement("td");
            tdName.textContent = sk + (isCustom ? "（自定义）" : "");
            tr.appendChild(tdName);

            var tdBase = document.createElement("td");
            tdBase.textContent = String(base);
            tr.appendChild(tdBase);

            // +职业点
            var tdOcc = document.createElement("td");
            var occInput = document.createElement("input");
            occInput.type = "number";
            occInput.min = 0;
            occInput.dataset.skillKey = sk;
            occInput.dataset.poolKind = "occ";
            if (isCustom) occInput.dataset.customSkill = "1";
            if (sk === CTHULHU_MYTHOS || !allowOccPoint(sk)) {
                occInput.disabled = true;
                occInput.value = "";
            } else {
                occInput.value = String(occInvested);
                occInput.addEventListener("input", onSkillPointChange);
            }
            tdOcc.appendChild(occInput);
            tr.appendChild(tdOcc);

            // +兴趣点
            var tdInt = document.createElement("td");
            var intInput = document.createElement("input");
            intInput.type = "number";
            intInput.min = 0;
            intInput.dataset.skillKey = sk;
            intInput.dataset.poolKind = "int";
            if (isCustom) intInput.dataset.customSkill = "1";
            if (sk === CTHULHU_MYTHOS || sk === CREDIT_RATING) {
                intInput.disabled = true;
                intInput.value = "";
            } else {
                intInput.value = String(intInvested);
                intInput.addEventListener("input", onSkillPointChange);
            }
            tdInt.appendChild(intInput);
            tr.appendChild(tdInt);

            // 合计
            var tdTotal = document.createElement("td");
            tdTotal.textContent = String(base + occInvested + intInvested);
            tr.appendChild(tdTotal);

            tbody.appendChild(tr);
        });
    }

    function renderBackground(card) {
        var wrap = document.getElementById("cc-background");
        wrap.innerHTML = "";
        BACKGROUND_FIELDS.forEach(function (f) {
            var key = f[0], label = f[1];
            var cap = BG_FIELD_CAPS[key] || 40;
            var div = document.createElement("div");
            div.className = "cc-bg-field";
            var lbl = document.createElement("label");
            lbl.textContent = label;
            div.appendChild(lbl);
            var ta = document.createElement("textarea");
            ta.maxLength = cap;
            ta.dataset.bgKey = key;
            ta.value = card.background[key] || "";
            ta.addEventListener("input", onBackgroundChange);
            div.appendChild(ta);
            var counter = document.createElement("span");
            counter.className = "cc-counter";
            counter.textContent = (card.background[key] || "").length + "/" + cap;
            div.appendChild(counter);
            wrap.appendChild(div);
        });
    }

    function renderInventory(card) {
        var wrap = document.getElementById("cc-inventory");
        wrap.innerHTML = "";
        (card.inventory || []).forEach(function (item, idx) {
            wrap.appendChild(buildInventoryItem(item, idx));
        });
    }

    function buildInventoryItem(value, idx) {
        var w = document.createElement("div");
        w.className = "cc-inventory-item";
        var inp = document.createElement("input");
        inp.type = "text";
        inp.maxLength = INV_ITEM_CAP;
        inp.value = value;
        inp.dataset.invIdx = idx;
        inp.addEventListener("input", onInventoryChange);
        w.appendChild(inp);
        var btn = document.createElement("button");
        btn.className = "cc-inventory-remove";
        btn.title = "删除";
        btn.textContent = "✕";
        btn.addEventListener("click", function () {
            var card = getCard();
            card.inventory.splice(idx, 1);
            renderInventory(card);
            validateAndUpdate();
        });
        w.appendChild(btn);
        return w;
    }

    function ccAddInventoryItem(value) {
        var card = getCard();
        if (!card.inventory) card.inventory = [];
        if (card.inventory.length >= INV_COUNT_CAP) {
            showError("起始物品不得超过 " + INV_COUNT_CAP + " 项");
            return;
        }
        card.inventory.push(value || "");
        renderInventory(card);
    }

    // === 输入事件 ===

    function ccOnInput() {
        var card = getCard();
        var nameEl = document.getElementById("cc-name");
        var ageEl = document.getElementById("cc-age");
        var sexEl = document.getElementById("cc-sex");
        var residenceEl = document.getElementById("cc-residence");
        var birthEl = document.getElementById("cc-birthplace");
        card.name = nameEl.value || "";
        document.getElementById("cc-name-len").textContent = String(card.name.length);
        var age = parseInt(ageEl.value, 10);
        if (!isNaN(age)) card.age = age;
        if (sexEl) card.sex = sexEl.value || "";
        if (residenceEl) card.residence = residenceEl.value || "";
        if (birthEl) card.birthplace = birthEl.value || "";
        validateAndUpdate();
    }

    function ccOnProfessionChange() {
        var card = getCard();
        card.profession = document.getElementById("cc-profession").value;
        // 切职业要重置信用评级到该职业下限并清掉技能投入
        Object.keys(card.skills).forEach(function (sk) {
            card.skills[sk] = resolveSkillBase(_skillsBase[sk], card.attributes);
        });
        card.skills[CTHULHU_MYTHOS] = 0;
        var p = getActiveProfession(card);
        if (p && p.credit_rating) card.skills[CREDIT_RATING] = p.credit_rating[0];
        renderProfessionInfo(card);
        renderPools(card);
        renderSkills(card);
        validateAndUpdate();
    }

    function ccOnEraChange() {
        var card = getCard();
        var eraEl = document.getElementById("cc-era");
        var eraCustomWrap = document.getElementById("cc-era-custom-wrap");
        var eraCustomEl = document.getElementById("cc-era-custom");
        var v = eraEl ? eraEl.value : "";
        // 只允许 ""/modern/1920s/custom 四种, 其它落回 ""。
        if (v !== "modern" && v !== "1920s" && v !== "custom") v = "";
        card.era = v;
        if (v !== "custom") {
            // 切回预设/未声明时清掉自定义文本, 避免 era_custom 残留误导后端 / AI
            card.era_custom = "";
            if (eraCustomEl) eraCustomEl.value = "";
        }
        if (eraCustomWrap) eraCustomWrap.style.display = (v === "custom") ? "" : "none";
        validateAndUpdate();
    }

    function ccOnEraCustomInput() {
        var card = getCard();
        var el = document.getElementById("cc-era-custom");
        card.era_custom = el ? (el.value || "") : "";
        validateAndUpdate();
    }

    function onAttributeChange(e) {
        var card = getCard();
        var k = e.target.dataset.attrKey;
        var v = parseInt(e.target.value, 10);
        if (isNaN(v)) v = 0;
        card.attributes[k] = v;
        // 重算技能基础值（影响 闪避=DEX/2、母语=EDU、教育=EDU）
        Object.keys(_skillsBase).forEach(function (sk) {
            var newBase = resolveSkillBase(_skillsBase[sk], card.attributes);
            if (sk === CTHULHU_MYTHOS) { card.skills[sk] = 0; return; }
            // 保持玩家的投入：投入 = 旧 total - 旧 base
            var oldBase = card.skills[sk] != null ? card.skills[sk] : 0;
            // 这里采用简化：属性变化后强制 total = newBase + invested_keep，但 invested_keep 难以追溯。
            // 安全起见：把 total 设为 max(newBase, current)。
            if (oldBase < newBase) card.skills[sk] = newBase;
        });
        renderDerived(card);
        renderPools(card);
        renderSkills(card);
        validateAndUpdate();
    }

    function onSkillPointChange(e) {
        var card = getCard();
        var sk = e.target.dataset.skillKey;
        var kind = e.target.dataset.poolKind;
        var row = e.target.closest("tr");
        var occ = parseInt(row.querySelector("input[data-pool-kind='occ']").value, 10) || 0;
        var int_ = parseInt(row.querySelector("input[data-pool-kind='int']").value, 10) || 0;
        if (occ < 0) occ = 0;
        if (int_ < 0) int_ = 0;
        var base = resolveSkillBase(_skillsBase[sk], card.attributes);
        card.skills[sk] = base + occ + int_;
        // 更新行末合计列
        row.children[4].textContent = String(card.skills[sk]);
        validateAndUpdate();
    }

    function onBackgroundChange(e) {
        var card = getCard();
        var key = e.target.dataset.bgKey;
        card.background[key] = e.target.value || "";
        var counter = e.target.parentElement.querySelector(".cc-counter");
        var cap = BG_FIELD_CAPS[key] || 40;
        if (counter) counter.textContent = card.background[key].length + "/" + cap;
        validateAndUpdate();
    }

    function onInventoryChange(e) {
        var card = getCard();
        var idx = parseInt(e.target.dataset.invIdx, 10);
        card.inventory[idx] = e.target.value || "";
        validateAndUpdate();
    }

    // === 校验（前端镜像） + 双点数池显示 ===

    function validateAndUpdate() {
        var card = getCard();
        var errs = [];

        // 双点数池累加
        var pools = card.skill_pools || { occupation_total: 0, interest_total: 0 };
        var occUsed = 0, intUsed = 0;
        var p = getActiveProfession(card);
        var skillChoices = (p && p.skill_choices) || [];
        document.querySelectorAll(".cc-skills-table input[data-skill-key]").forEach(function (inp) {
            var v = parseInt(inp.value, 10) || 0;
            if (v < 0) v = 0;
            if (inp.dataset.poolKind === "occ") occUsed += v;
            else intUsed += v;
        });

        document.getElementById("cc-pool-occ-used").textContent = String(occUsed);
        document.getElementById("cc-pool-int-used").textContent = String(intUsed);
        var occOver = occUsed > pools.occupation_total;
        var intOver = intUsed > pools.interest_total;
        var occEl = document.getElementById("cc-pool-occ-used");
        occEl.classList.toggle("over", occOver);
        var intEl = document.getElementById("cc-pool-int-used");
        intEl.classList.toggle("over", intOver);
        if (occOver) errs.push("职业点超额：" + occUsed + " / " + pools.occupation_total);
        if (intOver) errs.push("兴趣点超额：" + intUsed + " / " + pools.interest_total);

        card.skill_pools.occupation_used = occUsed;
        card.skill_pools.interest_used = intUsed;

        // 属性范围 + 高亮
        ATTRIBUTE_KEYS.forEach(function (k) {
            var range = ATTRIBUTE_RANGES[k];
            var inp = document.querySelector(".cc-attrs input[data-attr-key='" + k + "']");
            if (!inp) return;
            var v = parseInt(inp.value, 10);
            var bad = isNaN(v) || v < range[0] || v > range[1];
            inp.classList.toggle("invalid", bad);
            if (bad) errs.push(k + " 超出 [" + range[0] + "," + range[1] + "]");
        });

        // 技能 ≤ 90（克苏鲁神话除外，且强制 0）
        Object.keys(card.skills || {}).forEach(function (sk) {
            var v = card.skills[sk];
            if (sk === CTHULHU_MYTHOS) {
                if (v !== 0) errs.push("克苏鲁神话初始必须 = 0");
            } else if (v > SKILL_HARD_CAP) {
                errs.push(sk + " 超过上限 " + SKILL_HARD_CAP);
            }
        });

        // 信用评级范围
        if (p && p.credit_rating && card.skills[CREDIT_RATING] != null) {
            var cr = card.skills[CREDIT_RATING];
            if (cr < p.credit_rating[0] || cr > p.credit_rating[1]) {
                errs.push("信用评级超出 [" + p.credit_rating[0] + "," + p.credit_rating[1] + "]");
            }
        }

        // 姓名非空
        if (!sanitizeText(card.name, NAME_CAP)) errs.push("姓名不能为空");

        var saveBtn = document.getElementById("cc-save-btn");
        if (saveBtn) saveBtn.disabled = errs.length > 0;

        var errEl = document.getElementById("cc-error");
        if (errs.length === 0) {
            errEl.classList.add("hidden");
            errEl.textContent = "";
        } else {
            errEl.classList.remove("hidden");
            errEl.textContent = "校验未通过：\n- " + errs.join("\n- ");
        }
    }

    function showError(msg) {
        var errEl = document.getElementById("cc-error");
        if (!errEl) { alert(msg); return; }
        errEl.classList.remove("hidden");
        errEl.textContent = msg;
    }

    // === 操作按钮 ===

    function ccRollAttributes() {
        fetchJSON("/trpg/api/character-card/roll-attributes").then(function (r) {
            if (!r.ok) { showError("Roll 属性失败"); return; }
            var card = getCard();
            var attrs = (r.body && r.body.attributes) || {};
            ATTRIBUTE_KEYS.forEach(function (k) {
                if (attrs[k] != null) card.attributes[k] = attrs[k];
            });
            // 重置技能为各属性派生的基础
            Object.keys(_skillsBase).forEach(function (sk) {
                card.skills[sk] = resolveSkillBase(_skillsBase[sk], card.attributes);
            });
            card.skills[CTHULHU_MYTHOS] = 0;
            var p = getActiveProfession(card);
            if (p && p.credit_rating) card.skills[CREDIT_RATING] = p.credit_rating[0];
            renderAll();
        });
    }

    function ccRandomRoll() {
        // 把当前卡的 era 传给后端, 让随机池按年代抽 (避免现代卡出现 1920s 租界地名)
        var card = getCard();
        var era = (card && (card.era === "modern" || card.era === "1920s" || card.era === "custom")) ? card.era : "modern";
        fetchJSON("/trpg/api/character-card/random?era=" + encodeURIComponent(era)).then(function (r) {
            if (!r.ok || !r.body || !r.body.card) {
                showError("生成随机调查员失败：" + JSON.stringify((r.body && r.body.errors) || r.body));
                return;
            }
            var newCard = r.body.card;
            // version 兜底 + 保留用户已选 era (后端默认 modern, 这里若用户原本是别的就尊重之)
            newCard.version = 2;
            if (era === "1920s" || era === "custom") {
                newCard.era = era;
                if (era === "custom") newCard.era_custom = (card && card.era_custom) || "";
            }
            setCard(newCard);
        });
    }

    function ccSave() {
        var card = getCard();
        // 服务端权威校验
        fetchJSON("/trpg/api/character-card/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(card)
        }).then(function (r) {
            if (!r.ok || !r.body || !r.body.ok) {
                showError("服务端校验未通过：\n- " + ((r.body && r.body.errors) || ["unknown"]).join("\n- "));
                return;
            }
            var normalized = r.body.normalized || card;
            saveToLocalStorage(normalized);
            window._ccCard = normalized;
            updateBannerStatus();
            closeCharacterCardModal();
        }).catch(function (e) {
            showError("保存失败：" + (e && e.message));
        });
    }

    function ccExport() {
        var card = getCard();
        var blob = new Blob([JSON.stringify(card, null, 2)], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        var ts = new Date().toISOString().replace(/[:.]/g, "-").substring(0, 19);
        var name = sanitizeText(card.name, NAME_CAP) || "investigator";
        a.href = url;
        a.download = "card_" + name + "_" + ts + ".json";
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(url); document.body.removeChild(a); }, 0);
    }

    function ccExportTemplate() {
        // 拉取服务端生成的带 _hint 注释的空白模板，让 AI 离线协助填写
        fetchJSON("/trpg/api/character-card/template").then(function (r) {
            if (!r.ok || !r.body || !r.body.template) {
                showError("获取模板失败：" + JSON.stringify((r.body && r.body.error) || r.body));
                return;
            }
            var template = r.body.template;
            var blob = new Blob([JSON.stringify(template, null, 2)], { type: "application/json" });
            var url = URL.createObjectURL(blob);
            var a = document.createElement("a");
            var ts = new Date().toISOString().replace(/[:.]/g, "-").substring(0, 19);
            a.href = url;
            a.download = "coc7_card_template_" + ts + ".json";
            document.body.appendChild(a);
            a.click();
            setTimeout(function () { URL.revokeObjectURL(url); document.body.removeChild(a); }, 0);
        }).catch(function (e) {
            showError("导出模板失败：" + (e && e.message));
        });
    }

    function ccImport(e) {
        var f = e.target.files && e.target.files[0];
        if (!f) return;
        if (f.size > 16 * 1024) {
            showError("文件过大（>16KB），导入拒绝");
            e.target.value = "";
            return;
        }
        var reader = new FileReader();
        reader.onload = function () {
            try {
                var parsed = JSON.parse(reader.result);
                fetchJSON("/trpg/api/character-card/validate", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(parsed)
                }).then(function (r) {
                    if (!r.ok || !r.body || !r.body.ok) {
                        showError("导入未通过校验：\n- " + ((r.body && r.body.errors) || ["unknown"]).join("\n- "));
                        return;
                    }
                    setCard(r.body.normalized || parsed);
                });
            } catch (err) {
                showError("文件 JSON 解析失败：" + err.message);
            }
        };
        reader.readAsText(f);
        e.target.value = "";
    }

    function ccClear() {
        if (!confirm("确定清除当前调查员卡？")) return;
        clearLocalStorage();
        window._ccCard = emptyCard();
        renderAll();
        updateBannerStatus();
    }

    function updateBannerStatus() {
        var status = document.getElementById("character-card-banner-status");
        if (!status) return;
        var card = loadFromLocalStorage();
        if (card && card.name) {
            status.textContent = "已配置：" + card.name + "（" + (card.profession || "") + "）";
            status.classList.add("has-card");
        } else {
            status.textContent = "未配置 — 将使用默认调查员";
            status.classList.remove("has-card");
        }
    }

    // === 暴露给主流程的接口 ===

    function getCurrentCharacterCard() {
        // 进入模组时被 app.js startGame 调用，注入 /trpg/api/start 请求体
        return loadFromLocalStorage();
    }

    // === 全局绑定（供 onclick 使用） ===

    window.openCharacterCardModal = openCharacterCardModal;
    window.closeCharacterCardModal = closeCharacterCardModal;
    window.ccRandomRoll = ccRandomRoll;
    window.ccRollAttributes = ccRollAttributes;
    window.ccSave = ccSave;
    window.ccExport = ccExport;
    window.ccExportTemplate = ccExportTemplate;
    window.ccImport = ccImport;
    window.ccClear = ccClear;
    window.ccOnInput = ccOnInput;
    window.ccOnProfessionChange = ccOnProfessionChange;
    window.ccOnEraChange = ccOnEraChange;
    window.ccOnEraCustomInput = ccOnEraCustomInput;
    window.ccAddInventoryItem = ccAddInventoryItem;
    window.getCurrentCharacterCard = getCurrentCharacterCard;

    document.addEventListener("DOMContentLoaded", updateBannerStatus);
})();
