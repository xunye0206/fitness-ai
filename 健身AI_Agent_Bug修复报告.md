# 健身 AI Agent · Bug 修复报告

**项目**：个人健身 AI Agent（后端 M1–M9 + 前端 WorkBuddy 风格 SPA）
**整理周期**：2026-07-07 ~ 2026-07-08
**模型链路**：DeepSeek（推理）+ Qwen-VL-Max（视觉）+ text-embedding-v3（向量）
**说明**：本报告覆盖调试期用户实测发现的全部缺陷，不含 M1–M9 功能开发本身。

---

## 一、概览

| 维度 | 数量 |
|---|---|
| 缺陷修复总数 | **12 项**（其中 2 项含新功能） |
| 后端 / 大模型链路 | 6 项 |
| 前端 / 交互 | 6 项 |
| 涉及提交 | `4784896` `76eece4` `44eb055` `01069b7` `c338051` `06c033d` `18956f4` `73083eb` `1195263` `ace0973` `e830440` |
| 测试状态 | 全部回归用例 50 passed 全绿 |

**按阶段**

| 提交 | 一句话结论 |
|---|---|
| `4784896` | 去重导航栏 AI 教练 + 聊天 60s 超时防卡死 |
| `76eece4` | AI 教练改 SSE 流式输出，首 token 极早到达 |
| `44eb055` | 聊天图片识别加 SSE 状态推送，解决静默失败 |
| `01069b7` | 视觉识别全链路加诊断日志，错误不再为空 |
| `c338051` | 修 openai_compat 缩进丢失 → 视觉 API 不再 NotImplementedError |
| `06c033d` | 修 _msg_to_dict 缺 self → 流式输出不再返回空 |
| `18956f4` | 流式 tool_call 改用 index 做 key → log_training 不再记 0 分钟 |
| `73083eb` | 修饮食表头重复 + 报告 Markdown 不渲染 |
| `1195263` | 修饮食时间偏移 8h + 按时间自动判断餐次（含 feat） |
| `ace0973` | CDN 被墙 → 改本地 Markdown 解析器 |
| `e830440` | 教练聊天 Markdown 渲染 + 旧数据餐次兜底 |

---

## 二、后端 / 大模型链路修复明细

### ① 视觉 API 一直抛 NotImplementedError
- **现象**：图片识别功能完全不可用，调用即报 `NotImplementedError`。
- **根因**：`app/llm/providers/openai_compat.py` 在编辑时**缩进丢失**，`see` / `embed` / `_msg_to_dict` 三个方法从类内方法变成了**模块级函数**，类内调用 `self.see(...)` 指向了未实现的占位方法。
- **修复**：恢复三个方法的类内缩进，重新挂回 `OpenAICompatProvider`。
- **提交**：`c338051`
- **关键文件**：`app/llm/providers/openai_compat.py`

### ② 流式输出返回空（教练不发话）
- **现象**：聊天改为流式后，教练回复文本为空或"教练暂时没回应"。
- **根因**：`_msg_to_dict` 方法**缺少 `self` 参数**，且内部 3 处调用未加 `self.` 前缀，触发 `NameError` 被外层 `except` **静默吞掉**，最终 delta 列表为空。
- **修复**：补 `self` 参数 + 3 处调用补全 `self.` 前缀。
- **提交**：`06c033d`
- **关键文件**：`app/llm/providers/openai_compat.py`

### ③ 图片识别失败但错误信息为空
- **现象**：识别失败时前端只显示"识别失败："，没有原因，无法排查。
- **根因**：逐层异常被 `except Exception` 吞掉，未把原始报错透出。
- **修复**：`vision_node` / `see` / `diet api` 三层加诊断日志，记录真实异常再向上抛。
- **提交**：`01069b7`
- **关键文件**：`app/agent/nodes/vision_node.py`、`app/llm/providers/openai_compat.py`、`app/modules/diet/api.py`

