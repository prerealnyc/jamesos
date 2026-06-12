"""Brand kit endpoints — read/update the identity every render carries."""

from fastapi import APIRouter
from pydantic import BaseModel

from .brand_kit import get_brand_kit, set_brand_kit

router = APIRouter()


class BrandKitUpdate(BaseModel):
    display_name: str | None = None
    tagline: str | None = None
    handle: str | None = None
    logo_url: str | None = None


@router.get("/brand-kit")
async def brand_kit_get() -> dict:
    return await get_brand_kit()


@router.put("/brand-kit")
async def brand_kit_put(req: BrandKitUpdate) -> dict:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    return await set_brand_kit(updates)
