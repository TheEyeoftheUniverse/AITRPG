# 模组编辑器

一个完全本地、不调 LLM 的模组可视化工具。当前入口策略是：

- WebUI 首页按钮暂时关闭
- 仍然可以直接访问 `/trpg/module-editor/`
- 也可以直接打开 `tools/module-editor/index.html`

它现在主要用来：

- 导入/导出模组 JSON
- 自由拖拽地图节点，调整 `map_position`
- 预览地图分组、节点视觉样式、边样式和微场景入口
- 走入任意场景，查看硬文本和结构字段
- 编辑节点视觉字段与出口视觉字段，再导出新 JSON

## 怎么打开

有两种方式：

1. WebUI 服务器已启动时，直接访问 `/trpg/module-editor/`
2. 直接双击 `index.html` 用 `file://` 打开

Chrome / Edge / Firefox 都能用，单独打开时不需要后端。

> 如果某些浏览器在 `file://` 下拒绝加载脚本，开 Chrome 时加 `--allow-file-access-from-files`，或者把 `tools/module-editor/` 目录起一个简单的 HTTP 服务器（如 `python -m http.server`）。

## 用法

1. **导入模组**：点顶栏“导入”，选择 `modules/*.json`，或直接把文件拖进页面
2. **看地图**：所有 location 都会画出来，包含 hidden 和微场景；按 `map_group` 分组，未显式设置时会从 `floor` 自动派生
3. **拖拽节点**：直接拖动房间节点，位置会写回 `map_position.col / row`；现在支持浮点，不吸附、不限楼层
4. **走入房间**：点击节点，中间下方会显示该房间的硬文本和结构字段
5. **改视觉字段**：右栏可以改 `icon`、`displayColor`、`displayAlpha`、`badges`，也能改每条 `exit` 的 `style` / `direction`
6. **导出**：点“导出”下载新 JSON，文件名是 `<原名>_edited.json`

## `map_position` 是什么

```jsonc
{
    "name": "次卧",
    "floor": 2,
    "map_position": { "col": 2.5, "row": -1.25 }
}
```

- `col`：横向位置，支持浮点
- `row`：纵向位置，支持浮点
- 编辑器不会强制整数吸附，也不会把节点限制在某一层带内部

完整说明见 `modules/README.md` 的 locations 字段表。

## 已知限制

- 没有撤销/重做
- 仍然以 JSON 导入/导出为主，不会直接写回 `modules/`
- 运行时逻辑不会在这里完整模拟；它偏向结构校对和地图编辑
- `file://` 下浏览器没法直接写文件，导出仍然走浏览器下载流程
- 微场景节点位置仍然跟着 `parent_location` 走，不单独拖拽

## 当前结构

- 左栏：模组元信息、地图组只读列表、结构树导航
- 中栏：Cytoscape 地图 + 场景硬文本预览
- 右栏：当前节点信息、玩家视角预览卡、节点/边视觉字段编辑

## 文件结构

```text
tools/module-editor/
├── index.html              单页入口
├── css/editor.css          样式
├── lib/cytoscape.min.js    Cytoscape 本地副本
└── js/
    ├── state.js            数据源 + pub-sub
    ├── module-io.js        导入导出
    ├── map-canvas.js       地图 + 拖拽 + overlay
    ├── scene-panel.js      场景硬文本预览
    ├── left-panel.js       左栏元信息 / 结构树
    ├── right-panel.js      节点视觉编辑 / 出口样式编辑
    └── main.js             启动装配
```
