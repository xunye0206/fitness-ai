#!/usr/bin/env python3
"""
部署健身AI Agent 到 CloudBase 云托管（容器型服务）。

- 读取项目根 .env 的全部配置（LLM key / JWT / 行为开关等；.env 已被 gitignore，不入库）
- 用命令行传入的云端 DATABASE_URL / REDIS_URL 覆盖（容器内 localhost 不可用）
- 自动拼接 tcb run deploy 的 --envParams 并执行

用法：
  python scripts/deploy_cloudbase.py \
      --db "postgresql+asyncpg://user:pass@host:5432/db" \
      --redis "redis://user:pass@host:6379/0" \
      [--region sh|gz|bj] [--cpu 1] [--mem 2] [--min-num 1]

说明：
  - 本脚本不把任何密钥写入被 git 追踪的文件。
  - 部署前请确认项目根 .env 里 REASONING_PROVIDER / VISION_PROVIDER /
    EMBEDDING_PROVIDER / DEEPSEEK_API_KEY / QWEN_API_KEY / JWT_SECRET 已填好真值。
"""
import argparse
import os
import subprocess
import sys
from dotenv import dotenv_values

ENV_ID = "js-agent001-d0g039uk4d55548bf"
SERVICE = "fitness-agent"
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)

# 部署上线必须到位的关键项（缺失会告警）
REQUIRED = [
    "REASONING_PROVIDER",
    "VISION_PROVIDER",
    "JWT_SECRET",
    "DEEPSEEK_API_KEY",
    "QWEN_API_KEY",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="云端 Postgres(pgvector) 连接串，须带 +asyncpg 后缀")
    ap.add_argument("--redis", required=True, help="云端 Redis 连接串，形如 redis://user:pass@host:6379/0")
    ap.add_argument("--region", default="sh", help="地域：sh(上海)/gz(广州)/bj(北京)")
    ap.add_argument("--cpu", default="1", help="单实例 CPU 核数")
    ap.add_argument("--mem", default="2", help="单实例内存 GB")
    ap.add_argument("--min-num", default="1", help="最小副本数（1=常驻，避免冷启动）")
    ap.add_argument("--max-num", default="5", help="最大副本数")
    args = ap.parse_args()

    env_path = os.path.join(PROJECT_DIR, ".env")
    if not os.path.exists(env_path):
        print(f"[ERROR] 找不到 {env_path}，请先配置本地 .env", file=sys.stderr)
        sys.exit(1)

    env = dotenv_values(env_path)
    # 以 .env 全量为基准，覆盖云端数据库/缓存
    params = {k: (v if v is not None else "") for k, v in env.items()}
    params["DATABASE_URL"] = args.db
    params["REDIS_URL"] = args.redis

    # 关键项缺失告警
    missing = [k for k in REQUIRED if not params.get(k)]
    if missing:
        print(f"[WARN] .env 缺少关键项（功能可能异常）：{missing}", file=sys.stderr)

    # 值里含 & 会破坏 envParams 的 k=v&k2=v2 解析，提前告警
    for k, v in params.items():
        if "&" in v:
            print(f"[WARN] 环境变量 {k} 的值含 '&'，可能破坏 envParams 解析，请检查", file=sys.stderr)

    env_str = "&".join(f"{k}={v}" for k, v in params.items() if v != "")
    cmd = [
        "npx", "-p", "@cloudbase/cli", "tcb", "run", "deploy",
        "-e", ENV_ID, "-s", SERVICE,
        "--path", PROJECT_DIR,
        "--containerPort", "8000",
        "--envParams", env_str,
        "--cpu", args.cpu, "--mem", args.mem,
        "--minNum", args.min_num, "--maxNum", args.max_num,
        "--noConfirm", "--override",
        "-r", args.region,
    ]
    print(f"[deploy] -> 服务 {SERVICE} | 环境 {ENV_ID} | 地域 {args.region} | 端口 8000")
    print("[deploy] 执行 tcb run deploy（envParams 明细已省略）...")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] 部署命令失败：{e}", file=sys.stderr)
        sys.exit(1)
    print("[deploy] 完成。请到云托管控制台确认公网访问地址，并访问 /health 验证服务存活。")


if __name__ == "__main__":
    main()
