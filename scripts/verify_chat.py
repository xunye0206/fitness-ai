#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the public CloudBase Run chat endpoint uses REAL models (not fake)."""
import json
import time
import urllib.request
import urllib.error

BASE = "https://fitness-agent-279338-7-1451590672.sh.run.tcloudbase.com"
USER = "probe_%d" % int(time.time())
PW = "probe123"


def post_json(path, data, token=None, timeout=30):
    url = BASE + path
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa
        return -1, str(e)


def main():
    print("[1] health")
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=20) as r:
            print("   ", r.status, r.read().decode()[:80])
    except Exception as e:  # noqa
        print("    health error:", e)

    print("[2] register probe user:", USER)
    st, body = post_json("/auth/register", {"username": USER, "password": PW})
    print("    status:", st, "| body:", body[:200])
    if st >= 300:
        print("    register failed, abort")
        return
    token = json.loads(body).get("access_token")
    print("    token len:", len(token or ""))

    print("[3] chat (SSE) — expecting REAL deepseek reply, NOT [fake-reason]")
    url = BASE + "/agent/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps({"message": "你好，我最近在增肌，今晚训练后吃什么比较好？给我具体建议。"}).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa
        print("    chat error:", e)
        return

    # SSE lines: "data: {...}"
    texts = []
    fake = False
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            obj = json.loads(line[5:].strip())
        except Exception:  # noqa
            continue
        if obj.get("type") == "delta" and obj.get("text"):
            texts.append(obj["text"])
        if "[fake-reason]" in obj.get("text", "") or "[fake]" in obj.get("text", "").lower():
            fake = True

    full = "".join(texts)
    print("    reply length:", len(full))
    print("    reply preview:", full[:400].replace("\n", " "))
    print("    FAKE_MARKER_PRESENT:", fake)
    print("    VERDICT:", "REAL MODEL ✅" if (not fake and len(full) > 20) else "STILL FAKE/EMPTY �’")


if __name__ == "__main__":
    main()
