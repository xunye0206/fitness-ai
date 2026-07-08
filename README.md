# 健身 AI Agent · 后端

M1–M9 已交付：后端骨架 + 可插拔 LLM 层 + 饮食/训练/报告/推送闭环 + 记忆检索层 +
**生产级存储栈（Postgres + pgvector + Redis，本地/测试自动回退 SQLite）**。

Agent 增强（里程碑）：
- **M10 长期画像记忆**：每轮聊天自动从对话抽取用户画像/反思/洞察，写入向量库，下次聊天定向召回实现个性化教练。
- **合规护栏（双层）**：关键词黑名单 + LLM 语义复核，拦截越界医疗措辞，每条回复强制附免责声明。
- **上下文缓存失效**：饮食/训练/报告写入后自动失效 7 天聚合缓存，避免教练看到旧数据。
- **M11 训练截图识别**：聊天或训练页发 Keep 等截图 → 视觉识别结构化训练数据 → 用户确认后才入库。

## 你如何自己验收（不必读代码）

### 1. 准备环境
```bash
# 用本项目托管的 Python 3.13
PY="C:/Users/kanade/.workbuddy/binaries/python/versions/3.13.12/python.exe"
$PY -m venv .venv
.venv/Scripts/activate        # Windows；Mac/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 跑测试（核心验收：全绿即通过）
```bash
pytest -q
```
预期：84 个用例全过（auth / llm / diet / training / report / push / memory / redis /
agent 能力 / 合规护栏 / 画像记忆 / 聊天训练识别），
全程不联网、不填 key、零基础设施（自动走 SQLite + 无 Redis 回退）。

### 3. 本地启动看接口
```bash
uvicorn app.main:app --reload --port 8000
# 浏览器打开 http://127.0.0.1:8000/health
```
或用 curl 走一遍注册→登录→me：
```bash
curl -X POST localhost:8000/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"secret123"}'
curl -X POST localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"secret123"}'
# 用返回的 access_token 调：
curl localhost:8000/auth/me -H 'Authorization: Bearer <access_token>'
```

### 4. 生产部署（Postgres + pgvector + Redis）
```bash
# 一键起本地全套依赖（Postgres 带 pgvector + Redis）
docker compose up -d
# .env 里把 DATABASE_URL 改成 postgresql+asyncpg://...，REDIS_URL 改成 redis://...
# 安装生产依赖：pip install asyncpg pgvector redis
```
`init_db` 会在 Postgres 模式下自动 `CREATE EXTENSION vector` 并建 HNSW 索引，无需手工迁移。

## 目录角色（对应已定架构）
- `app/config.py`：读 .env，按用途拆分 LLM 供应商 + 数据库/Redis 连接
- `app/core/`：db（按 URL 自动选 Postgres/SQLite 会话）、redis（客户端单例+降级）、security（JWT/密码）、logging
- `app/llm/`：可插拔 LLM 层（base 抽象 / registry 装配 / router 业务入口 / providers fake+openai兼容）
- `app/modules/`：auth / diet / training / report / push / memory（各含 domain/service/api）
- `app/agent/`：LangGraph 状态机 + 护栏 + context（含语义召回与 Redis 热缓存）
- `app/main.py`：FastAPI 入口，挂载路由 + 启动建表/建扩展
- `tests/`：镜像模块的冒烟测试（零网络）

## 关键设计决策
- **LLM 可插拔**：业务只调 `app.llm.router` 的 `reason/see/embed`，换模型只改 `.env`。
- **FakeProvider**：无 key 即可跑通链路，开发与 CI 零成本。
- **不写死供应商**：OpenAI/DeepSeek/Qwen/混元 共用一个兼容客户端。
- **存储双栈**：生产=Postgres(pgvector 向量)+Redis(缓存/限流)；本地/测试=SQLite 回退 + 无 Redis 降级，业务代码无感。
- **安全红线**：`.env` 与 `*.db` 已 gitignore，密钥与数据不入库。

## 下一步
上线非代码项：微信支付商户 + 小程序"工具"类目资质（措辞避医疗词）+ 可穿戴同步（二期）。
