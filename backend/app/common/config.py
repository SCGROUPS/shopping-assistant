from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    demo_mode: bool = True
    database_url: str | None = None

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2025-04-01-preview"
    azure_openai_chat_deployment: str = "gpt-5.4-mini"
    azure_openai_intent_deployment: str = "gpt-5-nano"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    azure_openai_image_deployment: str = "gpt-image-1-mini"
    openai_embedding_dimensions: int = 512
    openai_max_output_tokens: int = 500
    openai_daily_budget: float = 10.0

    assistant_max_tool_rounds: int = 3
    assistant_max_session_turns: int = 12
    search_lexical_candidates: int = 50
    search_semantic_candidates: int = 50
    search_rrf_k: int = 60
    recommendation_mmr_lambda: float = 0.75
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @property
    def azure_enabled(self) -> bool:
        return bool(self.azure_openai_endpoint) and not self.demo_mode


@lru_cache
def get_settings() -> Settings:
    return Settings()
