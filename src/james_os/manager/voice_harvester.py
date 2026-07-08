"""Brand Voice Harvester — pull the brand's REAL voice from what's already out
there, at onboarding. Ported from bm2.0 backend/app/agents/voice_harvester.py
onto the james-os substrate.

The mandate: before we produce anything, learn how the brand actually sounds
from its own words in the wild — YouTube videos they host, posts they've
written, interviews/podcasts they've given — verify they said it (every
exemplar cited to its source URL, never fabricated), and distil a voice
profile. That voice, and only that voice, is what the fidelity gate scores new
content against, and what the 'hands' write toward.

This is the AUTO-pull twin of the manual Voice Studio (voice_ingest.py's
Google Drive door — untouched by this module):
- Spoken content: own-channel uploads (+ any extra URLs the operator pastes)
  -> transcribe (captions first, then audio) -> cited exemplar chunks.
- Written content: the brand's own posts — connected accounts via the
  aggregator (tenants.config['postproxy_profile_key']) or, pre-connection,
  the public handles in the profile's channels/identity fields.
- Then an LLM distils a voice profile onto the 'voice' profile section
  (source=RESEARCHED primary=True, cited to the harvested sources).

Exemplars land where the auditor's promotions already live — events with
event_type='note', payload.category='voice_corpus' (the exact category
content._bucket_of counts as voice) — tagged origin='harvested' with the
citation on payload.url + EventSource.uri. Dedupe rides EventSource.dedupe_key
against the shared corpus: posts reuse the auditor's exact exemplar-{platform}-
{post_id} key (a post already promoted by an audit never duplicates), and
transcript chunks use exemplar-harvest-{url}#{i}. Ingestion is idempotent and
strictly additive. Best-effort throughout: a source we can't reach is a note,
never a crash.
"""

import json
import re
from datetime import datetime, timezone
from uuid import UUID

from .. import db
from ..config import settings
from ..models import EventCreate, EventSource
from . import profile, runs
from .auditor import _exemplar_clean, _ingest_new, _parse_dt, _profile_handles
from .contracts import Citation, FieldWrite, Source
from .providers import get_providers
from .providers.base import Providers

AGENT = "voice_harvester"
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z']{2,}")

_VOICE_FIELDS = ("register", "tone", "sentence_style", "signature_phrases", "vocabulary", "dos", "donts", "summary")

PROMPT_SYSTEM = (
    "You are a brand voice analyst. From REAL, verbatim things the brand has said and written, you "
    "distil how this brand actually sounds, so future content can be written in that exact voice and "
    "nothing else. You never invent traits; you describe only what the samples show."
)
PROMPT = (
    "From these verbatim samples of the brand's OWN words (spoken transcripts and posts), describe the "
    "brand voice a writer must match.\n\nBRAND: {brand}\n\nSAMPLES:\n{samples}\n\n"
    "Return JSON only: {{\"register\": str, \"tone\": str, \"sentence_style\": str, "
    "\"signature_phrases\": [str], \"vocabulary\": [str], \"dos\": [str], \"donts\": [str], "
    "\"summary\": str (2-3 sentences a writer could follow)}}. Base every trait on the samples; if the "
    "samples are thin, say so in summary rather than inventing."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    cfg = config or {}
    extra_urls = [str(u).strip() for u in (cfg.get("extra_urls") or []) if str(u or "").strip()]
    handle = await runs.start_run(
        AGENT, trigger=cfg.get("trigger", "manual"), input={"extra_urls": extra_urls}, tenant_id=tenant_id
    )
    try:
        report = await _harvest(providers, tenant_id, extra_urls)
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={
        "exemplars_added": report["exemplars_added"],
        "sources": len(report["sources"]),
        "voice_derived": bool(report.get("voice_profile")),
        "notes": report["notes"][:10],
    })
    return report


# --------------------------------------------------------------- harvesting


