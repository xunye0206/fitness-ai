#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trigger a CloudBase Run (TCBR) cloud BUILD + deploy directly via API,
bypassing the interactive `tcb cloudrun deploy` CLI (which in non-interactive
mode only updates metadata and never triggers the source build).

Flow (reverse-engineered from @cloudbase/cli dist/standalone/cli.js):
  1. tcbr.DescribeCloudRunServerDetail        -> current ServerConfig (cpu/mem/.../EnvParams)
  2. tcb.DescribeCloudBaseBuildService        -> COS presigned UploadUrl + PackageName/Version
  3. zip project (sensitive/heavy paths excluded) -> PUT to UploadUrl
  4. tcb.CreateCloudBaseRunServerVersion(uploadType='package') -> starts build+deploy
  5. tcb.DescribeCloudBaseRunOperateBasic     -> poll until Status != 'creating'

Credentials: read from ~/.config/.cloudbase/auth.json (STS tmp token), same as tcbr_api.py.
"""
import json
import os
import sys
import time
import io
import zipfile
import hmac
import hashlib
import urllib.request
import urllib.error

ENV_ID = "js-agent001-d0g039uk4d55548bf"
SERVER_NAME = "fitness-agent"
REGION = "ap-shanghai"

# Two API products are involved:
#  - tcbr.tencentcloudapi.com  (service "tcbr") : DescribeCloudRunServerDetail
#  - tcb.tencentcloudapi.com   (service "tcb")  : build / version / operate APIs
PRODUCTS = {
    "tcbr": {"host": "tcbr.tencentcloudapi.com", "version": "2022-02-17"},
    "tcb":  {"host": "tcb.tencentcloudapi.com",  "version": "2018-06-08"},
}

AUTH_PATHS = [
    os.path.expanduser("~/.config/.cloudbase/auth.json"),
    os.path.expanduser("~/.tcb/account.json"),
]

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Paths excluded from the build zip (secrets / caches / heavy / local db)
EXCLUDE_DIRS = {
    ".git", ".workbuddy", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".idea", ".vscode",
    "data", "test_uploads", "stress_uploads",
}
EXCLUDE_FILES = {
    ".env", "cloudbaserc.json",
    ".dockerignore", ".gitignore",
}
EXCLUDE_SUFFIXES = (
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".log",
)


# ---------- credential handling (same as tcbr_api.py) ----------
def load_credential():
    for p in AUTH_PATHS:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("credential", data), p
    raise SystemExit("No cloudbase auth.json found")


def is_tmp_expired(cred):
    exp = cred.get("tmpExpired") or cred.get("expired") or 0
    return int(time.time() * 1000) >= exp


def refresh_tmp_token(cred):
    token_id = cred.get("tokenId")
    refresh = cred.get("refreshToken")
    if not token_id or not refresh:
        raise SystemExit("No tokenId/refreshToken to refresh with")
    url = "https://tcb-api.tencentcloudapi.com/web?env=" + ENV_ID
    body = json.dumps({"action": "RefreshToken", "tokenId": token_id,
                       "refreshToken": refresh}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    cred_new = out.get("data", {}).get("credential") or out.get("credential") or out
    if "tmpSecretId" in cred_new:
        cred.update(cred_new)
        p = [x for x in AUTH_PATHS if os.path.exists(x)][0]
        with open(p, "r", encoding="utf-8") as f:
            store = json.load(f)
        store["credential"] = cred
        with open(p, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        print("[refresh] token refreshed")
        return cred
    raise SystemExit("Refresh response unexpected")


def get_cred():
    cred, _ = load_credential()
    if is_tmp_expired(cred):
        print("[auth] tmp token expired, refreshing...")
        cred = refresh_tmp_token(cred)
    return cred


# ---------- TC3 signing ----------
def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signed_request(product, action, params, cred):
    cfg = PRODUCTS[product]
    host, svc, ver = cfg["host"], product, cfg["version"]
    payload = json.dumps(params).encode("utf-8")
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    hashed_payload = hashlib.sha256(payload).hexdigest()
    canon_headers = (
        "content-type:application/json\nhost:%s\nx-tc-action:%s\nx-tc-timestamp:%d\n"
        % (host, action.lower(), ts)
    )
    signed_headers = "content-type;host;x-tc-action;x-tc-timestamp"
    canonical_request = "\n".join(["POST", "/", "", canon_headers, signed_headers, hashed_payload])
    scope = "%s/%s/tc3_request" % (date, svc)
    hashed_cr = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join(["TC3-HMAC-SHA256", str(ts), scope, hashed_cr])
    secret = cred["tmpSecretKey"].encode("utf-8")
    kd = _sign(b"TC3" + secret, date)
    ks = _sign(kd, svc)
    ksign = _sign(ks, "tc3_request")
    signature = hmac.new(ksign, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = ("TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
            % (cred["tmpSecretId"], scope, signed_headers, signature))
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": ver,
        "X-TC-Timestamp": str(ts),
        "X-TC-Region": REGION,
        "X-TC-Token": cred.get("tmpToken", ""),
    }
    req = urllib.request.Request("https://%s/" % host, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"__http_error__": e.code, "__body__": e.read().decode("utf-8", "ignore")}


def tcb(action, params):
    return signed_request("tcb", action, params, get_cred())


def tcbr(action, params):
    return signed_request("tcbr", action, params, get_cred())


# ---------- build zip ----------
def make_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PROJECT_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, PROJECT_DIR)
                rel_parts = rel.split(os.sep)
                if any(p in EXCLUDE_DIRS for p in rel_parts):
                    continue
                if fn in EXCLUDE_FILES:
                    continue
                if rel.endswith(EXCLUDE_SUFFIXES):
                    continue
                if fn.endswith(EXCLUDE_SUFFIXES):
                    continue
                zf.write(full, rel)
    data = buf.getvalue()
    print("[zip] built %d bytes from %s" % (len(data), PROJECT_DIR))
    return data


# ---------- main ----------
def main():
    print("[1/5] fetch current ServerConfig")
    d = tcbr("DescribeCloudRunServerDetail", {"EnvId": ENV_ID, "ServerName": SERVER_NAME})
    cfg = d.get("Response", {}).get("ServerConfig", {})
    if not cfg:
        print("[!] describe failed:", json.dumps(d, ensure_ascii=False)[:800])
        sys.exit(1)
    cpu = cfg.get("Cpu", 1)
    mem = cfg.get("Mem", 2)
    min_num = cfg.get("MinNum", 1)
    max_num = cfg.get("MaxNum", 5)
    port = cfg.get("Port", 8000)
    dockerfile = cfg.get("Dockerfile", "Dockerfile")
    env_params = cfg.get("EnvParams", "{}")
    print("      cpu=%s mem=%s min=%s max=%s port=%s dockerfile=%s"
          % (cpu, mem, min_num, max_num, port, dockerfile))

    print("[2/5] request build upload slot (DescribeCloudBaseBuildService)")
    bs = tcb("DescribeCloudBaseBuildService", {"EnvId": ENV_ID, "ServiceName": SERVER_NAME})
    bsr = bs.get("Response", bs)
    pkg_name = bsr.get("PackageName")
    pkg_ver = bsr.get("PackageVersion")
    upload_url = bsr.get("UploadUrl")
    if not (pkg_name and pkg_ver and upload_url):
        print("[!] build service unexpected:", json.dumps(bs, ensure_ascii=False)[:800])
        sys.exit(1)
    print("      PackageName=%s PackageVersion=%s" % (pkg_name, pkg_ver))

    print("[3/5] zip + upload source")
    zdata = make_zip()
    req = urllib.request.Request(upload_url, data=zdata, method="PUT")
    req.add_header("Content-Type", "application/x-zip-compressed")
    req.add_header("Accept", "*/*")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            print("      upload HTTP %s, bytes=%d" % (resp.status, len(zdata)))
    except urllib.error.HTTPError as e:
        print("[!] upload failed HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:500]))
        sys.exit(1)

    print("[4/5] submit build via UpdateCloudRunServer (DeployInfo package) -> starts cloud build")
    # Mirror the proven `tcb cloudrun deploy` path:
    #   Items = minimal diff (preserve env + access); build config (BuildDir/Dockerfile)
    #   is inherited from the existing server. DeployInfo triggers the source build.
    items = [
        {"Key": "EnvParam", "Value": env_params},
        {"Key": "OpenAccessTypes", "ArrayValue": ["OA", "PUBLIC", "MINIAPP"]},
    ]
    us = tcbr("UpdateCloudRunServer", {
        "EnvId": ENV_ID,
        "ServerName": SERVER_NAME,
        "DeployInfo": {
            "DeployType": "package",
            "PackageName": pkg_name,
            "PackageVersion": pkg_ver,
            "ReleaseType": "FULL",
            "DeployRemark": "frontend: index=AI coach, dashboard removed",
        },
        "Items": items,
    })
    usr = us.get("Response", us)
    print("      UpdateCloudRunServer resp:", json.dumps(usr, ensure_ascii=False)[:600])
    if us.get("Error") or usr.get("Error"):
        print("[!] submit failed:", json.dumps(us, ensure_ascii=False)[:1000])
        sys.exit(1)

    print("[5/5] poll until a NEW version goes online (build can take several minutes)")
    domain = "https://fitness-agent-279338-7-1451590672.sh.run.tcloudbase.com"
    prev_versions = {v.get("VersionName") for v in
                     tcbr("DescribeCloudRunServerDetail", {"EnvId": ENV_ID, "ServerName": SERVER_NAME})
                     .get("Response", {}).get("OnlineVersionInfos", [])}
    print("      versions before build:", prev_versions)
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        time.sleep(12)
        d = tcbr("DescribeCloudRunServerDetail", {"EnvId": ENV_ID, "ServerName": SERVER_NAME})
        resp = d.get("Response", {})
        versions = resp.get("OnlineVersionInfos", [])
        cur = {v.get("VersionName") for v in versions}
        new_v = cur - prev_versions
        status = resp.get("BaseInfo", {}).get("Status")
        print("      online=%s status=%s" % (sorted(cur), status))
        if new_v:
            print("      >>> new version online: %s" % new_v)
            break
        # Check deploy records for failure
        rec = tcbr("DescribeCloudRunDeployRecord", {"EnvId": ENV_ID, "ServerName": SERVER_NAME})
        recs = (rec.get("Response") or rec).get("DeployRecords", []) if isinstance(rec.get("Response"), dict) else []
        if recs:
            latest = recs[0]
            if latest.get("Status") in ("failed", "Failed", "BUILD_FAIL"):
                print("[!] build failed. Record:", json.dumps(latest, ensure_ascii=False)[:800])
                bid = latest.get("BuildId")
                if bid:
                    log = tcbr("DescribeCloudRunBuildLog",
                               {"EnvId": ENV_ID, "ServerName": SERVER_NAME, "BuildId": bid, "Offset": 0})
                    print(json.dumps((log.get("Response") or log), ensure_ascii=False)[:2000])
                sys.exit(1)
    else:
        print("[!] timed out waiting for new version; check console deploy records.")
        sys.exit(1)

    # Final check: public homepage should no longer contain the dashboard.
    print("[verify] fetch public homepage")
    try:
        import urllib.request as _u
        html = _u.urlopen(domain, timeout=25).read().decode("utf-8", "ignore")
        print("      page-dashboard count:", html.count("page-dashboard"),
              "| default active dashboard:", 'id="page-dashboard" class="page active"' in html)
    except Exception as e:
        print("      homepage fetch error:", e)
    print("Deploy finished. Verify: python scripts/tcbr_api.py describe")


if __name__ == "__main__":
    main()
