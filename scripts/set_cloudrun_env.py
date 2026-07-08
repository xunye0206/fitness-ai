"""把 cloudbaserc.json 里的 run.envParams 写到 CloudBase 云托管服务配置(ServerConfig.EnvParams)。

CloudBase 云托管的环境变量字段是 ServerConfig.EnvParams（JSON 字符串），
而 tcb cloudrun deploy 不读 cloudbaserc.json 的 run.envParams，
所以之前 17 个变量一个没注入容器 -> 应用回落 fake 模式。

本脚本：
1. 读 cloudbaserc.json 的 run.envParams（对象）
2. 调 tcb api tcbr UpdateCloudRunServer --api-version 2022-02-17 把 EnvParams 写进服务配置
3. 顺带把 MinNum 设成 1，避免无流量时缩容到 0 导致 503 冷启动

写完后仍需一次 tcb cloudrun deploy 重新部署，让新版本继承这些环境变量。
"""
import json
import subprocess
import sys

ENV_ID = "js-agent001-d0g039uk4d55548bf"
SERVER = "fitness-agent"


def main():
    with open("cloudbaserc.json", encoding="utf-8") as f:
        cfg = json.load(f)
    env = cfg["run"]["envParams"]
    env_params_str = json.dumps(env, ensure_ascii=False)

    # 用云端查到的当前 ServerConfig 值，只改 EnvParams 与 MinNum
    body = {
        "EnvId": ENV_ID,
        "ServerName": SERVER,
        "ServerConfig": {
            "Cpu": 1,
            "Mem": 2,
            "MinNum": 1,          # 关键：最小副本 1，解决 503 冷启动
            "MaxNum": 5,
            "PolicyDetails": [
                {"PolicyType": "cpu", "PolicyThreshold": 60},
                {"PolicyType": "mem", "PolicyThreshold": 60},
            ],
            "CustomLogs": "stdout",
            "EnvParams": env_params_str,
            "InitialDelaySeconds": 2,
            "Port": 8000,
            "HasDockerfile": True,
            "Dockerfile": "Dockerfile",
            "BuildDir": ".",
            "OpenAccessTypes": ["MINIAPP", "OA", "PUBLIC"],
            "OperationMode": "alwaysScale",
            "PublicNetConf": {"PublicNetStatus": "ENABLE"},
        },
    }
    body_str = json.dumps(body, ensure_ascii=False)

    cmd = [
        "npx", "-p", "@cloudbase/cli", "tcb", "api", "tcbr",
        "UpdateCloudRunServer", "--api-version", "2022-02-17",
        "--body", body_str,
    ]
    print(">>> 调用 UpdateCloudRunServer ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("STDOUT:", r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
