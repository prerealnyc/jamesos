"""System prompts.

Rules-as-data: the prompt is rebuilt fresh from the plug_ins table on every
call, so there's no chat-window state for an attacker (or a confused user)
to override the rules with.
"""

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


async def build_system_prompt(tenant_id: UUID | None = None) -> str:
    guidelines = await _load_active_guidelines(tenant_id)
    return SYSTEM_PROMPT_BASE.format(
        tenant_name="this tenant",  # TODO: read from tenants.name
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
