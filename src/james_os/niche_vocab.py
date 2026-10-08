"""One closed list of industries, so a brand and a layout can agree on a niche.

WHY THIS EXISTS. Niche tags were free text on BOTH sides and nobody agreed a
vocabulary. Brands carry what onboarding recorded against their competitors
('golf resort', 'tour packages, travel and holidays', 'commercial spaceport',
'AI automation for operations teams', 'New Jersey Comedy' ...); the catalogue
carries whatever a curator or the harvest typed ('golf', 'real estate',
'travel and holidays'). The matcher compared WORDS, so 'commercial spaceport'
matched 'commercial real estate' on 'commercial' and a spaceport was handed
real-estate layouts ahead of untagged ones. A stopword patched that one pair;
the next pair of free-text phrases that share a filler word would do it again.
And 313 of 324 approved curated rows (measured 2026-10-08) carry NO tag at
all, so for most of the catalogue there was nothing to match on in the first
place.

So both sides are mapped onto the same CLOSED list of labels:

  canonical(text)          free text -> labels. Deterministic, no network:
                           word-boundary keyword match, longest phrases first
                           (a phrase consumes its words, so 'office space'
                           never also reads as something about 'space'), then
                           each label's parents.
  infer_from_image(image)  a picture -> labels, for the rows nobody tagged.
                           One gpt-4o call at detail "low" (85 image tokens,
                           ~0.1 cent), metered through spend.py.

Unknown text maps to [] and the caller falls back to what it did before; a
label list that is wrong is worse than none.
"""

from __future__ import annotations

import base64
import json
import logging
import re

from . import spend
from .config import settings

logger = logging.getLogger(__name__)

