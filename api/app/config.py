"""Every setting comes from the environment. Nothing is hardcoded."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://hisaab:hisaab@db:5432/hisaab"

    # Anything OpenAI-compatible. Leave the base url empty to talk to OpenAI
    # directly, or point it at a gateway like OpenRouter. Only these three
    # settings change between them.
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "openai/gpt-4o-mini"

    # Hard cap on LLM calls per batch (plan section 7, layer 3, rule 5).
    llm_call_budget: int = 40

    # Fixed so the generated dataset is identical on every run.
    random_seed: int = 20260829

    log_level: str = "info"

    # Origins allowed to call the API. The Vite dev server runs on 5173.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    @property
    def has_real_llm_key(self) -> bool:
        """The placeholder from .env.example must not count as configured.

        Covers both shapes: OpenAI keys start `sk-`, OpenRouter keys `sk-or-`.
        """
        key = self.llm_api_key
        return key.startswith("sk-") and "xxxx" not in key

    @property
    def llm_provider(self) -> str:
        """Whatever the base url points at. Shown on the health endpoint so a
        misconfigured demo is obvious before it matters."""
        if not self.llm_base_url:
            return "openai"
        if "openrouter" in self.llm_base_url:
            return "openrouter"
        return self.llm_base_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
