# 部署与上线指南 · 健身AI Agent

本文件说明如何把「健身AI Agent」部署成公网可访问的服务，以及上线前的合规清单。
适用对象：已有代码（本地 git 已提交、绝不推送远端），需要让别人也能访问。

---

## 0. 红线提醒（务必先读）

本项目约定：**git 仅本地提交，绝不推送远端**。因此：

- ✅ 推荐：在**自有服务器 / 本地打包上传**方式部署（不碰 git）。
- ⚠️ 若用 Render / Railway 等「连 GitHub 自动部署」，需你**明确授权打破该红线**。

无论哪种方式，真实密钥（`.env`）都**不进镜像、不进仓库**，运行时注入。

---

## 1. 部署前置清单（一次性）

| 项 | 说明 | 必填 |
|---|---|---|
| 云服务器 / 容器平台 | 自有 ECS、或 Render/Railway、或本地 Docker 机器 | 是 |
| PostgreSQL（带 `vector` 扩展） | Supabase / Neon / 阿里云 RDS / 自托管（docker-compose 已含） | 是 |
| Redis | Upstash（免费）/ 阿里云 Redis / 自托管 | 是（不装则自动降级为内存缓存） |
| 域名 + HTTPS | 公网访问建议配；Render 等自带 HTTPS | 建议 |
| DeepSeek API Key | 推理模型 | 是（真机） |
| 通义千问 API Key | 视觉 +  embedding | 是（真机） |

---

## 2. 方式 A：自有服务器直传（推荐，不碰 git）

1. 把项目目录打成包（排除 `.git` / `.venv` / `.env`）：
   ```powershell
   cd D:\Ai\健身日志项目
   Compress-Archive -Path . -DestinationPath fitness-agent.zip -Force
   ```
2. 上传到服务器（scp / 对象存储 / 面板），解压到 `/opt/fitness-agent`。
3. 服务器装 Docker，构建并运行：
   ```bash
   cd /opt/fitness-agent
   docker build -t fitness-agent .
   docker run -d --name fitness-agent -p 8000:8000 \
     --env-file .env fitness-agent
   ```
4. 用 nginx / Caddy 反代 `localhost:8000` 到你的域名，并配置 HTTPS。
5. 访问 `https://你的域名/` 验证；`https://你的域名/health` 应返回 `{"status":"ok"}`。

> `DATABASE_URL` / `REDIS_URL` / `JWT_SECRET` / 各 API Key 都在服务器上的 `.env` 里填好。
> Postgres 需先 `CREATE EXTENSION vector;`（Supabase 默认自带；Neon 付费版可执行）。

---

## 3. 方式 B：Render / Railway（需授权破 git 红线）

- 将代码推到私有 GitHub 仓库（**需你明确同意打破红线**）。
- Render：导入仓库 → 选 `render.yaml` Blueprint，或在 Web Service 里指定 Dockerfile。
- 数据库 / Redis 按 `render.yaml` 注释在控制台手动加。
- `sync:false` 的变量在 Environment 面板逐个填。
- 免费层有休眠限制，正式运营建议付费。

---

## 4. 上线前合规清单（公网收集健康数据必做）

- [x] **隐私政策页**：`/privacy` 路由 + `static/privacy.html` 已落地（基于《策划书》§七）。
      前端导航「隐私政策」入口已加。
- [ ] **`JWT_SECRET` 必须改**：部署环境绝不能沿用本地默认值，用强随机串。
- [ ] **API Key 通过环境变量注入**：不写进镜像、不入库。
- [ ] **HTTPS 开启**：公网传输加密（健康数据属敏感个人信息）。
- [ ] **微信支付商户 + 小程序「工具」类目**：正式收费前补齐（非代码，需本人办理）。
      表述全程避开「诊断 / 医疗 / 体脂 / 康复」等词，Agent 已加双层合规护栏 + 免责声明。
- [ ] **用户数据可导出 / 可删除**：账号设置页应提供（功能层已有删除链路，导出可后续补）。
- [ ] **备案 / 资质**：若面向国内公众，按平台与监管要求办理。

---

## 5. 本地验证（上线前自测）

```powershell
# 代码层：全量测试应全绿
& "C:\Users\kanade\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m pytest -q

# 镜像构建验证（可选）
docker build -t fitness-agent .
```

---

## 6. 回滚与运维

- 镜像版本化：`docker build -t fitness-agent:v1.x .`，出问题 `docker run` 旧标签即可回滚。
- 数据库迁移由代码 `init_db()` 在启动时自动执行，无需手动。
- 日志：容器内 stdout/stderr，平台可查；本地运行见 `_server.log`（已 gitignore）。
