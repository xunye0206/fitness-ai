"""Agent 能力探针测试：上下文召回 / 缓存命中 / 语义记忆召回 / 上下文融合语义记忆。

设计原则（与现有测试一致）：
- 不联网、不依赖真实 API key（conftest 已把模型置为 fake、embedding 置空）。
- 缓存命中用 fake redis 替换 get_redis 模拟；语义召回用 fake embed 注入。
- 每个测试复用 client fixture 建表，内部用 async_session_factory 直接操作。
"""
import asyncio
from datetime import datetime, timedelta, timezone

from app.agent import context as context_mod
from app.agent.context import build_context
from app.core import redis as redis_mod
from app.core.db import async_session_factory
from app.core.redis import cache_get
from app.llm.base import LLMResult
from app.modules.diet.domain import DietEntry
from app.modules.memory.service import index_wiki, recall
from app.modules.training.domain import TrainingEntry
from app.modules.training.schemas import TrainingCreate
from app.modules.training.service import record_training

# ---------- fake embed（确定性伪向量，仅测试用）----------
VOCAB = list("健身饮食蛋白质训练睡眠蔬菜水果西兰花菠菜黄瓜番茄胸背腿")


def _vec(text: str) -> list[float]:
    return [float(text.count(ch)) for ch in VOCAB]


async def fake_embed(texts):
    return LLMResult("", ok=True, raw={"vectors": [_vec(t) for t in texts]})


# ---------- fake redis（模拟缓存层，行为对齐 redis.asyncio 子集）----------
class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        self.store[k] = v

    async def delete(self, k):
        self.store.pop(k, None)

    async def incr(self, k):
        self.store[k] = str(int(self.store.get(k, 0)) + 1)
        return int(self.store[k])

    async def expire(self, k, t):
        pass


# ---------- 测试数据 ----------
async def _seed(session, uid: int, *, offset_days: int = 0) -> None:
    """给 uid 造一条饮食 + 一条训练；offset_days>0 时把时间推到窗口外。"""
    base = datetime.now(timezone.utc) - timedelta(days=offset_days)
    session.add(DietEntry(
        user_id=uid, name="烤鸡胸肉配蔬菜", calories=450.0,
        protein_g=40, carbs_g=20, fat_g=10, status="confirmed",
        meal_type="lunch", created_at=base,
    ))
    session.add(DietEntry(
        user_id=uid, name="燕麦牛奶", calories=300.0, status="confirmed",
        meal_type="breakfast", created_at=base,
    ))
    session.add(TrainingEntry(
        user_id=uid, date=base.strftime("%Y-%m-%d"), exercise_type="跑步",
        duration_min=30, intensity="medium", calories_burned=280.0,
    ))
    # 干扰用户：不应出现在 uid 的上下文里
    session.add(DietEntry(
        user_id=9999, name="别人吃的火锅", calories=1200.0, status="confirmed",
        meal_type="dinner", created_at=base,
    ))
    await session.commit()


# ---------- 1. 上下文召回：聚合 + 用户隔离 ----------
def test_context_recall_aggregates_and_isolates(client):
    async def run():
        async with async_session_factory() as s:
            await _seed(s, 1001)
            return await build_context(s, 1001, days=7, use_semantic=False)

    ctx = asyncio.run(run())
    # 本用户的记录应被聚合进上下文
    assert "烤鸡胸肉配蔬菜" in ctx
    assert "燕麦牛奶" in ctx
    assert "跑步" in ctx
    assert "30 分钟" in ctx
    assert "近 7 天回顾" in ctx
    # 别的用户数据必须隔离出去
    assert "火锅" not in ctx


# ---------- 2. 上下文召回：窗口边界（窗口外数据不召回）----------
def test_context_excludes_out_of_window(client):
    async def run():
        async with async_session_factory() as s:
            await _seed(s, 1005, offset_days=10)  # 推到 10 天前，超出 7 天窗口
            return await build_context(s, 1005, days=7, use_semantic=False)

    ctx = asyncio.run(run())
    assert "烤鸡胸肉配蔬菜" not in ctx  # 窗口外不召回
    assert "近窗口内暂无记录" in ctx    # 窗口内为空时应有明确提示


