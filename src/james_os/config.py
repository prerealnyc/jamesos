from uuid import UUID

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Force .env to win over already-set-but-empty shell env vars.
# Without override=True, an empty ANTHROPIC_API_KEY=" " inherited from a
# parent shell (Claude Desktop, IDE, etc.) silently beats the .env value.
load_dotenv(override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://james_os:james_os@localhost:5433/james_os"
    default_tenant_id: UUID = UUID("00000000-0000-0000-0000-000000000001")

    # Connection tuning. Supabase requires SSL; its transaction-mode pooler
    # (port 6543) does not support prepared statements, so set
    # DB_STATEMENT_CACHE_SIZE=0 if you use that pooler. The session pooler
    # and direct connection support our set_config-based RLS and prepared
    # statements — prefer those.
    db_ssl: str = "prefer"  # disable | prefer | require
    db_statement_cache_size: int = 256  # set 0 for transaction-mode poolers

    embedding_provider: str = "stub"  # voyage | openai | stub
    embedding_model: str = "voyage-3-large"
    embedding_dim: int = 1024
    voyage_api_key: str = ""

    llm_provider: str = "stub"  # anthropic | google | stub
    llm_model: str = "claude-opus-4-7"
    anthropic_api_key: str = ""
    google_api_key: str = ""

    cohere_api_key: str = ""

    log_level: str = "INFO"

    # Retrieval tuning
    retrieval_top_k_per_index: int = 50
    retrieval_top_k_after_rerank: int = 12
    retrieval_min_score: float = Field(default=0.0, ge=0.0, le=1.0)


settings = Settings()
