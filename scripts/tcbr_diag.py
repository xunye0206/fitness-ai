#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only diagnostic: what is actually happening on the cloud right now?"""
import sys
import json

sys.path.insert(0, r"D:/Ai/健身日志项目/scripts")
import tcbr_build_deploy as m

print("=== token ===")
cred, _ = m.load_credential()
print("tmpSecretId=%s expires_in=%s min" % (cred.get("tmpSecretId"),
      (cred.get("tmpExpired", 0) - __import__("time").time() * 1000) / 60000))

print("\n=== DescribeCloudRunDeployRecord ===")
r = m.tcbr("DescribeCloudRunDeployRecord", {"EnvId": m.ENV_ID, "ServerName": m.SERVER_NAME})
print(json.dumps((r.get("Response") or r), ensure_ascii=False)[:3000])

print("\n=== try version-list APIs ===")
for act in ["DescribeCloudBaseRunServerVersion",
            "DescribeCloudRunServerVersionList",
            "DescribeCloudRunServerVersions",
            "DescribeCloudBaseRunServerDeployRecord"]:
    try:
        rr = m.tcbr(act, {"EnvId": m.ENV_ID, "ServerName": m.SERVER_NAME})
        print("--- %s ---" % act)
        print(json.dumps((rr.get("Response") or rr), ensure_ascii=False)[:1500])
    except Exception as e:
        print(act, "EXC", repr(e))