### ④ 聊天图片识别静默失败（用户感知不到）
- **现象**：发图后教练"好像没加载成功"，没有任何进度或结果反馈。
- **根因**：图片处理过程纯后端执行，前端无状态推送。
- **修复**：加 SSE `status` 事件（"正在识别" / "✅ 识别成功" / "⚠️ 识别失败"），让用户实时看到进度。
- **提交**：`44eb055`
- **关键文件**：`app/agent/api.py`、`static/index.html`（SSE 解析）

### ⑤ log_training 记成 0 分钟 / 0 kcal
- **现象**：教练识别到"跑了 30 分钟步配速 5'50""，回复文本正确，但底部动作卡显示"训练 0 分钟（medium，0 kcal）"。
- **根因**：流式 `tool_call` 累加器用 `tc.id` 做 key。**DeepSeek 流式协议仅首片带 id，后续 `arguments` 片的 `id=None`**，被兜底成 `_t{len(acc)}` 新 key → arguments 碎片分散到无 name 的条目 → 合并时丢弃 → `ToolCall.arguments = {}`。
- **修复**：改用 `enumerate(tool_calls)` 的**下标 index** 做累加 key，同一工具的所有增量片段（id/name/arguments）归并到同一条目。
- **提交**：`18956f4`
- **关键文件**：`app/llm/providers/openai_compat.py`

### ⑥ 饮食记录时间偏移 8 小时
- **现象**：下午录入的饮食，时间显示为错误时刻。
- **根因**：`created_at` 后端存 UTC（`datetime.now(timezone.utc)`），但 SQLite `DateTime` 列默认 `timezone=False`，aware 时间入库被当本地 naive **丢弃时区信息**，前端再按浏览器本地解析 → **整体偏移 8 小时**。
- **修复**：`created_at` 列显式声明 `DateTime(timezone=True)` 保留时区；前端统一用 `toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'})` 强制北京时间。
- **提交**：`1195263`
- **关键文件**：`app/modules/diet/domain.py`、`app/modules/diet/schemas.py`、`app/core/db.py`、`static/index.html`

---

## 三、前端 / 交互修复明细

### ⑦ 导航栏重复 AI 教练 + 聊天卡死
- **现象**：左侧导航出现两个"AI 教练"入口；聊天长时间"思考中"不返回，界面卡死。
- **根因**：导航栏渲染逻辑重复插入；聊天请求无超时，真模型慢时一直挂起。
- **修复**：去重导航项；聊天请求加 60 秒超时，超时给出友好提示而非无限等待。
- **提交**：`4784896`
- **关键文件**：`static/index.html`、`app/agent/api.py`

### ⑧ 教练回复慢、体验差
- **现象**：每条消息要等整段生成完才一次性返回，体感像"死了"。
- **根因**：原为非流式一次性返回。
- **修复**：`/agent/chat` 改为 SSE `StreamingResponse`，逐字推送 delta；同时关闭每次对话的 embedding 往返，首 token 极早到达。
- **提交**：`76eece4`
- **关键文件**：`app/agent/api.py`、`app/llm/router.py`

### ⑨ 饮食历史表格表头无限重复
- **现象**："烤鸡胸肉配蔬菜"每字一行，表头（时间/食物/热量…）在右侧无限重复。
- **根因**：`_loadList` 中 `arr.map(fmtFn)` 把 `(元素, 下标)` 两个参数传给回调，`_fmtDiet(x, isHead)` 的 `isHead` 被下标 1/2/3… 抢走（均为 truthy）→ 每行都被当表头渲染。
- **修复**：`arr.map(fmtFn)` → `arr.map(x => fmtFn(x))`，显式只传元素。
- **提交**：`73083eb`
- **关键文件**：`static/index.html`

