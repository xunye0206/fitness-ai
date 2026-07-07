# 健身 AI Agent · 后端（M1 里程碑）

M1 交付：后端骨架 + SQLite 存储 + 可插拔 LLM 层（默认 FakeProvider，零 key 跑通）。
后续 M2 饮食切片 / M3 报告闭环 / M4 推送，将在此基座上叠加。

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
预期：health / auth / llm 路由 共 6 个用例全过，全程不联网、不填 key。

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

## 目录角色（对应已定架构）
- `app/config.py`：读 .env，按用途拆分 LLM 供应商
- `app/core/`：db（SQLite 会话）、security（JWT/密码）、logging
- `app/llm/`：可插拔 LLM 层（base 抽象 / registry 装配 / router 业务入口 / providers fake+openai兼容）
- `app/modules/auth/`：登录模块（domain 表 / service 逻辑 / api 路由）
- `app/main.py`：FastAPI 入口，挂载路由 + 启动建表
- `tests/`：镜像模块的冒烟测试（零网络）

## 关键设计决策
- **LLM 可插拔**：业务只调 `app.llm.router` 的 `reason/see/embed`，换模型只改 `.env`。
- **FakeProvider**：无 key 即可跑通链路，开发与 CI 零成本。
- **不写死供应商**：OpenAI/DeepSeek/Qwen/混元 共用一个兼容客户端。
- **安全红线**：`.env` 与 `*.db` 已 gitignore，密钥与数据不入库。

## 下一步
M2：拍照→视觉估算（带置信度）→用户修正→落库；引入 LangGraph 状态机与护栏。
