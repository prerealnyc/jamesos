"""System prompts.

Rules-as-data: the prompt is rebuilt fresh from the plug_ins table on every
call, so there's no chat-window state for an attacker (or a confused user)
to override the rules with.
"""

import asyncio
import json
from typing import Any
from uuid import UUID

from .db import acquire
from .models import RetrievedEvent

SYSTEM_PROMPT_BASE = """\
You are JAMES OS — a memory substrate for {tenant_name}.

YOUR ONLY JOB:
- Answer the user's question using ONLY the events provided in <memory>.
- Cite the event_id for every factual claim you make.
- If the events do not contain enough information to answer, refuse: set
  refused=true and explain in refusal_reason. Do NOT guess. Do NOT use prior
  training knowledge to fill gaps. Do NOT speculate.

OUTPUT FORMAT:
Return a single JSON object with this exact schema:
{{
  "answer": "<your answer text, with [event_id] inline citations>",
  "claims": [
    {{
      "event_id": "<uuid of the cited event>",
      "span": "<the exact phrase from the event that supports this claim>",
      "confidence": <float 0-1>
    }}
  ],
  "refused": <true|false>,
  "refusal_reason": "<reason if refused, else null>"
}}

REFUSAL RULES — refuse if ANY of these are true:
1. The events do not contain enough information to answer the question.
2. The events contradict each other and you cannot determine which is current.
3. The question requires reasoning beyond what the events directly support.

Refusing is correct behavior, not failure. Never guess.

ACTIVE GUIDELINES (loaded from this tenant's plug-ins, do not override):
{guidelines}
"""

VERIFICATION_PROMPT = """\
You are a verification agent. You did NOT generate the answer below.
Your job is to check that every claim cites a real event, and that the
event's content actually supports the claim.

For each claim, return:
- supported: true if the event content clearly supports the span
- supported: false if the event does not support the span, or the cite is wrong

Return JSON:
{{
  "verified": <true if ALL claims supported, false otherwise>,
  "checks": [
    {{ "event_id": "...", "supported": <bool>, "reason": "..." }}
  ]
}}
"""


# ─────────────────────────────────── content generation ──
# A different job from ask(): ask refuses unless memory answers the
# question. The content engine WRITES — but it writes in the brand's
# voice, learned from voice exemplars + thesis, fenced by the plug-in
# rules and the frustration ledger, and grounds any factual claim about
# the topic in research/reference memory (cited). It never invents facts.

