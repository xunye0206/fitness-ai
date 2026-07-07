# Agent 工程要点（设计稿）

> 适用：本项目（个人级、SQLite + wiki 混合存储、功能模块 + Agent 横切编排、LLM 可插拔）。
> 定位：这是**设计讨论稿**，把 6 个 Agent 工程维度落到本项目的具象做法；待定稿后再变代码。
> 关联：《代码规范.md》《健身AI_Agent_策划书_升级版.md》。

---

## 1. 上下文工程 Context Engineering

**一句话**：在有限的上下文窗口里，喂最小必要信息、最高信噪比。

**本项目落地**
- LangGraph 的 `state` 就是 Working Context 载体——结构化为 pydantic/TypedDict，**绝不把整段聊天原文塞进 prompt**。
- 组装器 `agent/context.py: build_context(state)` 固定分块、定序输出：
  1. `system`（稳定，Prefix Cache 友好）：角色设定 + 护栏规则（固定文本，争取缓存命中）
  2. `user_profile`（来自 `wiki/profile.md`，压成要点，不全文）
  3. `recent_aggregates`（SQL 聚合结果，如"近 7 天均摄入/消耗/缺口"，**不是原始行**）
  4. `current_event / task`（本次要决策的事，如"是否触发恢复预警"）
  5. `relevant_wiki`（语义命中的反思/洞察页摘要）
- **关键信息位置**：护栏规则 + 当前事件放**最前**（primacy），最新一条用户消息放**最后**（recency）。
- **历史压缩**：跨周对话不传全文，用 wiki 摘要 + 近期聚合替代。
- **Prefix Cache**：`system + 护栏` 作为不变前缀；变化部分（事件、聚合）放后面，保证前缀命中、省重复 token。

---

## 2. 记忆系统 Memory

**一句话**：让 Agent 像人一样分层记事，写入/读取/遗忘都有策略。

**三层映射到本项目**
| 层 | 本项目载体 | 生命周期 |
|---|---|---|
| Working（工作记忆） | LangGraph `state`（进程内存） | 单次运行，结束即弃 |
| Short-term（短期记忆） | SQLite `session` 表（带日期，跨日**归档**而非销毁；未提交项转入 recent 持有区） | 当日临时上下文；长期记忆永久不丢 |
| Long-term（长期记忆） | 结构化：SQLite 全量（用户/饮食/训练/睡眠/推送）<br>叙事：`wiki/*.md`（profile / reflections / insights） | 持久 |

**写入策略**
- 结构化：每次记录/同步即写（落库即真）。
- wiki：**避免每次调用都写**（降成本、防噪声）。触发点：①用户问卷/修正 → 更新 `profile`；②周报生成时把"本周洞察"追加 `reflections`；③出现稳定模式（如"一熬夜次日必崩"）→ 写 `insights`。
- **升档优先**：session 里的"当日未归档临时上下文"（进行中的修正、今日口头目标）在当日运行中会被**升档**到 SQLite / wiki；session 跨日归档时，凡用户有意义的信息早已不在 session 里，故清空不会造成失忆。

**读取策略**
- 运行开始读 `wiki/profile`；按需 SQL 聚合；极少读全文历史。
- **召回窗口（关键，防"变笨"）**：`build_context` 默认拉取**近 7 天**的聚合（摄入/消耗/训练/睡眠趋势）+ 语义命中的 wiki 页，使 Agent 每轮都"带着上周的记忆"开工，而不是只看得见今天。窗口长度可配置（默认 7，可上调）。
- **为什么 session 跨日清空 ≠ 模型变笨**：真正的"记忆"在 SQLite（全量结构化）+ wiki（叙事洞察），这两者永久；session 仅是单次日内的临时草稿。Agent 每轮通过召回窗口从长期记忆取近期上下文，与 session 是否清空无关。

**遗忘 / 压缩策略**
- `wiki/reflections` 按季度摘要合并，旧细节折叠。
- SQLite 原始逐分钟体征保留窗口（如 90 天），超出转日/周聚合后丢弃明细。
- 失效画像字段标记不删（保留可追溯）。

**落点**：`core/db.py`（SQLite）、`wiki/`（叙事）、`agent/state.py`（working）、一个 `agent/memory.py` 协调读写。

---

## 3. 工具调用 Tool Calling

**一句话**：让 Agent 能"动手"，但数量受控、失败可控、事前有约束。

