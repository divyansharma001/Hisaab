"""Every setting comes from the environment. Nothing is hardcoded."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://hisaab:hisaab@db:5432/hisaab"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

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
        """The placeholder from .env.example must not count as configured."""
        key = self.openai_api_key
        return key.startswith("sk-") and "xxxx" not in key


@lru_cache
def get_settings() -> Settings:
    return Settings()
