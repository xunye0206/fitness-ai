# 健身AI Agent

个人级 **AI 健身教练后端**。采用「主动干预 + 用户确认」模式：Agent 理解用户的饮食 / 训练 / 睡眠记录，主动给出建议与提醒，但关键动作需用户确认后才落库。支持长期画像记忆（向量召回）、周报生成、定时推送。

> 当前前端为后端直接托管的 Web 演示页（`static/index.html`，AI 教练对话），聊天走 `/agent/chat`（SSE 流式）。小程序等正式前端暂缓。

---

## 一、技术栈

| 层 | 选型 |
| --- | --- |
| 语言 | Python 3.13 |
| Web 框架 | FastAPI（异步 ASGI） |
| 服务器 | Uvicorn |
| ORM / 模型 | SQLModel（基于 SQLAlchemy Core） |
| 数据库 | **SQLite**（`aiosqlite`，本地 / 测试，零运维）｜ **PostgreSQL + pgvector**（生产，向量记忆） |
| 缓存 / 限流 | Redis（可选，`REDIS_URL` 留空则自动降级，不拖主链路） |
| 鉴权 | Passlib（`pbkdf2_sha256`）+ Python-JOSE（JWT HS256） |
| LLM | OpenAI 兼容协议客户端（`openai` SDK）。按用途拆分供应商：`reasoning`(文本) / `vision`(视觉) / `embedding`(向量)，可分别接 DeepSeek / Qwen / OpenAI；视觉用 Qwen-VL，向量用 Qwen `text-embedding-v3` |
| 调度 | APScheduler（每日固化 + 事件巡检推送） |
| 校验 / 配置 | Pydantic / pydantic-settings |
| 前端演示页 | 原生 HTML（`static/index.html`），由 FastAPI 以 `FileResponse` 托管 |

---

## 二、项目框架

后端按「**业务垂直切片 + Agent 横切编排**」组织，模块间不直接 import 业务代码，外部 IO 统一收口。

```
app/
├── main.py            # FastAPI 入口：装配路由、lifespan 初始化、CORS、静态页挂载
├── config.py          # pydantic-settings 读 .env；按用途拆分 LLM 供应商（reasoning/vision/embedding）
├── core/              # 横切基础设施
│   ├── db.py          # 异步引擎 + init_db（按 DATABASE_URL 自适应 SQLite/Postgres）
│   ├── security.py    # JWT 签发/校验 + 密码哈希
│   ├── ratelimit.py   # 内存滑动窗口限流（注册/登录）
│   ├── redis.py       # Redis 异步客户端（上下文热缓存/限流计数，缺失自动降级）
│   └── logging.py
├── llm/               # LLM 抽象层（可插拔）
│   └── providers/openai_compat.py   # 单一 OpenAICompatibleProvider 覆盖 DeepSeek/Qwen/OpenAI
│   └── router.py      # 业务只调 reason() / see() / embed()，换模型只改 .env
├── integrations/      # 所有外部 IO 收口（推送通道等）
├── modules/           # 业务垂直切片，每模块内 api → service → domain
│   ├── auth/          # 注册 / 登录 / JWT
│   ├── diet/          # 饮食记录（文本 / 图片识别）
│   ├── training/      # 训练记录（含图片）
│   ├── report/        # 周报生成（Agent 产物消费者）
│   ├── push/          # 推送事件与调度（APScheduler）
│   └── memory/        # 向量记忆层（index_wiki / recall，pgvector 支撑长期画像）
└── agent/             # Agent 编排层
    ├── registry.py / tool.py   # 工具注册表（单一数据源，导出 OpenAI function schema）
    ├── loop.py / graph.py      # 多轮循环 / 图编排（max_iterations 防死循环、流式 delta）
    ├── guardrails.py / guardrails_coach.py  # 工具护栏（黑名单 + 破坏性需 confirmed）
    ├── session.py     # 会话存储 / 超 token 预算压缩
    ├── profile.py     # 长期画像抽取与召回（写入 memory_embeddings）
    ├── prompts.py / schemas.py / validation.py / summarize.py
    └── api.py         # /agent/chat（SSE 流式）端点
```

**关键设计点**
- **LLM 可插拔**：`reasoning` / `vision` / `embedding` 各自可接不同供应商，由 `.env` 决定；业务代码只调 `app.llm.router` 的 `reason / see / embed`，不直接 import 任何供应商 SDK。换模型只改配置，代码零改动。
- **存储分层**：结构化数值（用户 / 饮食 / 训练 / 睡眠 / 推送）进数据库；叙事记忆（画像 / 反思 / 洞察）向量化进 `memory_embeddings`（pgvector 余弦召回）；Redis 做 agent 热缓存与推送限流。数值只进数据库，绝不进记忆文本。
- **护栏与安全**：工具执行前经 schema 强转 + 必填报检；破坏性工具需 `confirmed`；启动时硬性拦截默认弱 `JWT_SECRET`；注册 / 登录带滑动窗口限流。

---

## 三、必要依赖库

完整清单见 [`requirements.txt`](./requirements.txt)（已按 `app/` 实际 import 还原）。核心包：

- **Web**：`fastapi`、`uvicorn`、`python-multipart`
- **ORM / DB**：`sqlmodel`、`pydantic`、`pydantic-settings`、`aiosqlite`（本地）、`asyncpg` + `pgvector`（生产 PG）
- **鉴权**：`passlib[bcrypt]`、`python-jose[cryptography]`
- **LLM**：`openai`（OpenAI 兼容客户端）
- **缓存 / 调度**：`redis`、`apscheduler`

> 本地仅用 SQLite 跑通时，`asyncpg` / `pgvector` / `redis` 可不装（代码对缺失自动降级）。

---

## 四、启动流程

### A. 本地开发（SQLite，零运维）

```bash
# 1) 准备 Python 3.13 虚拟环境并激活
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2) 安装依赖
pip install -r requirements.txt

# 3) 配置 .env（复制模板后至少设置强随机 JWT_SECRET）
cp .env.example .env
# 生成强随机密钥填入 JWT_SECRET：
python -c "import secrets;print(secrets.token_hex(32))"
# 默认 REASONING_PROVIDER=fake 可不填 key 先跑通链路；接真模型改 REASONING_PROVIDER 等

# 4) 启动（热重载）
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问：
- `http://localhost:8000/` —— AI 教练对话页
- `http://localhost:8000/health` —— 健康检查
- `http://localhost:8000/docs` —— 交互式接口文档
- `http://localhost:8000/privacy` —— 隐私政策页

跑测试：`pytest`

### B. 生产 / 完整栈（PostgreSQL + pgvector + Redis）

```bash
# 1) 起 Postgres(pgvector) + Redis
docker compose up -d

# 2) .env 设置
# DATABASE_URL=postgresql+asyncpg://fitness:fitness@localhost:5432/fitness
# REDIS_URL=redis://localhost:6379/0

# 3) 安装生产驱动
pip install asyncpg pgvector redis

# 4) 用 Dockerfile 构建镜像部署
docker build -t fitness-agent .
docker run -p 8000:8000 --env-file .env fitness-agent
```

### 注意事项
- 启动时若 `JWT_SECRET` 仍为默认值 `dev-secret-change-me`，会**直接拒绝启动**（防弱密钥进入公开仓库）。
- `data/uploads` 目录会在启动时由代码自动创建（图片上传落盘目录），无需手动建。
- 数据库切换：仅改 `DATABASE_URL` 即可在 SQLite 与 PostgreSQL 间切换，业务代码不变。