# label -> (keywords/phrases, parents). Keywords are matched on word boundaries
# against lowercased text whose punctuation has been read as spaces, with an
# optional plural "s"/"es". A label's own text must map back to the label (a
# stored label is re-read through canonical() at ranking time) — pinned by a
# test over every entry.
#
# Words that are deliberately NOT keywords, because they put brands in the
# wrong industry: 'commercial' (spaceport vs real estate — the false match this
# module exists to end), 'course' (golf courses are not education), 'space'
# (office space, coworking space), 'campaign' (marketing vs politics),
# 'packages', 'services', 'operations', 'design', 'digital', 'family', 'app'.
VOCAB: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "real estate": (("real estate", "realestate", "realty", "realtor", "property",
                     "properties", "homebuyer", "home buying", "home buyer",
                     "homes for sale", "housing", "apartment", "condo", "landlord",
                     "property management"), ()),
    "commercial real estate": (("commercial real estate", "commercial property",
                                "commercial properties", "office space",
                                "industrial real estate", "retail space", "cre"),
                               ("real estate",)),
    "golf": (("golf", "golfer", "golfing", "golf course", "golf club",
              "country club", "pga"), ()),
    "travel": (("travel", "traveler", "traveller", "tour", "tour operator",
                "tour package", "tourism", "tourist", "holiday", "vacation",
                "itinerary", "travel agency", "travel agent", "cruise", "safari",
                "getaway", "trip", "backpacking"), ()),
    "hospitality": (("hospitality", "hotel", "resort", "motel", "inn", "lodge",
                     "lodging", "bed and breakfast", "boutique hotel",
                     "vacation rental", "airbnb"), ()),
    "restaurant & food": (("restaurant", "food", "foods", "cafe", "coffee",
                           "bakery", "catering", "caterer", "chef", "cuisine",
                           "dining", "recipe", "meal", "pizza", "burger",
                           "brewery", "winery", "wine", "beer", "cocktail",
                           "food truck", "grocery"), ()),
    "fitness": (("fitness", "gym", "workout", "personal trainer",
                 "personal training", "crossfit", "yoga", "pilates",
                 "bodybuilding", "strength training"), ()),
    "wellness": (("wellness", "spa", "meditation", "mindfulness", "nutrition",
                  "supplement", "holistic", "self care"), ()),
    "beauty": (("beauty", "salon", "cosmetic", "cosmetics", "makeup", "skincare",
                "skin care", "hair salon", "barber", "barbershop", "nail salon",
                "lash", "med spa", "medspa"), ()),
    "fashion": (("fashion", "apparel", "clothing", "clothes", "jewelry",
                 "jewellery", "streetwear", "footwear", "shoe", "sneaker",
                 "handbag", "menswear", "womenswear", "boutique"), ()),
    "retail": (("retail", "retailer", "store", "shop", "shopping", "ecommerce",
                "e commerce", "online store", "dtc", "direct to consumer"), ()),
    "electronics": (("electronics", "electronic", "gadget", "consumer electronics",
                     "smartphone", "phone repair", "computer repair"), ()),
    "technology": (("technology", "tech", "software", "saas", "startup", "it services",
                    "cybersecurity", "cloud computing", "web development",
                    "developer", "proptech"), ()),
    "ai & automation": (("ai", "artificial intelligence", "automation", "automate",
                         "machine learning", "chatbot", "llm", "generative ai",
                         "workflow automation", "ai agents", "robotics"),
                        ("technology",)),
    "finance": (("finance", "financial", "fintech", "investing", "investment",
                 "investor", "wealth", "wealth management", "bank", "banking",
                 "accounting", "accountant", "bookkeeping", "tax", "mortgage",
                 "lending", "loan", "credit", "crypto", "cryptocurrency",
                 "trading", "stock market", "private equity", "venture capital"), ()),
    "insurance": (("insurance", "insurer", "insurance agency", "insurance agent"), ()),
    "healthcare": (("healthcare", "health care", "medical", "clinic", "doctor",
                    "dentist", "dental", "hospital", "physician", "therapy",
                    "therapist", "chiropractor", "chiropractic", "pharmacy",
                    "mental health", "nurse", "nursing", "orthodontist"), ()),
    "education": (("education", "educational", "school", "university", "college",
                   "tutoring", "tutor", "online course", "elearning", "e learning",
                   "academy", "teacher", "student", "edtech"), ()),
    "legal": (("legal", "law firm", "lawyer", "attorney", "law", "paralegal",
               "immigration law", "personal injury"), ()),
    "automotive": (("automotive", "car", "auto", "dealership", "car dealer",
                    "vehicle", "motorcycle", "auto repair", "mechanic",
                    "car wash", "detailing"), ()),
    "home services": (("home services", "home service", "plumbing", "plumber", "hvac",
                       "roofing", "roofer", "landscaping", "lawn care", "cleaning service",
                       "house cleaning", "pest control", "electrician", "handyman",
                       "moving company", "movers"), ()),
    "construction": (("construction", "contractor", "builder", "home builder",
                      "remodeling", "remodel", "renovation", "architecture",
                      "architect"), ()),
    "interior design": (("interior design", "interior designer", "furniture",
                         "home decor", "decor", "interiors"), ()),
    "events": (("event", "event planning", "event planner", "wedding",
                "event venue", "venue", "conference", "festival", "party rental",
                "trade show"), ()),
    "entertainment": (("entertainment", "music", "musician", "film", "movie",
                       "cinema", "theater", "theatre", "concert", "gaming",
                       "video game", "nightlife", "streaming", "dj"), ()),
    "comedy": (("comedy", "comedian", "stand up", "standup", "humor", "humour",
                "funny", "improv", "meme", "satire"), ("entertainment",)),
    "politics": (("politics", "political", "politician", "election", "candidate",
                  "political campaign", "elected official", "congress", "senate",
                  "mayor", "government", "civic", "lobbying"), ()),
    "nonprofit": (("nonprofit", "non profit", "charity", "ngo", "fundraising",
                   "volunteer", "donation", "philanthropy"), ()),
    "sports": (("sports", "sport", "athletics", "athlete", "football", "soccer",
                "basketball", "baseball", "tennis", "pickleball", "hockey",
                "cycling", "running club", "martial arts", "boxing"), ()),
    "aerospace": (("aerospace", "spaceport", "space launch", "space industry",
                   "space tourism", "rocket", "satellite", "aviation", "airline",
                   "airport", "aircraft", "drone"), ()),
    "marketing": (("marketing", "advertising", "digital marketing",
                   "social media marketing", "seo", "branding", "ad agency",
                   "marketing agency", "pr agency", "public relations",
                   "influencer", "content creator", "lead generation",
                   "social media"), ()),
    "coaching": (("coaching", "coach", "life coach", "business coach", "mentor",
                  "mentoring", "self improvement", "personal development"), ()),
    "business services": (("business services", "consulting", "consultant",
                           "consultancy", "staffing", "recruiting", "recruitment",
                           "b2b", "professional services", "outsourcing"), ()),
    "parenting": (("parenting", "parent", "mom", "mother", "motherhood", "dad",
                   "fatherhood", "baby", "babies", "kid", "children", "toddler"), ()),
    "pets": (("pet", "dog", "cat", "puppy", "kitten", "veterinary", "veterinarian",
              "vet", "dog grooming", "pet grooming"), ()),
    "faith": (("faith", "church", "ministry", "christian", "religion", "religious",
               "spiritual", "mosque", "synagogue", "bible", "worship", "pastor"), ()),
    "media & publishing": (("media", "news", "publishing", "publisher", "magazine",
                            "journalism", "podcast", "blog", "newsletter", "book",
                            "author"), ()),
    "photography": (("photography", "photographer", "videography", "videographer",
                     "video production", "photo studio"), ()),
    "art & design": (("art", "artist", "gallery", "graphic design", "design studio",
                      "illustration", "illustrator", "crafts", "handmade",
                      "tattoo"), ()),
    "agriculture": (("agriculture", "farm", "farming", "farmer", "ranch",
                     "agritech", "nursery garden"), ()),
    "energy": (("energy", "solar", "renewable", "oil and gas", "utilities",
                "ev charging", "power plant"), ()),
    "logistics": (("logistics", "shipping", "freight", "trucking", "supply chain",
                   "warehouse", "courier", "delivery service"), ()),
    "manufacturing": (("manufacturing", "manufacturer", "factory", "industrial",
                       "machining", "fabrication"), ()),
}

