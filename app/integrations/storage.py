"""图片存储收口：Supabase Storage（生产）或本地盘（本地/测试降级）。

业务层只调 upload_image()，不关心背后是对象存储还是本地文件。
- 配置了 SUPABASE_URL/KEY/BUCKET → 上传到 Supabase，返回公开 URL（持久，redeploy 不丢）。
- 未配置（本地开发/测试）→ 降级写本地盘 data/uploads，返回本地路径。
这样本地 pytest 零配置照跑，上线配了 Supabase 自动切换，无需改业务代码。
"""
import os
import uuid

from app.config import settings

_client = None  # Supabase 客户端懒加载缓存


def _get_client():
    """仅在配置了 Supabase 且尚未初始化时，懒加载客户端。

    懒导入 supabase 包，避免本地/测试环境未装该依赖时 import 失败。
    """
    global _client
    if _client is None and settings.supabase_url and settings.supabase_key:
        from supabase import create_client

        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


def upload_image(data: bytes, filename: str, folder: str = "uploads") -> str:
    """上传图片，返回可访问地址（Supabase 公开 URL 或本地路径）。

    folder 用于区分业务目录（如 diet / training），避免 Storage 内文件平铺冲突。
    """
    client = _get_client()
    ext = os.path.splitext(filename)[1] or ".png"
    key = f"{folder}/{uuid.uuid4().hex}{ext}"

    if client is not None:
        # 注意：bucket 须设为 Public，get_public_url 才能直接访问；
        # 若设为 Private 需改 signed URL。上传失败直接抛错，让问题暴露在上线期而非静默丢图。
        client.storage.from_(settings.supabase_bucket).upload(
            key, data, {"content-type": "image/png"}
        )
        return client.storage.from_(settings.supabase_bucket).get_public_url(key)

    # 降级：本地盘（与改造前行为一致）
    os.makedirs(settings.upload_dir, exist_ok=True)
    path = os.path.join(settings.upload_dir, f"{uuid.uuid4().hex}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path
