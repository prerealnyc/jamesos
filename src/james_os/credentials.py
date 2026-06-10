"""Tenant-managed credentials.

The product is meant to be sold: a tenant must be able to drop their own
API keys into the Settings UI and have JAMES OS connect automatically —
no hand-editing `.env`, no restart.

Model (deliberately simple and honest):

  * `.env` is the *default* layer. Whatever config.py loaded at boot is the
    baseline (snapshotted here at import).
  * The DB (`tenants.config -> 'credentials'`) is the *override* layer,
    per tenant, edited from the Settings UI.
  * `load_into_settings()` = reset managed fields to the .env baseline,
    then apply the DB overlay on top, then re-pick providers and bust the
    cached provider singletons. Called at startup and after every save, so
    a saved key takes effect on the very next request.

Honest limitations (not hidden):

  * Secrets are stored as plaintext JSON in Postgres — the same trust
    level as a plaintext `.env`. For a real multi-tenant SaaS the next
    step is envelope encryption (KMS/Fernet). Flagged, not silently shipped
    as "secure".
  * `settings` is a per-process object. This mutation is visible only in
    the worker that handled the save. Fine for a single-process uvicorn
    (the current deployment); a multi-worker deployment needs a shared
    cache invalidation signal. Flagged, not pretended away.
"""

import json
from dataclasses import dataclass

from . import apify as _apify_mod
from . import embedder as _embedder_mod
from . import llm as _llm_mod
from . import research as _research_mod
from .config import settings
from .db import acquire


@dataclass(frozen=True)
class ManagedField:
    name: str          # the Settings attribute name
    label: str         # human label for the UI
    group: str         # UI grouping
    secret: bool = True  # True → masked everywhere; False → shown plainly
    placeholder: str = ""


