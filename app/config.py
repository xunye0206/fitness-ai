"""应用配置：通过 pydantic-settings 读取 .env，按用途拆分 LLM 供应商。

业务层不直接读这里决定模型，只通过 app.llm.router 调用；换模型只改 .env。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 基础
    app_name: str = "健身AI Agent"
    database_url: str = "sqlite+aiosqlite:///./fitness.db"  # 生产改为 postgresql+asyncpg:// 托管云连接串
    upload_dir: str = "data/uploads"  # 上传图片落盘目录（不入库）

    # Redis（agent 上下文热缓存 / 推送限流计数）。留空 = 不启用，自动降级（不拖主链路）
    redis_url: str = ""

    # embedding 向量维度（Qwen text-embedding-v3 默认 1024；须与 EMBEDDING_MODEL 维度一致）
    embedding_dim: int = 1024

    # 鉴权
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_days: int = 30

    # LLM 供应商按用途拆分（值即 provider 名，见 app.llm.registry）
    reasoning_provider: str = "fake"   # 文本大脑
    vision_provider: str = "fake"      # 识图
    embedding_provider: str = ""       # 空 = 不启用向量

    # 按用途的模型覆盖（可选；不填则用对应 provider 的默认模型）
    reasoning_model: str = ""
    vision_model: str = ""
    embedding_model: str = ""

    # embedding 专用端点（可选）。默认复用 embedding_provider 的 base_url；
    # 若你的 MaaS 端点不支持 /embeddings，可单独指向 DashScope 主站 compatible-mode 端点。
    embedding_base_url: str = ""

    # 各供应商密钥与端点（仅在 provider != fake 时使用）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-vl-max"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    @property
    def is_default_secret(self) -> bool:
        """生产环境必须替换默认 jwt_secret。"""
        return self.jwt_secret == "dev-secret-change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