**每个 skill = 一个 tool**：注册到 `agent/skills.py`，必须带 JSON Schema（name / description / params / returns / errors）。

**数量上限 ~10**（用统一聚合入口合并 getter，保持 ≤10）：
1. `record_diet`（拍照估算入库）
2. `correct_diet`（用户修正）
3. `record_training`
4. `get_aggregates`（统一入口，param: `kind=day|week`，替代多个 getter）
5. `get_sleep`
6. `get_weather`
7. `send_push`（受护栏 + 每日 ≤3 限流）
8. `update_profile`
9. `search_memory`（合并 read_wiki / query_insight）
10. （预留）`sync_health`（二期）

**失败与重试**：工具返回结构化 `{ok, data, error}`；Agent 见到 error 按 guidance 重试（≤2 次），仍失败→降级（如视觉失败改手动录入提示），**绝不抛异常中断整轮**。

**事前约束**：护栏在 tool 执行前校验（伤病信号→禁止 `send_push` 含"加量"）；工具只经 `integrations/` 调外部，业务层不直连第三方 SDK。

**落点**：`agent/skills.py`（注册 + schema），各 `modules/*/skill.py` 提供实现。

---

## 4. 可靠性 Reliability

**一句话**：从"确定性思维"切到"概率性思维"——LLM 不永远对，要用工程兜底。

- **幻觉**：识食热量带置信度，入库前需用户确认（不自动采信）；建议必须附数据依据。
- **指令偏离**：护栏用**代码强制**（不只 prompt）；输出用结构化 schema（function calling / Pydantic）而非自由文本。
- **格式不稳**：严格 JSON schema 校验，解析失败进重试/降级。
- **死循环**：LangGraph 设 `max_steps` / `max_iterations`，单轮运行有上限；每次 tool 调用后回到受控节点。
- **级联崩溃**：每个外部依赖加熔断（vision / LLM / weather 各自降级），单点故障不拖垮整轮；运行失败有兜底（缓存通用建议 / 跳过本次推送）。

**落点**：`agent/guardrails.py`（硬规则）、`agent/graph.py`（max_steps、节点回环受控）、`integrations/*`（各带 retry / circuit-breaker）。

---

## 5. 成本控制 Cost

**一句话**：钱是真的会烧的，靠习惯在规模放大前就锁住。

- **Prefix Cache**：`system + 护栏` 不变前缀，争取 DeepSeek/兼容端点缓存命中。
- **模型分级**（呼应 LLM 路由）：
  - 路由/分类（是否推送、归类）→ 最便宜模型或纯规则，不调大模型；
  - 推理/报告 → DeepSeek-V3（便宜强）；
  - 视觉 → Qwen-VL 等便宜 VL。
- **减少 Step**：单轮尽量 1 次 LLM 调用产出（如日报一次性生成），不在循环里反复调。
- **上下文剪枝**：只传聚合 + wiki 摘要，不传原始行/全文历史。
- **体量预估**：单人每日若干次调用，月费约几毛到几元，量级可忽略；但上述习惯让将来规模放大时不爆。

**落点**：`llm/router`（分级）+ `agent/context.py`（剪枝）+ 护栏/路由规则。

---

## 6. 评估 Eval

**一句话**：没有 Eval 的优化都是玄学。

- **离线**
  - 识食：固定图集 → 期望热量区间（容差 ±15%），测视觉精度与置信度校准。
  - 护栏：构造 case（伤病信号 / 静息心率连续↑）→ 断言 Agent **不产出**违规建议（确定性单测）。
  - 报告质量：采样用 LLM-as-Judge 打 empathy / correctness / actionability。
- **在线**（呼应 策划书 §八 北极星）：饮食记录 7 日留存、推送点击率、建议采纳率、周报打开率。
- **A/B**：推送文案两版比 CTR；报告语气两版比采纳率（个人项目可手动小样本）。
- **落点**：`tests/`（离线单测 + 数据集），线上指标接 SQLite 事件表统计。

---

## 何时做（安排）

- **设计阶段（现在）**：本稿定稿，作为后续实现的验收基准。
- **与 MVP 一期同步落地**：上下文组装器、记忆三层（SQLite + wiki + state）、工具 schema + 护栏前置、可靠性（结构化输出 + max_steps + 降级）、成本（路由分级 + 剪枝）。
- **二期增强**：Eval 体系（离线数据集 + LLM-Judge）、更细成本监控、记忆压缩定时任务。
