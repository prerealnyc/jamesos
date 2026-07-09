import os
from uuid import UUID

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Force .env to win over already-set-but-EMPTY shell env vars.
# Without override=True, an empty ANTHROPIC_API_KEY=" " inherited from a
# parent shell (Claude Desktop, IDE, etc.) silently beats the .env value.
#
# But explicit NON-EMPTY environment variables must beat .env (12-factor) —
# otherwise a process launched with DATABASE_URL=<production> gets silently
# re-pointed at the repo's local .env (find_dotenv walks up from THIS file's
# directory, so no working-directory trick escapes it; this bit us pointing
# a local instance at production). Capture real env first, re-apply after.
_explicit_env = {k: v for k, v in os.environ.items() if v.strip()}
load_dotenv(override=True)
os.environ.update(_explicit_env)


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
    apify_api_key: str = ""          # Apify token — IG/TikTok/YouTube trend scraping
    youtube_api_key: str = ""        # YouTube Data API — trend discovery

    # ─── Integration credentials (loaded, not yet all wired) ───
    # These make the keys AVAILABLE to JAMES OS. They become ACTIVE only
    # when the subsystem that uses each one is built (see /api/integrations
    # for live status). Never logged, never returned by any endpoint —
    # only their presence (bool) is ever exposed.
    openai_api_key: str = ""       # Whisper transcription, GPT, Sora
    # When set, long-form transcription uses AssemblyAI (speaker diarization +
    # word timestamps) instead of Whisper — lets the reel cutter avoid crossing
    # into the next speaker's turn. Empty = fall back to Whisper (no speakers).
    assemblyai_api_key: str = ""   # ASSEMBLYAI_API_KEY
    elevenlabs_api_key: str = ""   # voice synthesis / cloning
    elevenlabs_voice_id: str = ""  # the brand's cloned voice (podcast narration)
    podcast_words_target: int = 1100   # ~7-8 min episode at speaking pace
    heygen_api_key: str = ""       # avatar video
    heygen_avatar_id: str = ""     # default avatar for renders
    xpoz_api_key: str = ""         # Xpoz social data API (X/IG/TikTok/Reddit)
    runway_api_key: str = ""       # video generation
    minimax_api_key: str = ""      # video generation
    postproxy_api_key: str = ""    # multi-platform publishing
    meta_access_token: str = ""    # Meta Graph (IG/FB/Threads)
    meta_app_id: str = ""          # Meta Developer App ID (OAuth client)
    meta_app_secret: str = ""      # Meta Developer App Secret (server-side)
    meta_business_id: str = ""     # Meta Business Manager ID
    meta_ad_account_id: str = ""   # Ads Manager account (act_XXXXXXXXX)
    meta_ig_business_id: str = ""  # IG Business account ID (auto-discoverable)
    meta_ads_access_token: str = "" # Separate EAA token for Ads / Marketing API
    twitter_bearer_token: str = "" # X/Twitter
    xpoz_api_key: str = ""         # social engagement read

    # ─── Video generation ───
    # Generative clips (Runway Gen-3/4). Provider-abstracted with a stub
    # so the durable job pipeline (submit → poll → land in approval queue)
    # is provable end-to-end WITHOUT burning render credits. Flip to
    # `runway` once the key is verified. Higgsfield is wired; MiniMax is
    # intentionally NOT wired — no usable public REST API / no key — and
    # is not faked.
    video_provider: str = "higgsfield"  # runway | higgsfield | stub
    runway_model: str = "gen4_turbo"          # gen4_turbo | gen3a_turbo
    runway_api_version: str = "2024-11-06"    # X-Runway-Version header
    runway_video_ratio: str = "1280:720"
    runway_video_duration: int = 5            # seconds (Runway: 5 or 10)
    # Higgsfield image-to-video. Pinned to the official REST docs
    # (docs.higgsfield.ai): Authorization: Key key:secret; the model id is the
    # path — POST /{model} {image_url, prompt, duration}; poll
    # /requests/{id}/status → video.url.
    higgsfield_api_key: str = ""              # HF_API_KEY
    higgsfield_api_secret: str = ""           # HF_API_SECRET (joined as key:secret)
    higgsfield_model: str = "higgsfield-ai/dop/standard"  # I2V model path (swap to kling/seedance ids)
    # Trained Soul ID (Higgsfield custom-reference) for the brand hero. When
    # set, B-roll stills the script tags `uses_hero` render the SAME person
    # (e.g. James) across every cut, instead of relying on photo-reference.
    higgsfield_soul_id: str = ""              # empty = off (fall back to photo refs)
    higgsfield_soul_strength: float = 0.8     # 0..1 how hard the Soul drives the still

    # ─── Video productions (script → scene plan → clips → assembled mp4) ───
    # Each stage is provider-abstracted with a stub so the whole pipeline is
    # provable end-to-end without spending credits. Providers auto-activate
    # from the presence of their key (see credentials._auto_select_providers).
    avatar_provider: str = "stub"     # heygen | stub  (talking-head)
    heygen_api_version: str = "v2"
    heygen_voice_id: str = ""         # HeyGen voice id (required to speak text)
    image_model: str = "gpt-image-1"  # OpenAI image model for B-roll seed stills
    ocr_model: str = "gpt-4o-mini"    # vision model for document-image OCR
    # Auto-trim trailing silence on every avatar/broll clip and snap the
    # scene's duration to the trimmed length. Eliminates dead air between
    # scenes in Creatomate's stitched output. Disable for raw clips.
    auto_trim_silence: bool = True
    # ─── Intra-clip tightening (de-um / de-silence the chosen reel window) ───
    # Off by default — flip on after verifying on a few reels. When on, the reel
    # builder cuts INTERNAL silent gaps longer than max_gap_s out of the clip on
    # word boundaries and remaps captions + B-roll to the compressed timeline,
    # so the reel is punchier (no dead air). Best-effort: any failure ships the
    # untightened clip. Filler-word removal is a separate, riskier toggle.
    clip_tighten_enabled: bool = True     # ON: every clip gets dead air cut ("kill the fluff")
    clip_tighten_max_gap_s: float = 0.55   # only excise silences longer than this
    clip_tighten_min_savings_s: float = 0.8  # skip if we'd save less (not worth a re-encode)
    clip_tighten_remove_fillers: bool = True   # also drop isolated fillers ("um/uh/…")
    # ─── Auto-clip: the clipper works ON ITS OWN ───
    # When a source finishes analysis, automatically render its top-N scored
    # candidates into reels (they land in the approval queue) — no manual
    # "Render reel" click. Bounded per source to keep render spend predictable.
    auto_clip_enabled: bool = True
    auto_clip_top_n: int = 3               # how many top candidates to auto-render per source
    auto_clip_broll_style: str = ""        # ''(literal)|cinematic for auto-clips
    auto_clip_caption_style: str = ""      # blank → pipeline default
    # ─── Speaker-following auto-reframe (2-person interviews) ───
    # When a wide (16:9) cut is reframed to 9:16, keyframe the crop to pan to
    # WHOEVER is speaking (using AssemblyAI diarization + face detection), so
    # each person is in-frame during their turn — instead of one static crop
    # that can center the wrong person. Best-effort; degrades to the static
    # single-face pan, then a center crop. Needs assemblyai_api_key.
    speaker_follow_enabled: bool = True
    speaker_follow_min_turn_s: float = 1.2   # ignore turns shorter than this (no jitter)
    speaker_follow_ramp_s: float = 0.4       # ease duration for each pan (seconds)
    speaker_follow_left_bias: float = 0.42   # active speaker's face target (0..1, center-LEFT)
    # Auto-reuse of previously-rendered B-roll across videos. OFF: per human
    # feedback the same clips kept reappearing and drifting off the spoken
    # words, so every reel now generates fresh, transcript-grounded B-roll
    # (still SAVED to the library for deliberate, named reuse later). Flip on
    # to restore credit-saving automatic substitution.
    broll_reuse_enabled: bool = False
    # Style prefix applied to every B-roll seed image prompt — pushes the
    # output away from cartoon/illustration toward real-looking footage.
    image_style: str = (
        "Photorealistic cinematic photograph, real-world setting, "
        "natural lighting, sharp focus, high-quality DSLR look, "
        "NOT cartoon, NOT illustration, NOT 3D render."
    )
    assembly_provider: str = "stub"   # creatomate | shotstack | stub
    creatomate_api_key: str = ""
    shotstack_api_key: str = ""
    shotstack_env: str = "stage"      # stage | v1 (production)
    # Brand layer applied at assembly. All optional — left empty just skips
    # that element honestly rather than faking it.
    brand_logo_url: str = ""          # public URL to the brand logo (PNG with alpha)
    music_url_upbeat: str = ""
    music_url_calm: str = ""
    music_url_dramatic: str = ""
    music_url_tension: str = ""
    # Brand industry / visual world — steers B-roll toward the brand's REAL
    # domain (literal properties, neighborhoods, signings) instead of generic
    # abstract symbols. Empty → the picker grounds itself from brand_context.
    brand_industry: str = (
        "commercial and residential real estate — property investment, "
        "development, and brokerage (Staten Island & NYC metro)"
    )
    # Consistent cinematic color grade baked into every generated B-roll
    # prompt so the AI footage is cohesive with itself and with the speaker.
    broll_color_grade: str = (
        "warm cinematic color grade, soft teal shadows, gentle film contrast, "
        "natural golden-hour key light"
    )

    # ─── Media storage backend ───
    # Auto-flips to 'supabase' when SUPABASE_SERVICE_KEY is set; else 'local'.
    media_storage: str = "local"
    supabase_url: str = ""            # https://<ref>.supabase.co (REST/Storage)
    supabase_service_key: str = ""    # service_role secret — server-side ONLY
    supabase_media_bucket: str = "media"

    # ─── Google Drive auto-importer for James's real clips ───
    google_service_account_json: str = ""   # absolute path to JSON key file
    google_drive_folder_id: str = ""        # the Drive folder that holds clips

    # ─── Signup gating ───
    # POST /auth/signup is default-CLOSED: the very first signup (which
    # claims the legacy default tenant) is always allowed, but every
    # signup after that mints a brand-new tenant and is refused unless
    # SIGNUP_INVITE_CODE is set and the request carries the matching
    # invite_code. Leaving this empty keeps the install single-tenant.
    signup_invite_code: str = ""

    # ─── Brand Manager layer (bm2.0 merge, docs/unification-plan.md) ───
    # Master feature flag for the ported manager surfaces (P1+). Per-tenant
    # override lives in tenants.config['manager_v2']; this is the default.
    manager_v2: bool = False
    # D8 provider layer mode: mock = deterministic keyless fixtures (demo-able
    # with zero keys); live = each provider goes live iff its key is set,
    # per-provider mock fallback otherwise.
    manager_env: str = "mock"  # mock | live
    serper_api_key: str = ""       # web search (also carries the reddit stream)
    firecrawl_api_key: str = ""    # page scraping
    gnews_api_key: str = ""        # news (serper /news is the fallback)
    ayrshare_api_key: str = ""     # social aggregator fallback behind PostProxy
    postproxy_redirect_url: str = "http://localhost:3000/onboard?connected=1"
    scrapecreators_api_key: str = ""  # peer social data (Apify preferred when set)
    resend_api_key: str = ""       # email hand (Resend); nothing sends without approval
    email_from: str = "Brand Manager <hello@brandmanager.local>"
    blog_publish_url: str = ""     # CMS/webhook endpoint that accepts a post payload
    blog_api_key: str = ""         # bearer for blog_publish_url
    blog_public_base: str = ""     # public base to build post URLs, e.g. https://brand.blog
    # brand-voice harvest caps (cost control on transcription)
    voice_max_videos: int = 6
    voice_max_exemplars_per_source: int = 8
    voice_exemplar_min_chars: int = 40
    voice_exemplar_max_chars: int = 400
    # manager daily-cycle heartbeat (registered on the scheduled_jobs scheduler)
    manager_scheduler_enabled: bool = True
    daily_cycle_hour: int = 7
    # model routing tiers (D8): extract / content / strategy
    llm_extract_model: str = "claude-haiku-4-5-20251001"
    llm_content_model: str = "claude-sonnet-5"
    llm_strategy_model: str = "claude-opus-4-8"

    # DEV ONLY: open the login door — every request runs as the default
    # tenant. For local feature testing (launch.json 'unified-backend' sets
    # it); hard-refused against Supabase URLs in the middleware.
    dev_autologin: bool = False

    log_level: str = "INFO"

    # Retrieval tuning
    retrieval_top_k_per_index: int = 50
    retrieval_top_k_after_rerank: int = 12
    retrieval_min_score: float = Field(default=0.0, ge=0.0, le=1.0)

    # Content engine: independent voice-QA must score the draft at or
    # above this to pass un-flagged. Below it, the draft is still queued
    # for a human but flagged "needs revision" — never silently shipped.
    content_voice_floor: float = Field(default=0.7, ge=0.0, le=1.0)
    content_voice_k: int = 12  # voice/thesis exemplars pulled per draft
    content_facts_k: int = 6   # research/reference events pulled per draft
    content_voice_exemplars: int = 4  # diverse random voice-corpus samples injected


settings = Settings()