async def _harvest(providers: Providers, tenant_id: UUID | None, extra_urls: list[str]) -> dict:
    started = _now()
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT current_setting('app.current_tenant', true) AS tenant, "
            "(SELECT config FROM tenants "
            " WHERE id = current_setting('app.current_tenant', true)::uuid) AS config"
        )
        tcfg = row["config"] if row else None
        if isinstance(tcfg, str):
            tcfg = json.loads(tcfg or "{}")
        tcfg = tcfg or {}
        fields = await profile.current_fields(conn)

    brand_id = str((row["tenant"] if row else "") or tenant_id or "")
    fmap = _fields_map(fields)
    brand_name = str(fmap.get("identity.display_name") or tcfg.get("brand_name") or "the brand")
    entity_type = str(fmap.get("identity.entity_type") or "brand")
    profile_key = str(tcfg.get("postproxy_profile_key") or "")

    sources: list[dict] = []
    notes: list[str] = []
    samples: list[str] = []  # for the voice-profile synthesis
    added_total = 0

    # 1) spoken content: own-channel uploads + any operator-pasted URLs
    video_urls = await _spoken_sources(providers, fields, brand_name, extra_urls, notes)
    for url, title in video_urls:
        try:
            tr = await providers.transcription.transcribe_source(url, title=title)
        except Exception as exc:  # provider itself should not raise, but be safe
            notes.append(f"transcription error for {url}: {str(exc)[:80]}")
            continue
        if not (tr.text or "").strip():
            sources.append({"kind": "video", "url": url, "title": title, "method": tr.method,
                            "exemplars": 0, "note": tr.note or "no transcript"})
            continue
        n = await _ingest_new(
            _transcript_events("youtube", url, tr.text, title, tr.method), tenant_id, started
        )
        added_total += n
        samples.append(_clip(tr.text, 900))
        sources.append({"kind": "video", "url": url, "title": title, "method": tr.method, "exemplars": n})

    # 2) written content: the brand's own posts on its connected accounts / public handles
    for platform, handle, posts in await _own_posts(providers, fields, profile_key, notes):
        events: list[EventCreate] = []
        for p in posts:
            if not p.text or not _good(p.text) or not _exemplar_clean(p.text):
                continue
            events.append(_post_event(platform, p))
            samples.append(_clip(p.text, 300))
            if len(events) >= settings.voice_max_exemplars_per_source:
                break
        pn = await _ingest_new(events, tenant_id, started)
        added_total += pn
        if pn:
            sources.append({"kind": "posts", "platform": platform, "handle": handle, "exemplars": pn})

    # 3) distil the voice profile over everything gathered (+ any pre-existing exemplars)
    voice_profile = await _derive_voice(
        providers, tenant_id, brand_id, f"{brand_name} ({entity_type})", samples, sources, notes
    )

    async with db.acquire(tenant_id) as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM events WHERE payload->>'category' = 'voice_corpus' "
            "AND superseded_by IS NULL"
        )

    return {
        "brand_id": brand_id,
        "sources": sources,
        "exemplars_added": added_total,
        "total_exemplars": int(total or 0),
        "voice_profile": voice_profile,
        "notes": notes,
    }


def _fields_map(fields: list[dict]) -> dict:
    """Latest current value per field_key (rows arrive version DESC)."""
    out: dict = {}
    for f in fields:
        v = f["value"].get("v") if isinstance(f.get("value"), dict) else None
        if v not in (None, "", [], {}):
            out.setdefault(f["field_key"], v)
    return out


