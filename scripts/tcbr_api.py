#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-contained CloudBase Run (TCBR) API client.
- Reads credentials from the tcb/cloudbase auth.json store
- Implements Tencent Cloud TC3-HMAC-SHA256 signing manually (no SDK deps)
- Refreshes the temporary STS token using refreshToken if expired
Usage:
  python tcbr_api.py describe            -> DescribeCloudRunServerDetail
  python tcbr_api.py update-env          -> UpdateCloudRunServer (EnvParams + MinNum)
  python tcbr_api.py raw <Action> '<json params>'  -> generic call
"""
import json
import os
import sys
import time
import hmac
import hashlib
import urllib.request
import urllib.error

ENV_ID = "js-agent001-d0g039uk4d55548bf"
SERVER_NAME = "fitness-agent"
REGION = "ap-shanghai"
HOST = "tcbr.tencentcloudapi.com"
SERVICE = "tcbr"
API_VERSION = "2022-02-17"

AUTH_PATHS = [
    os.path.expanduser("~/.config/.cloudbase/auth.json"),
    os.path.expanduser("~/.tcb/account.json"),
]

# ---- credential handling ----
def load_credential():
    for p in AUTH_PATHS:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # auth.json: {"credential": {...}}
            cred = data.get("credential", data)
            return cred, p
    raise SystemExit("No cloudbase auth.json found")


def is_tmp_expired(cred):
    exp = cred.get("tmpExpired") or cred.get("expired") or 0
    now_ms = int(time.time() * 1000)
    return now_ms >= exp


def refresh_tmp_token(cred):
    """Refresh STS token using refreshToken against cloudbase auth endpoint."""
    token_id = cred.get("tokenId")
    refresh = cred.get("refreshToken")
    if not token_id or not refresh:
        raise SystemExit("No tokenId/refreshToken to refresh with")
    url = "https://tcb-api.tencentcloudapi.com/web?env=" + ENV_ID
    body = json.dumps({
        "action": "RefreshToken",
        "tokenId": token_id,
        "refreshToken": refresh,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit("Refresh HTTP error: %s %s" % (e.code, e.read().decode("utf-8", "ignore")))
    # response shape varies; look for credential fields
    cred_new = out.get("data", {}).get("credential") or out.get("credential") or out
    if "tmpSecretId" in cred_new:
        cred.update(cred_new)
        # persist back to auth.json
        p = [p for p in AUTH_PATHS if os.path.exists(p)][0]
        with open(p, "r", encoding="utf-8") as f:
            store = json.load(f)
        store["credential"] = cred
        with open(p, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        print("[refresh] token refreshed and saved")
        return cred
    raise SystemExit("Refresh response unexpected: %s" % json.dumps(out, ensure_ascii=False)[:500])


def get_cred():
    cred, _ = load_credential()
    if is_tmp_expired(cred):
        print("[auth] tmp token expired, refreshing...")
        cred = refresh_tmp_token(cred)
    else:
        exp = cred.get("tmpExpired", 0) / 1000
        print("[auth] tmp token valid, expires in %.0f min" % ((exp - time.time()) / 60))
    return cred


# ---- TC3 signing ----
def sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def tc3_request(action, params, cred):
    payload = json.dumps(params).encode("utf-8")
    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))

    hashed_payload = hashlib.sha256(payload).hexdigest()
    canonical_headers = (
        "content-type:application/json\n"
        "host:%s\n"
        "x-tc-action:%s\n"
        "x-tc-timestamp:%d\n"
    ) % (HOST, action.lower(), timestamp)
    signed_headers = "content-type;host;x-tc-action;x-tc-timestamp"
    canonical_request = "\n".join([
        "POST",
        "/",
        "",
        canonical_headers,
        signed_headers,
        hashed_payload,
    ])

    credential_scope = "%s/%s/tc3_request" % (date, SERVICE)
    hashed_cr = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(timestamp),
        credential_scope,
        hashed_cr,
    ])

    secret = cred["tmpSecretKey"].encode("utf-8")
    secret_date = sign(b"TC3" + secret, date)
    secret_service = sign(secret_date, SERVICE)
    secret_signing = sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        "TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (cred["tmpSecretId"], credential_scope, signed_headers, signature)
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Host": HOST,
        "X-TC-Action": action,
        "X-TC-Version": API_VERSION,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": REGION,
        "X-TC-Token": cred.get("tmpToken", ""),
    }

    url = "https://%s/" % HOST
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        return {"__http_error__": e.code, "__body__": e.read().decode("utf-8", "ignore")}


# ---- actions ----
def do_describe():
    cred = get_cred()
    out = tc3_request("DescribeCloudRunServerDetail", {
        "EnvId": ENV_ID,
        "ServerName": SERVER_NAME,
    }, cred)
    print(json.dumps(out, ensure_ascii=False, indent=2))


# Current online image (from DescribeCloudRunServerDetail, version fitness-agent-007)
CURRENT_IMAGE = "ccr.ccs.tencentyun.com/tcb-100050513584-rhan/ca-sqwlixnq_fitness-agent:fitness-agent-003-20260708153553"


def load_env_params():
    """Load env vars from the gitignored cloudbaserc.json so real API keys
    never get hard-coded into this tracked script."""
    cb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cloudbaserc.json")
    if os.path.exists(cb):
        try:
            with open(cb, "r", encoding="utf-8") as f:
                data = json.load(f)
            ep = data.get("run", {}).get("EnvParams")
            if isinstance(ep, dict) and ep:
                return ep
        except Exception:  # noqa
            pass
    # Fallback subset (no secrets) — keeps the update functional if cloudbaserc missing.
    return {
        "APP_NAME": "健身AI Agent",
        "DATABASE_URL": "sqlite+aiosqlite:///./fitness.db",
        "JWT_SECRET": "local-dev-secret-please-change-9f3a2b",
        "JWT_ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_DAYS": "30",
        "REASONING_PROVIDER": "deepseek",
        "VISION_PROVIDER": "qwen",
        "EMBEDDING_MODEL": "text-embedding-v3",
        "EMBEDDING_DIM": "1024",
    }


def do_update_env():
    cred = get_cred()
    # EnvParams MUST be a JSON string; passed via Items[Key="EnvParam"].
    env_params = load_env_params()
    ep_str = json.dumps(env_params, ensure_ascii=False)
    out = tc3_request("UpdateCloudRunServer", {
        "EnvId": ENV_ID,
        "ServerName": SERVER_NAME,
        # DeployInfo is required; re-deploy the SAME image (no rebuild).
        "DeployInfo": {
            "DeployType": "image",
            "ImageUrl": CURRENT_IMAGE,
            "ReleaseType": "FULL",
        },
        # Differential config update — the supported way to set env + scaling.
        "Items": [
            {"Key": "EnvParam", "Value": ep_str},
            {"Key": "MinNum", "IntValue": 1},
        ],
    }, cred)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def do_raw():
    cred = get_cred()
    action = sys.argv[2]
    params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    out = tc3_request(action, params, cred)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "describe"
    if cmd == "describe":
        do_describe()
    elif cmd == "update-env":
        do_update_env()
    elif cmd == "raw":
        do_raw()
    else:
        print("unknown command: %s" % cmd)