# ---------- 3. 缓存命中：重复请求直接命中，聚合逻辑只算一次 ----------
def test_context_cache_hit_saves_recompute(client, monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_mod, "get_redis", lambda: fake)  # 启用"缓存层"
    calls = {"n": 0}
    real_inner = context_mod._build_context_inner

    async def spy(session, user_id, days, use_semantic=True):
        calls["n"] += 1
        return await real_inner(session, user_id, days, use_semantic)

    monkeypatch.setattr(context_mod, "_build_context_inner", spy)

    async def run():
        async with async_session_factory() as s:
            await _seed(s, 1002)
            c1 = await build_context(s, 1002, days=7, use_semantic=False)
            c2 = await build_context(s, 1002, days=7, use_semantic=False)  # 第二次应命中
        return c1, c2

    c1, c2 = asyncio.run(run())
    assert c1 == c2                         # 命中缓存，结果一致
    assert calls["n"] == 1                  # 聚合只真正算一次（关键量化指标）
    assert "ctx:1002:7:0" in fake.store     # 缓存键确实写入了


# ---------- 4. 上下文融合语义记忆（use_semantic=True 时带上长期记忆）----------
def test_context_folds_in_semantic_memory(client, monkeypatch):
    import app.llm.router as router_mod
    monkeypatch.setattr(router_mod, "embed", fake_embed)  # 让 recall 走确定性向量

    async def run():
        async with async_session_factory() as s:
            await _seed(s, 1006)
            await index_wiki(
                s, 1006, "wiki/偏好",
                "我讨厌吃西兰花和菠菜，蔬菜只吃黄瓜和番茄。", embed=fake_embed,
            )
            return await build_context(s, 1006, days=7, use_semantic=True)

    ctx = asyncio.run(run())
    assert "长期记忆" in ctx                 # 应出现语义召回段落
    assert "西兰花" in ctx                   # 召回到的记忆块应进入上下文


# ---------- 5. 语义记忆召回：top-K + 用户隔离 ----------
def test_semantic_recall_returns_topk_and_isolates(client):
    async def run():
        async with async_session_factory() as s:
            await index_wiki(
                s, 1003, "wiki/饮食偏好",
                "我讨厌吃西兰花和菠菜，蔬菜里只吃黄瓜和番茄。", embed=fake_embed,
            )
            await index_wiki(
                s, 1003, "wiki/训练偏好",
                "我每周练三次，主要练胸和背，不喜欢练腿。", embed=fake_embed,
            )
            hits = await recall(s, 1003, "我不爱吃西兰花", k=2, embed=fake_embed)
            hits_other = await recall(s, 1004, "我不爱吃西兰花", k=2, embed=fake_embed)
        return hits, hits_other

    hits, hits_other = asyncio.run(run())
    assert len(hits) >= 1
    assert "西兰花" in hits[0].text        # 最相关块排第一
    assert hits[0].score > 0.3             # 相关性显著高于无关块
    assert hits_other == []                # 不同用户查不到对方的记忆


# ---------- 6. 缓存命中（无需 Redis：默认走进程内内存兜底）----------
def test_context_cache_hit_without_redis(client, monkeypatch):
    """不配置 REDIS_URL 时，build_context 第二次请求应命中进程内内存缓存。"""
    calls = {"n": 0}
    real_inner = context_mod._build_context_inner

    async def spy(session, user_id, days, use_semantic=True):
        calls["n"] += 1
        return await real_inner(session, user_id, days, use_semantic)

    monkeypatch.setattr(context_mod, "_build_context_inner", spy)

    async def run():
        async with async_session_factory() as s:
            await _seed(s, 2001)
            c1 = await build_context(s, 2001, days=7, use_semantic=False)
            c2 = await build_context(s, 2001, days=7, use_semantic=False)
        return c1, c2

    c1, c2 = asyncio.run(run())
    assert c1 == c2
    assert calls["n"] == 1   # 不装 Redis 也只算一次 → 缓存真正命中


# ---------- 7. 写入后上下文缓存失效：教练能看到刚发生的事 ----------
def test_context_cache_invalidated_after_write(client):
    """记录一条训练后，原 ctx 缓存应被清除，下次 build_context 重新聚合含新数据。"""
    async def run():
        async with async_session_factory() as s:
            await _seed(s, 2010)
            # 首次 build 写入缓存
            c1 = await build_context(s, 2010, days=7, use_semantic=False)
            assert (await cache_get("ctx:2010:7:0")) is not None
            # 记录一条新训练（应触发上下文缓存失效）
            await record_training(
                s, 2010,
                TrainingCreate(exercise_type="游泳", duration_min=45, intensity="high",
                               calories_burned=400.0),
            )
            # 缓存必须被清（否则教练看到的还是旧汇总）
            assert (await cache_get("ctx:2010:7:0")) is None
            # 再次 build 应重新聚合，且包含新训练
            c2 = await build_context(s, 2010, days=7, use_semantic=False)
        return c1, c2

    c1, c2 = asyncio.run(run())
    assert "游泳" in c2          # 新训练出现在重新聚合的上下文
    assert "游泳" not in c1      # 而首次缓存里没有它（证明是失效后重算，而非误判）
