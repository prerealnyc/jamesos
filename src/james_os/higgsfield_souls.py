"""Higgsfield Soul ID (a.k.a. custom-references) — list / get / create, and
generate a consistent CHARACTER image from a Soul ID.

Soul ID is Higgsfield's trained-character feature: train a digital double on
photos → a `reference_id` you can pass into generation so the SAME person comes
out every time. Endpoints (server https://platform.higgsfield.ai):

  * GET  /v1/custom-references/list          — list your Soul IDs
  * GET  /v1/custom-references/{id}           — one Soul ID
  * POST /v1/custom-references                — create/train (name + images)
  * DEL  /v1/custom-references/{id}           — delete
  * POST /higgsfield-ai/soul/character        — GENERATE using a Soul ID:
        required: prompt, custom_reference_id (= the Soul ID),
                  custom_reference_strength (0..1)
        optional: aspect_ratio, resolution, batch_size, seed, style_id,
                  enhance_prompt, style_strength, image_reference_url
  * GET  /requests/{request_id}/status        — poll a generation

Auth is the same `Key {key}:{secret}` pair as the video provider
(settings.higgsfield_api_key / _api_secret), populated from the encrypted
credential vault at startup. Failures are surfaced honestly, never faked.
"""

import httpx

from .config import settings

_BASE = "https://platform.higgsfield.ai"
_TIMEOUT = 60.0


def _creds() -> tuple[str, str]:
    return (
        (settings.higgsfield_api_key or "").strip(),
        (settings.higgsfield_api_secret or "").strip(),
    )


def configured() -> bool:
    k, s = _creds()
    return bool(k and s)


def _headers() -> dict:
    k, s = _creds()
    return {
        "Authorization": f"Key {k}:{s}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _norm_soul(it: dict) -> dict:
    """Normalize one custom-reference record to a stable shape for the UI,
    defensively (the exact field names aren't in the public OpenAPI spec)."""
    if not isinstance(it, dict):
        return {}
    return {
        "id": str(it.get("id") or it.get("reference_id") or it.get("uuid") or ""),
        "name": it.get("name") or it.get("title") or it.get("label") or "",
        "status": it.get("status") or it.get("state") or "",
        "thumbnail": (
            it.get("thumbnail_url") or it.get("preview_url")
            or it.get("image_url") or it.get("cover_url") or ""
        ),
        "created_at": it.get("created_at") or it.get("createdAt") or "",
    }


def _extract_items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "references", "custom_references", "data", "results"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


async def list_souls() -> dict:
    """List the account's Soul IDs. Returns
    {configured, souls:[{id,name,status,thumbnail,created_at}], count, error}."""
    if not configured():
        return {"configured": False, "souls": [], "count": 0,
                "error": "Higgsfield API key/secret not set in Settings."}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/v1/custom-references/list", headers=_headers())
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "souls": [], "count": 0,
                "error": f"transport error: {e}"}
    if r.status_code in (401, 403):
        return {"configured": True, "souls": [], "count": 0,
                "error": f"auth/permission denied (HTTP {r.status_code}): {r.text[:240]}"}
    if r.status_code >= 400:
        return {"configured": True, "souls": [], "count": 0,
                "error": f"HTTP {r.status_code}: {r.text[:240]}"}
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return {"configured": True, "souls": [], "count": 0,
                "error": f"unparseable response: {r.text[:200]}"}
    souls = [s for s in (_norm_soul(it) for it in _extract_items(data)) if s.get("id")]
    return {"configured": True, "souls": souls, "count": len(souls), "error": None}


async def get_soul(reference_id: str) -> dict:
    if not configured():
        return {"error": "Higgsfield API key/secret not set."}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(
            f"{_BASE}/v1/custom-references/{reference_id}", headers=_headers()
        )
    if r.status_code >= 400:
        return {"error": f"HTTP {r.status_code}: {r.text[:240]}"}
    return {"soul": _norm_soul(r.json()), "error": None}