async def _spoken_sources(
    providers: Providers, fields: list[dict], brand_name: str, extra_urls: list[str], notes: list[str]
) -> list[tuple[str, str]]:
    """(url, title) pairs to transcribe: own YouTube uploads + operator URLs."""
    out: list[tuple[str, str]] = []
    channel_url = _youtube_channel_url(fields)
    if not channel_url:
        try:
            for r in await providers.search.search(f"{brand_name} youtube channel", num=8):
                if any(m in r.url for m in ("youtube.com/channel", "youtube.com/@", "youtube.com/c/", "youtube.com/user/")):
                    channel_url = r.url
                    break
        except Exception:
            pass
    if channel_url:
        try:
            uploads = await providers.video.recent_uploads(channel_url, limit=settings.voice_max_videos)
            out.extend((v.url, v.title) for v in uploads)
            if not uploads:
                notes.append("YouTube channel located but no uploads returned")
        except Exception as exc:
            notes.append(f"YouTube uploads lookup failed: {str(exc)[:80]}")
    else:
        notes.append("no YouTube channel located for the brand (spoken harvest skipped)")
    for u in extra_urls:
        u = (u or "").strip()
        if u:
            out.append((u, "operator-added source"))
    return out


def _youtube_channel_url(fields: list[dict]) -> str:
    """The brand's own channel from the profile envelope. The researcher
    writes channels.youtube as {'platform','handle','url'}; string values
    (a URL, or the bm2.0-style channels.youtube.url key) are accepted too."""
    for f in fields:
        key = f["field_key"]
        if key not in ("channels.youtube", "channels.youtube.url"):
            continue
        v = f["value"].get("v") if isinstance(f.get("value"), dict) else None
        if isinstance(v, dict) and v.get("url"):
            return str(v["url"])
        if isinstance(v, str) and "youtube" in v and "/" in v:
            return v.strip()
    return ""


async def _own_posts(
    providers: Providers, fields: list[dict], profile_key: str, notes: list[str]
) -> list[tuple]:
    """The brand's own written posts. Connected accounts win (authoritative);
    otherwise pull from the public handles in the profile envelope (the
    auditor's handle-extraction helper, reused)."""
    out: list[tuple] = []
    if profile_key:
        accounts = []
        try:
            accounts = await providers.social.list_accounts(profile_key)
        except Exception as exc:
            notes.append(f"aggregator account listing failed: {str(exc)[:60]}")
        if accounts:
            for a in accounts[:4]:
                try:
                    posts = await providers.social.post_history(profile_key, a.platform, months=12)
                    out.append((a.platform, a.handle, posts[:20]))
                except Exception as exc:
                    notes.append(f"post history unavailable for {a.platform}: {str(exc)[:60]}")
            return out
    # pre-connection: own handles from the profile
    for platform, handle in sorted(_profile_handles(fields).items())[:4]:
        try:
            posts = await providers.peers.recent_posts(platform, handle, limit=12)
            out.append((platform, handle, posts))
        except Exception as exc:
            notes.append(f"public posts unavailable for {platform}:@{handle}: {str(exc)[:60]}")
    return out[:3]


# ---------------------------------------------------------- exemplar chunking


def _transcript_events(
    platform: str, source_url: str, text: str, title: str, method: str
) -> list[EventCreate]:
    """Transcript -> cited voice_corpus exemplar events, donor quality gates
    intact (passage min/max chars, _good signal check, per-source cap)."""
    events: list[EventCreate] = []
    for i, passage in enumerate(
        _passages(text, settings.voice_exemplar_min_chars, settings.voice_exemplar_max_chars)
    ):
        if len(events) >= settings.voice_max_exemplars_per_source:
            break
        if not _good(passage):
            continue
        events.append(
            EventCreate(
                event_type="note",
                payload={
                    "text": passage,
                    "category": "voice_corpus",
                    "origin": "harvested",
                    "platform": platform,
                    "url": source_url,
                    "title": title,
                    "method": method,
                    "speaker": "brand",
                },
                raw_content=passage,
                source=EventSource(
                    adapter="voice_harvester",
                    uri=source_url,
                    dedupe_key=f"exemplar-harvest-{source_url}#{i}",
                    raw_metadata={"category": "voice_corpus", "origin": "harvested"},
                ),
                entities=["category:voice_corpus", f"platform:{platform}"],
            )
        )
    return events


