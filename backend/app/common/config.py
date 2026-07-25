from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    demo_mode: bool = True
    database_url: str | None = None

    # The first operator credential. Somebody has to be able to sign in
    # before any operator exists, and in demo mode there is no database to
    # hold one. Set as a secret in a real environment; rotate by changing it.
    admin_bootstrap_key: str | None = None
    admin_bootstrap_email: str = "bootstrap@vietra.local"

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2025-04-01-preview"
    # Well below `catalog.indexing.LEASE_SECONDS`, so a stalled request always
    # gives up before the lease it is holding expires underneath it.
    azure_openai_timeout_seconds: float = 60.0
    azure_openai_chat_deployment: str = "gpt-5.4-mini"
    azure_openai_intent_deployment: str = "gpt-5-nano"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    azure_openai_image_deployment: str = "gpt-image-1-mini"
    openai_embedding_dimensions: int = 512
    openai_max_output_tokens: int = 500
    openai_daily_budget: float = 10.0

    assistant_max_tool_rounds: int = 3
    assistant_max_session_turns: int = 12
    # Control group for the core thesis that guided selling beats manual search.
    # Off by default: switch it on deliberately when running the experiment.
    assistant_holdout_rate: float = 0.0
    search_lexical_candidates: int = 50
    search_semantic_candidates: int = 50
    search_rrf_k: int = 60
    recommendation_mmr_lambda: float = 0.75

    # Ranking weights (POC_SPEC.md §11.3 requires these in configuration).
    # Search ranks on expected value: P(book | query, context, item) x value.
    search_weight_relevance: float = 0.45
    search_weight_preference_fit: float = 0.14
    search_weight_availability_fit: float = 0.12
    search_weight_price_fit: float = 0.11
    search_weight_quality: float = 0.07
    search_weight_conversion: float = 0.06
    search_weight_margin: float = 0.05

    recommendation_weight_session: float = 0.30
    recommendation_weight_context_fit: float = 0.18
    recommendation_weight_item_similarity: float = 0.15
    recommendation_weight_availability_fit: float = 0.12
    recommendation_weight_popularity: float = 0.15
    recommendation_weight_quality: float = 0.10
    recommendation_complement_bonus: float = 0.12

    # Recent behaviour dominates same-day tourist booking, so session signal
    # decays fast. Half-life is in seconds.
    behaviour_half_life_seconds: float = 1800.0
    # Bayesian prior keeps new inventory from being starved by zero bookings.
    conversion_prior_rate: float = 0.02
    conversion_prior_strength: float = 40.0

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @property
    def azure_enabled(self) -> bool:
        return bool(self.azure_openai_endpoint) and not self.demo_mode


@lru_cache
def get_settings() -> Settings:
    return Settings()
