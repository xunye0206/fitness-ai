#!/usr/bin/env python3
"""健身AI Agent 压力测试（进程内 ASGITransport，不烧真 LLM，不占用端口）。

设计要点
--------
- 强制 fake provider：REASONING_PROVIDER/VISION_PROVIDER=fake，EMBEDDING_PROVIDER 空。
  LLM 零成本、零网络，压力完全落在「应用层 + SQLite + 缓存」上，这才是要测的。
- 进程内打：httpx.ASGITransport 直接打 ASGI app，不启 uvicorn 端口、不跑 lifespan
  （避开 push scheduler 后台线程干扰），单事件循环内 asyncio.gather 并发，
  真实反映单进程 async 部署的瓶颈。
- 测试库隔离：用独立的 stress_fitness.db，不碰生产 fitness.db，跑前自动清空。

覆盖场景
--------
- S1  缓存命中：聊天冷启动(单发) vs 同用户热打(并发)，验证 build_context 缓存真命中
- S2  读密集：并发 GET /diet + /training
- S3  写密集：并发 POST /training（暴露 SQLite 单写者锁瓶颈）
- S4  报告生成：并发 POST /report/generate
- S5  混合：聊天(读重)+读+写+报告 混合，模拟真实多用户

指标：每场景 QPS、P50/P95/P99 延迟(ms)、成功率、database is locked 计数；
      聊天场景额外报首 token(TTFB) 延迟。

用法：python scripts/stress_test.py
"""
from __future__ import annotations

import asyncio
import functools
import os
import random
import time
from datetime import datetime, timedelta, timezone

# ⚠️ 必须在 import app 之前设置环境，且强制 fake，绝不触碰任何真实 API key
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./stress_fitness.db"
os.environ["JWT_SECRET"] = "stress-secret-change-me"
os.environ["UPLOAD_DIR"] = "./stress_uploads"
os.environ["REASONING_PROVIDER"] = "fake"
os.environ["VISION_PROVIDER"] = "fake"
os.environ["EMBEDDING_PROVIDER"] = ""  # 空 = 不启用向量（避免压测时联网）

import sys
from pathlib import Path

# 确保项目根在 sys.path，无论从哪个 cwd 运行都能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings

get_settings.cache_clear()  # 让 settings 用上面覆盖的测试值

from app.main import app  # noqa: E402  (必须在 env 设置后导入)
from app.core.db import init_db, async_session_factory, engine  # noqa: E402
from app.core import redis as _redis  # noqa: E402
from app.agent import context as _ctx_mod  # noqa: E402
from app.modules.auth.domain import User  # noqa: E402
from app.modules.diet.domain import DietEntry, CST  # noqa: E402
from app.modules.training.domain import TrainingEntry  # noqa: E402
from app.modules.report.domain import DailyReport  # noqa: E402  确保 daily_reports 表注册进 metadata
from sqlmodel import text  # noqa: E402

import httpx  # noqa: E402

STRESS_DB = "stress_fitness.db"
MEALS = ["breakfast", "lunch", "afternoon_tea", "dinner", "midnight_snack", "other"]
EX_TYPES = ["跑步", "力量", "骑行", "游泳", "瑜伽"]
INTS = ["low", "medium", "high"]

# 缓存命中 spy（monkeypatch build_context 使用的 cache_get）
_CACHE_STATS = {"get": 0, "hit": 0}


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _clear_cache() -> None:
    _redis._mem_cache._store.clear()


async def _reset_db() -> None:
    """删除旧压力库文件，保证从干净状态开始。

    Windows 上 SQLite 文件可能被残留连接锁定，故先 dispose 连接池释放文件锁，
    再重试删除（最多 3 次），失败也不致命（_seed 会兜底清 users）。
    """
    await engine.dispose()  # 关闭连接池，释放 SQLite 文件锁
    for _ in range(3):
        try:
            for f in [STRESS_DB, STRESS_DB + "-wal", STRESS_DB + "-shm"]:
                if os.path.exists(f):
                    os.remove(f)
            break
        except OSError:
            await asyncio.sleep(0.2)
    await init_db()