async def create_reference(*, name: str, image_urls: list[str]) -> dict:
    """Train a NEW Soul ID (custom reference) from hosted training photos.

    POST /v1/custom-references with input_images as public image URLs (the
    documented shape: [{"type":"image_url","image_url": "https://…"}]).
    Higgsfield wants 5–20 face photos (8–12 ideal) and a paid plan (Basic+);
    training takes ~3–5 min — poll readiness via list_souls. Returns
    {reference_id, status, trained_on, error}."""
    if not configured():
        return {"reference_id": "", "status": "failed", "trained_on": 0,
                "error": "Higgsfield API key/secret not set."}
    urls = [u for u in (image_urls or []) if isinstance(u, str) and u.startswith("http")]
    if len(urls) < 5:
        return {"reference_id": "", "status": "failed", "trained_on": len(urls),
                "error": f"Need at least 5 public photo URLs to train a Soul; got {len(urls)}."}
    urls = urls[:20]  # Higgsfield caps training at 20 images
    body = {
        "name": (name or "hero").strip()[:80],
        "input_images": [{"type": "image_url", "image_url": u} for u in urls],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                f"{_BASE}/v1/custom-references", headers=_headers(), json=body
            )
    except Exception as e:  # noqa: BLE001
        return {"reference_id": "", "status": "failed", "trained_on": len(urls),
                "error": f"transport error: {e}"}
    if r.status_code in (401, 403):
        return {"reference_id": "", "status": "failed", "trained_on": len(urls),
                "error": f"auth/permission denied (HTTP {r.status_code}): {r.text[:240]}"}
    if r.status_code >= 400:
        return {"reference_id": "", "status": "failed", "trained_on": len(urls),
                "error": f"HTTP {r.status_code}: {r.text[:240]}"}
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return {"reference_id": "", "status": "failed", "trained_on": len(urls),
                "error": f"unparseable response: {r.text[:200]}"}
    rid = str(data.get("id") or data.get("reference_id") or data.get("uuid") or "")
    return {
        "reference_id": rid,
        "status": str(data.get("status", "training")),
        "trained_on": len(urls),
        "error": None if rid else f"no id in response: {str(data)[:200]}",
    }


async def generate_character_image(
    *,
    custom_reference_id: str,
    prompt: str,
    aspect_ratio: str = "9:16",
    strength: float = 0.8,
    resolution: str = "2K",
) -> dict:
    """Submit a soul/character generation that renders the trained person from
    their Soul ID. Returns {request_id, status, error}. Poll with poll_request."""
    if not configured():
        return {"request_id": "", "status": "failed",
                "error": "Higgsfield API key/secret not set."}
    if not custom_reference_id:
        return {"request_id": "", "status": "failed",
                "error": "custom_reference_id (Soul ID) is required."}
    body = {
        "prompt": prompt[:1500],
        "custom_reference_id": custom_reference_id,
        "custom_reference_strength": max(0.0, min(float(strength), 1.0)),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                f"{_BASE}/higgsfield-ai/soul/character",
                headers=_headers(), json=body,
            )
    except Exception as e:  # noqa: BLE001
        return {"request_id": "", "status": "failed", "error": f"transport error: {e}"}
    if r.status_code in (401, 403):
        return {"request_id": "", "status": "failed",
                "error": f"auth/permission denied (HTTP {r.status_code}): {r.text[:240]}"}
    if r.status_code >= 400:
        return {"request_id": "", "status": "failed",
                "error": f"HTTP {r.status_code}: {r.text[:240]}"}
    data = r.json()
    rid = str(data.get("request_id") or data.get("id") or "")
    return {"request_id": rid, "status": str(data.get("status", "queued")),
            "error": None if rid else f"no request_id: {str(data)[:200]}"}


async def poll_request(request_id: str) -> dict:
    """Poll a generation. Returns {status, image_url, error}. Never raises —
    a transport blip or HTTP error surfaces as an empty status with an
    "HTTP "/"transport error" error string so the caller can keep polling
    (transient) rather than mistaking it for a terminal render failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as c:
            r = await c.get(
                f"{_BASE}/requests/{request_id}/status", headers=_headers()
            )
    except Exception as e:  # noqa: BLE001
        return {"status": "", "image_url": "", "error": f"transport error: {e}"}
    if r.status_code >= 400:
        return {"status": "", "image_url": "", "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    try:
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"status": "", "image_url": "", "error": f"unparseable: {e}"}
    status = str(data.get("status", "")).lower()
    url = ""
    imgs = data.get("images") or data.get("results") or data.get("output") or []
    if isinstance(imgs, list) and imgs:
        first = imgs[0]
        url = first.get("url") if isinstance(first, dict) else str(first)
    elif isinstance(data.get("image_url"), str):
        url = data["image_url"]
    return {"status": status, "image_url": url or "", "error": None}


__all__ = [
    "configured", "list_souls", "get_soul", "create_reference",
    "generate_character_image", "poll_request",
]