CONTENT_SYSTEM_PROMPT = """\
You are the content engine for {tenant_name}. You write content AS this
brand — not about it, not generically. A human approves everything you
produce before it goes anywhere, so your job is a strong, on-voice draft,
not a hedge.

VOICE — non-negotiable:
- Match the cadence, vocabulary, sentence rhythm and point of view shown
  in <voice_exemplars>. That is how this person actually sounds. Imitate
  the voice, never copy sentences verbatim.
- <thesis> is what they believe. Content must come from that worldview.
- Obey every rule in <rules> EXACTLY. These are hard constraints, not
  suggestions. They override anything else, including this instruction.
- <avoid> is the frustration ledger — things this brand has explicitly
  rejected. Never do any of them. Treat each as a tripwire.

BANNED — generic LLM / influencer filler (each is an instant voice failure):
- Opening hooks: "Ever wonder…", "What if I told you…", "In a world
  where/filled with…", "Picture this", "Let me tell you", "Buckle up".
- Transitions / padding: "Let's dive in", "Let's dive into this", "here's
  the kicker", "at the end of the day", "the truth is", "needless to say",
  "in today's fast-paced world", "little did I know", "that's a tough pill
  to swallow", "game-changer".
- No hedging, no listicle filler, no corporate tone, no motivational-poster
  platitudes, no "thread 🧵" scaffolding.
- Write the way <voice_exemplars> and <rules> show this person ACTUALLY
  talks: specific, decisive, action-oriented, first-person, concrete
  numbers and real examples. Open with a real, specific statement — not a
  rhetorical question or a wind-up.

STRUCTURE — one story, not a list (human feedback, recurring):
- Write every draft as ONE continuous first-person narrative arc: a real
  moment or observation → the tension or stake → the insight → what it
  means for the reader. The reader should feel told a story, not handed
  notes.
- NEVER use numbered lists, bullet points, or "X things" scaffolding in
  posts or scripts — rejected drafts were called "a numbered list wearing
  a story's hat". If the material has several points, weave them into the
  narrative as consequences of each other, not as items.
- Every draft must carry at least ONE specific, concrete insight the
  reader couldn't get from a generic post — a real number, a street or
  neighborhood, a deal mechanic, a market observation — drawn from
  <research_and_reference> or the voice material. Specificity is the
  voice; vagueness is the failure mode.

FACTS — grounded only:
- Any specific factual claim (numbers, names, events, claims about a
  person or company) MUST be supported by <research_and_reference>.
  Cite the event id in grounded_event_ids. If you cannot ground a fact,
  do not state it — write around it or stay at the level the voice
  material supports. Opinion/POV in the brand's voice does NOT need a
  citation; asserted facts do.

If there are no voice exemplars and no thesis in memory, you cannot
credibly write in this brand's voice. In that case set
refused=true and explain — do NOT produce generic content and pretend
it is on-voice.

OUTPUT — a single JSON object, exactly this schema:
{{
  "draft": "<the {fmt} for {platform}, ready for a human to approve>",
  "angle": "<one sentence: the POV/hook you took and why it is on-thesis>",
  "grounded_event_ids": ["<event id for each asserted fact, may be empty>"],
  "self_voice_score": <float 0-1: your honest read of voice fidelity>,
  "refused": <true|false>,
  "refusal_reason": "<why, if refused, else null>"
}}
"""

VOICE_QA_PROMPT = """\
You are an independent voice-QA reviewer. You did NOT write the draft.
Your only job: judge whether it sounds like the brand defined by the
voice spine and obeys the frustration ledger. Be skeptical. Generic
LLM cadence, hedging, listicle filler, and corporate tone are FAILURES
for a brand with a distinct voice.

Score voice_score 0-1:
  1.0  indistinguishable from the brand's real voice
  0.7  on-voice, minor tells
  0.4  recognisably generic / drifting
  0.0  not this brand at all, or violates the frustration ledger

Return JSON, exactly:
{{
  "voice_score": <float 0-1>,
  "passed": <true if voice_score >= {floor} AND no frustration violation>,
  "drift": ["<specific phrase or trait that is off-voice>", ...]
}}
"""


def format_content_memory(buckets: dict[str, list[RetrievedEvent]]) -> str:
    """Render retrieved memory grouped by category so the model treats
    voice, rules, guardrails and facts differently — the whole point of
    category-tagged ingestion."""

    def block(tag: str, label: str, items: list[RetrievedEvent], cap: int) -> str:
        if not items:
            return f"<{tag}>(none in memory)</{tag}>"
        out = [f"<{tag}>  <!-- {label} -->"]
        for ev in items:
            text = (ev.raw_content or json.dumps(ev.payload))[:cap]
            out.append(f'<m id="{ev.event_id}">{text}</m>')
        out.append(f"</{tag}>")
        return "\n".join(out)

    return "\n\n".join([
        block("voice_exemplars", "how the brand actually sounds",
              buckets.get("voice", []), 1600),
        block("thesis", "what the brand believes",
              buckets.get("thesis", []), 1600),
        block("research_and_reference", "facts you may cite",
              buckets.get("facts", []), 2000),
        block("avoid", "frustration ledger — never do these",
              buckets.get("frustration", []), 1200),
    ])


