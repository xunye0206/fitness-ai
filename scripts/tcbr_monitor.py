#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live monitor: poll TCBR until a NEW version is online, then check homepage."""
import json
import sys
import time
import urllib.request
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tcbr_build_deploy as m

DOMAIN = "https://fitness-agent-279338-7-1451590672.sh.run.tcloudbase.com"
PREV = {"fitness-agent-007"}


def describe():
    d = m.tcbr("DescribeCloudRunServerDetail", {"EnvId": m.ENV_ID, "ServerName": m.SERVER_NAME})
    return d.get("Response", {})


def homepage_has_dashboard():
    try:
        html = urllib.request.urlopen(DOMAIN, timeout=25).read().decode("utf-8", "ignore")
        return html.count("page-dashboard")
    except Exception as e:
        return "ERR:%s" % e


def main():
    deadline = time.time() + 12 * 60
    print("[monitor] watching for new online version (prev=%s)" % PREV, flush=True)
    while time.time() < deadline:
        try:
            resp = describe()
            versions = [v.get("VersionName") for v in resp.get("OnlineVersionInfos", [])]
            status = resp.get("BaseInfo", {}).get("Status")
            print("[%s] online=%s status=%s dash=%s"
                  % (time.strftime("%H:%M:%S"), sorted(versions), status, homepage_has_dashboard()),
                  flush=True)
            if any(v not in PREV for v in versions):
                print("[monitor] NEW VERSION ONLINE -> %s" % sorted(set(versions) - PREV), flush=True)
                break
        except Exception as e:
            print("[monitor] describe err: %s" % e, flush=True)
        time.sleep(15)
    else:
        print("[monitor] timed out", flush=True)


if __name__ == "__main__":
    main()