# What the Settings UI is allowed to manage. NOTE: provider *toggles*
# (embedding_provider / llm_provider) are intentionally NOT here — they
# stay driven by .env so a working core can't be broken from the UI.
# Research auto-selects its provider from the presence of a key instead
# (see _auto_select_providers).
MANAGED_FIELDS: list[ManagedField] = [
    # Memory & AI core
    ManagedField("anthropic_api_key", "Anthropic (Claude) API key", "Memory & AI core"),
    ManagedField("voyage_api_key", "Voyage embeddings API key", "Memory & AI core"),
    ManagedField("cohere_api_key", "Cohere rerank API key", "Memory & AI core"),
    ManagedField("openai_api_key", "OpenAI API key (Whisper/GPT)", "Memory & AI core"),
    # Market research / internet intelligence
    ManagedField("perplexity_api_key", "Perplexity API key", "Market research"),
    ManagedField(
        "perplexity_model", "Perplexity model", "Market research",
        secret=False, placeholder="sonar-pro",
    ),
    ManagedField(
        "google_search_api_key", "Google Custom Search API key", "Market research"
    ),
    ManagedField(
        "google_search_cx", "Google Custom Search engine id (cx)", "Market research",
        secret=False,
    ),
    ManagedField("apify_api_key", "Apify API token (trend scraping)", "Market research"),
    ManagedField(
        "youtube_api_key", "YouTube Data API key (trend discovery)", "Market research"
    ),
    # Video & media
    ManagedField("heygen_api_key", "HeyGen API key", "Video & media"),
    ManagedField(
        "heygen_avatar_id", "HeyGen default avatar id", "Video & media", secret=False
    ),
    ManagedField(
        "heygen_voice_id", "HeyGen voice id (to speak text)", "Video & media", secret=False
    ),
    ManagedField("descript_api_key", "Descript API key", "Video & media"),
    ManagedField("runway_api_key", "Runway API key", "Video & media"),
    ManagedField("higgsfield_api_key", "Higgsfield API key (HF_API_KEY) — experimental", "Video & media"),
    ManagedField("higgsfield_api_secret", "Higgsfield API secret (HF_API_SECRET)", "Video & media"),
    ManagedField("higgsfield_model", "Higgsfield I2V model (e.g. higgsfield-ai/dop/standard, kling-video/v2.1/pro/image-to-video)", "Video & media", secret=False),
    ManagedField("creatomate_api_key", "Creatomate API key (video assembly)", "Video & media"),
    ManagedField("shotstack_api_key", "Shotstack API key (video assembly)", "Video & media"),
    ManagedField(
        "brand_logo_url", "Brand logo URL (public PNG, alpha)", "Video & media",
        secret=False, placeholder="https://…/logo.png",
    ),
    ManagedField("music_url_upbeat", "Music URL: upbeat", "Video & media", secret=False),
    ManagedField("music_url_calm", "Music URL: calm", "Video & media", secret=False),
    ManagedField("music_url_dramatic", "Music URL: dramatic", "Video & media", secret=False),
    ManagedField("music_url_tension", "Music URL: tension", "Video & media", secret=False),
    # Media storage (Supabase Storage gives Creatomate publicly fetchable URLs)
    ManagedField("supabase_service_key", "Supabase service_role key (media storage)", "Storage"),
    ManagedField(
        "supabase_url", "Supabase project URL (auto-derived if blank)",
        "Storage", secret=False, placeholder="https://<ref>.supabase.co",
    ),
    ManagedField(
        "supabase_media_bucket", "Media storage bucket", "Storage",
        secret=False, placeholder="media",
    ),
    # Google Drive auto-importer for James's real clips
    ManagedField(
        "google_service_account_json", "Google service account JSON path", "Storage",
        secret=False, placeholder="/Users/.../service-account.json",
    ),
    ManagedField(
        "google_drive_folder_id", "Google Drive folder id (clips)", "Storage",
        secret=False, placeholder="1AbCdEf…",
    ),
    ManagedField("elevenlabs_api_key", "ElevenLabs API key", "Video & media"),
    ManagedField("minimax_api_key", "MiniMax API key", "Video & media"),
    # Publishing & social
    ManagedField("postproxy_api_key", "PostProxy API key", "Publishing & social"),
    ManagedField("meta_access_token", "Meta (IG/FB/Threads) token", "Publishing & social"),
    ManagedField("twitter_bearer_token", "X/Twitter bearer token", "Publishing & social"),
    ManagedField("xpoz_api_key", "Xpoz API key", "Publishing & social"),
    # Meta Developer App credentials — used by the upcoming Instagram /
    # Facebook OAuth flow. App ID is technically public (it ships in
    # client-side OAuth URLs) but we still treat it as managed for
    # easy editing. Secret is server-side only and never exposed.
    ManagedField(
        "meta_app_id", "Meta App ID (Instagram / Facebook OAuth)",
        "Publishing & social", secret=False,
        placeholder="e.g. 1234567890123456",
    ),
    ManagedField(
        "meta_app_secret", "Meta App Secret (Instagram / Facebook OAuth)",
        "Publishing & social",
    ),
    # Business / Ads Manager identifiers — used to scope Graph API
    # calls to a specific business or ad account. Not secrets but
    # tedious to dig out, so we store them here for one-click use.
    ManagedField(
        "meta_business_id", "Meta Business Manager ID",
        "Publishing & social", secret=False,
        placeholder="e.g. 1234567890123456",
    ),
    ManagedField(
        "meta_ad_account_id", "Meta Ad Account ID (Ads insights)",
        "Publishing & social", secret=False,
        placeholder="e.g. act_1234567890",
    ),
    ManagedField(
        "meta_ig_business_id", "Instagram Business Account ID (optional)",
        "Publishing & social", secret=False,
        placeholder="auto-discovered if blank",
    ),
    # Separate token for Ads Manager / Marketing API. Different scope
    # set (ads_read, ads_management, business_management) than the
    # content-side meta_access_token (instagram_basic, *_insights,
    # pages_*). Using the right token per endpoint avoids missing-
    # scope errors. Both are EAA-prefixed long-lived tokens.
    ManagedField(
        "meta_ads_access_token",
        "Meta Ads Manager / Marketing API token",
        "Publishing & social",
    ),
]