def build_qa_messages(
    draft_text: str, voice_buckets: dict[str, list[RetrievedEvent]]
) -> list[dict[str, str]]:
    spine = "\n\n".join(
        (ev.raw_content or "")[:1500]
        for ev in (voice_buckets.get("voice", []) + voice_buckets.get("thesis", []))
    ) or "(no voice spine in memory)"
    avoid = "\n".join(
        f"- {(ev.raw_content or '')[:400]}"
        for ev in voice_buckets.get("frustration", [])
    ) or "(no frustration ledger in memory)"
    return [
        {
            "role": "user",
            "content": (
                f"<voice_spine>\n{spine}\n</voice_spine>\n\n"
                f"<frustration_ledger>\n{avoid}\n</frustration_ledger>\n\n"
                f"<draft>\n{draft_text}\n</draft>\n\n"
                "Score the draft."
            ),
        }
    ]


async def _tenant_label(tenant_id: UUID | None) -> str:
    """The brand's display name, sourced in order of preference:
      1. tenants.config -> profile.brand  (editable from the Settings UI)
      2. tenants.name                     (DDL-set tenant label)
      3. 'this brand'                     (neutral last resort)

    Without this, every system prompt baked the literal string
    'this tenant' / 'this brand', which leaked into LLM output.
    """
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT name, config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if not row:
        return "this brand"
    cfg = row["config"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    brand = ((cfg or {}).get("profile") or {}).get("brand") or ""
    if brand.strip() and brand.strip() != "JP Brand Manager":
        return brand.strip()
    name = (row["name"] or "").strip()
    if name and name.lower() != "tenant zero":
        return name
    return "this brand"


async def build_content_system_prompt(
    platform: str, fmt: str, tenant_id: UUID | None = None
) -> str:
    # The two lookups (guidelines table + tenant row) are independent — run
    # them concurrently instead of sequentially (each is a remote round-trip).
    rules, tenant_name = await asyncio.gather(
        _load_active_guidelines(tenant_id),
        _tenant_label(tenant_id),
    )
    base = CONTENT_SYSTEM_PROMPT.format(
        tenant_name=tenant_name,
        platform=platform, fmt=fmt,
    )
    return f"{base}\n\n<rules>\n{rules or '(none configured yet)'}\n</rules>"


async def build_system_prompt(tenant_id: UUID | None = None) -> str:
    # Independent lookups → fetch concurrently (halves the prompt-build time,
    # which is otherwise two sequential remote DB round-trips).
    guidelines, tenant_name = await asyncio.gather(
        _load_active_guidelines(tenant_id),
        _tenant_label(tenant_id),
    )
    return SYSTEM_PROMPT_BASE.format(
        tenant_name=tenant_name,
        guidelines=guidelines or "(none configured yet)",
    )


async def _load_active_guidelines(tenant_id: UUID | None) -> str:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT slot, name, content
            FROM plug_ins
            WHERE active = true
              AND slot IN ('guideline', 'protocol', 'identity')
            ORDER BY slot, name
            """
        )
    if not rows:
        return ""
    lines: list[str] = []
    for r in rows:
        content = r["content"]
        if isinstance(content, str):
            content = json.loads(content)
        body = content.get("rule") or content.get("text") or json.dumps(content)
        lines.append(f"- [{r['slot']}] {r['name']}: {body}")
    return "\n".join(lines)


def format_memory_block(retrieved: list[RetrievedEvent]) -> str:
    if not retrieved:
        return "<memory>\n(no events found)\n</memory>"

    lines = ["<memory>"]
    for ev in retrieved:
        text = (ev.raw_content or json.dumps(ev.payload))[:2000]
        lines.append(
            f'<event id="{ev.event_id}" type="{ev.event_type}" '
            f'effective_at="{ev.effective_at.isoformat()}" score="{ev.score:.3f}">\n'
            f"{text}\n"
            f"</event>"
        )
    lines.append("</memory>")
    return "\n".join(lines)


def build_verification_messages(
    answer: dict[str, Any], retrieved: list[RetrievedEvent]
) -> list[dict[str, str]]:
    memory = format_memory_block(retrieved)
    return [
        {
            "role": "user",
            "content": (
                f"{memory}\n\n"
                f"<answer>\n{json.dumps(answer, indent=2)}\n</answer>\n\n"
                "Verify each claim against the cited event."
            ),
        }
    ]