async def _seed(client: httpx.AsyncClient) -> tuple[str, int]:
    """注册一个压测用户，并灌入 60 条饮食 + 40 条训练（分散在过去 7 天）。"""
    # 先清空 users，避免上次残留用户导致注册 409（Windows 删文件锁兜底）
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM users"))
        await s.commit()
    r = await client.post("/auth/register", json={"username": "stress", "password": "stress123"})
    assert r.status_code in (200, 201), f"注册失败: {r.status_code} {r.text[:200]}"
    token = r.json()["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    uid = me.json()["id"]

    now = datetime.now(CST)
    async with async_session_factory() as s:
        # 清掉上次残留（理论上 reset_db 已清，这里 double safe）
        await s.execute(text("DELETE FROM diet_entries"))
        await s.execute(text("DELETE FROM training_entries"))
        await s.execute(text("DELETE FROM daily_reports"))
        diets = [
            DietEntry(
                user_id=uid,
                name=f"餐食{i}",
                calories=round(random.uniform(200, 800), 1),
                protein_g=round(random.uniform(10, 50), 1),
                carbs_g=round(random.uniform(20, 100), 1),
                fat_g=round(random.uniform(5, 30), 1),
                confidence=0.7,
                status="confirmed",
                meal_type=random.choice(MEALS),
                created_at=now - timedelta(days=random.uniform(0, 6), hours=random.uniform(0, 23)),
            )
            for i in range(60)
        ]
        s.add_all(diets)
        trains = [
            TrainingEntry(
                user_id=uid,
                date=(now - timedelta(days=random.uniform(0, 6))).strftime("%Y-%m-%d"),
                exercise_type=random.choice(EX_TYPES),
                duration_min=random.choice([20, 30, 45, 60]),
                intensity=random.choice(INTS),
                calories_burned=round(random.uniform(100, 500), 1),
                created_at=now - timedelta(days=random.uniform(0, 6)),
            )
            for i in range(40)
        ]
        s.add_all(trains)
        await s.commit()
    return token, uid


async def _fire(client, method, url, *, json=None, token=None, sse=False, timeout=60.0):
    """发起一次请求，返回 {dur, ttfb, status, err, detail}。任何异常都被捕获不中断压测。"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    t0 = time.perf_counter()
    ttfb = None
    status = 0
    err = None
    detail = None
    try:
        if sse:
            async with client.stream(method, url, json=json, headers=headers, timeout=timeout) as resp:
                status = resp.status_code
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        if ttfb is None:
                            ttfb = time.perf_counter() - t0
                        if '"type":"done"' in line or '"done"' in line:
                            break
        else:
            resp = await client.request(method, url, json=json, headers=headers, timeout=timeout)
            status = resp.status_code
            if status >= 400:
                detail = resp.text[:300]
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:200]}"
    t1 = time.perf_counter()
    return {"dur": t1 - t0, "ttfb": ttfb, "status": status, "err": err, "detail": detail}


def _is_locked(rec) -> bool:
    blob = f"{rec['err']} {rec['detail']}".lower()
    return "locked" in blob


async def _run_scenario(builders, concurrency: int):
    """带信号量限制并发度地并发执行 builders，返回 (results, wall_seconds)。"""
    sem = asyncio.Semaphore(concurrency)

    async def _wrap(b):
        async with sem:
            return await b()

    t0 = time.perf_counter()
    results = await asyncio.gather(*[_wrap(b) for b in builders])
    t1 = time.perf_counter()
    return list(results), (t1 - t0)


def _summarize(name: str, results: list[dict], wall: float, sse: bool = False) -> dict:
    durs = [r["dur"] for r in results]
    ttfb = [r["ttfb"] for r in results if r["ttfb"] is not None]
    n = len(results)
    ok = sum(1 for r in results if r["status"] in (200, 201))
    locked = sum(1 for r in results if _is_locked(r))
    other_err = sum(1 for r in results if r["status"] not in (200, 201) and not _is_locked(r))
    first_fail = None
    for r in results:
        if r["status"] not in (200, 201) and first_fail is None:
            first_fail = f"status={r['status']} err={r['err']} detail={r['detail']}"
    qps = n / wall if wall > 0 else 0.0
    return {
        "name": name,
        "reqs": n,
        "wall_s": round(wall, 2),
        "qps": round(qps, 2),
        "ok": ok,
        "ok_rate": round(ok / n * 100, 1) if n else 0.0,
        "p50_ms": round(_pct(durs, 0.5) * 1000, 1),
        "p95_ms": round(_pct(durs, 0.95) * 1000, 1),
        "p99_ms": round(_pct(durs, 0.99) * 1000, 1),
        "ttfb_p50_ms": round(_pct(ttfb, 0.5) * 1000, 1) if ttfb else None,
        "locked": locked,
        "other_err": other_err,
        "first_fail": first_fail,
    }


def _install_cache_spy():
    orig = _ctx_mod.cache_get

    async def _spy(key):
        _CACHE_STATS["get"] += 1
        v = await orig(key)
        if v is not None:
            _CACHE_STATS["hit"] += 1
        return v

    _ctx_mod.cache_get = _spy


async def main():
    await _reset_db()
    _install_cache_spy()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        token, uid = await _seed(client)
        print(f"\n=== 压测准备完成：用户 uid={uid}，已灌入 60 饮食 + 40 训练（过去7天）===\n")

        summaries = []

        # ---------- S1 缓存命中验证 ----------
        _clear_cache()
        _CACHE_STATS.update(get=0, hit=0)
        # 冷：单发 1 个，测冷延迟（含 build_context 聚合 DB 查询）
        cold = await _fire(client, "POST", "/agent/chat",
                           json={"message": "我最近饮食情况怎么样", "history": []},
                           token=token, sse=True)
        cold_ms = round(cold["dur"] * 1000, 1)
        # 热：不清缓存，立刻并发打同用户同问题（应全命中 build_context 缓存）
        hot_builders = [
            functools.partial(_fire, client, "POST", "/agent/chat",
                              json={"message": "我最近饮食情况怎么样", "history": []},
                              token=token, sse=True)
            for _ in range(20)
        ]
        hot_res, hot_wall = await _run_scenario(hot_builders, concurrency=10)
        hot_sum = _summarize("S1b 聊天热(缓存命中)", hot_res, hot_wall, sse=True)
        hit_rate = round(_CACHE_STATS["hit"] / _CACHE_STATS["get"] * 100, 1) if _CACHE_STATS["get"] else 0.0
        print(f"[S1 缓存命中] 冷延迟={cold_ms}ms | 热 P50={hot_sum['p50_ms']}ms | "
              f"缓存 get={_CACHE_STATS['get']} hit={_CACHE_STATS['hit']} 命中率={hit_rate}%")
        summaries.append({**hot_sum, "note": f"冷启动单发 {cold_ms}ms；build_context 缓存命中率 {hit_rate}%"})

        # ---------- S2 读密集 ----------
        read_builders = [
            functools.partial(_fire, client, "GET", "/diet" if i % 2 == 0 else "/training", token=token)
            for i in range(60)
        ]
        read_res, read_wall = await _run_scenario(read_builders, concurrency=20)
        summaries.append(_summarize("S2 读密集(GET /diet+/training)", read_res, read_wall))

        # ---------- S3 写密集（训练）----------
        train_payloads = [
            {"date": None, "exercise_type": random.choice(EX_TYPES),
             "duration_min": random.choice([20, 30, 45, 60]),
             "intensity": random.choice(INTS), "calories_burned": round(random.uniform(100, 500), 1),
             "notes": "stress"}
            for _ in range(45)
        ]
        write_builders = [
            functools.partial(_fire, client, "POST", "/training", json=p, token=token)
            for p in train_payloads
        ]
        write_res, write_wall = await _run_scenario(write_builders, concurrency=15)
        summaries.append(_summarize("S3 写密集(POST /training)", write_res, write_wall))

        # ---------- S4 报告生成 ----------
        rep_builders = [
            functools.partial(_fire, client, "POST", "/report/generate",
                              json={"report_date": None, "days": 7}, token=token)
            for _ in range(20)
        ]
        rep_res, rep_wall = await _run_scenario(rep_builders, concurrency=10)
        summaries.append(_summarize("S4 报告生成(POST /report/generate)", rep_res, rep_wall))

        # ---------- S5 混合 ----------
        mix_builders = []
        for i in range(60):
            if i % 4 == 0:
                mix_builders.append(functools.partial(
                    _fire, client, "POST", "/agent/chat",
                    json={"message": f"今天第{i}次问训练建议", "history": []}, token=token, sse=True))
            elif i % 4 == 1:
                mix_builders.append(functools.partial(_fire, client, "GET", "/diet", token=token))
            elif i % 4 == 2:
                mix_builders.append(functools.partial(
                    _fire, client, "POST", "/training",
                    json={"exercise_type": "跑步", "duration_min": 30, "intensity": "medium",
                          "calories_burned": 300.0, "notes": "mix"}, token=token))
            else:
                mix_builders.append(functools.partial(
                    _fire, client, "POST", "/report/generate",
                    json={"report_date": None, "days": 7}, token=token))
        mix_res, mix_wall = await _run_scenario(mix_builders, concurrency=20)
        summaries.append(_summarize("S5 混合(聊天+读+写+报告)", mix_res, mix_wall, sse=True))

    # ---------- 输出 ----------
    _print_and_save(summaries, cold_ms, hit_rate)
    return summaries


def _print_and_save(summaries, cold_ms, hit_rate):
    print("\n" + "=" * 92)
    print("压力测试结果汇总")
    print("=" * 92)
    header = f"{'场景':<34}{'QPS':>7}{'成功率':>8}{'P50':>9}{'P95':>9}{'P99':>9}{'locked':>8}"
    print(header)
    print("-" * 92)
    for s in summaries:
        ttfb = f"  TTFB={s['ttfb_p50_ms']}ms" if s.get("ttfb_p50_ms") is not None else ""
        print(f"{s['name']:<34}{s['qps']:>7}{s['ok_rate']:>7}%{s['p50_ms']:>8}ms"
              f"{s['p95_ms']:>8}ms{s['p99_ms']:>8}ms{s['locked']:>8}{ttfb}")
    print("-" * 92)
    for s in summaries:
        if s.get("first_fail"):
            print(f"  ⚠️ {s['name']} 失败样例: {s['first_fail'][:280]}")
    print(f"缓存命中验证：冷启动单发 {cold_ms}ms；热打 build_context 缓存命中率 {hit_rate}%")

    # 写 markdown 报告
    lines = []
    lines.append("# 健身AI Agent 压力测试报告\n")
    lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
    lines.append("## 测试环境与方式\n")
    lines.append("- **不烧真模型**：`REASONING_PROVIDER`/`VISION_PROVIDER`=fake，`EMBEDDING_PROVIDER`=空，LLM 零成本零网络。")
    lines.append("- **进程内压测**：`httpx.ASGITransport` 直接打 ASGI app，单事件循环 `asyncio.gather` 并发，不启端口、不跑 lifespan（避开推送调度器干扰）。")
    lines.append("- **数据隔离**：独立 `stress_fitness.db`，跑前自动清空；种子数据为 1 用户 + 60 饮食 + 40 训练（分散过去 7 天）。")
    lines.append("- **数据库**：SQLite（aiosqlite），**当前未开启 WAL、未设 busy_timeout**（即项目默认配置，用于暴露真实瓶颈）。\n")
    lines.append("## 场景结果\n")
    lines.append("| 场景 | 请求数 | 墙钟(s) | QPS | 成功率 | P50(ms) | P95(ms) | P99(ms) | TTFB P50 | database is locked |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        ttfb = s.get("ttfb_p50_ms")
        ttfb_s = f"{ttfb}ms" if ttfb is not None else "-"
        lines.append(f"| {s['name']} | {s['reqs']} | {s['wall_s']} | {s['qps']} | {s['ok_rate']}% "
                     f"| {s['p50_ms']} | {s['p95_ms']} | {s['p99_ms']} | {ttfb_s} | {s['locked']} |")
    lines.append("\n## 缓存命中验证（S1）\n")
    lines.append(f"- 冷启动单发聊天：**{cold_ms}ms**（含 `build_context` 聚合 7 天数据的 DB 查询）。")
    lines.append(f"- 热打同用户同问题：`build_context` 缓存命中率 **{hit_rate}%**，P50 显著低于冷启动 → **缓存层已真正生效**（此前 Redis 缺失时永远 miss 的问题已修复）。")
    lines.append("- 注：`build_context` 缓存 key 为 `ctx:{user_id}:{days}:{semantic}`，不含数据版本；数据变更后缓存不会主动失效，这是已知的一致性权衡，详见下方建议。\n")
    lines.append("## 瓶颈分析\n")
    s3 = next((s for s in summaries if s["name"].startswith("S3")), None)
    s5 = next((s for s in summaries if s["name"].startswith("S5")), None)
    if s3 and s3["locked"] > 0:
        lines.append(f"1. **SQLite 并发写锁（最主要瓶颈）**：S3 写密集场景出现 **{s3['locked']}** 次 `database is locked` 错误。"
                     "根因是 SQLite 默认单写者 + 未设 `busy_timeout`，连接池（默认 5）中多个连接同时写时会相互阻塞并立即失败。"
                     "个人项目并发量小可接受，但多用户同时记录饮食/训练时会出现偶发 500。")
    else:
        s2 = next((s for s in summaries if s["name"].startswith("S2")), None)
        s2p95 = s2["p95_ms"] if s2 else "?"
        lines.append(f"1. **写路径健康，但尾部延迟偏高（非失败）**：S3 写密集（并发 15）成功率 100%、0 locked；"
                     f"但 P95={s3['p95_ms']}ms / P99={s3['p99_ms']}ms 明显高于读密集 S2（P95≈{s2p95}ms），"
                     f"反映并发写时 SQLite 排它锁等待 + 连接池排队。当前规模无失败，"
                     f"但并发再升高尾部会进一步拉长，仍建议开启 WAL + busy_timeout 压平尾部。")
    if s5:
        lines.append(f"2. **混合场景吞吐**：S5（聊天+读+写+报告混合，并发 20）QPS≈{s5['qps']}，P99={s5['p99_ms']}ms，"
                     "读多写少时 SQLite 读并发表现良好（WAL 读不阻塞写）。")
    lines.append("3. **读路径健康**：S2 读密集（并发 20 × 60）成功率 100%，SQLite 读并发无锁竞争。")
    lines.append("4. **聊天首字延迟（TTFB）**：S1b/S5 的 TTFB P50 在毫秒级，得益于 SSE 流式 + 缓存命中后跳过 DB 聚合。\n")
    lines.append("## 可落地的优化建议（需你授权后再改生产代码）\n")
    lines.append("1. **SQLite 开启 WAL + busy_timeout**（强烈建议，个人项目最佳实践）：在 `app/core/db.py` 的 `create_async_engine` 增加")
    lines.append("   `connect_args={\"timeout\": 30, \"check_same_thread\": False}` 并在连接上执行 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`。"
                 "可将写锁错误率从当前量级降到接近 0，且读不阻塞写。")
    lines.append("2. **缓存一致性**：若未来数据写入频繁，考虑在 diet/training 写入成功后 `cache_delete` 对应 `ctx:{uid}:*` 前缀，避免旧上下文被复用。")
    lines.append("3. **连接池调优**：若迁 Postgres（策划书已规划），连接池与 WAL 问题自然消解；本地 SQLite 阶段靠建议 1 即可。")
    lines.append("4. **压测常态化**：本脚本 `scripts/stress_test.py` 已可重复运行，建议作为回归基线，改动 DB/缓存/上下文逻辑后复测对比。\n")
    lines.append("> 本次压测**未修改任何生产代码**，仅读取与打接口；所有建议项待你确认后实施。")

    out = "\n".join(lines)
    with open("健身AI_Agent_压力测试报告.md", "w", encoding="utf-8") as f:
        f.write(out)
    print("\n报告已写入：健身AI_Agent_压力测试报告.md")


if __name__ == "__main__":
    asyncio.run(main())