def _post_event(platform: str, p) -> EventCreate:
    """One brand-authored post -> a voice_corpus exemplar event. The dedupe
    key is the auditor's exact exemplar-{platform}-{post_id} convention, so a
    post already promoted by an audit never duplicates in the shared corpus."""
    text = p.text.strip()
    return EventCreate(
        event_type="note",
        payload={
            "text": text,
            "category": "voice_corpus",
            "origin": "harvested",
            "platform": platform,
            "post_id": p.post_id,
            "url": p.url,
            "method": "post",
            "speaker": "brand",
            "published_at": p.published_at,
        },
        raw_content=text,
        source=EventSource(
            adapter="voice_harvester",
            uri=p.url or None,
            dedupe_key=f"exemplar-{platform}-{p.post_id}",
            raw_metadata={"category": "voice_corpus", "origin": "harvested"},
        ),
        entities=["category:voice_corpus", f"platform:{platform}"],
        effective_at=_parse_dt(p.published_at),
    )


def _passages(text: str, min_c: int, max_c: int):
    """Group sentences into passages <= max_c chars, yielding those >= min_c —
    natural, quotable chunks of how the brand talks."""
    buf = ""
    for sent in _SENT_SPLIT.split((text or "").strip()):
        sent = sent.strip()
        if not sent:
            continue
        if len(buf) + len(sent) + 1 > max_c and buf:
            if len(buf) >= min_c:
                yield buf.strip()
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if len(buf) >= min_c:
        yield buf.strip()


def _good(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < settings.voice_exemplar_min_chars:
        return False
    words = _WORD.findall(t)
    if len(words) < 6:  # too short / mostly symbols to carry a voice signal
        return False
    hashy = t.count("#") + t.count("http")
    return hashy <= 3  # not a hashtag/link dump


def _clip(text: str, n: int) -> str:
    t = " ".join((text or "").split())
    return t[:n]


# --------------------------------------------------------- voice-profile synth


async def _derive_voice(
    providers: Providers, tenant_id: UUID | None, brand_id: str, brand_label: str,
    samples: list[str], sources: list[dict], notes: list[str],
) -> dict:
    """LLM distils the voice from real samples -> profile 'voice' section,
    cited to the sources it was learned from (source=RESEARCHED, primary: the
    brand's own words)."""
    if not samples:
        # fall back to any exemplars already in the corpus (auditor-promoted
        # or Voice Studio uploads)
        async with db.acquire(tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT coalesce(payload->>'text', raw_content) AS text FROM events "
                "WHERE payload->>'category' = 'voice_corpus' AND superseded_by IS NULL "
                "ORDER BY created_at DESC LIMIT 20"
            )
        samples = [_clip(r["text"], 300) for r in rows if r["text"]]
    if not samples:
        notes.append("no voice samples gathered — nothing to distil yet")
        return {}

    sample_block = "\n---\n".join(samples[:24])
    raw = await providers.llm.complete_json(
        "content", PROMPT_SYSTEM,
        PROMPT.format(brand=brand_label, samples=_clip(sample_block, 6000)),
    )
    profile_out: dict = {}
    citations = [Citation(url=s["url"], note=s.get("title", "")) for s in sources if s.get("url")][:5]
    if not citations:
        citations = [Citation(ref=f"voice_harvest:{brand_id}", note="brand's own posts")]

    async with db.acquire(tenant_id) as conn:
        for key in _VOICE_FIELDS:
            val = raw.get(key)
            if val in (None, "", [], {}):
                continue
            await profile.write_field(
                conn,
                FieldWrite(
                    section="voice",
                    field_key=f"voice.{key}",
                    value=val,
                    source=Source.RESEARCHED,
                    citations=citations,
                    primary=True,  # distilled from the brand's OWN words = primary source
                    updated_by=AGENT,
                ),
            )
            profile_out[key] = val
    return profile_out