_FIELD_NAMES = {f.name for f in MANAGED_FIELDS}
_FIELD_BY_NAME = {f.name: f for f in MANAGED_FIELDS}

# The .env / config.py baseline, captured once at import — before any
# DB overlay is applied. Clearing a key in the UI reverts to this.
_ENV_BASELINE: dict[str, str] = {
    f.name: str(getattr(settings, f.name, "") or "") for f in MANAGED_FIELDS
}

# Provider-toggle baseline. NOT in MANAGED_FIELDS (UI can't change them),
# but captured here so an explicit env choice (e.g. LLM_PROVIDER=openai
# because Anthropic credits ran out) survives credential-driven
# re-evaluation in _auto_select_providers.
_PROVIDER_BASELINE: dict[str, str] = {
    "llm_provider": str(getattr(settings, "llm_provider", "") or "").lower(),
    "embedding_provider": str(getattr(settings, "embedding_provider", "") or "").lower(),
    "video_provider": str(getattr(settings, "video_provider", "") or "").lower(),
}


def mask(value: str, secret: bool = True) -> str:
    """Never returns enough to reconstruct a secret."""
    if not value:
        return ""
    if not secret:
        return value
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}••••{value[-4:]}"


def _auto_select_providers() -> None:
    """'Connect automatically': a key's mere presence wires its capability.

    Covers every provider that has a clean stub fallback. A user pasting
    an Anthropic or Voyage key into Settings expects the AI core to start
    working — pre-2026-05 it didn't, because LLM/embedding stayed
    .env-driven. Now they auto-flip too.

    Explicit env choices win: if .env set `LLM_PROVIDER=openai` (e.g.
    because Anthropic credits ran out), the baseline survives even if an
    Anthropic key is also present. Auto-flip only activates when the
    baseline is 'stub' (the user didn't choose).

    Embedding has only one supported live vendor (Voyage), so it's a
    straight key-present-or-stub flip.
    """
    llm_baseline = _PROVIDER_BASELINE.get("llm_provider") or "stub"
    if llm_baseline != "stub":
        settings.llm_provider = llm_baseline  # honor explicit env choice
    elif (settings.anthropic_api_key or "").strip():
        settings.llm_provider = "anthropic"
    elif (settings.openai_api_key or "").strip():
        settings.llm_provider = "openai"
    else:
        settings.llm_provider = "stub"

    emb_baseline = _PROVIDER_BASELINE.get("embedding_provider") or "stub"
    if emb_baseline != "stub":
        settings.embedding_provider = emb_baseline
    else:
        settings.embedding_provider = (
            "voyage" if (settings.voyage_api_key or "").strip() else "stub"
        )

    settings.research_provider = (
        "perplexity" if (settings.perplexity_api_key or "").strip() else "stub"
    )
    # Talking-head avatar: a HeyGen key flips it live.
    settings.avatar_provider = (
        "heygen" if (settings.heygen_api_key or "").strip() else "stub"
    )
    # B-roll generator: Runway is the proven engine and wins when its key is
    # present. Higgsfield (experimental) activates only when it's the ONLY
    # video credential — so pasting a Runway key keeps Runway, and pasting
    # only Higgsfield keys switches to it. Explicit VIDEO_PROVIDER in .env
    # overrides both (lets an operator pin Higgsfield deliberately).
    video_baseline = _PROVIDER_BASELINE.get("video_provider") or "stub"
    if video_baseline != "stub":
        settings.video_provider = video_baseline
    elif (settings.runway_api_key or "").strip():
        settings.video_provider = "runway"
    elif (settings.higgsfield_api_key or "").strip() and (settings.higgsfield_api_secret or "").strip():
        settings.video_provider = "higgsfield"
    else:
        settings.video_provider = "stub"
    # Video assembly: Creatomate preferred, then Shotstack, else stub.
    if (settings.creatomate_api_key or "").strip():
        settings.assembly_provider = "creatomate"
    elif (settings.shotstack_api_key or "").strip():
        settings.assembly_provider = "shotstack"
    else:
        settings.assembly_provider = "stub"
    # Media storage: Supabase Storage when its service_role is present,
    # otherwise local-disk. This makes uploads publicly reachable by
    # Creatomate without a manual provider switch.
    settings.media_storage = (
        "supabase" if (settings.supabase_service_key or "").strip() else "local"
    )


