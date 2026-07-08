import sys, time, re, json, subprocess, urllib.request, urllib.error, uuid

PY = "C:/Users/kanade/.workbuddy/binaries/python/versions/3.13.12/python.exe"
SCRIPT_DIR = "D:/Ai/健身日志项目/scripts"
BASE = "https://fitness-agent-279338-7-1451590672.sh.run.tcloudbase.com"


def shell(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)


def get_version():
    r = shell([PY, "tcbr_api.py", "describe"])
    m = re.search(r'"VersionName":\s*"fitness-agent-(\d+)"', r.stdout)
    return m.group(1) if m else "?"


def wait_version(timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = get_version()
        if v not in ("007", "?"):
            return v
        time.sleep(15)
    return get_version()


def http_get(path, timeout=20):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return None, str(e)


def main():
    print("=== 等待云端版本升级 (最多~5分钟) ===")
    v = wait_version()
    print("当前线上版本: fitness-agent-%s" % v)
    time.sleep(10)  # 等容器完全就绪

    print("\n=== /health ===")
    s, b = http_get("/health")
    print("status=%s  body=%s" % (s, b[:150]))

    print("\n=== 首页 HTML 检查 (应无'仪表盘', 有'AI 教练'且 coach 为默认页) ===")
    s, html = http_get("/")
    if s == 200:
        print("  含'仪表盘':", html.count("仪表盘"))
        print("  含'AI 教练':", html.count("AI 教练"))
        print("  默认教练页 active:", 'id="page-coach" class="page active"' in html)
    else:
        print("  首页获取失败 status=%s" % s)

    print("\n=== 聊天真模型检查 (register + /agent/chat) ===")
    uname = "verify_%s" % uuid.uuid4().hex[:8]
    req = urllib.request.Request(
        BASE + "/auth/register",
        data=json.dumps({"username": uname, "password": "test123456", "email": uname + "@x.com"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read())["access_token"]
        print("  注册 OK, token 长度:", len(tok))
    except Exception as e:
        print("  注册失败:", e)
        return

    req = urllib.request.Request(
        BASE + "/agent/chat",
        data=json.dumps({"message": "我刚吃了鸡胸肉150克和一碗米饭，帮我记一下"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "ignore")
        is_fake = "[fake-reason]" in raw
        print("  聊天响应长度:", len(raw))
        print("  是否 fake 模式:", is_fake)
        print("  结论:", "❌ FAKE 模式(需补 env)" if is_fake else "✅ 真模型正常")
    except Exception as e:
        print("  聊天失败:", e)


if __name__ == "__main__":
    main()