LABELS: tuple[str, ...] = tuple(VOCAB)

# The most labels the vision read may assign one picture. More than three and
# the tags stop saying anything: a quote card "suits" everyone.
MAX_INFERRED = 3


def _norm(text: str) -> str:
    """Lowercase; '&' read as 'and'; every other non-alphanumeric run is a space."""
    t = str(text or "").lower().replace("&", " and ")
    return " ".join(re.split(r"[^a-z0-9]+", t))


# (keyword, label) pairs, longest phrase first (most words, then most letters),
# so a phrase is matched before any single word inside it.
_RULES: list[tuple[str, str, re.Pattern]] = sorted(
    ((kw_n, label, re.compile(r"(?<![a-z0-9])" + re.escape(kw_n) + r"(?:s|es)?(?![a-z0-9])"))
     for label, (kws, _) in VOCAB.items()
     for kw_n in {_norm(k) for k in kws} if kw_n),
    key=lambda r: (-len(r[0].split()), -len(r[0]), r[0], r[1]),
)


def _parents(label: str) -> list[str]:
    out: list[str] = []
    for p in VOCAB.get(label, ((), ()))[1]:
        if p in VOCAB and p not in out:
            out.append(p)
            out.extend(q for q in _parents(p) if q not in out)
    return out


def canonical(text_or_list) -> list[str]:
    """The vocabulary labels a niche phrase (or a list of them) names.

    Order is deterministic: labels in the order their keyword first appears in
    the text (a list read item by item), each followed by its parents, distinct.
    Unknown text -> [].
    """
    if text_or_list is None:
        return []
    items = [text_or_list] if isinstance(text_or_list, str) else list(text_or_list)
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = f" {_norm(item)} "
        if not text.strip():
            continue
        hits: list[tuple[int, str]] = []
        for _kw, label, pat in _RULES:
            for m in pat.finditer(text):
                hits.append((m.start(), label))
            # A matched phrase consumes its words: blank it so a shorter keyword
            # inside it ('real estate' inside 'commercial real estate', 'space'
            # inside 'office space') cannot match again on its own.
            text = pat.sub(lambda m: " " * len(m.group(0)), text)
        for _pos, label in sorted(hits):
            for lab in [label, *_parents(label)]:
                if lab not in out:
                    out.append(lab)
    return out


