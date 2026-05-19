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

    # ─── Market research / internet intelligence ───
    # Researches a subject on the open internet and ingests the findings
    # into the SAME memory substrate (category:research), citation-grounded.
    # Provider-abstracted so the loop is provable with a stub before a key
    # is configured. Perplexity is primary (live-web LLM with citations);
    # Google Custom Search is supplemental (raw result links).
    research_provider: str = "stub"  # perplexity | stub
    perplexity_api_key: str = ""
    perplexity_model: str = "sonar-pro"
    google_search_api_key: str = ""  # Google Custom Search JSON API
    google_search_cx: str = ""       # Custom Search engine id (cx)

    # ─── Integration credentials (loaded, not yet all wired) ───
    # These make the keys AVAILABLE to JAMES OS. They become ACTIVE only
    # when the subsystem that uses each one is built (see /api/integrations
    # for live status). Never logged, never returned by any endpoint —
    # only their presence (bool) is ever exposed.
    openai_api_key: str = ""       # Whisper transcription, GPT, Sora
    elevenlabs_api_key: str = ""   # voice synthesis / cloning
    heygen_api_key: str = ""       # avatar video
    heygen_avatar_id: str = ""     # default avatar for renders
    descript_api_key: str = ""     # audio/video editing
    runway_api_key: str = ""       # video generation
    minimax_api_key: str = ""      # video generation
    postproxy_api_key: str = ""    # multi-platform publishing
    meta_access_token: str = ""    # Meta Graph (IG/FB/Threads)
    twitter_bearer_token: str = "" # X/Twitter
    xpoz_api_key: str = ""         # social engagement read

    # ─── Video generation ───
    # Generative clips (Runway Gen-3/4). Provider-abstracted with a stub
    # so the durable job pipeline (submit → poll → land in approval queue)
    # is provable end-to-end WITHOUT burning render credits. Flip to
    # `runway` once the key is verified. Higgsfield/Descript/MiniMax are
    # intentionally NOT wired — no usable public REST API / no key — and
    # are not faked.
    video_provider: str = "stub"  # runway | stub
    runway_model: str = "gen4_turbo"          # gen4_turbo | gen3a_turbo
    runway_api_version: str = "2024-11-06"    # X-Runway-Version header
    runway_video_ratio: str = "1280:720"
    runway_video_duration: int = 5            # seconds (Runway: 5 or 10)

    log_level: str = "INFO"

    # Retrieval tuning
    retrieval_top_k_per_index: int = 50
    retrieval_top_k_after_rerank: int = 12
    retrieval_min_score: float = Field(default=0.0, ge=0.0, le=1.0)

    # Content engine: independent voice-QA must score the draft at or
    # above this to pass un-flagged. Below it, the draft is still queued
    # for a human but flagged "needs revision" — never silently shipped.
    content_voice_floor: float = Field(default=0.7, ge=0.0, le=1.0)
    content_voice_k: int = 6   # voice/thesis exemplars pulled per draft
    content_facts_k: int = 6   # research/reference events pulled per draft


settings = Settings()
