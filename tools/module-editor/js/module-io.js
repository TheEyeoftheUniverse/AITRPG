/**
 * Editor.ModuleIO — JSON 导入 / 导出
 *
 * file:// 协议下的考量:
 *   - 导入: 只能靠 <input type="file"> 或拖放, 不能 fetch
 *   - 导出: Blob + URL.createObjectURL + <a download>, 跨浏览器都行
 *   - JSON.stringify 默认就不转义中文 (ensure_ascii=False 等价物), 缩进 4 空格
 *
 * 数据保真:
 *   导出时直接序列化 state.module 整体, 不做字段过滤. 这样作者手写的所有字段
 *   (description / npc_reactions / preset_tasks / endings 等) 都原样保留,
 *   编辑器只可能让 locations[*].map_position 的值发生变化.
 */
window.Editor = window.Editor || {};
window.Editor.ModuleIO = (function () {
    "use strict";

    const State = window.Editor.State;

    function _readFileAsText(file) {
        return new Promise(function (resolve, reject) {
            const reader = new FileReader();
            reader.onload = function () { resolve(reader.result); };
            reader.onerror = function () { reject(reader.error); };
            reader.readAsText(file, "utf-8");
        });
    }

    /** 从 File 对象解析模组 JSON 并装入 state. 抛错时返回 Promise.reject. */
    async function importFromFile(file) {
        if (!file) throw new Error("没有选择文件");
        const text = await _readFileAsText(file);
        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            throw new Error("JSON 解析失败: " + e.message);
        }
        if (!data || typeof data !== "object" || !data.locations || typeof data.locations !== "object") {
            throw new Error("文件结构不像模组 JSON: 顶层缺 locations 字段");
        }
        // 记一下文件名, 导出时保留原名 (加 _edited 后缀)
        State.loadModule(data);
        State.getState().runtime.lastImportFilename = file.name;
        return data;
    }

    /** 从 input/dropfile 入口接 file event 用的便利包装 */
    function bindFileInput(inputEl, onError) {
        inputEl.addEventListener("change", async function (e) {
            const file = e.target.files && e.target.files[0];
            if (!file) return;
            try {
                await importFromFile(file);
            } catch (err) {
                console.error(err);
                if (onError) onError(err);
                else alert(err.message);
            }
            // 允许重选同一文件
            inputEl.value = "";
        });
    }

    /** 拖放整文件到页面上的处理 */
    function bindDropZone(rootEl, overlayEl, onError) {
        let dragDepth = 0;

        function showOverlay() { if (overlayEl) overlayEl.classList.remove("hidden"); }
        function hideOverlay() { if (overlayEl) overlayEl.classList.add("hidden"); }

        rootEl.addEventListener("dragenter", function (e) {
            // 仅响应文件拖入 (不响应文本/链接拖动)
            if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
            e.preventDefault();
            dragDepth++;
            showOverlay();
        });
        rootEl.addEventListener("dragover", function (e) {
            if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
        });
        rootEl.addEventListener("dragleave", function () {
            dragDepth = Math.max(0, dragDepth - 1);
            if (dragDepth === 0) hideOverlay();
        });
        rootEl.addEventListener("drop", async function (e) {
            if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files[0]) return;
            e.preventDefault();
            dragDepth = 0;
            hideOverlay();
            const file = e.dataTransfer.files[0];
            try {
                await importFromFile(file);
            } catch (err) {
                console.error(err);
                if (onError) onError(err);
                else alert(err.message);
            }
        });
    }

    /** 把当前 state.module 序列化并触发浏览器下载 */
    function exportToFile() {
        const s = State.getState();
        if (!s.module) {
            alert("还没加载模组, 没东西可以导出");
            return;
        }
        // 4 空格缩进, 中文不转义
        const text = JSON.stringify(s.module, null, 4) + "\n";
        const blob = new Blob([text], { type: "application/json;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = _suggestExportFilename(s);
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        // 异步 revoke, 给浏览器一帧消化
        setTimeout(function () { URL.revokeObjectURL(url); }, 0);
    }

    function _suggestExportFilename(s) {
        const base = (s.runtime.lastImportFilename || "module.json").replace(/\.json$/i, "");
        return base + "_edited.json";
    }

    return {
        importFromFile: importFromFile,
        bindFileInput: bindFileInput,
        bindDropZone: bindDropZone,
        exportToFile: exportToFile
    };
})();