def is_label(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in VOCAB


# ───────────────────────────────────────────── the read: a picture -> labels ──

_MODEL = "gpt-4o"

_SYSTEM = (
    "You label ONE social-media post image with the industry it is FOR, so a "
    "design catalogue can offer it to brands in that industry. Choose ONLY from "
    "this list, spelled exactly as written:\n"
    + "\n".join(f"- {label}" for label in LABELS)
    + "\n\nRules: pick at most 3, most specific first. Judge from what the image "
    "shows and says (a golf course, a property listing, a menu, a candidate's "
    "name). A post that carries nothing industry-specific — a plain quote card, "
    "an abstract pattern, a generic motivational line — gets an EMPTY list; "
    "never guess. Return STRICT JSON: {\"labels\": [\"...\"]}."
)

# Tokens the call is expected to bill, for the backfill's dry-run estimate:
# 85 for the image at detail "low" (OpenAI's fixed figure), the prompt at ~4
# characters a token plus the user turn, and a short JSON answer.
EST_TOKENS_IN = 85 + len(_SYSTEM) // 4 + 20
EST_TOKENS_OUT = 25


def est_usd_per_image() -> float:
    return spend.estimate_tokens_usd(_MODEL, EST_TOKENS_IN, EST_TOKENS_OUT) or 0.0


def _client():
    """The OpenAI client, or None with no key — never a faked answer."""
    key = (settings.openai_api_key or "").strip()
    if not key:
        return None
    from openai import AsyncOpenAI

    # A short leash: this read runs inside an admin's upload request, file after
    # file. The SDK default (600 s, 2 retries) let a slow OpenAI hold one upload
    # for many minutes before the "store it untagged" fallback was reached.
    return AsyncOpenAI(api_key=key, timeout=20.0, max_retries=1)


def has_key() -> bool:
    return bool((settings.openai_api_key or "").strip())


def _sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _local_media_bytes(uri: str) -> bytes | None:
    """A /media-files/... path the local store served, as bytes. The model
    cannot fetch a path on our own disk, so the picture is sent inline. Refuses
    anything that resolves outside the media root."""
    from .media import media_root

    root = media_root().resolve()
    rel = uri[len("/media-files/"):]
    try:
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path.read_bytes()
    except OSError:
        return None


def _image_url(image) -> str | None:
    if isinstance(image, (bytes, bytearray)):
        data = bytes(image)
        return f"data:{_sniff_mime(data)};base64," + base64.b64encode(data).decode()
    uri = str(image or "").strip()
    if uri.startswith(("https://", "http://")):
        return uri
    if uri.startswith("/media-files/"):
        data = _local_media_bytes(uri)
        if data:
            return _image_url(data)
    return None


def filter_labels(raw) -> list[str]:
    """What the model said, kept only where it is in the vocabulary: lowercased,
    distinct, at most MAX_INFERRED. Anything else is dropped, never mapped."""
    out: list[str] = []
    for v in raw if isinstance(raw, list) else []:
        if not isinstance(v, str):
            continue
        t = v.strip().lower()
        if t in VOCAB and t not in out:
            out.append(t)
        if len(out) >= MAX_INFERRED:
            break
    return out


async def infer_detail(image) -> dict:
    """{status: ok | no_key | failed, labels}. `image` is bytes, an http(s) URL
    or a /media-files/ path. Only 'ok' is an answer — and an ok with [] is a
    real answer ("nothing industry-specific"), distinct from a failure."""
    client = _client()
    if client is None:
        return {"status": "no_key", "labels": []}
    url = _image_url(image)
    if not url:
        return {"status": "failed", "labels": [], "error": "no readable image"}
    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Which industries is this post for?"},
                    {"type": "image_url", "image_url": {"url": url, "detail": "low"}},
                ]},
            ],
            max_tokens=60,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        await spend.record_tokens("openai", getattr(resp, "model", "") or _MODEL,
                                  getattr(resp, "usage", None), "niche_vocab.infer")
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 — a failed read is reported, never guessed
        logger.warning("niche inference failed: %s", str(exc)[:200])
        return {"status": "failed", "labels": [], "error": str(exc)[:200]}
    return {"status": "ok",
            "labels": filter_labels(out.get("labels") if isinstance(out, dict) else None)}


async def infer_from_image(url) -> list[str]:
    """The vocabulary labels a post image is for: at most 3, [] when it carries
    nothing industry-specific, when there is no key, or when the read failed.
    Never raises — an upload must not fail because its niche could not be read."""
    try:
        return list((await infer_detail(url)).get("labels") or [])
    except Exception:  # noqa: BLE001
        logger.warning("niche inference raised", exc_info=True)
        return []


__all__ = ["VOCAB", "LABELS", "MAX_INFERRED", "canonical", "is_label",
           "infer_from_image", "infer_detail", "filter_labels", "est_usd_per_image",
           "has_key"]
