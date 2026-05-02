/**
 * Editor.main — 启动装配
 *
 * 顺序:
 *   1. 各 panel 初始化 (订阅 state 事件)
 *   2. 顶栏按钮绑定
 *   3. 文件输入 / 拖放绑定
 *   4. 键盘快捷键 (留扩展位)
 *
 * 装配完后等用户导入 JSON, 各 panel 会自己根据 module:loaded 事件渲染.
 */
(function () {
    "use strict";

    function $(id) { return document.getElementById(id); }

    document.addEventListener("DOMContentLoaded", function () {
        const State = window.Editor.State;
        const ModuleIO = window.Editor.ModuleIO;
        const MapCanvas = window.Editor.MapCanvas;
        const ScenePanel = window.Editor.ScenePanel;
        const LeftPanel = window.Editor.LeftPanel;
        const RightPanel = window.Editor.RightPanel;

        // 1) 各 panel 初始化 (订阅事件)
        MapCanvas.init("map-canvas");
        ScenePanel.init("scene-panel");
        LeftPanel.init();
        RightPanel.init();

        // 2) 顶栏按钮
        $("btn-import").addEventListener("click", function () { $("file-input").click(); });
        $("btn-export").addEventListener("click", function () { ModuleIO.exportToFile(); });
        $("btn-reset").addEventListener("click", function () {
            if (confirm("清空当前模组? 未导出的修改会丢失.")) State.reset();
        });
        $("btn-fit").addEventListener("click", function () { MapCanvas.fit(); });

        // 3) 文件 IO
        ModuleIO.bindFileInput($("file-input"));
        ModuleIO.bindDropZone(document.body, $("drop-overlay"));

        // 4) 模组加载/清空时切换按钮可用性 + 顶栏标题
        State.on("module:loaded", function (payload) {
            const hasModule = !!(payload && payload.module);
            $("btn-export").disabled = !hasModule;
            $("btn-reset").disabled = !hasModule;
            const display = $("module-name-display");
            if (hasModule) {
                const info = payload.module.module_info || {};
                display.textContent = info.name || "(未命名模组)";
            } else {
                display.textContent = "未加载模组";
            }
        });

        // 5) Phase 2 扩展位: 把全局 Editor 对象暴露好让插件式扩展能自己注册
        //    将来要加新工具, 可以这样写:
        //      window.Editor.MyDebugTool = (function () { ... })();
        //      然后在它的 init 里: document.querySelector('[data-extension-slot="right-tools"]').appendChild(...)
        //    然后在这里加一行 MyDebugTool.init();
        //    无需改其他文件.

        console.log("[Editor] booted");
    });
})();
