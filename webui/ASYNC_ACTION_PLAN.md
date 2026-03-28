# /api/action 异步化实现计划

## 背景与目标

**问题**：`/api/action` 当前是同步的（`async with lock` 直到三层 AI 全部完成才返回）。三层 AI 总耗时可能超过 60-120 秒，网络层（Nginx/代理）会掐断 HTTP 长连接，导致前端报网络错误，但后端 AI 仍在正常运行。

**目标**：
1. `POST /api/action` 立即返回 `202 Accepted` + `job_id`
2. 三层 AI 在后台 asyncio Task 中运行
3. 前端通过已有的 `GET /api/progress` 轮询进度（无需新增端点）
4. 新增 `GET /api/action/result` 供前端在进度完成后取结果
5. `/api/retry` 同样异步化（同样立即返回202，结果从 `/api/action/result` 取）

---

## 后端修改（server.py）

### 1. 新增模块级存储

```python
# 已有：_session_locks, _web_sessions
# 新增：
_action_results: dict[str, dict] = {}   # cookie_id -> result payload 或 error payload
```

### 2. 改造 `api_action`

**原逻辑**（伪码）：
```
async with lock:
    result = await plugin._process_action_core(...)
    ... 后处理 ...
    return jsonify({success: True, ...})
```

**新逻辑**：
```
# 1. 检查锁（不等待）
if lock.locked():
    return jsonify({"error": "正在处理上一个行动，请稍候"}), 429

# 2. 清除上次结果
_action_results.pop(cookie_id, None)

# 3. 定义 _run_action() 协程：
#    async with lock:
#        result = await plugin._process_action_core(...)
#        ... 完整后处理（历史更新、持久化等）...
#        _action_results[cookie_id] = {"success": True, ...}
#    except Exception:
#        _action_results[cookie_id] = {"error": ..., "can_retry": ...}

# 4. asyncio.ensure_future(_run_action())
# 5. return jsonify({"job_id": cookie_id, "status": "accepted"}), 202
```

**注意**：`_run_action` 内部的完整后处理逻辑（历史追加、`_persist_web_session`、chat_messages、theatrical 效果、workflow 缓存、state/map 查询、dice_rolls 构建）必须全部保留，只是移入后台 task。

### 3. 新增 `GET /api/action/result`

```python
@app.route("/trpg/api/action/result", methods=["GET"])
async def api_action_result():
    cookie_id = _get_cookie_id()
    if not cookie_id:
        return jsonify({"error": "无有效会话"}), 400
    result = _action_results.get(cookie_id)
    if result is None:
        # 还在运行或从未发起
        return jsonify({"status": "pending"}), 202
    return jsonify(result)
```

### 4. 改造 `api_retry`

同 `api_action`：立即返回202，后台 task 运行，结果写入 `_action_results[cookie_id]`。

### 5. `_action_results` 清理

在 `_cleanup_stale_sessions` 中同步清理过期 cookie_id 对应的 `_action_results` 条目（已有 session 清理逻辑，参照处理）。

---

## 前端修改（static/js/app.js）

### 当前轮询逻辑

前端已有 `GET /api/progress` 轮询（在 AI 运行时显示进度面板）。

### 新逻辑

1. `sendAction()` 发 POST `/api/action`
   - 收到 `202` + `{job_id, status: "accepted"}`：开始轮询
   - 收到其他（400/429/500）：直接报错显示

2. 轮询循环（替换原来等待 POST 响应的逻辑）：
   - 每 1.5s 调 `GET /api/progress` 更新进度面板
   - 同时每次也调 `GET /api/action/result`：
     - `202 {status: "pending"}`：继续轮询
     - `200 {success: true, ...}`：停止轮询，处理结果（原 handleActionResponse 逻辑）
     - `200 {error: ...}`：停止轮询，显示错误（原错误处理逻辑）

3. retry 按钮同理：POST `/api/retry` → 202 → 轮询 `/api/action/result`

### 关键细节

- 轮询超时：设 300s 上限，超时后提示用户刷新
- 取到结果后调用原有的 `handleActionResponse(data)` 函数（不重复实现）
- 进度面板显示逻辑不变

---

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `webui/server.py` | 新增 `_action_results` dict；改造 `api_action`；新增 `api_action_result` 路由；改造 `api_retry` |
| `webui/static/js/app.js` | `sendAction` 改为处理202；新增轮询循环；`retryAction` 同步改造 |

---

## 实现顺序

1. [x] 恢复干净的 server.py（git checkout HEAD）
2. [ ] 修改 server.py：`_action_results` + `api_action` 异步化 + `api_action_result` 路由 + `api_retry` 异步化
3. [ ] 修改 app.js：sendAction/retryAction 改轮询
4. [ ] 本地测试验证