def _bust_provider_caches() -> None:
    _embedder_mod._embedder = None
    _llm_mod._llm = None
    _research_mod._provider = None
    _apify_mod._provider = None
    from . import assembly as _assembly_mod
    from . import heygen as _heygen_mod
    from . import media as _media_mod
    from . import video as _video_mod
    _heygen_mod._provider = None
    _assembly_mod._provider = None
    _video_mod._provider = None
    _media_mod._storage = None  # picks fresh local/supabase backend next call


def _apply(overlay: dict[str, str]) -> None:
    """Baseline → overlay → re-pick providers → drop cached singletons."""
    for name in _FIELD_NAMES:
        setattr(settings, name, _ENV_BASELINE.get(name, ""))
    for name, val in overlay.items():
        if name in _FIELD_NAMES and val:
            setattr(settings, name, val)
    _auto_select_providers()
    _bust_provider_caches()


async def _fetch_overlay() -> dict[str, str]:
    """Read the credentials overlay for the current tenant. Values
    are transparently decrypted — encrypted-on-disk rows look the
    same as plaintext rows to callers, so we can roll out encryption
    without a data migration."""
    from .encryption import decrypt
    async with acquire() as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    creds = (cfg or {}).get("credentials", {})
    return {
        k: decrypt(str(v))
        for k, v in creds.items()
        if k in _FIELD_NAMES and v
    }


async def load_into_settings() -> None:
    """Startup + post-save hook: make the live process reflect DB state."""
    _apply(await _fetch_overlay())


async def save(updates: dict[str, str]) -> dict:
    """Persist UI edits. Empty string = clear (revert to the .env default).

    Values are Fernet-encrypted at write time. The in-process settings
    object gets the PLAINTEXT (so providers can use it), but the DB
    column carries ciphertext only.
    """
    from .encryption import encrypt
    overlay = await _fetch_overlay()  # already-decrypted plaintext
    for name, raw in updates.items():
        if name not in _FIELD_NAMES:
            continue
        val = (raw or "").strip()
        if val:
            overlay[name] = val
        else:
            overlay.pop(name, None)

    # Encrypt every field before persisting.
    encrypted = {k: encrypt(v) for k, v in overlay.items() if v}

    async with acquire() as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{credentials}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(encrypted),
        )

    _apply(overlay)
    return await status()


async def status() -> dict:
    """Per-field: configured? where from? masked preview. No raw secrets."""
    overlay = await _fetch_overlay()
    fields = []
    for f in MANAGED_FIELDS:
        current = str(getattr(settings, f.name, "") or "")
        if f.name in overlay:
            src = "ui"
        elif _ENV_BASELINE.get(f.name):
            src = "env"
        else:
            src = "none"
        fields.append({
            "name": f.name,
            "label": f.label,
            "group": f.group,
            "secret": f.secret,
            "placeholder": f.placeholder,
            "configured": bool(current),
            "masked": mask(current, f.secret),
            "source": src,
        })
    return {
        "fields": fields,
        "research_provider": settings.research_provider,
        "note": (
            "Keys are stored per-tenant in Postgres, Fernet-encrypted at "
            "rest. They take effect on the next request — no restart. "
            "Adding a Perplexity key auto-activates live research."
        ),
    }
