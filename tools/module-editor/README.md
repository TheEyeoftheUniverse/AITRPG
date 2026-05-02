# 模组编辑器

一个**完全本地、零依赖、不调 LLM** 的模组可视化工具。给模组作者用来：

- 可视化拖拽地图节点，画出符合空间直觉的房间布局
- 不启动游戏服务器，"假装走入"任意房间，查看场景的硬文本（description / objects / exits / npc_present_description / first_entry_blocked.text 等）以核对模组内容
- 把改动导出成 JSON，直接拷回 `modules/` 替换原模组

## 怎么打开

直接双击 `index.html`，浏览器会用 `file://` 协议打开。**不需要任何后端在跑**。Chrome / Edge / Firefox 都行。

> 如果某些浏览器在 `file://` 下拒绝加载脚本，开 Chrome 时加 `--allow-file-access-from-files`，或者把 `tools/module-editor/` 目录起一个简单的 HTTP 服务器（如 `python -m http.server`）。一般情况下不需要。

## 用法

1. **导入模组**：点顶栏 "📂 导入" 选 `modules/default_module.json`，或直接把文件拖进页面
2. **看地图**：所有 location 都会画出来（包括 hidden 的，会自动虚化），按 `floor` 分上下楼层带，已有 `map_position` 的会按你的坐标摆，没有的按 BFS 自动排
3. **拖拽节点**：抓住任意房间拖到想要的位置，松手时坐标自动吸附到整数 `(col, row)`，同步更新内存里的模组副本
4. **走入房间**：点击节点，中间下方会立刻显示该房间的所有硬文本，金色椭圆标记是"你刚走入"
5. **导出**：点 "💾 导出" 下载新 JSON。文件名是 `<原名>_edited.json`

## 楼层边界规则

拖拽节点时，**Y 方向被 clamp 在所属楼层的 band 范围内**，不允许越过下层楼板。想给某个房间换楼层？目前还得手编 JSON 改 `floor` 字段（Phase 2 会在右栏加图形化的 floor 编辑器）。

## map_position 是什么

```jsonc
{
    "name": "次卧",
    "floor": 2,
    "map_position": { "col": 2, "row": -1 }
}
```

- `col`：同楼层内的列号，0 起步，可负
- `row`：选填默认 0，子行偏移，支持把 hub 房间分上下两边（如走廊上方一间、下方一间）

完整说明见 `modules/README.md` 的 locations 字段表。

## 已知限制（Phase 1）

- **只能改 `map_position`**，其他字段（description / exits / npc 等）一字不动
- 拖拽**不能跨楼层**（要换 floor，目前还得手编 JSON）
- 没有撤销/重做（建议每改完几个房间就导出一次做版本快照）
- 微场景节点目前不支持拖拽（它们的位置自动跟着 parent_location）
- `file://` 下浏览器没法直接写文件，导出走的是浏览器下载流程，落到默认下载目录

## 后续规划

左栏和右栏的扩展位（HTML 里的 `data-extension-slot` 节点）已经预留，未来这些功能会陆续上：

| 位置 | 功能 |
| --- | --- |
| 左栏 | 模组结构树（locations / objects / npcs），点击聚焦 |
| 左栏 | 字段在线编辑（description / exits / floor 等） |
| 右栏 | 给玩家加物品 / 加 flag（mock） |
| 右栏 | 测试 reveal_conditions / first_entry_blocked / object.requires 是否能命中 |
| 右栏 | 模拟门锁 / 守卫 / 管家激活 |
| 右栏 | 切换调查员人设 |

如果有别的需求，告诉我们。

## 文件结构

```
tools/module-editor/
├── index.html              单页入口
├── css/editor.css          样式
├── lib/cytoscape.min.js    Cytoscape 本地副本 (跟 webui/static/lib/ 一致)
└── js/
    ├── state.js            数据源 + pub-sub
    ├── module-io.js        导入导出
    ├── map-canvas.js       地图 + 拖拽
    ├── scene-panel.js      场景硬文本
    ├── left-panel.js       左栏元信息
    ├── right-panel.js      右栏占位 + 当前节点信息
    └── main.js             启动装配
```