### ⑩ 报告页 Markdown 不渲染（裸显）
- **现象**：报告内容显示原始 `**加粗**`、`- 列表` 文本。
- **根因**：项目无 Markdown 解析库，`d.summary` / `d.advice` 直接塞 `innerHTML`。
- **修复（第一轮）**：引入 marked.js（CDN）+ `renderMd()` 辅助函数 + 样式。
- **修复（第二轮，`ace0973`）**：CDN（jsdelivr）国内加载不稳定/被墙，`marked` 未定义降级回原文 → **移除 CDN，改纯本地正则解析器**（零网络依赖）。
- **提交**：`73083eb` → `ace0973`
- **关键文件**：`static/index.html`（`renderMd()`）

### ⑪ 教练聊天回复 Markdown 不渲染
- **现象**：教练聊天气泡里的 `**加粗**`、`- 列表` 仍裸显（报告页已修，聊天没修）。
- **根因**：`doChat` 中每个 delta 用 `createTextNode(ev.text)` 写入气泡（纯文本节点不解析 HTML），且 `renderMd` 只覆盖报告页路径。
- **修复**：流式阶段保持逐字（保证体验），**流结束后整体用 `renderMd(full)` 重新渲染**气泡为富文本。
- **提交**：`e830440`
- **关键文件**：`static/index.html`（`doChat` / `appendChat`）

### ⑫ 饮食餐次全显示"其他"
- **现象**：饮食表格餐次列全是"其他"。
- **根因**：旧数据在 `meal_type` 字段加入前创建，ALTER 补列后默认 `'other'`；新记录正常。
- **修复**：前端 `_fmtDiet` 兜底——`meal_type` 为"其他"且有时间时，按北京时间小时动态推断餐次（与后端 `infer_meal_type` 同规则）。
- **新增功能（同批）**：`infer_meal_type(dt)` 按记录时刻自动归类（早餐 5–11 / 午餐 11–15 / 下午茶 15–18 / 晚餐 18–22 / 宵夜 22–5），两条录入路径（图片识别 + 聊天记录）自动赋值，无需手动选。
- **提交**：`1195263` + `e830440`
- **关键文件**：`app/modules/diet/service.py`、`app/agent/tools.py`、`static/index.html`

---

## 四、根因共性归类（经验沉淀）

| 共性陷阱 | 命中 Bug | 防范措施 |
|---|---|---|
| **流式协议解析坑**（DeepSeek 增量片段 id/name/arguments 分片到达） | ⑤ 幽灵 tool_call、② 流式空 | 累加器用稳定下标 index，过滤空 name，合并前完整拼接 arguments |
| **Python 缩进 / self 陷阱** | ① 缩进丢失、② 缺 self | 关键方法加单测覆盖调用路径；except 不要裸吞（至少记日志） |
| **时区意识缺失** | ⑥ 偏移 8h | 一律用带时区类型 + 显示端显式指定时区，不依赖本地 naive |
| **前端 map 隐式参数污染** | ⑨ 表头重复 | 回调只接需要的参数，避免 `arr.map(fn)` 隐式传 (el, idx) |
| **外部 CDN 不可靠** | ⑩ 被墙降级 | 核心渲染能力本地自实现，零外部网络依赖 |
| **纯文本节点不渲染 HTML** | ⑪ 聊天裸显 | 流式阶段用 textNode，结束后统一 innerHTML 渲染 |

---

## 五、遗留 / 下一步

- [ ] **M10 长期画像 wiki 模块**：Agent 自动维护叙事记忆（画像/反思/洞察），分块索引进向量库，聊天与报告语义召回（任务 #45，待设计）。
- [ ] **真机复测**：重启后端逐一验证上述修复，尤其 ⑤ 训练参数、⑥ 时间、⑫ 餐次。
- [ ] **身份档案收尾**：`BOOTSTRAP.md` 待删，仍缺用户"怎么称呼"与"所在城市"两项。
- [ ] **教练夸页**：修复同批完成，建议复测确认 `page-coach` 不再误覆盖其他页。

---

*报告由调试记录整理，所有修复均经 `pytest` 回归（50 passed）并本地 git 提交（未推送远端）。*
