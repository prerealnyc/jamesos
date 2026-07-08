"""Deterministic demo fixtures for APP_ENV=mock (D8).

ALL DATA IN THIS MODULE IS SYNTHETIC. Names, quotes, outlets, metrics, URLs
(.example TLD), phone numbers (555 range) and street addresses are invented
for demo purposes and must never be surfaced as real facts. Everything is
anchored to ANCHOR (fixed) — no clocks, no randomness: identical inputs
always yield identical outputs.

Two brands: "turtleback" (physical_asset, verification brand) and
"james-prendamano" (person, demo brand) per D10. mocks.py fuzzy-matches on
the `aliases` lists; unknown queries fall back to GENERIC.

Conventions the agents depend on:
- search_results entries may carry "boost": [tokens] — mocks._rank_results
  floats them for matching angle queries so their URLs enter the researcher's
  citation-validation set.
- llm.field_extraction citations are URL STRINGS (researcher.PROMPT_EXTRACT
  shape) and every cited URL appears in at least one batch's material
  (scraped pages from entity_candidates[0].urls, news, or search results).
- llm.review_judge is {"score": float, "notes": str} (reviewer judge contract).
- wiki/channel/place/deep are the D11 research-lane fixtures. An explicit
  None is a DESIGNED gap (no Wikipedia page for Turtleback/James, no YouTube
  channel for Turtleback, no maps listing for a person) that downstream
  recommendations depend on — do not "fix" one by inventing data.
- a "channel" fixture is reachable only if its url also appears in the
  brand's search_results: the youtube lane LOCATES the channel via a
  site:youtube.com search (D11) before statting the located url.
- llm.lane_findings is PROMPT_EXTRACT-shaped; every citation URL appears in
  the corresponding lane fixture's output, so mocks' grounded filtering keeps
  exactly the fields whose lane material is present in the prompt.
"""

from datetime import datetime, timedelta, timezone

ANCHOR = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
ANCHOR_ISO = ANCHOR.isoformat()


def _iso(days_ago: int) -> str:
    return (ANCHOR - timedelta(days=days_ago)).replace(hour=16, minute=0).isoformat()


def build_posts(
    platform: str,
    handle: str,
    captions: list[str],
    followers: int,
    base_likes: int,
    like_spread: int,
    per_month: int = 3,
    months: int = 12,
    views_multiplier: int = 0,
) -> list[dict]:
    """Deterministic post history, newest first; metrics derived from index only."""
    posts: list[dict] = []
    total = months * per_month
    step = max(28 // max(per_month, 1), 1)
    i = 0
    for m in range(months):
        for k in range(per_month):
            likes = base_likes + (i * 37) % like_spread
            comments = 2 + (i * 11) % max(base_likes // 10, 6)
            shares = (i * 7) % max(base_likes // 20, 4)
            metrics: dict = {
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "engagement_rate": round((likes + comments + shares) / followers, 4),
            }
            if views_multiplier:
                metrics["views"] = likes * views_multiplier + (i * 131) % 900
            posts.append(
                {
                    "platform": platform,
                    "post_id": f"{platform}-{handle}-{total - i:03d}",
                    "url": f"https://{platform}.example/{handle}/p/{total - i:03d}",
                    "text": captions[i % len(captions)],
                    "published_at": _iso(m * 30 + k * step + 1),
                    "metrics": metrics,
                }
            )
            i += 1
    return posts


GENERIC_CAPTIONS = [
    "Behind the scenes from this week — more soon.",
    "Big things in the works. Stay tuned.",
    "Thanks to everyone who stopped by this weekend.",
    "New week, new goals. Let's get to work.",
    "Throwback to one of our favorite moments this season.",
    "Q&A: drop your questions below and we'll answer the top three.",
]


# =====================================================================
# Turtleback Golf Course — physical_asset (verification brand)
# =====================================================================

_TB_IG_CAPTIONS = [
    "Twilight tee times are open for the week — the back nine at golden hour is the best show in Sandoval County.",
    "Course update: greens rolling true after this morning's mow. Carts on path for 7-9 while the new turf settles in.",
    "Junior league Saturdays are back. Ages 7-15, loaner clubs available, zero pressure — just kids learning to love the game.",
    "Hole 16, 178 yards, wind out of the west. Club up and trust it.",
    "The Shell Grill's green-chile cheeseburger plus a large range bucket: the Turtleback Tuesday special.",
    "Monsoon season tip: morning rounds beat the 2pm buildup. First tee opens at 6:30.",
    "Members' scramble recap: Team Ocotillo takes it at 12-under. Full board in the clubhouse.",
    "Aeration week on the front nine — short-term bumps, long-term pure. Twilight rates discounted all week.",
    "Season passes are live: unlimited golf, range included, no initiation fee.",
    "Staff pick: the uphill par-5 12th plays a full club longer than the card says. You've been warned.",
    "Frost delay this morning — coffee's on us in the pro shop until the first group goes off.",
    "The Sandoval Cup charity scramble is filling fast. Four-person teams, dinner at the Shell Pavilion included.",
]

_TB_FB_CAPTIONS = [
    "Community night at the Shell Pavilion this Friday — live music, range games, and twilight nine-hole rates for anyone who walks in. Bring the family.",
    "Our junior league grew from 40 to 85 kids this year. Huge thanks to the volunteer coaches who make Saturday mornings happen.",
    "Water-wise turf update: the back nine renovation is complete. Same fairways, roughly 30% less water. High-desert golf done right.",
    "Weddings and events at the Shell Pavilion are booking into next spring. Message us for a walkthrough.",
    "League standings are posted in the clubhouse and on the website. Tight race at the top going into the final month.",
    "Reminder: tee times release 7 days out at 7am. Weekend twilight slots go fast in July.",
]

_TB_RESORT_CAPTIONS = [
    "Golden hour on the canyon nine. This is why you book the sunset round.",
    "Stay-and-play packages for the fall season are live — two nights, three rounds, caddie included.",
    "Course tour: the signature island-green 17th from the drone. Sound on.",
    "Our agronomy team walks you through what it takes to keep bentgrass alive in the high desert.",
    "Member-guest weekend recap: 96 players, one hole-in-one, zero bad views.",
    "The spa-and-back-nine day is the most underrated booking on the property. Trust us.",
]

_TB_MUNI_CAPTIONS = [
    "Weekend tee sheet is nearly full — book early, walk-ups can't be guaranteed.",
    "New rental fleet arrived this week. Carts now have GPS and USB charging.",
    "Men's club results posted. Congrats to the flight winners.",
    "Twilight special: 9 holes and a cart after 5pm, all summer.",
    "Course maintenance Monday morning — front nine opens at 11am.",
    "Footgolf Fridays are back on the short course. All ages welcome.",
]

TURTLEBACK: dict = {
    "slug": "turtleback",
    "name": "Turtleback Golf Course",
    "entity_type": "physical_asset",
    "aliases": ["turtleback", "turtlebackgolf", "shell pavilion"],
    "website": "https://www.turtlebackgolf.example/",
    "search_results": [
        {
            "title": "Turtleback Golf Course | High-Desert 18 in Rio Rancho, NM",
            "url": "https://www.turtlebackgolf.example/",
            "snippet": "18 holes, par 72, 6,850 yards on Rio Rancho's West Mesa with Sandia views. "
            "Twilight rates all summer, junior league, and the Shell Grill clubhouse.",
        },
        {
            "title": "Rates & Tee Times — Turtleback Golf Course",
            "url": "https://www.turtlebackgolf.example/rates",
            "snippet": "Weekday $54, weekend $68, twilight $39, junior $22. Annual passes from $1,850. "
            "Tee times release 7 days out.",
            "boost": ["rates", "products", "services", "fees", "tee"],
        },
        {
            "title": "Turtleback Golf Course review — honest high-desert golf",
            "url": "https://teesheetreviews.example/nm/turtleback-golf-course",
            "snippet": "4.4/5 from 212 reviews. Firm, fast, and windy in the afternoons; the back nine "
            "at twilight is the reason locals keep season passes.",
            "boost": ["reviews", "review", "rating"],
        },
        {
            "title": "Turtleback completes water-wise turf renovation on back nine",
            "url": "https://sandovalsignal.example/news/turtleback-turf-renovation",
            "snippet": "The Rio Rancho course cut irrigation roughly 30% after re-grassing the back "
            "nine with drought-tolerant turf, the operations director said.",
            "boost": ["news", "press"],
        },
        {
            "title": "Turtleback Golf Course (@turtlebackgolfnm) — Instagram",
            "url": "https://instagram.example/turtlebackgolfnm",
            "snippet": "High-desert golf in Rio Rancho, NM. 18 holes, par 72. Twilight rates all summer.",
            "boost": ["instagram", "social"],
        },
        {
            "title": "About — Turtleback Golf Course",
            "url": "https://www.turtlebackgolf.example/about",
            "snippet": "Built in 1998 on the West Mesa; 62 bunkers rebuilt in 2024; 2026 water-wise "
            "turf program. Locally owned, 34 seasonal staff, PGA teaching professional.",
            "boost": ["about", "history"],
        },
        {
            "title": "Events at the Shell Pavilion — Turtleback Golf Course",
            "url": "https://www.turtlebackgolf.example/events",
            "snippet": "Twilight Series scrambles, the Sandoval Cup, weddings and corporate outings "
            "for up to 160 guests. Catering services by the Shell Grill.",
            "boost": ["events", "products", "services", "weddings"],
        },
        {
            "title": "Turtleback Golf Course — Facebook",
            "url": "https://facebook.example/turtlebackgolfnm",
            "snippet": "Rio Rancho's high-desert public 18. Tee times, Shell Pavilion events, junior golf.",
            "boost": ["facebook", "social", "about"],
        },
        {
            "title": "Cabezon Links (@cabezonlinks) — Instagram",
            "url": "https://instagram.example/cabezonlinks",
            "snippet": "Rio Rancho's neighborhood links — walkable 18 a few miles from Turtleback; "
            "the two courses run rival league nights. [Synthetic mock result]",
            "boost": ["competitors", "competitor", "courses"],
        },
        {
            "title": "High Desert Pines Golf Club (@highdesertpinesgc) — Instagram",
            "url": "https://instagram.example/highdesertpinesgc",
            "snippet": "Albuquerque westside public golf, 27 holes and a lighted range — the other "
            "value option for Rio Rancho golfers. [Synthetic mock result]",
            "boost": ["competitors", "competitor", "courses"],
        },
        {
            "title": "Mesa Grande Golf Resort (@mesagrandegolfresort) — Instagram",
            "url": "https://instagram.example/mesagrandegolfresort",
            "snippet": "Destination 36-hole high-desert resort: stay-and-play, spa, sunset rounds. "
            "Benchmark account for high-desert course content. [Synthetic mock result]",
            "boost": ["competitors", "competitor", "resort"],
        },
        {
            "title": "Red Rock Canyon Resort & Golf (@redrockcanyonresort) — Instagram",
            "url": "https://instagram.example/redrockcanyonresort",
            "snippet": "Canyon-rim golf and resort living; weekly drone tours. The production tier "
            "regional courses study. [Synthetic mock result]",
            "boost": ["competitors", "competitor", "resort"],
        },
        {
            "title": "r/GolfNM — 'Most underrated value courses around ABQ?' (thread)",
            "url": "https://reddit.example/r/golfnm/comments/underrated-abq-munis",
            "snippet": "Multiple commenters call Turtleback's $39 twilight rate the best golf "
            "value in Sandoval County; the recurring gripe is afternoon wind, not conditions. "
            "[Synthetic mock result]",
            "boost": ["reddit", "site", "community", "forum"],
        },
    ],
    "pages": {
        "https://www.turtlebackgolf.example/": {
            "title": "Turtleback Golf Course | High-Desert 18 in Rio Rancho, NM",
            "text": (
                "Turtleback Golf Course is Rio Rancho's high-desert public 18: par 72, 6,850 yards "
                "laid across the West Mesa with views of the Sandia Mountains from eleven holes. "
                "Opened in 1998 and re-bunkered in 2024, the course plays firm and fast, with "
                "afternoon west winds that make club selection half the game. Amenities include a "
                "grass-tee driving range, short-game area, the Shell Grill restaurant, and the Shell "
                "Pavilion event space for tournaments and weddings. Twilight rates run all summer "
                "and the junior league hosts kids ages 7-15 on Saturday mornings. Book tee times "
                "online or call the pro shop at (505) 555-0147. 4200 Mesa Vista Loop NE, Rio "
                "Rancho, NM. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "homepage"},
        },
        "https://www.turtlebackgolf.example/rates": {
            "title": "Rates & Tee Times — Turtleback Golf Course",
            "text": (
                "Green fees: weekday $54, weekend $68, twilight (after 4pm) $39, junior $22, "
                "nine-hole $32. All rates include cart; walking permitted anytime. Annual season "
                "pass $1,850 with unlimited golf and range balls; no initiation fee. Tee times "
                "release seven days out at 7am. League and tournament blocks available Tuesday and "
                "Thursday mornings. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "rates"},
        },
        "https://www.turtlebackgolf.example/events": {
            "title": "Events — Turtleback Golf Course",
            "text": (
                "Signature events: the Turtleback Twilight Series (nine-hole scrambles, Friday "
                "evenings June-August), the Sandoval Cup charity scramble each September benefiting "
                "local junior golf, and the Members' Scramble the first Saturday of every month. "
                "The Shell Pavilion hosts weddings, banquets, and corporate outings for up to 160 "
                "guests with catering from the Shell Grill. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "events"},
        },
        "https://www.turtlebackgolf.example/about": {
            "title": "About — Turtleback Golf Course",
            "text": (
                "Built in 1998 on Rio Rancho's West Mesa, Turtleback takes its name from the "
                "turtle-shell ridgeline visible from the 5th tee. A 2024 renovation rebuilt all 62 "
                "bunkers, and a 2026 water-wise turf program re-grassed the back nine to cut "
                "irrigation roughly 30% — high-desert golf that respects high-desert water. The "
                "course is locally owned and employs 34 people in season, including a PGA teaching "
                "professional and a junior-golf program that doubled in 2026. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "about"},
        },
    },
    "news": [
        {
            "title": "Turtleback Golf Course completes water-wise turf renovation on back nine",
            "url": "https://sandovalsignal.example/news/turtleback-turf-renovation",
            "published_at": _iso(6),
            "source": "Sandoval Signal",
            "snippet": "The Rio Rancho course re-grassed its back nine with drought-tolerant turf, "
            "cutting irrigation roughly 30% while keeping fairways championship-firm.",
        },
        {
            "title": "Junior golf league doubles enrollment at Rio Rancho's Turtleback",
            "url": "https://rioranchledger.example/sports/turtleback-junior-league-2026",
            "published_at": _iso(66),
            "source": "Rio Rancho Ledger",
            "snippet": "Saturday-morning junior league enrollment grew from 40 to 85 kids this "
            "season, prompting the course to add a second coaching block.",
        },
    ],
    "accounts": {
        "instagram": {
            "platform": "instagram",
            "handle": "turtlebackgolfnm",
            "display_name": "Turtleback Golf Course",
            "followers": 3420,
            "bio": "High-desert golf in Rio Rancho, NM. 18 holes, par 72. Twilight rates all summer. Book online.",
            "meta": {"synthetic": True, "kind": "own", "cadence_per_week": 1.4},
        },
        "facebook": {
            "platform": "facebook",
            "handle": "turtlebackgolfnm",
            "display_name": "Turtleback Golf Course",
            "followers": 4870,
            "bio": "Rio Rancho's high-desert public 18. Tee times, events at the Shell Pavilion, junior golf.",
            "meta": {"synthetic": True, "kind": "own", "cadence_per_week": 0.9},
        },
    },
    "analytics": {
        "instagram": {
            "followers": 3420,
            "following": 310,
            "posts_last_30d": 6,
            "avg_engagement_rate": 0.041,
            "impressions_last_90d": 88400,
            "reach_last_90d": 61200,
            "profile_views_last_90d": 2140,
            "demographics": {
                "top_locations": ["Rio Rancho, NM", "Albuquerque, NM", "Bernalillo, NM"],
                "age_ranges": {"25-34": 0.24, "35-44": 0.31, "45-54": 0.22, "55+": 0.23},
                "gender": {"female": 0.38, "male": 0.62},
            },
            "lookback_months": 12,
            "synthetic": True,
        },
        "facebook": {
            "followers": 4870,
            "posts_last_30d": 4,
            "avg_engagement_rate": 0.028,
            "impressions_last_90d": 64100,
            "reach_last_90d": 47800,
            "demographics": {
                "top_locations": ["Rio Rancho, NM", "Albuquerque, NM", "Corrales, NM"],
                "age_ranges": {"25-34": 0.14, "35-44": 0.26, "45-54": 0.28, "55+": 0.32},
                "gender": {"female": 0.44, "male": 0.56},
            },
            "lookback_months": 12,
            "synthetic": True,
        },
    },
    "posts": {
        "instagram": build_posts(
            "instagram", "turtlebackgolfnm", _TB_IG_CAPTIONS,
            followers=3420, base_likes=110, like_spread=140, per_month=6, views_multiplier=0,
        ),
        "facebook": build_posts(
            "facebook", "turtlebackgolfnm", _TB_FB_CAPTIONS,
            followers=4870, base_likes=60, like_spread=90, per_month=4, views_multiplier=0,
        ),
    },
    "peers": {
        "cabezonlinks": {
            "platform": "instagram",
            "handle": "cabezonlinks",
            "display_name": "Cabezon Links",
            "followers": 5100,
            "bio": "Rio Rancho's neighborhood links. Walkable 18, footgolf Fridays, twilight nine.",
            "meta": {"synthetic": True, "kind": "direct", "cadence_per_week": 3.2, "avg_engagement_rate": 0.037},
        },
        "highdesertpinesgc": {
            "platform": "instagram",
            "handle": "highdesertpinesgc",
            "display_name": "High Desert Pines Golf Club",
            "followers": 4300,
            "bio": "Albuquerque westside public golf. 27 holes, lighted range, league central.",
            "meta": {"synthetic": True, "kind": "direct", "cadence_per_week": 2.8, "avg_engagement_rate": 0.033},
        },
        "mesagrandegolfresort": {
            "platform": "instagram",
            "handle": "mesagrandegolfresort",
            "display_name": "Mesa Grande Golf Resort",
            "followers": 68000,
            "bio": "Destination golf in the high desert. 36 holes, stay-and-play, spa, sunset rounds.",
            "meta": {"synthetic": True, "kind": "aspirational", "cadence_per_week": 5.5, "avg_engagement_rate": 0.048},
        },
        "redrockcanyonresort": {
            "platform": "instagram",
            "handle": "redrockcanyonresort",
            "display_name": "Red Rock Canyon Resort & Golf",
            "followers": 112000,
            "bio": "Canyon-rim golf and resort living. Drone tours weekly. Book the sunset round.",
            "meta": {"synthetic": True, "kind": "aspirational", "cadence_per_week": 6.1, "avg_engagement_rate": 0.052},
        },
    },
    "peer_posts": {
        "cabezonlinks": build_posts(
            "instagram", "cabezonlinks", _TB_MUNI_CAPTIONS,
            followers=5100, base_likes=140, like_spread=120, per_month=3, months=4,
        ),
        "highdesertpinesgc": build_posts(
            "instagram", "highdesertpinesgc", _TB_MUNI_CAPTIONS,
            followers=4300, base_likes=110, like_spread=100, per_month=3, months=4,
        ),
        "mesagrandegolfresort": build_posts(
            "instagram", "mesagrandegolfresort", _TB_RESORT_CAPTIONS,
            followers=68000, base_likes=2600, like_spread=1800, per_month=5, months=4, views_multiplier=14,
        ),
        "redrockcanyonresort": build_posts(
            "instagram", "redrockcanyonresort", _TB_RESORT_CAPTIONS,
            followers=112000, base_likes=4400, like_spread=3200, per_month=6, months=4, views_multiplier=16,
        ),
    },
    "transcript": (
        "[Synthetic demo transcript] Groundskeeper interview, Turtleback back-nine renovation: "
        "'People hear water-wise and think brown. What we did is the opposite — the new turf "
        "holds color at two-thirds the water because the root structure goes twice as deep. "
        "The back nine is actually firmer and faster now. Come out at twilight in July, that's "
        "when this place shows off. The junior kids on Saturday mornings, that's the future of "
        "the course, full stop.'"
    ),
    # ---- D11 research-lane fixtures ----
    # BY DESIGN: no Wikipedia page — the missing-page finding
    # (positioning.wikipedia_presence=false) drives the Strategist's
    # 'create a Wikipedia page' recommendation. Do not add a page.
    "wiki": None,
    # BY DESIGN: no YouTube channel — an honest gap the Strategist can
    # surface; do not invent a channel.
    "channel": None,
    "place": {
        "name": "Turtleback Golf Course",
        "address": "4200 Mesa Vista Loop NE, Rio Rancho, NM 87144",
        "rating": 4.6,
        "reviews_count": 284,
        "categories": ["Golf course", "Event venue"],
        "website": "https://www.turtlebackgolf.example/",
        "url": "https://maps.example/place/turtleback-golf-course",
    },
    "deep": {
        "synthesis": "Turtleback Golf Course reads consistently across sources as Rio "
        "Rancho's value-forward high-desert public 18, with twilight rounds and firm, "
        "fast conditions doing the reputational work. Recent coverage centers on the "
        "2026 water-wise turf renovation that cut back-nine irrigation roughly 30% and "
        "a junior league that grew from 40 to 85 kids. The through-line is a locally "
        "owned course converting operational stewardship into community loyalty.",
        "citations": [
            "https://www.turtlebackgolf.example/about",
            "https://sandovalsignal.example/news/turtleback-turf-renovation",
            "https://rioranchledger.example/sports/turtleback-junior-league-2026",
        ],
    },
    "llm": {
        "entity_candidates": {
            "candidates": [
                {
                    "name": "Turtleback Golf Course (Rio Rancho, NM)",
                    "description": "High-desert public 18-hole course on Rio Rancho's West Mesa; "
                    "twilight rates, junior league, Shell Pavilion events venue.",
                    "urls": [
                        "https://www.turtlebackgolf.example/",
                        "https://www.turtlebackgolf.example/rates",
                        "https://www.turtlebackgolf.example/events",
                        "https://www.turtlebackgolf.example/about",
                        "https://instagram.example/turtlebackgolfnm",
                    ],
                    "score": 0.92,
                },
                {
                    "name": "Turtleback Ridge Golf Club (Prescott, AZ)",
                    "description": "Semi-private mountain course in central Arizona; similar name, different state and ownership.",
                    "urls": ["https://turtlebackridge.example/"],
                    "score": 0.31,
                },
                {
                    "name": "Turtle Back Family Mini Golf (NJ)",
                    "description": "Miniature golf attraction; name collision only.",
                    "urls": ["https://turtlebackminigolf.example/"],
                    "score": 0.08,
                },
            ]
        },
        # researcher.PROMPT_EXTRACT shape: citations are URL strings present in
        # the batch material; the agent builds FieldWrite rows itself and the
        # profile service assigns source/confidence (D2).
        "field_extraction": {
            "fields": [
                {
                    "section": "identity",
                    "field_key": "identity.display_name",
                    "item_key": None,
                    "value": "Turtleback Golf Course",
                    "citations": ["https://www.turtlebackgolf.example/"],
                },
                {
                    "section": "identity",
                    "field_key": "identity.positioning",
                    "item_key": None,
                    "value": "Rio Rancho's high-desert public 18 — championship-length golf without the resort price.",
                    "citations": [
                        "https://www.turtlebackgolf.example/",
                        "https://teesheetreviews.example/nm/turtleback-golf-course",
                    ],
                },
                {
                    "section": "identity",
                    "field_key": "identity.locations",
                    "item_key": None,
                    "value": ["Rio Rancho, New Mexico (West Mesa)"],
                    "citations": ["https://www.turtlebackgolf.example/"],
                },
                {
                    "section": "identity",
                    "field_key": "identity.story",
                    "item_key": None,
                    "value": "Opened 1998 on the West Mesa; re-bunkered 2024; 2026 water-wise turf "
                    "program cut back-nine irrigation ~30%. Locally owned, 34 seasonal staff.",
                    "citations": [
                        "https://www.turtlebackgolf.example/about",
                        "https://sandovalsignal.example/news/turtleback-turf-renovation",
                    ],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "green-fees",
                    "value": {"name": "Green fees", "detail": "weekday $54 / weekend $68 / twilight $39 / junior $22"},
                    "citations": ["https://www.turtlebackgolf.example/rates"],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "season-pass",
                    "value": {"name": "Annual season pass", "detail": "$1,850, unlimited golf + range, no initiation"},
                    "citations": ["https://www.turtlebackgolf.example/rates"],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "events-venue",
                    "value": {"name": "Shell Pavilion events", "detail": "weddings/banquets/outings up to 160 guests"},
                    "citations": ["https://www.turtlebackgolf.example/events"],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "junior-league",
                    "value": {"name": "Junior league", "detail": "ages 7-15, Saturday mornings, 85 enrolled 2026"},
                    "citations": [
                        "https://www.turtlebackgolf.example/",
                        "https://rioranchledger.example/sports/turtleback-junior-league-2026",
                    ],
                },
                {
                    "section": "channels",
                    "field_key": "channels.website",
                    "item_key": None,
                    "value": "https://www.turtlebackgolf.example/",
                    "citations": ["https://www.turtlebackgolf.example/"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.instagram",
                    "item_key": None,
                    "value": {"platform": "instagram", "handle": "turtlebackgolfnm", "url": "https://instagram.example/turtlebackgolfnm"},
                    "citations": ["https://instagram.example/turtlebackgolfnm"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.facebook",
                    "item_key": None,
                    "value": {"platform": "facebook", "handle": "turtlebackgolfnm", "url": "https://facebook.example/turtlebackgolfnm"},
                    "citations": ["https://facebook.example/turtlebackgolfnm"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "cabezonlinks",
                    "value": {"name": "Cabezon Links", "platform": "instagram", "handle": "cabezonlinks", "kind": "direct"},
                    "citations": ["https://instagram.example/cabezonlinks"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "highdesertpinesgc",
                    "value": {"name": "High Desert Pines Golf Club", "platform": "instagram", "handle": "highdesertpinesgc", "kind": "direct"},
                    "citations": ["https://instagram.example/highdesertpinesgc"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "mesagrandegolfresort",
                    "value": {"name": "Mesa Grande Golf Resort", "platform": "instagram", "handle": "mesagrandegolfresort", "kind": "aspirational"},
                    "citations": ["https://instagram.example/mesagrandegolfresort"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "redrockcanyonresort",
                    "value": {"name": "Red Rock Canyon Resort & Golf", "platform": "instagram", "handle": "redrockcanyonresort", "kind": "aspirational"},
                    "citations": ["https://instagram.example/redrockcanyonresort"],
                },
            ],
            "failures": [],
        },
        # D11 lane-extraction payload (PROMPT_EXTRACT shape). mocks filters
        # these to fields whose citations appear in the prompt material, so
        # each lane surfaces only its own findings. wikipedia_presence=false
        # is NOT here — a missing page has no citable material, so the lane
        # code writes that finding directly (D11).
        "lane_findings": {
            "fields": [
                {
                    "section": "identity",
                    "field_key": "identity.address",
                    "item_key": None,
                    "value": "4200 Mesa Vista Loop NE, Rio Rancho, NM 87144",
                    "citations": [
                        "https://maps.example/place/turtleback-golf-course",
                        "https://www.turtlebackgolf.example/",
                    ],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.local_reputation",
                    "item_key": None,
                    "value": {"rating": 4.6, "reviews_count": 284, "source": "maps listing"},
                    "citations": ["https://maps.example/place/turtleback-golf-course"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.community_sentiment",
                    "item_key": None,
                    "value": "Named the best golf value in Sandoval County in r/GolfNM threads; "
                    "the recurring complaint is afternoon wind, not conditions.",
                    "citations": ["https://reddit.example/r/golfnm/comments/underrated-abq-munis"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.research_synthesis",
                    "item_key": None,
                    "value": "Locally owned high-desert public 18 whose reputation rides twilight "
                    "value and operational stewardship — the 2026 water-wise turf renovation and "
                    "a junior league that doubled are the proof points sources keep repeating.",
                    "citations": [
                        "https://sandovalsignal.example/news/turtleback-turf-renovation",
                        "https://rioranchledger.example/sports/turtleback-junior-league-2026",
                    ],
                },
            ],
            "failures": [],
        },
        "weekly_plan": {
            "brand_id": "",
            "period_start": "2026-07-06T00:00:00+00:00",
            "period_end": "2026-07-12T23:59:59+00:00",
            "rationale": "Tracked peers out-post Turtleback 5.8 to 1.4 posts/week and their reels "
            "carry the gap. July twilight demand is the seasonal peak and the turf-renovation press "
            "item is fresh — lead with course-condition proof and twilight CTAs; push junior league "
            "on Facebook where the family audience skews.",
            "goals_snapshot": {
                "instagram_followers_target": 5000,
                "avg_engagement_target": 0.05,
                "online_bookings_lift_90d": 0.15,
            },
            "items": [
                {
                    "content_type": "reel",
                    "platform": "instagram",
                    "topic": "Golden-hour flyover of the renovated back nine + twilight rate CTA",
                    "count": 2,
                    "format_spec": {"length_sec": 22, "aspect": "9:16", "hook": "The best show in Sandoval County starts at 7:40pm"},
                    "rationale": "Reels are the top format for both aspirational resort accounts this "
                    "month; the renovation news gives a fresh, provable angle.",
                    "evidence": [
                        {"url": "https://instagram.example/redrockcanyonresort", "ref": "", "note": "drone-tour reels avg 5.2% ER"},
                        {"url": "https://sandovalsignal.example/news/turtleback-turf-renovation", "ref": "", "note": "turf renovation press"},
                    ],
                    "predicted_metrics": {"views": 4200, "likes": 190, "comments": 11, "engagement_rate": 0.055},
                },
                {
                    "content_type": "image",
                    "platform": "instagram",
                    "topic": "Course-condition proof shots + Shell Grill Tuesday special",
                    "count": 3,
                    "format_spec": {"aspect": "4:5", "carousel": True},
                    "rationale": "Condition posts are Turtleback's own top-performing static category over 12 months.",
                    "evidence": [
                        {"url": "https://instagram.example/turtlebackgolfnm", "ref": "", "note": "own post history: condition posts +38% vs account avg"}
                    ],
                    "predicted_metrics": {"likes": 150, "comments": 8, "engagement_rate": 0.046},
                },
                {
                    "content_type": "text_post",
                    "platform": "facebook",
                    "topic": "Junior league enrollment push — 85 kids and growing",
                    "count": 2,
                    "format_spec": {"cta": "sign-up link", "tone": "community"},
                    "rationale": "Facebook audience skews 45+, parent/grandparent heavy; junior-league "
                    "press item validates the angle.",
                    "evidence": [
                        {"url": "https://rioranchledger.example/sports/turtleback-junior-league-2026", "ref": "", "note": "enrollment doubled"}
                    ],
                    "predicted_metrics": {"reach": 1900, "comments": 14, "shares": 6},
                },
                {
                    "content_type": "image",
                    "platform": "facebook",
                    "topic": "Sandoval Cup charity scramble — save the date",
                    "count": 1,
                    "format_spec": {"aspect": "1:1"},
                    "rationale": "Event posts drive the highest share rate on the Facebook page.",
                    "evidence": [
                        {"url": "https://www.turtlebackgolf.example/events", "ref": "", "note": "signature event"}
                    ],
                    "predicted_metrics": {"reach": 2300, "shares": 12, "comments": 9},
                },
            ],
        },
        "morning_brief": {
            "brand_id": "",
            "date": ANCHOR_ISO,
            "headline": "Twilight demand is your opening this week — and the turf story is still warm",
            "sections": [
                {
                    "title": "Press",
                    "body": "The Sandoval Signal turf-renovation piece is six days old and unclaimed "
                    "on your own channels — one reel and one static can convert it into course-condition proof.",
                    "citations": ["https://sandovalsignal.example/news/turtleback-turf-renovation"],
                },
                {
                    "title": "Peers",
                    "body": "Red Rock Canyon's weekly drone-tour reel averaged 5.2% engagement over "
                    "the last month; Mesa Grande's sunset-round posts run 4.8%. Both formats are "
                    "replicable at your scale with a phone gimbal.",
                    "citations": [
                        "https://instagram.example/redrockcanyonresort",
                        "https://instagram.example/mesagrandegolfresort",
                    ],
                },
                {
                    "title": "Own baseline",
                    "body": "IG engagement holds at 4.1% on 1.4 posts/week — the constraint is volume, "
                    "not resonance. Direct competitors post 3x/week at lower engagement.",
                    "citations": ["https://instagram.example/turtlebackgolfnm"],
                },
            ],
            "recommended_actions": [
                "Film the back-nine flyover Thursday at 7:30pm (golden hour) for the twilight reel.",
                "Schedule the junior-league Facebook push for Saturday 8am, before coaching block.",
                "Add the turf-renovation link to the IG bio while the story is current.",
            ],
        },
        "peer_digest": {
            "brand_id": "",
            "period": "2026-06-29/2026-07-05",
            "peers": [
                {"handle": "cabezonlinks", "kind": "direct", "platform": "instagram", "followers": 5100,
                 "cadence_per_week": 3.2, "avg_engagement_rate": 0.037, "top_topics": ["tee sheet", "leagues", "footgolf"]},
                {"handle": "highdesertpinesgc", "kind": "direct", "platform": "instagram", "followers": 4300,
                 "cadence_per_week": 2.8, "avg_engagement_rate": 0.033, "top_topics": ["leagues", "range", "maintenance"]},
                {"handle": "mesagrandegolfresort", "kind": "aspirational", "platform": "instagram", "followers": 68000,
                 "cadence_per_week": 5.5, "avg_engagement_rate": 0.048, "top_topics": ["sunset rounds", "stay-and-play", "agronomy"]},
                {"handle": "redrockcanyonresort", "kind": "aspirational", "platform": "instagram", "followers": 112000,
                 "cadence_per_week": 6.1, "avg_engagement_rate": 0.052, "top_topics": ["drone tours", "member-guest", "signature holes"]},
            ],
            "benchmarks": {
                "cadence_per_week": 4.4,
                "avg_engagement_rate": 0.043,
                "formats": {"reel": 0.48, "image": 0.42, "text": 0.10},
            },
            "observations": [
                "Both aspirational accounts led the week with golden-hour video; Turtleback posted "
                "zero video [instagram.example/redrockcanyonresort].",
                "Cabezon Links' twilight-nine promo outperformed its account average by 60% — the "
                "same offer Turtleback runs but hasn't promoted this month [instagram.example/cabezonlinks].",
                "Direct competitors post 2-3x Turtleback's cadence at lower engagement; volume, not "
                "quality, is the gap [instagram.example/turtlebackgolfnm].",
            ],
        },
        "goals": {
            "goals": [
                {"section": "goals", "platform": "instagram", "metric": "followers", "baseline": 3420,
                 "target": 5000, "timeframe_days": 90, "priority": 0.35,
                 "rationale": "Direct competitors sit at 4.3-5.1k; parity is a 90-day cadence problem, not a content problem."},
                {"section": "goals", "platform": "instagram", "metric": "avg_engagement_rate", "baseline": 0.041,
                 "target": 0.05, "timeframe_days": 90, "priority": 0.25,
                 "rationale": "Aspirational benchmark is 4.8-5.2%; reels close the gap."},
                {"section": "goals", "platform": "facebook", "metric": "reach_per_post", "baseline": 1500,
                 "target": 2500, "timeframe_days": 90, "priority": 0.15,
                 "rationale": "Community/event posts already outperform; raise the floor with 2/week."},
                {"section": "goals", "platform": None, "metric": "online_tee_time_bookings", "baseline": 100,
                 "target": 115, "timeframe_days": 90, "priority": 0.25,
                 "rationale": "Business goal: +15% online bookings, indexed to 100 = current weekly average."},
            ],
            "narrative": "Accounts at your scale that reached 5k did it at roughly 4 posts/week over "
            "one season. Committing to 2 reels + 3 statics weekly puts 5k inside 90 days at your "
            "current 4.1% engagement.",
        },
        "press_events": {
            "events": [
                {
                    "title": "Water-wise turf renovation completed on back nine",
                    "kind": "credibility_signal",
                    "content_worthy": True,
                    "summary": "Local press credits Turtleback with a 30% irrigation cut — a "
                    "sustainability proof point rare among public courses.",
                    "url": "https://sandovalsignal.example/news/turtleback-turf-renovation",
                    "suggested_angle": "Course-condition reel citing the article; add to About page proof points.",
                },
                {
                    "title": "Junior league enrollment doubles",
                    "kind": "credibility_signal",
                    "content_worthy": True,
                    "summary": "Family-audience story with strong local goodwill; ideal for Facebook.",
                    "url": "https://rioranchledger.example/sports/turtleback-junior-league-2026",
                    "suggested_angle": "Coach spotlight + sign-up CTA on Facebook.",
                },
            ]
        },
        # reviewer judge contract: {"score": float 0-1, "notes": str}
        "review_judge": {
            "score": 0.81,
            "notes": "Reads like the course's own dry, local-proud captions — concrete detail "
            "(hole numbers, times, prices) and no marketing filler. Twilight CTA matches the "
            "register of top exemplars.",
        },
        "draft_post": "The back nine just before eight is the best-kept secret in Sandoval County. "
        "New turf, same sunset, $39 after 4pm. Book the Thursday twilight slot and thank us later.",
        "draft_templates": (
            "{topic}. The crew has it dialed in and the July light is doing us favors. Tee times "
            "release seven days out at 7am; twilight after 4pm is $39. See you out there.",
            "{topic}. Short version: worth the trip. Long version is out on the course this week, "
            "and the pro shop can fill in the rest at (505) 555-0147.",
            "{topic}. No frills, just firm fairways and honest golf on the West Mesa. Book online "
            "or walk in; the Shell Grill will be open when you finish.",
        ),
        "revise_templates": (
            "{topic}. Take two, plainer this time: the course is ready, the light is right, and "
            "the tee sheet is open. Book it before the weekend crowd does.",
            "{topic}. Same story, fewer words: play it at twilight this week. $39 after 4pm, "
            "carts included.",
        ),
        "brief_text": "Morning brief, Turtleback: the turf-renovation story is still warm and "
        "unclaimed on your channels; peers won the week with golden-hour video; your engagement is "
        "fine, your volume isn't. Two reels and three statics this week — start with the back-nine "
        "flyover Thursday at 7:30pm.",
        "plan_narrative": "This week: 2 reels on the renovated back nine at golden hour, 3 statics "
        "on course conditions and the Shell Grill special, 2 Facebook posts on junior league, 1 "
        "Sandoval Cup save-the-date — because peers out-post you 4:1 and their video formats carry "
        "5%+ engagement while twilight demand peaks in July.",
    },
}


# =====================================================================
# James Prendamano — person (demo brand)
# =====================================================================

_JP_IG_CAPTIONS = [
    "Walked a 'dead' strip mall with a client today. Vacancy isn't a verdict — it's a mispriced option.",
    "The deal you don't do is still a decision. Underwrite your no's as carefully as your yes's.",
    "Everyone wants the waterfront rendering. Nobody wants the 14 months of zoning meetings behind it. That's the moat.",
    "Rates didn't kill your deal. Your assumptions did.",
    "Morning with the academy cohort: 40 people underwriting the same building, 40 different answers. The spread is the lesson.",
    "Term sheets are autobiographies. Read what the other side is telling you about themselves.",
    "Mindset Monday: the market doesn't owe you a cycle that matches your timeline.",
    "The north shore is still the most mispriced waterfront in the five boroughs. Still.",
    "A board seat at a spaceport and a golf course in New Mexico — the throughline is land, patience, and infrastructure.",
    "If your broker can't explain the downside in one sentence, they haven't found it yet.",
    "We teach kids compound interest on one worksheet and wonder why they rent their whole lives. Fix the curriculum.",
    "Deal autopsy on the show this week: the retail condo we passed on in 2024 — and what it traded for last month.",
]

_JP_LI_CAPTIONS = [
    "Three lessons from a waterfront assemblage that took four years longer than the pro forma said it would. Thread below.",
    "Hiring take: the best junior analysts I've worked with all kept a 'deals we passed on' journal. Institutional memory beats instinct.",
    "Commercial vacancy on the north shore fell for the third straight quarter. The narrative is two years behind the data.",
    "What a spaceport board seat teaches a real-estate operator: infrastructure timelines make zoning look fast.",
    "The academy's 400th student enrolled this week. The gap we fill isn't information — it's underwriting reps.",
    "Retail isn't dead. Lazy retail is dead. The operators winning right now are programming their spaces like media.",
    "Term limits, school curricula, land use — the boring civic machinery is where compounding actually happens.",
    "Podcast studios are the new storefronts: authority compounds faster than foot traffic.",
]

_JP_YT_TITLES = [
    "Ep. 142 — Why 'dead malls' are the best seller-financing lab in America",
    "Ep. 141 — Spaceport America: what commercial space taught me about land banking",
    "Ep. 140 — Underwriting a Staten Island waterfront assemblage, live",
    "Ep. 139 — Academy Q&A: first deals, worst deals, and the no's that saved us",
    "Ep. 138 — The retail condo we passed on in 2024: full deal autopsy",
    "Ep. 137 — Mindset for operators: patience as an underwriting input",
    "Ep. 136 — Education reform and the compound-interest worksheet problem",
    "Ep. 135 — North shore vacancy: the narrative is two years behind the data",
]

_JP_PEER_CAPTIONS = [
    "Deal autopsy: the mixed-use we lost at auction — and why the winner overpaid.",
    "Broker economics 101: your split is not your take-home. Full breakdown.",
    "Market take: the 'wait for rates' crowd has now missed two entry windows.",
    "Live underwriting session from this morning's cohort call. Numbers on screen.",
    "The listing that sat 300 days — what changed in week 43. Watch to the end.",
    "Cashflow breakdown: fourplex vs. small retail strip, same price, wildly different risk.",
]

JAMES: dict = {
    "slug": "james-prendamano",
    "name": "James Prendamano",
    "entity_type": "person",
    # NOTE: no space-industry aliases here — "Spaceport America" is its own
    # fixture (an institution test brand), and James merely sits on its board.
    "aliases": [
        "prendamano",
        "jamesprendamano",
        "prereal",
        "james prendamano show",
        "academy cohort",
        "underwrite",
    ],
    "website": "https://www.prereal.example/",
    "search_results": [
        {
            "title": "James Prendamano — CEO, PreReal | Prendamano Real Estate",
            "url": "https://www.prereal.example/",
            "snippet": "Real-estate operator and investor with 20+ years across brokerage, "
            "development, and advisory on Staten Island's north shore. Host of The James "
            "Prendamano Show. Spaceport America board member.",
        },
        {
            "title": "Spaceport America adds real-estate executive James Prendamano to board",
            "url": "https://spacecommercewire.example/news/spaceport-board-prendamano",
            "snippet": "The commercial spaceport named the New York real-estate executive to its "
            "board of directors, citing his land-use and infrastructure development background.",
            "boost": ["news", "press", "board"],
        },
        {
            "title": "The James Prendamano Show — YouTube",
            "url": "https://youtube.example/@jamesprendamanoshow",
            "snippet": "Weekly deal autopsies, live underwriting, and operator interviews. "
            "'The market doesn't owe you a cycle that matches your timeline.'",
            "boost": ["youtube", "show", "podcast"],
        },
        {
            "title": "'Retail isn't dead, lazy retail is' — Prendamano on Brick & Risk",
            "url": "https://brickandrisk.example/podcast/ep-88-prendamano",
            "snippet": "Podcast appearance covering north-shore waterfront repricing, seller "
            "financing in dead malls, and why authority compounds faster than foot traffic.",
            "boost": ["reviews", "podcast", "press"],
        },
        {
            "title": "James Prendamano (@jamesprendamano) — Instagram",
            "url": "https://instagram.example/jamesprendamano",
            "snippet": "Real estate operator. Deal autopsies, mindset, the occasional spaceport. "
            "Academy + show links below.",
            "boost": ["instagram", "social"],
        },
        {
            "title": "PreReal Investor Academy tops 400 students",
            "url": "https://harborledger.example/business/prereal-academy-400",
            "snippet": "The underwriting-focused course crossed 400 enrolled students; cohort "
            "sessions run live with deal walk-throughs.",
            "boost": ["products", "services", "academy"],
        },
        {
            "title": "James Prendamano — CEO, PreReal | LinkedIn",
            "url": "https://linkedin.example/in/jamesprendamano",
            "snippet": "20+ years in brokerage, development and advisory; Board of Directors, "
            "Spaceport America; host of The James Prendamano Show. Posts on deals, hiring, and "
            "civic reform.",
            "boost": ["about", "linkedin"],
        },
        {
            "title": "Marcus Bell | The Broker Breakdown (@thebrokerbreakdown) — Instagram",
            "url": "https://instagram.example/thebrokerbreakdown",
            "snippet": "Daily deal autopsies and broker economics; 148k followers. Frequently "
            "listed among real-estate authority accounts. [Synthetic mock result]",
            "boost": ["competitors", "competitor", "peers"],
        },
        {
            "title": "Alyssa Grant — Deal Flow Daily (@dealflowdaily) — Instagram",
            "url": "https://instagram.example/dealflowdaily",
            "snippet": "One underwritten deal per day, numbers on screen; 96k followers. "
            "[Synthetic mock result]",
            "boost": ["competitors", "competitor", "peers"],
        },
        {
            "title": "Tomás Rivera | Cashflow Chronicles (@cashflowchronicles) — Instagram",
            "url": "https://instagram.example/cashflowchronicles",
            "snippet": "Small commercial, big lessons — weekly live underwriting; 232k followers. "
            "[Synthetic mock result]",
            "boost": ["competitors", "competitor", "peers"],
        },
        {
            "title": "r/CommercialRealEstate — 'Podcasts that aren't guru content?' (thread)",
            "url": "https://reddit.example/r/commercialrealestate/comments/podcasts-not-guru",
            "snippet": "The James Prendamano Show comes up repeatedly for live underwriting and "
            "deal autopsies with real numbers — 'the opposite of guru content.' "
            "[Synthetic mock result]",
            "boost": ["reddit", "site", "community", "forum"],
        },
    ],
    "pages": {
        "https://www.prereal.example/": {
            "title": "PreReal | Prendamano Real Estate — Operators, not spectators",
            "text": (
                "PreReal is a real-estate brokerage, development, and advisory firm led by CEO "
                "James Prendamano, with two decades of work concentrated on Staten Island's north "
                "shore waterfront. Services: commercial brokerage, land assemblage, development "
                "advisory, and investment underwriting. James hosts The James Prendamano Show, a "
                "weekly video podcast on deals, operators, and market mechanics, and teaches the "
                "PreReal Investor Academy (400+ students). He serves on the board of directors of "
                "Spaceport America, bringing land-use and infrastructure experience to commercial "
                "space. Contact: (718) 555-0164. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "homepage"},
        },
        "https://www.prereal.example/press": {
            "title": "Press — James Prendamano / PreReal",
            "text": (
                "Selected press and appearances: Spaceport America board of directors appointment "
                "(April 2026). Brick & Risk podcast Ep. 88 — retail repricing and seller financing "
                "(June 2026). Harbor Business Ledger — PreReal Investor Academy crosses 400 "
                "students (June 2026). Keynote, Northeast Operators Summit — 'Underwriting the "
                "no' (March 2026). Recurring guest, regional business radio on waterfront "
                "redevelopment. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "press_page"},
        },
        "https://www.prereal.example/academy": {
            "title": "PreReal Investor Academy",
            "text": (
                "A live-cohort underwriting course for first-time and scaling investors: 8 weeks, "
                "weekly live deal walk-throughs, a shared deal library, and lifetime alumni access. "
                "The gap we fill isn't information — it's underwriting reps. 400+ students "
                "enrolled. Tuition published on application. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "product"},
        },
    },
    "news": [
        {
            "title": "Spaceport America adds real-estate executive James Prendamano to board of directors",
            "url": "https://spacecommercewire.example/news/spaceport-board-prendamano",
            "published_at": _iso(84),
            "source": "Space Commerce Wire",
            "snippet": "The board cited Prendamano's land-use, assemblage, and infrastructure "
            "development background as directly relevant to spaceport expansion planning.",
        },
        {
            "title": "'Retail isn't dead, lazy retail is': James Prendamano on the Brick & Risk podcast",
            "url": "https://brickandrisk.example/podcast/ep-88-prendamano",
            "published_at": _iso(27),
            "source": "Brick & Risk Podcast",
            "snippet": "A wide-ranging hour on north-shore waterfront repricing, dead-mall seller "
            "financing, and building authority through media.",
        },
        {
            "title": "PreReal Investor Academy tops 400 students",
            "url": "https://harborledger.example/business/prereal-academy-400",
            "published_at": _iso(5),
            "source": "Harbor Business Ledger",
            "snippet": "The live-cohort underwriting course crossed 400 enrolled students in its "
            "second year, with alumni closing first deals in three states.",
        },
    ],
    "accounts": {
        "instagram": {
            "platform": "instagram",
            "handle": "jamesprendamano",
            "display_name": "James Prendamano",
            "followers": 11800,
            "bio": "Real estate operator. Deal autopsies, mindset, the occasional spaceport. Academy + show below.",
            "meta": {"synthetic": True, "kind": "own", "cadence_per_week": 1.9},
        },
        "linkedin": {
            "platform": "linkedin",
            "handle": "jamesprendamano",
            "display_name": "James Prendamano",
            "followers": 9400,
            "bio": "CEO, PreReal | Prendamano Real Estate. Board of Directors, Spaceport America. Host, The James Prendamano Show.",
            "meta": {"synthetic": True, "kind": "own", "cadence_per_week": 1.4},
        },
        "youtube": {
            "platform": "youtube",
            "handle": "jamesprendamanoshow",
            "display_name": "The James Prendamano Show",
            "followers": 8200,
            "bio": "Weekly deal autopsies, live underwriting, operator interviews.",
            "meta": {"synthetic": True, "kind": "own", "cadence_per_week": 0.5},
        },
    },
    "analytics": {
        "instagram": {
            "followers": 11800,
            "following": 640,
            "posts_last_30d": 8,
            "avg_engagement_rate": 0.034,
            "impressions_last_90d": 412000,
            "reach_last_90d": 268000,
            "profile_views_last_90d": 9400,
            "demographics": {
                "top_locations": ["Staten Island, NY", "Brooklyn, NY", "New Jersey", "Manhattan, NY"],
                "age_ranges": {"25-34": 0.33, "35-44": 0.34, "45-54": 0.19, "55+": 0.14},
                "gender": {"female": 0.31, "male": 0.69},
            },
            "lookback_months": 12,
            "synthetic": True,
        },
        "linkedin": {
            "followers": 9400,
            "posts_last_30d": 6,
            "avg_engagement_rate": 0.048,
            "impressions_last_90d": 186000,
            "demographics": {
                "top_locations": ["New York City Metro", "New Jersey", "Philadelphia Metro"],
                "top_industries": ["Real Estate", "Financial Services", "Construction"],
            },
            "lookback_months": 12,
            "synthetic": True,
        },
        "youtube": {
            "followers": 8200,
            "posts_last_30d": 2,
            "avg_engagement_rate": 0.051,
            "views_last_90d": 96000,
            "avg_view_duration_sec": 486,
            "watch_time_hours_90d": 12900,
            "lookback_months": 12,
            "synthetic": True,
        },
    },
    "posts": {
        "instagram": build_posts(
            "instagram", "jamesprendamano", _JP_IG_CAPTIONS,
            followers=11800, base_likes=290, like_spread=380, per_month=8, views_multiplier=9,
        ),
        "linkedin": build_posts(
            "linkedin", "jamesprendamano", _JP_LI_CAPTIONS,
            followers=9400, base_likes=120, like_spread=210, per_month=6,
        ),
        "youtube": build_posts(
            "youtube", "jamesprendamanoshow", _JP_YT_TITLES,
            followers=8200, base_likes=210, like_spread=160, per_month=2, views_multiplier=22,
        ),
    },
    "peers": {
        "thebrokerbreakdown": {
            "platform": "instagram",
            "handle": "thebrokerbreakdown",
            "display_name": "Marcus Bell | The Broker Breakdown",
            "followers": 148000,
            "bio": "Deal autopsies and broker economics, daily. Ex-institutional, now independent.",
            "meta": {"synthetic": True, "kind": "aspirational", "cadence_per_week": 6.0, "avg_engagement_rate": 0.052},
        },
        "dealflowdaily": {
            "platform": "instagram",
            "handle": "dealflowdaily",
            "display_name": "Alyssa Grant — Deal Flow Daily",
            "followers": 96500,
            "bio": "One underwritten deal per day. Numbers on screen, no vibes.",
            "meta": {"synthetic": True, "kind": "aspirational", "cadence_per_week": 5.4, "avg_engagement_rate": 0.048},
        },
        "cashflowchronicles": {
            "platform": "instagram",
            "handle": "cashflowchronicles",
            "display_name": "Tomás Rivera | Cashflow Chronicles",
            "followers": 232000,
            "bio": "Small commercial, big lessons. Weekly live underwriting + market takes.",
            "meta": {"synthetic": True, "kind": "aspirational", "cadence_per_week": 6.5, "avg_engagement_rate": 0.065},
        },
    },
    "peer_posts": {
        "thebrokerbreakdown": build_posts(
            "instagram", "thebrokerbreakdown", _JP_PEER_CAPTIONS,
            followers=148000, base_likes=6100, like_spread=4200, per_month=6, months=4, views_multiplier=15,
        ),
        "dealflowdaily": build_posts(
            "instagram", "dealflowdaily", _JP_PEER_CAPTIONS,
            followers=96500, base_likes=3800, like_spread=2600, per_month=5, months=4, views_multiplier=13,
        ),
        "cashflowchronicles": build_posts(
            "instagram", "cashflowchronicles", _JP_PEER_CAPTIONS,
            followers=232000, base_likes=11800, like_spread=7400, per_month=6, months=4, views_multiplier=18,
        ),
    },
    "transcript": (
        "[Synthetic demo transcript] The James Prendamano Show, Ep. 141 excerpt: 'People ask what "
        "a real-estate guy is doing on a spaceport board. Land, patience, infrastructure — that's "
        "the whole job in both worlds. A launch complex is an assemblage problem with a longer "
        "timeline. And the discipline transfers back: once you've watched infrastructure get "
        "permitted on a twenty-year horizon, a fourteen-month zoning fight on the north shore "
        "stops feeling slow. Underwrite the timeline, not the rendering. That's the lesson I keep "
        "bringing back to the academy cohort.'"
    ),
    # ---- D11 research-lane fixtures ----
    # BY DESIGN: James has no Wikipedia page — this gap IS the demo:
    # positioning.wikipedia_presence=false feeds the Strategist's 'create a
    # Wikipedia page' recommendation. Do not add a page.
    "wiki": None,
    # Public channel overview for the video lane. Subscribers are the
    # combined PreReal channel figure (per the D11 lane fixture spec) and
    # intentionally differ from accounts.youtube followers (8,200 — the
    # aggregator-connected show account): a cross-source spread the D1
    # contradiction rule can surface.
    "channel": {
        "platform": "youtube",
        "channel_id": "mock-yt-jamesprendamanoshow",
        "title": "The James Prendamano Show — PreReal",
        "url": "https://youtube.example/@jamesprendamanoshow",
        "subscribers": 48200,
        "video_count": 320,
        "recent_titles": [
            "Ep. 143 — 2027 rate call: what we're underwriting for, not hoping for",
            "Ep. 142 — Why 'dead malls' are the best seller-financing lab in America",
            "North shore forecast: the three corridors that reprice first",
            "Ep. 141 — Spaceport America: what commercial space taught me about land banking",
            "Live underwriting: a waterfront assemblage, start to finish",
        ],
    },
    # BY DESIGN: a person, not a place — the places lane runs for
    # physical_asset/institution entity types only (D11).
    "place": None,
    "deep": {
        "synthesis": "Coverage of James Prendamano consistently frames him as a "
        "credibility-first real-estate operator: two decades on Staten Island's north "
        "shore, CEO of PreReal, and host of a weekly deal-autopsy show. The 2026 "
        "Spaceport America board appointment and the PreReal Investor Academy passing "
        "400 students extend the same thesis — authority built on land, patience, and "
        "underwriting discipline rather than hype.",
        "citations": [
            "https://www.prereal.example/",
            "https://spacecommercewire.example/news/spaceport-board-prendamano",
            "https://harborledger.example/business/prereal-academy-400",
        ],
    },
    "llm": {
        "entity_candidates": {
            "candidates": [
                {
                    "name": "James Prendamano (real-estate executive, Staten Island NY)",
                    "description": "CEO of PreReal | Prendamano Real Estate; host of The James "
                    "Prendamano Show; Spaceport America board member; runs the PreReal Investor Academy.",
                    "urls": [
                        "https://www.prereal.example/",
                        "https://www.prereal.example/press",
                        "https://www.prereal.example/academy",
                        "https://instagram.example/jamesprendamano",
                        "https://youtube.example/@jamesprendamanoshow",
                    ],
                    "score": 0.95,
                },
                {
                    "name": "James Prendamano Jr. (collegiate athlete)",
                    "description": "Namesake student athlete with minor local sports coverage; no "
                    "real-estate footprint.",
                    "urls": ["https://collegesportswire.example/roster/j-prendamano"],
                    "score": 0.12,
                },
            ]
        },
        # researcher.PROMPT_EXTRACT shape: citations are URL strings present in
        # the batch material; the agent builds FieldWrite rows itself and the
        # profile service assigns source/confidence (D2).
        "field_extraction": {
            "fields": [
                {
                    "section": "identity",
                    "field_key": "identity.display_name",
                    "item_key": None,
                    "value": "James Prendamano",
                    "citations": ["https://www.prereal.example/"],
                },
                {
                    "section": "identity",
                    "field_key": "identity.positioning",
                    "item_key": None,
                    "value": "Real-estate operator and authority voice — deal-level credibility "
                    "(20+ years, north-shore waterfront) packaged as media, education, and board-level proof.",
                    "citations": [
                        "https://www.prereal.example/",
                        "https://brickandrisk.example/podcast/ep-88-prendamano",
                    ],
                },
                {
                    "section": "identity",
                    "field_key": "identity.holdings",
                    "item_key": "prereal",
                    "value": {"name": "PreReal | Prendamano Real Estate", "role": "CEO", "kind": "company"},
                    "citations": ["https://www.prereal.example/"],
                },
                {
                    "section": "identity",
                    "field_key": "identity.holdings",
                    "item_key": "spaceport-america-board",
                    "value": {"name": "Spaceport America", "role": "Board of Directors", "kind": "board_seat"},
                    "citations": [
                        "https://spacecommercewire.example/news/spaceport-board-prendamano",
                        "https://www.prereal.example/press",
                    ],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.pillar_topics",
                    "item_key": "real-estate",
                    "value": {"topic": "real estate & deal-making", "weight": 0.6},
                    "citations": ["https://youtube.example/@jamesprendamanoshow"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.pillar_topics",
                    "item_key": "mindset",
                    "value": {"topic": "operator mindset", "weight": 0.15},
                    "citations": ["https://instagram.example/jamesprendamano"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.pillar_topics",
                    "item_key": "education-reform",
                    "value": {"topic": "education reform / financial literacy", "weight": 0.15},
                    "citations": ["https://youtube.example/@jamesprendamanoshow"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.pillar_topics",
                    "item_key": "term-limits",
                    "value": {"topic": "civic reform / term limits", "weight": 0.1},
                    "citations": ["https://linkedin.example/in/jamesprendamano"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.proof_points",
                    "item_key": "spaceport-board",
                    "value": "Board of Directors, Spaceport America (appointed April 2026)",
                    "citations": [
                        "https://spacecommercewire.example/news/spaceport-board-prendamano",
                        "https://www.prereal.example/press",
                    ],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.proof_points",
                    "item_key": "brick-and-risk-ep88",
                    "value": "Featured guest, Brick & Risk podcast Ep. 88 (June 2026)",
                    "citations": ["https://brickandrisk.example/podcast/ep-88-prendamano"],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "investor-academy",
                    "value": {"name": "PreReal Investor Academy", "detail": "8-week live-cohort underwriting course, 400+ students"},
                    "citations": [
                        "https://www.prereal.example/academy",
                        "https://harborledger.example/business/prereal-academy-400",
                    ],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "brokerage-advisory",
                    "value": {"name": "PreReal brokerage & advisory", "detail": "commercial brokerage, assemblage, development advisory"},
                    "citations": ["https://www.prereal.example/"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.website",
                    "item_key": None,
                    "value": "https://www.prereal.example/",
                    "citations": ["https://www.prereal.example/"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.instagram",
                    "item_key": None,
                    "value": {"platform": "instagram", "handle": "jamesprendamano", "url": "https://instagram.example/jamesprendamano"},
                    "citations": ["https://instagram.example/jamesprendamano"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.youtube",
                    "item_key": None,
                    "value": {"platform": "youtube", "handle": "jamesprendamanoshow", "url": "https://youtube.example/@jamesprendamanoshow"},
                    "citations": ["https://youtube.example/@jamesprendamanoshow"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.linkedin",
                    "item_key": None,
                    "value": {"platform": "linkedin", "handle": "jamesprendamano", "url": "https://linkedin.example/in/jamesprendamano"},
                    "citations": ["https://linkedin.example/in/jamesprendamano"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "thebrokerbreakdown",
                    "value": {"name": "Marcus Bell | The Broker Breakdown", "platform": "instagram", "handle": "thebrokerbreakdown", "kind": "aspirational"},
                    "citations": ["https://instagram.example/thebrokerbreakdown"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "dealflowdaily",
                    "value": {"name": "Alyssa Grant — Deal Flow Daily", "platform": "instagram", "handle": "dealflowdaily", "kind": "aspirational"},
                    "citations": ["https://instagram.example/dealflowdaily"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "cashflowchronicles",
                    "value": {"name": "Tomás Rivera | Cashflow Chronicles", "platform": "instagram", "handle": "cashflowchronicles", "kind": "aspirational"},
                    "citations": ["https://instagram.example/cashflowchronicles"],
                },
            ],
            "failures": [],
        },
        # D11 lane-extraction payload (PROMPT_EXTRACT shape; see the
        # Turtleback block for the filtering contract). No wiki field — the
        # missing page is the finding. channels.youtube_stats (not
        # channels.youtube) so the video lane SUPPLEMENTS the web lane's
        # channel row instead of colliding with it in _merge_fields.
        "lane_findings": {
            "fields": [
                {
                    "section": "channels",
                    "field_key": "channels.youtube_stats",
                    "item_key": None,
                    "value": {
                        "platform": "youtube",
                        "handle": "jamesprendamanoshow",
                        "subscribers": 48200,
                        "video_count": 320,
                    },
                    "citations": ["https://youtube.example/@jamesprendamanoshow"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.community_sentiment",
                    "item_key": None,
                    "value": "Recommended in r/CommercialRealEstate threads for live underwriting "
                    "and deal autopsies with real numbers — 'the opposite of guru content.'",
                    "citations": ["https://reddit.example/r/commercialrealestate/comments/podcasts-not-guru"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.research_synthesis",
                    "item_key": None,
                    "value": "Credibility-first real-estate operator: two decades on Staten "
                    "Island's north shore packaged as media, education, and board-level proof — "
                    "the Spaceport America seat and the 400-student academy extend one thesis: "
                    "authority built on underwriting discipline, not hype.",
                    "citations": [
                        "https://spacecommercewire.example/news/spaceport-board-prendamano",
                        "https://harborledger.example/business/prereal-academy-400",
                    ],
                },
            ],
            "failures": [],
        },
        "weekly_plan": {
            "brand_id": "",
            "period_start": "2026-07-06T00:00:00+00:00",
            "period_end": "2026-07-12T23:59:59+00:00",
            "rationale": "All three tracked peers lead with deal-autopsy reels (5.5% avg ER vs your "
            "3.4% on statics) at 3x your cadence. The Spaceport board press and the academy-400 "
            "milestone are unexploited authority assets — this week converts both into content and "
            "shifts the mix toward reels.",
            "goals_snapshot": {
                "instagram_followers_target": 20000,
                "youtube_subs_target": 12000,
                "academy_signups_90d": 150,
            },
            "items": [
                {
                    "content_type": "reel",
                    "platform": "instagram",
                    "topic": "Deal autopsy: the 2024 retail condo we passed on — and what it traded for last month",
                    "count": 3,
                    "format_spec": {"length_sec": 45, "aspect": "9:16", "hook": "We said no to this building in 2024. Here's the number that changed."},
                    "rationale": "Deal-autopsy reels are the highest-ER format across all three "
                    "tracked peers; James has the receipts to do it with real numbers.",
                    "evidence": [
                        {"url": "https://instagram.example/thebrokerbreakdown", "ref": "", "note": "autopsy reels avg 5.8% ER"},
                        {"url": "https://instagram.example/cashflowchronicles", "ref": "", "note": "live-underwriting format 6.5% ER"},
                    ],
                    "predicted_metrics": {"views": 18500, "likes": 610, "comments": 42, "engagement_rate": 0.055},
                },
                {
                    "content_type": "long_form_video",
                    "platform": "youtube",
                    "topic": "Spaceport America: what commercial space teaches about land banking",
                    "count": 1,
                    "format_spec": {"length_min": 22, "chapters": True, "cta": "academy waitlist"},
                    "rationale": "The board appointment is a credibility-ladder asset; press is 12 "
                    "weeks old but unexploited in long form. Authority content resurfaces well.",
                    "evidence": [
                        {"url": "https://spacecommercewire.example/news/spaceport-board-prendamano", "ref": "", "note": "board press"},
                        {"url": "https://youtube.example/@jamesprendamanoshow", "ref": "", "note": "Ep. 141 outperformed channel avg 2.1x"},
                    ],
                    "predicted_metrics": {"views": 5200, "avg_view_duration_sec": 540, "likes": 260, "new_subs": 90},
                },
                {
                    "content_type": "text_post",
                    "platform": "linkedin",
                    "topic": "Three lessons from the waterfront assemblage that ran 4 years past pro forma",
                    "count": 3,
                    "format_spec": {"format": "numbered thread", "cta": "newsletter"},
                    "rationale": "LinkedIn is James's highest-ER channel (4.8%); operator-lessons "
                    "posts are its top category over 12 months.",
                    "evidence": [
                        {"url": "https://linkedin.example/in/jamesprendamano", "ref": "", "note": "own history: lessons posts +52% vs avg"}
                    ],
                    "predicted_metrics": {"impressions": 7400, "reactions": 210, "comments": 28},
                },
                {
                    "content_type": "image",
                    "platform": "instagram",
                    "topic": "Academy 400-student milestone — proof-point quote card",
                    "count": 2,
                    "format_spec": {"aspect": "4:5", "template": "quote-card-dark"},
                    "rationale": "Fresh press (5 days) + funnel content for the academy signup goal.",
                    "evidence": [
                        {"url": "https://harborledger.example/business/prereal-academy-400", "ref": "", "note": "milestone press"}
                    ],
                    "predicted_metrics": {"likes": 320, "comments": 18, "engagement_rate": 0.029, "link_clicks": 140},
                },
            ],
        },
        "morning_brief": {
            "brand_id": "",
            "date": ANCHOR_ISO,
            "headline": "Your peers are winning with deal autopsies — and you're sitting on two unexploited press hits",
            "sections": [
                {
                    "title": "Press",
                    "body": "The academy-400 story (5 days old) and the Spaceport board appointment "
                    "(12 weeks) have zero owned-channel coverage. Both are credibility-ladder assets: "
                    "one feeds the funnel, one feeds authority.",
                    "citations": [
                        "https://harborledger.example/business/prereal-academy-400",
                        "https://spacecommercewire.example/news/spaceport-board-prendamano",
                    ],
                },
                {
                    "title": "Peers",
                    "body": "Deal-autopsy reels averaged 5.5-6.5% ER across all three tracked peers "
                    "this month, at 5-6 posts/week. Your statics run 3.4% at under 2 posts/week. The "
                    "format gap is the growth gap.",
                    "citations": [
                        "https://instagram.example/thebrokerbreakdown",
                        "https://instagram.example/dealflowdaily",
                        "https://instagram.example/cashflowchronicles",
                    ],
                },
                {
                    "title": "Own baseline",
                    "body": "YouTube is quietly your strongest asset: 8.1-minute average view "
                    "duration and Ep. 141 at 2.1x channel average. LinkedIn ER (4.8%) beats IG (3.4%).",
                    "citations": ["https://youtube.example/@jamesprendamanoshow"],
                },
            ],
            "recommended_actions": [
                "Film the retail-condo deal autopsy as this week's anchor reel — real numbers on screen.",
                "Cut the Spaceport long-form episode this week; clip 3 shorts from it for next week.",
                "Post the academy milestone quote card with a waitlist CTA within 48 hours while the press is fresh.",
            ],
        },
        "peer_digest": {
            "brand_id": "",
            "period": "2026-06-29/2026-07-05",
            "peers": [
                {"handle": "thebrokerbreakdown", "kind": "aspirational", "platform": "instagram", "followers": 148000,
                 "cadence_per_week": 6.0, "avg_engagement_rate": 0.052,
                 "top_topics": ["deal autopsies", "broker economics", "market takes"]},
                {"handle": "dealflowdaily", "kind": "aspirational", "platform": "instagram", "followers": 96500,
                 "cadence_per_week": 5.4, "avg_engagement_rate": 0.048,
                 "top_topics": ["daily underwriting", "numbers-on-screen", "listings"]},
                {"handle": "cashflowchronicles", "kind": "aspirational", "platform": "instagram", "followers": 232000,
                 "cadence_per_week": 6.5, "avg_engagement_rate": 0.065,
                 "top_topics": ["live underwriting", "small commercial", "market takes"]},
            ],
            "benchmarks": {
                "cadence_per_week": 6.0,
                "avg_engagement_rate": 0.055,
                "formats": {"reel": 0.68, "image": 0.18, "text": 0.14},
            },
            "observations": [
                "All three peers posted deal-breakdown reels this week; the format averaged 5.8% ER "
                "vs 3.4% for James's recent statics [instagram.example/thebrokerbreakdown].",
                "Cashflow Chronicles' live-underwriting session drew 41k views — the same format as "
                "James's academy cohort calls, which are currently unrecorded [instagram.example/cashflowchronicles].",
                "Peer cadence benchmark is 6/week; James posts 1.9/week on IG. Every peer at 100k+ "
                "sustains 5+ [instagram.example/dealflowdaily].",
            ],
        },
        "goals": {
            "goals": [
                {"section": "goals", "platform": "instagram", "metric": "followers", "baseline": 11800,
                 "target": 20000, "timeframe_days": 90, "priority": 0.3,
                 "rationale": "Peers who crossed 20k did it on 5+/week reel-led cadence; 90 days is aggressive but modelable."},
                {"section": "goals", "platform": "youtube", "metric": "subscribers", "baseline": 8200,
                 "target": 12000, "timeframe_days": 90, "priority": 0.25,
                 "rationale": "Strongest retention metrics of any channel; weekly long-form + clipped shorts."},
                {"section": "goals", "platform": "linkedin", "metric": "avg_engagement_rate", "baseline": 0.048,
                 "target": 0.055, "timeframe_days": 90, "priority": 0.15,
                 "rationale": "Highest-ER channel; maintain while volume shifts to video."},
                {"section": "goals", "platform": None, "metric": "academy_signups", "baseline": 0,
                 "target": 150, "timeframe_days": 90, "priority": 0.3,
                 "rationale": "Business goal: 150 new academy signups, fed by autopsy reels and the milestone press."},
            ],
            "narrative": "Accounts like yours that reached 100k did it in roughly 14 months at 5-6 "
            "posts/week, reel-led. A 90-day commit of 3 reels + 2 statics weekly on IG plus weekly "
            "YouTube puts 20k IG / 12k YT in range while the academy funnel compounds.",
        },
        "press_events": {
            "events": [
                {
                    "title": "Appointed to Spaceport America board of directors",
                    "kind": "credibility_signal",
                    "content_worthy": True,
                    "summary": "Board seat at a commercial spaceport — top-tier authority proof "
                    "point linking real estate to infrastructure/space.",
                    "url": "https://spacecommercewire.example/news/spaceport-board-prendamano",
                    "suggested_angle": "Long-form episode + proof-point placement in bio and press page.",
                },
                {
                    "title": "PreReal Investor Academy tops 400 students",
                    "kind": "credibility_signal",
                    "content_worthy": True,
                    "summary": "Fresh milestone press with direct funnel value for the academy goal.",
                    "url": "https://harborledger.example/business/prereal-academy-400",
                    "suggested_angle": "Quote card + waitlist CTA within 48 hours.",
                },
                {
                    "title": "Brick & Risk podcast appearance (Ep. 88)",
                    "kind": "credibility_signal",
                    "content_worthy": True,
                    "summary": "Guest slot on a mid-tier industry podcast — credibility-ladder rung; "
                    "next tier: national business podcasts.",
                    "url": "https://brickandrisk.example/podcast/ep-88-prendamano",
                    "suggested_angle": "Clip 2 shorts from the episode audio; pitch next-tier shows citing it.",
                },
            ]
        },
        # reviewer judge contract: {"score": float 0-1, "notes": str}
        "review_judge": {
            "score": 0.84,
            "notes": "Direct, aphoristic, no-hedging cadence matches the exemplar corpus "
            "('Rates didn't kill your deal. Your assumptions did.'). Claims are number-anchored, "
            "consistent with register.",
        },
        "draft_post": "We passed on a retail condo in 2024 because the seller's pro forma assumed "
        "a tenant that didn't exist yet. It traded last month at 22% below that ask. The no's keep "
        "the lights on. Underwrite your no's.",
        "draft_templates": (
            "{topic}. The numbers tell this story better than any pitch, so we put the numbers "
            "on screen. Watch it once for the deal, twice for the discipline. Then underwrite it "
            "yourself.",
            "{topic}. Most people scroll past this stuff; the ones who stop are the ones closing. "
            "Full breakdown on the show this week, receipts included.",
            "{topic}. We did the work up front so you can check the math yourself. No hype, no "
            "hedge, just the deal as it actually happened. Link in bio.",
        ),
        "revise_templates": (
            "{topic}. Cleaner take: the facts up front, the lesson in the middle, the ask at the "
            "end. It traded exactly how the underwriting said it would. Details on the show.",
            "{topic}. Second pass, tighter cut. One deal, one number that mattered, one decision "
            "you can copy. That's the whole post.",
        ),
        "brief_text": "Morning brief, James: two press hits are sitting unexploited — the academy "
        "milestone (5 days) and the Spaceport board seat. Peers won the week with deal-autopsy "
        "reels at 5.5%+ engagement. Anchor this week on the retail-condo autopsy, cut the "
        "Spaceport long-form, and ship the milestone quote card inside 48 hours.",
        "plan_narrative": "This week: 3 deal-autopsy reels, 1 Spaceport long-form on YouTube, 3 "
        "LinkedIn lesson threads, 2 academy proof-point statics — because every tracked peer wins "
        "with autopsy reels at 3x your cadence, and both of your live press hits are currently "
        "unconverted into content.",
    },
}


# =====================================================================
# Spaceport America — institution (test brand: NM commercial spaceport)
# =====================================================================

_SP_IG_CAPTIONS = [
    "Launch morning at Spaceport America: pads clear by 6am, chase crews staged, and the "
    "Jornada del Muerto quiet enough to hear the countdown.",
    "The vertical launch area saw its 14th mission of the year this week. The desert doesn't "
    "make it easy; that's exactly why our tenants test here.",
    "Tour season is open. Two hours, the Gateway to Space terminal, the runway apron, and a "
    "view of the San Andres range you won't get anywhere else.",
    "Student teams from 24 countries are on site for the Spaceport America Cup. 158 rockets, "
    "one very busy range crew.",
    "12,000 feet of runway, built for vehicles that didn't exist when it was poured. That's "
    "the job: infrastructure ahead of demand.",
    "New tenant on the west campus: a reusable-launch startup signed a 10-year lease. First "
    "test window opens in Q4.",
    "Weather hold this morning, wind out of the southwest at 28 knots. The range reopens at "
    "noon; the desert sets the schedule.",
    "Behind the scenes with our fire and rescue team, the people who make every test window "
    "possible.",
    "The Gateway to Space terminal turns 15 this year. Still the most photographed building "
    "in Sierra County.",
    "Night launch window this Friday. If you're within 50 miles of Truth or Consequences, "
    "look up around 9pm.",
    "Our visitor center in T or C has new exhibits on the vertical launch program. Free for "
    "Sierra County residents.",
    "Spaceport America Cup applications open next month. Faculty advisors: the updated range "
    "safety briefing is online now.",
]

_SP_X_CAPTIONS = [
    "Mission update: VLA-14 nominal. Apogee 91 km, recovery inside the range boundary. "
    "Congratulations to the flight team.",
    "Runway 16/34 closed 0600-0900 MT tomorrow for surface testing. NOTAM filed.",
    "Welcome to the newest member of the tenant roster: 10-year lease on the west campus, "
    "first test window in Q4.",
    "Spaceport America Cup by the numbers: 158 teams, 24 countries, 6 days on the range.",
    "Public tours run Friday through Sunday from the T or C visitor center. Book ahead; "
    "launch weeks sell out.",
    "Annual report is out: a record 41 launch operations this fiscal year. Full document on "
    "the site.",
]

_SP_PEER_CAPTIONS = [
    "Liftoff. Another crew is on its way to the station.",
    "The view from 400 kilometers up never gets old. Taken this morning over the Pacific.",
    "Static fire complete. Launch window opens Thursday.",
    "Landing confirmed. The booster is back on the pad after its ninth flight.",
    "New imagery from this week's mission, processed and ready for download.",
    "T-minus one day. Weather is 80% favorable for tomorrow's window.",
]

SPACEPORT: dict = {
    "slug": "spaceport-america",
    "name": "Spaceport America",
    "entity_type": "institution",
    "aliases": ["spaceport america", "spaceportamerica", "spaceport"],
    "website": "https://www.spaceportamerica.example/",
    "search_results": [
        {
            "title": "Spaceport America | The World's First Purpose-Built Commercial Spaceport",
            "url": "https://www.spaceportamerica.example/",
            "snippet": "18,000 acres in the Jornada del Muerto desert, Sierra County, New Mexico: "
            "a 12,000-ft runway, vertical launch area, and the Gateway to Space terminal. Home "
            "to commercial launch and flight-test tenants.",
        },
        {
            "title": "Visit Spaceport America — Tours & Visitor Center",
            "url": "https://www.spaceportamerica.example/visit",
            "snippet": "Guided tours from the Truth or Consequences visitor center Friday-Sunday: "
            "the Gateway terminal, runway apron, and vertical launch area overlook.",
            "boost": ["products", "services", "tours", "visit"],
        },
        {
            "title": "About — Spaceport America",
            "url": "https://www.spaceportamerica.example/about",
            "snippet": "Opened 2011; operated by the New Mexico Spaceport Authority. A record 41 "
            "licensed launch operations this fiscal year across vertical and horizontal programs.",
            "boost": ["about", "history"],
        },
        {
            "title": "Spaceport America posts record year: 41 launch operations",
            "url": "https://desertlaunchwire.example/news/spaceport-america-record-year",
            "snippet": "The New Mexico spaceport logged 41 licensed launch operations this fiscal "
            "year, up from 29, driven by suborbital research flights and student rocketry.",
            "boost": ["news", "press"],
        },
        {
            "title": "Spaceport America (@spaceportamerica) — Instagram",
            "url": "https://instagram.example/spaceportamerica",
            "snippet": "The world's first purpose-built commercial spaceport. Sierra County, New "
            "Mexico. Launches, tours, and desert skies.",
            "boost": ["instagram", "social"],
        },
        {
            "title": "Spaceport America (@spaceportamerica) — X",
            "url": "https://x.example/spaceportamerica",
            "snippet": "Mission updates, range schedules, and tenant news from New Mexico's "
            "commercial spaceport.",
            "boost": ["x", "twitter", "social"],
        },
        {
            "title": "Reusable-launch startup signs 10-year lease at Spaceport America",
            "url": "https://sierracountysun.example/business/spaceport-tenant-lease",
            "snippet": "The west-campus lease is the facility's largest tenant commitment since "
            "its anchor tenant, the Spaceport Authority said.",
            "boost": ["news", "press", "tenant"],
        },
        {
            "title": "NASA (@nasa) — Instagram",
            "url": "https://instagram.example/nasa",
            "snippet": "Exploring the universe and our home planet. The benchmark account for "
            "institutional space storytelling. [Synthetic mock result]",
            "boost": ["competitors", "competitor", "peers"],
        },
        {
            "title": "SpaceX (@spacex) — Instagram",
            "url": "https://instagram.example/spacex",
            "snippet": "Launch and landing footage with relentless cadence; the commercial "
            "reference point for launch content. [Synthetic mock result]",
            "boost": ["competitors", "competitor", "peers"],
        },
        {
            "title": "Virgin Galactic (@virgingalactic) — Instagram",
            "url": "https://instagram.example/virgingalactic",
            "snippet": "Commercial spaceline flying from Spaceport America; cinematic flight-day "
            "storytelling. [Synthetic mock result]",
            "boost": ["competitors", "competitor", "peers"],
        },
        {
            "title": "r/NewMexico — 'Is the Spaceport America tour worth the drive?' (thread)",
            "url": "https://reddit.example/r/newmexico/comments/spaceport-tour-worth-it",
            "snippet": "Consensus: yes during launch weeks, book ahead; several commenters wish "
            "the spaceport posted launch windows earlier. [Synthetic mock result]",
            "boost": ["reddit", "site", "community", "forum"],
        },
        {
            # The youtube lane's locator hit (site:youtube.com search): the
            # url matches the "channel" fixture below so the located url
            # resolves to the overview, mirroring live.
            "title": "Spaceport America — YouTube",
            "url": "https://youtube.example/@spaceportamerica",
            "snippet": "Launch days at the vertical launch area, Gateway terminal walkthroughs, "
            "and the Spaceport America Cup. [Synthetic mock result]",
            "boost": ["youtube", "channel", "video"],
        },
    ],
    "pages": {
        "https://www.spaceportamerica.example/": {
            "title": "Spaceport America | The World's First Purpose-Built Commercial Spaceport",
            "text": (
                "Spaceport America is the world's first purpose-built commercial spaceport: "
                "18,000 acres in the Jornada del Muerto desert of Sierra County, New Mexico, with "
                "restricted airspace, a 12,000-foot spaceway (Runway 16/34), a vertical launch "
                "area, and the LEED-certified Gateway to Space terminal. Operated by the New "
                "Mexico Spaceport Authority, the campus hosts commercial launch providers, "
                "flight-test programs, and the annual Spaceport America Cup, the world's largest "
                "intercollegiate rocketry competition. Public tours depart from the visitor "
                "center in Truth or Consequences. Contact: (575) 555-0132. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "homepage"},
        },
        "https://www.spaceportamerica.example/about": {
            "title": "About — Spaceport America",
            "text": (
                "Commissioned by the State of New Mexico and opened in 2011, Spaceport America "
                "was built ahead of demand on the bet that commercial space would need dedicated "
                "ground infrastructure. The bet is paying off: a record 41 licensed launch "
                "operations this fiscal year across the vertical launch area and the spaceway, a "
                "growing tenant roster anchored by a commercial spaceline, and the Spaceport "
                "America Cup bringing 158 student teams from 24 countries each June. The New "
                "Mexico Spaceport Authority employs 62 staff across operations, range safety, "
                "and fire and rescue. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "about"},
        },
        "https://www.spaceportamerica.example/visit": {
            "title": "Visit — Spaceport America",
            "text": (
                "Guided tours run Friday through Sunday from the Spaceport America visitor "
                "center in Truth or Consequences: the Gateway to Space terminal, the runway "
                "apron, and the vertical launch area overlook. Adults $35, students and Sierra "
                "County residents discounted; launch-week tours sell out early. Private group "
                "and education bookings available. The visitor center's exhibits on the vertical "
                "launch program are free to Sierra County residents. [Synthetic demo content]"
            ),
            "meta": {"synthetic": True, "kind": "visit"},
        },
    },
    "news": [
        {
            "title": "Spaceport America posts record year with 41 licensed launch operations",
            "url": "https://desertlaunchwire.example/news/spaceport-america-record-year",
            "published_at": _iso(9),
            "source": "Desert Launch Wire",
            "snippet": "Suborbital research flights and student rocketry drove the New Mexico "
            "spaceport's busiest fiscal year since opening, up from 29 operations last year.",
        },
        {
            "title": "Reusable-launch startup signs 10-year lease at Spaceport America's west campus",
            "url": "https://sierracountysun.example/business/spaceport-tenant-lease",
            "published_at": _iso(31),
            "source": "Sierra County Sun",
            "snippet": "The Authority called it the facility's largest tenant commitment since "
            "its anchor spaceline, with first test windows expected in Q4.",
        },
        {
            "title": "Spaceport America Cup returns with 158 teams from 24 countries",
            "url": "https://studentrocketrytoday.example/spaceport-america-cup-2026",
            "published_at": _iso(21),
            "source": "Student Rocketry Today",
            "snippet": "The world's largest intercollegiate rocketry competition fills Sierra "
            "County hotels for a week and doubles as a recruiting pipeline for tenants.",
        },
    ],
    "accounts": {
        "instagram": {
            "platform": "instagram",
            "handle": "spaceportamerica",
            "display_name": "Spaceport America",
            "followers": 58400,
            "bio": "The world's first purpose-built commercial spaceport. Sierra County, NM. Launches, tours, desert skies.",
            "meta": {"synthetic": True, "kind": "own", "cadence_per_week": 1.1},
        },
        "x": {
            "platform": "x",
            "handle": "spaceportamerica",
            "display_name": "Spaceport America",
            "followers": 41200,
            "bio": "Mission updates, range schedules, and tenant news from New Mexico's commercial spaceport.",
            "meta": {"synthetic": True, "kind": "own", "cadence_per_week": 1.6},
        },
    },
    "analytics": {
        "instagram": {
            "followers": 58400,
            "following": 210,
            "posts_last_30d": 5,
            "avg_engagement_rate": 0.028,
            "impressions_last_90d": 1240000,
            "reach_last_90d": 861000,
            "profile_views_last_90d": 30200,
            "demographics": {
                "top_locations": ["Albuquerque, NM", "El Paso, TX", "Las Cruces, NM", "Phoenix, AZ"],
                "age_ranges": {"18-24": 0.19, "25-34": 0.31, "35-44": 0.24, "45+": 0.26},
                "gender": {"female": 0.41, "male": 0.59},
            },
            "lookback_months": 12,
            "synthetic": True,
        },
        "x": {
            "followers": 41200,
            "posts_last_30d": 7,
            "avg_engagement_rate": 0.014,
            "impressions_last_90d": 720000,
            "lookback_months": 12,
            "synthetic": True,
        },
    },
    "posts": {
        "instagram": build_posts(
            "instagram", "spaceportamerica", _SP_IG_CAPTIONS,
            followers=58400, base_likes=1450, like_spread=1100, per_month=4, views_multiplier=11,
        ),
        "x": build_posts(
            "x", "spaceportamerica", _SP_X_CAPTIONS,
            followers=41200, base_likes=310, like_spread=260, per_month=6,
        ),
    },
    # Aspirational peers use realistic follower magnitudes for the real
    # accounts they name (NASA ~97M IG, SpaceX tens of millions, Virgin
    # Galactic sub-1M) so the peer digest reads plausibly on a projector.
    "peers": {
        "nasa": {
            "platform": "instagram",
            "handle": "nasa",
            "display_name": "NASA",
            "followers": 97200000,
            "bio": "Exploring the universe and our home planet.",
            "meta": {"synthetic": True, "kind": "aspirational", "cadence_per_week": 5.8, "avg_engagement_rate": 0.006},
        },
        "spacex": {
            "platform": "instagram",
            "handle": "spacex",
            "display_name": "SpaceX",
            "followers": 36400000,
            "bio": "SpaceX designs, manufactures and launches advanced rockets and spacecraft.",
            "meta": {"synthetic": True, "kind": "aspirational", "cadence_per_week": 3.4, "avg_engagement_rate": 0.011},
        },
        "virgingalactic": {
            "platform": "instagram",
            "handle": "virgingalactic",
            "display_name": "Virgin Galactic",
            "followers": 792000,
            "bio": "The world's first commercial spaceline, flying from Spaceport America.",
            "meta": {"synthetic": True, "kind": "aspirational", "cadence_per_week": 2.6, "avg_engagement_rate": 0.018},
        },
    },
    "peer_posts": {
        "nasa": build_posts(
            "instagram", "nasa", _SP_PEER_CAPTIONS,
            followers=97200000, base_likes=412000, like_spread=260000, per_month=12, months=4, views_multiplier=9,
        ),
        "spacex": build_posts(
            "instagram", "spacex", _SP_PEER_CAPTIONS,
            followers=36400000, base_likes=268000, like_spread=210000, per_month=8, months=4, views_multiplier=12,
        ),
        "virgingalactic": build_posts(
            "instagram", "virgingalactic", _SP_PEER_CAPTIONS,
            followers=792000, base_likes=8900, like_spread=6200, per_month=5, months=4, views_multiplier=14,
        ),
    },
    "transcript": (
        "[Synthetic demo transcript] Range operations briefing excerpt: 'People see an empty "
        "desert and ask why here. Restricted airspace to 100,000 feet, 340 flyable days a year, "
        "and no neighbors under the flight path. You cannot build that in a city. Our job is to "
        "keep the range ready so a tenant can go from test stand to launch window without "
        "leaving the fence line. The record year didn't happen because space got easier; it "
        "happened because the infrastructure was already here.'"
    ),
    # ---- D11 research-lane fixtures ----
    # The one test brand WITH a Wikipedia page — the wiki lane's positive
    # case (Turtleback and James stay page-less by design).
    "wiki": {
        "title": "Spaceport America",
        "url": "https://en.wikipedia.example/wiki/Spaceport_America",
        "summary": (
            "Spaceport America is a commercial spaceport in the Jornada del Muerto desert "
            "basin of Sierra County, New Mexico, United States. Described as the world's "
            "first purpose-built commercial spaceport, the 18,000-acre campus comprises a "
            "12,000-foot spaceway (Runway 16/34), a vertical launch area, and the "
            "LEED-certified Gateway to Space terminal, all under restricted airspace. "
            "[Synthetic demo content]\n\n"
            "Commissioned by the State of New Mexico and opened in 2011, the facility is "
            "operated by the New Mexico Spaceport Authority. It serves as the operating "
            "base of its anchor spaceline alongside a roster of launch and flight-test "
            "tenants, and hosts the annual Spaceport America Cup, the world's largest "
            "intercollegiate rocketry competition. The spaceport recorded 41 licensed "
            "launch operations in fiscal year 2026, its busiest year since opening. "
            "[Synthetic demo content]"
        ),
        "facts": {
            "opened": "2011",
            "location": "Jornada del Muerto desert, Sierra County, New Mexico",
            "operator": "New Mexico Spaceport Authority",
            "notable_tenants": "Virgin Galactic (anchor spaceline); reusable-launch "
            "startup on a 10-year west-campus lease",
        },
    },
    "channel": {
        "platform": "youtube",
        "channel_id": "mock-yt-spaceportamerica",
        "title": "Spaceport America",
        "url": "https://youtube.example/@spaceportamerica",
        "subscribers": 12100,
        "video_count": 96,
        "recent_titles": [
            "Launch day at the vertical launch area: VLA-14 from pad clear to recovery",
            "Inside the Gateway to Space terminal — full walkthrough",
            "Spaceport America Cup 2026: 158 teams, 24 countries, one range",
            "How range safety clears a launch window",
            "Night launch over the Jornada del Muerto",
        ],
    },
    # The maps listing the places lane finds is the public-facing visitor
    # center in T or C, not the gated range itself.
    "place": {
        "name": "Spaceport America Visitor Center",
        "address": "500 Desert Gateway Plaza, Truth or Consequences, NM 87901",
        "rating": 4.4,
        "reviews_count": 612,
        "categories": ["Visitor center", "Tourist attraction"],
        "website": "https://www.spaceportamerica.example/visit",
        "url": "https://maps.example/place/spaceport-america-visitor-center",
    },
    "deep": {
        "synthesis": "Coverage converges on one story: the build-ahead-of-demand bet is "
        "maturing, with a record 41 licensed launch operations this fiscal year and the "
        "largest tenant commitment since the anchor spaceline. The differentiators are "
        "structural — restricted airspace, 340 flyable days, a 12,000-foot spaceway — "
        "while public visibility still leans on tours and the annual Spaceport America "
        "Cup rather than the operational record.",
        "citations": [
            "https://www.spaceportamerica.example/about",
            "https://desertlaunchwire.example/news/spaceport-america-record-year",
            "https://sierracountysun.example/business/spaceport-tenant-lease",
        ],
    },
    "llm": {
        "entity_candidates": {
            "candidates": [
                {
                    "name": "Spaceport America (Sierra County, New Mexico)",
                    "description": "The world's first purpose-built commercial spaceport: 12,000-ft "
                    "spaceway, vertical launch area, Gateway to Space terminal; operated by the New "
                    "Mexico Spaceport Authority; hosts the Spaceport America Cup.",
                    "urls": [
                        "https://www.spaceportamerica.example/",
                        "https://www.spaceportamerica.example/about",
                        "https://www.spaceportamerica.example/visit",
                        "https://instagram.example/spaceportamerica",
                        "https://x.example/spaceportamerica",
                    ],
                    "score": 0.94,
                },
                {
                    "name": "Spaceport America Cup (student rocketry competition)",
                    "description": "The annual intercollegiate rocketry event hosted AT the "
                    "spaceport — an event brand, not the facility itself.",
                    "urls": ["https://studentrocketrytoday.example/spaceport-america-cup-2026"],
                    "score": 0.34,
                },
                {
                    "name": "Houston Spaceport (TX)",
                    "description": "Urban aerospace campus at Ellington Airport; different "
                    "facility, different state.",
                    "urls": ["https://houstonspaceport.example/"],
                    "score": 0.09,
                },
            ]
        },
        "field_extraction": {
            "fields": [
                {
                    "section": "identity",
                    "field_key": "identity.display_name",
                    "item_key": None,
                    "value": "Spaceport America",
                    "citations": ["https://www.spaceportamerica.example/"],
                },
                {
                    "section": "identity",
                    "field_key": "identity.positioning",
                    "item_key": None,
                    "value": "The world's first purpose-built commercial spaceport — launch "
                    "infrastructure built ahead of demand in the New Mexico desert.",
                    "citations": [
                        "https://www.spaceportamerica.example/",
                        "https://www.spaceportamerica.example/about",
                    ],
                },
                {
                    "section": "identity",
                    "field_key": "identity.locations",
                    "item_key": None,
                    "value": ["Sierra County, New Mexico (Jornada del Muerto desert); visitor center in Truth or Consequences, NM"],
                    "citations": ["https://www.spaceportamerica.example/"],
                },
                {
                    "section": "identity",
                    "field_key": "identity.story",
                    "item_key": None,
                    "value": "Commissioned by the State of New Mexico, opened 2011, operated by "
                    "the New Mexico Spaceport Authority; record 41 licensed launch operations "
                    "this fiscal year; 62 staff across operations, range safety, fire and rescue.",
                    "citations": [
                        "https://www.spaceportamerica.example/about",
                        "https://desertlaunchwire.example/news/spaceport-america-record-year",
                    ],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "launch-operations",
                    "value": {"name": "Launch & flight-test operations", "detail": "vertical launch area + 12,000-ft spaceway, restricted airspace, range services"},
                    "citations": ["https://www.spaceportamerica.example/"],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "tenant-leasing",
                    "value": {"name": "Tenant campus leasing", "detail": "hangar and pad leases; newest tenant signed a 10-year west-campus lease"},
                    "citations": ["https://sierracountysun.example/business/spaceport-tenant-lease"],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "public-tours",
                    "value": {"name": "Public tours", "detail": "Fri-Sun from the T or C visitor center; adults $35; launch-week tours sell out"},
                    "citations": ["https://www.spaceportamerica.example/visit"],
                },
                {
                    "section": "products",
                    "field_key": "products.offers",
                    "item_key": "spaceport-america-cup",
                    "value": {"name": "Spaceport America Cup", "detail": "world's largest intercollegiate rocketry competition; 158 teams, 24 countries"},
                    "citations": ["https://studentrocketrytoday.example/spaceport-america-cup-2026"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.website",
                    "item_key": None,
                    "value": "https://www.spaceportamerica.example/",
                    "citations": ["https://www.spaceportamerica.example/"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.instagram",
                    "item_key": None,
                    "value": {"platform": "instagram", "handle": "spaceportamerica", "url": "https://instagram.example/spaceportamerica"},
                    "citations": ["https://instagram.example/spaceportamerica"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.x",
                    "item_key": None,
                    "value": {"platform": "x", "handle": "spaceportamerica", "url": "https://x.example/spaceportamerica"},
                    "citations": ["https://x.example/spaceportamerica"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "nasa",
                    "value": {"name": "NASA", "platform": "instagram", "handle": "nasa", "kind": "aspirational"},
                    "citations": ["https://instagram.example/nasa"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "spacex",
                    "value": {"name": "SpaceX", "platform": "instagram", "handle": "spacex", "kind": "aspirational"},
                    "citations": ["https://instagram.example/spacex"],
                },
                {
                    "section": "competitors",
                    "field_key": "competitors.roster",
                    "item_key": "virgingalactic",
                    "value": {"name": "Virgin Galactic", "platform": "instagram", "handle": "virgingalactic", "kind": "aspirational"},
                    "citations": ["https://instagram.example/virgingalactic"],
                },
            ],
            "failures": [],
        },
        # D11 lane-extraction payload (PROMPT_EXTRACT shape; see the
        # Turtleback block for the filtering contract). channels.youtube is a
        # fresh discovery here — Spaceport's accounts fixture has no YouTube,
        # so there is no merge collision (unlike James).
        "lane_findings": {
            "fields": [
                {
                    "section": "positioning",
                    "field_key": "positioning.wikipedia_presence",
                    "item_key": None,
                    "value": True,
                    "citations": ["https://en.wikipedia.example/wiki/Spaceport_America"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.proof_points",
                    "item_key": "wikipedia-page",
                    "value": "Documented on Wikipedia — opened 2011, operated by the New Mexico "
                    "Spaceport Authority; anchor spaceline tenant plus the Spaceport America Cup.",
                    "citations": ["https://en.wikipedia.example/wiki/Spaceport_America"],
                },
                {
                    "section": "channels",
                    "field_key": "channels.youtube",
                    "item_key": None,
                    "value": {
                        "platform": "youtube",
                        "handle": "spaceportamerica",
                        "url": "https://youtube.example/@spaceportamerica",
                        "subscribers": 12100,
                        "video_count": 96,
                    },
                    "citations": ["https://youtube.example/@spaceportamerica"],
                },
                {
                    "section": "identity",
                    "field_key": "identity.address",
                    "item_key": None,
                    "value": "Visitor center: 500 Desert Gateway Plaza, Truth or Consequences, NM 87901",
                    "citations": [
                        "https://maps.example/place/spaceport-america-visitor-center",
                        "https://www.spaceportamerica.example/visit",
                    ],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.local_reputation",
                    "item_key": None,
                    "value": {"rating": 4.4, "reviews_count": 612, "source": "maps listing"},
                    "citations": ["https://maps.example/place/spaceport-america-visitor-center"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.community_sentiment",
                    "item_key": None,
                    "value": "r/NewMexico consensus: the tour is worth it during launch weeks — "
                    "and visitors wish launch windows were announced earlier.",
                    "citations": ["https://reddit.example/r/newmexico/comments/spaceport-tour-worth-it"],
                },
                {
                    "section": "positioning",
                    "field_key": "positioning.research_synthesis",
                    "item_key": None,
                    "value": "The build-ahead-of-demand bet is maturing — a record 41 licensed "
                    "launch operations and the largest tenant commitment since the anchor "
                    "spaceline — while public visibility still leans on tours and the Cup.",
                    "citations": [
                        "https://desertlaunchwire.example/news/spaceport-america-record-year",
                        "https://sierracountysun.example/business/spaceport-tenant-lease",
                    ],
                },
            ],
            "failures": [],
        },
        "weekly_plan": {
            "brand_id": "",
            "period_start": "2026-07-06T00:00:00+00:00",
            "period_end": "2026-07-12T23:59:59+00:00",
            "rationale": "The record-year press (9 days old) is unclaimed on owned channels while "
            "peer accounts win with launch-day video; the Cup and the new tenant lease give two "
            "more evergreen proof points. Lead with operational video on Instagram and put the "
            "annual-report numbers on X where the aerospace audience lives.",
            "goals_snapshot": {
                "instagram_followers_target": 75000,
                "avg_engagement_target": 0.032,
                "tour_bookings_lift_90d": 0.2,
            },
            "items": [
                {
                    "content_type": "reel",
                    "platform": "instagram",
                    "topic": "Launch-day timelapse from the vertical launch area with the record-year stat",
                    "count": 2,
                    "format_spec": {"length_sec": 30, "aspect": "9:16", "hook": "41 launches this year. Here's what one looks like."},
                    "rationale": "Launch and landing footage is the top-performing format across "
                    "all three tracked space accounts; the record-year press gives the numbers.",
                    "evidence": [
                        {"url": "https://instagram.example/spacex", "ref": "", "note": "launch footage carries peer engagement"},
                        {"url": "https://desertlaunchwire.example/news/spaceport-america-record-year", "ref": "", "note": "record-year press"},
                    ],
                    "predicted_metrics": {"views": 52000, "likes": 2100, "comments": 90, "engagement_rate": 0.038},
                },
                {
                    "content_type": "image",
                    "platform": "instagram",
                    "topic": "Spaceport America Cup countdown: 158 teams, 24 countries",
                    "count": 2,
                    "format_spec": {"aspect": "4:5", "carousel": True},
                    "rationale": "Cup content brings the student-rocketry audience and fills "
                    "Sierra County hotels; strongest annual traffic driver.",
                    "evidence": [
                        {"url": "https://studentrocketrytoday.example/spaceport-america-cup-2026", "ref": "", "note": "Cup press"}
                    ],
                    "predicted_metrics": {"likes": 1700, "comments": 60, "engagement_rate": 0.031},
                },
                {
                    "content_type": "text_post",
                    "platform": "x",
                    "topic": "Annual report highlights: a record 41 launch operations this fiscal year",
                    "count": 2,
                    "format_spec": {"format": "numbered thread", "cta": "annual report link"},
                    "rationale": "The aerospace-industry audience on X converts report numbers "
                    "into earned mentions; tenant-pipeline value.",
                    "evidence": [
                        {"url": "https://desertlaunchwire.example/news/spaceport-america-record-year", "ref": "", "note": "record year"}
                    ],
                    "predicted_metrics": {"impressions": 38000, "reposts": 120, "replies": 40},
                },
                {
                    "content_type": "image",
                    "platform": "instagram",
                    "topic": "Weekend tour CTA: the Gateway to Space terminal up close",
                    "count": 1,
                    "format_spec": {"aspect": "1:1"},
                    "rationale": "Tour posts are the direct revenue lever; launch-week tours sell "
                    "out, so the CTA rides the record-year attention.",
                    "evidence": [
                        {"url": "https://www.spaceportamerica.example/visit", "ref": "", "note": "tour product page"}
                    ],
                    "predicted_metrics": {"likes": 1400, "comments": 45, "link_clicks": 260},
                },
            ],
        },
        "morning_brief": {
            "brand_id": "",
            "date": ANCHOR_ISO,
            "headline": "The record year is your story to tell this week — peers are telling theirs with launch video",
            "sections": [
                {
                    "title": "Press",
                    "body": "The record-year story (9 days) and the west-campus tenant lease (4 "
                    "weeks) have minimal owned-channel coverage. Both are institutional proof "
                    "points: one for the public audience, one for the tenant pipeline.",
                    "citations": [
                        "https://desertlaunchwire.example/news/spaceport-america-record-year",
                        "https://sierracountysun.example/business/spaceport-tenant-lease",
                    ],
                },
                {
                    "title": "Peers",
                    "body": "SpaceX's launch reels and Virgin Galactic's flight-day cinematics "
                    "carried the week; NASA's cadence stays near 6/week. Operational footage at "
                    "your scale is shootable with the range cameras you already run.",
                    "citations": [
                        "https://instagram.example/spacex",
                        "https://instagram.example/virgingalactic",
                    ],
                },
                {
                    "title": "Own baseline",
                    "body": "IG engagement holds at 2.8% on roughly one post a week — the "
                    "constraint is volume. X does the industry work at 1.4% with the range "
                    "updates audience.",
                    "citations": ["https://instagram.example/spaceportamerica"],
                },
            ],
            "recommended_actions": [
                "Cut the launch-day timelapse from existing range footage while the record-year story is warm.",
                "Schedule the Cup countdown carousel for the weekend traffic window.",
                "Draft the annual-report thread for X and tag the tenant announcement.",
            ],
        },
        "peer_digest": {
            "brand_id": "",
            "period": "2026-06-29/2026-07-05",
            "peers": [
                {"handle": "nasa", "kind": "aspirational", "platform": "instagram", "followers": 97200000,
                 "cadence_per_week": 5.8, "avg_engagement_rate": 0.006,
                 "top_topics": ["missions", "earth imagery", "launch coverage"]},
                {"handle": "spacex", "kind": "aspirational", "platform": "instagram", "followers": 36400000,
                 "cadence_per_week": 3.4, "avg_engagement_rate": 0.011,
                 "top_topics": ["launches", "landings", "vehicle tests"]},
                {"handle": "virgingalactic", "kind": "aspirational", "platform": "instagram", "followers": 792000,
                 "cadence_per_week": 2.6, "avg_engagement_rate": 0.018,
                 "top_topics": ["flight days", "astronaut stories", "spaceport life"]},
            ],
            "benchmarks": {
                "cadence_per_week": 3.4,
                "avg_engagement_rate": 0.011,
                "formats": {"reel": 0.55, "image": 0.35, "text": 0.10},
            },
            "observations": [
                "All three tracked accounts led the week with launch or flight-day video; "
                "Spaceport America posted none [instagram.example/spacex].",
                "Virgin Galactic — flying from this facility — posts 2.4x Spaceport America's "
                "cadence and mentions the spaceport in a third of its captions "
                "[instagram.example/virgingalactic].",
                "Institutional accounts at scale sustain 3-6 posts/week; the own account runs "
                "1.1 [instagram.example/spaceportamerica].",
            ],
        },
        "goals": {
            "goals": [
                {"section": "goals", "platform": "instagram", "metric": "followers", "baseline": 58400,
                 "target": 75000, "timeframe_days": 90, "priority": 0.3,
                 "rationale": "Launch-video cadence at 3/week puts 75k in range on current growth."},
                {"section": "goals", "platform": "x", "metric": "followers", "baseline": 41200,
                 "target": 50000, "timeframe_days": 90, "priority": 0.2,
                 "rationale": "Industry audience compounds off report threads and mission updates."},
                {"section": "goals", "platform": None, "metric": "tour_bookings", "baseline": 100,
                 "target": 120, "timeframe_days": 90, "priority": 0.5,
                 "rationale": "Business goal: +20% tour bookings, indexed to 100 = current weekly average."},
            ],
            "narrative": "Institutional accounts that grew through a milestone year did it by "
            "packaging operations as content: launch windows, range life, and visitor access. "
            "Three posts a week for 90 days puts 75k Instagram followers and a 20% tour lift in "
            "reach.",
        },
        "press_events": {
            "events": [
                {
                    "title": "Record 41 launch operations in a fiscal year",
                    "kind": "credibility_signal",
                    "content_worthy": True,
                    "summary": "The busiest year since opening — the institutional proof point "
                    "for tenants and the public alike.",
                    "url": "https://desertlaunchwire.example/news/spaceport-america-record-year",
                    "suggested_angle": "Launch-day timelapse with the stat; annual-report thread on X.",
                },
                {
                    "title": "10-year west-campus tenant lease signed",
                    "kind": "credibility_signal",
                    "content_worthy": True,
                    "summary": "Largest tenant commitment since the anchor spaceline; pipeline "
                    "signal for the aerospace audience.",
                    "url": "https://sierracountysun.example/business/spaceport-tenant-lease",
                    "suggested_angle": "Welcome post + facility tour of the west campus.",
                },
            ]
        },
        # reviewer judge contract: {"score": float 0-1, "notes": str}
        "review_judge": {
            "score": 0.83,
            "notes": "Matches the account's register: operational detail first (pad status, "
            "wind holds, lease terms), quiet civic pride, no exclamation marks. Consistent "
            "with the exemplar corpus.",
        },
        "draft_post": "Launch window opens Friday at 0900 MT. If you're anywhere near Truth or "
        "Consequences, the viewing area at the visitor center is free and the desert does the "
        "rest. Come watch the range earn its record year.",
        "draft_templates": (
            "{topic}. The range crew has the window, the weather is cooperating, and the "
            "desert is ready to show off. Viewing details and times at the link.",
            "{topic}. Out here the infrastructure comes first and the missions follow; that's "
            "the whole idea. See it up close on a weekend tour from the T or C visitor center.",
            "{topic}. 18,000 acres of desert, a 12,000-foot runway, and a range schedule that "
            "keeps filling up. The full story is on the site.",
        ),
        "revise_templates": (
            "{topic}. Simpler cut: what's launching, when to watch, and where to stand. "
            "Everything else is on the site.",
            "{topic}. Take two from the range office: the dates, the access points, and the "
            "one thing visitors always ask about. Link below.",
        ),
        "brief_text": "Morning brief, Spaceport America: the record-year story is still warm "
        "and unclaimed on your channels; peers won the week with launch video. Cut the "
        "timelapse, ship the Cup countdown, and put the annual-report numbers on X.",
        "plan_narrative": "This week: 2 launch-day reels, 2 Cup countdown statics, 2 "
        "annual-report threads on X, 1 tour CTA — because every tracked peer wins with "
        "operational video and your record year is the proof point they can't copy.",
    },
}


# =====================================================================
# Generic fallbacks — unknown queries/handles must stay coherent
# =====================================================================

GENERIC: dict = {
    "slug": "mock-brand",
    "name": "Mock Brand",
    "entity_type": "company",
    "transcript": (
        "[Synthetic demo transcript] Speaker: 'Thanks for having me. The short version is we "
        "focus on doing one thing well, we listen to the people who show up every week, and we "
        "publish what we learn. The numbers follow the habit, not the other way around.'"
    ),
    # D11 deep-lane fallback: the mock provider stamps deterministic citation
    # urls derived from the question slug (mocks.MockDeepResearchProvider).
    "deep": {
        "synthesis": "Public sourcing for this brand is thin but consistent: an "
        "established operation with a modest web footprint, no major press in the last "
        "quarter, and steady rather than campaign-driven publishing. Nothing found "
        "contradicts the seed information provided at onboarding.",
    },
    "llm": {
        # name/description/urls are re-stamped from the seed in the prompt by
        # mocks._json_entity_candidates so unknown brands get a plausible,
        # correctly named candidate instead of internal placeholder jargon.
        "entity_candidates": {
            "candidates": [
                {
                    "name": "Closest public-web match",
                    "description": "Best available match assembled from public search results; "
                    "confirm the sources below to continue.",
                    "urls": ["https://www.example.com/"],
                    "score": 0.55,
                }
            ]
        },
        # citations/display_name/positioning are re-stamped from the prompt
        # material by mocks._json_field_extraction so generic fields survive
        # the researcher's cite-only-input-URLs validation.
        "field_extraction": {
            "fields": [
                {
                    "section": "identity",
                    "field_key": "identity.display_name",
                    "item_key": None,
                    "value": "Mock Brand",
                    "citations": ["https://www.example.com/"],
                },
                {
                    "section": "identity",
                    "field_key": "identity.positioning",
                    "item_key": None,
                    "value": "An established name in its field with a growing public presence; "
                    "a sharper positioning line is pending source confirmation.",
                    "citations": ["https://www.example.com/"],
                },
            ],
            "failures": ["limited public sources for this brand; extraction is partial"],
        },
        # D11 lane-extraction fallback: citations are re-stamped from the
        # prompt material by mocks._json_lane_findings so the field survives
        # the researcher's cite-only-input-URLs validation.
        "lane_findings": {
            "fields": [
                {
                    "section": "positioning",
                    "field_key": "positioning.research_synthesis",
                    "item_key": None,
                    "value": "Public sourcing for this brand is thin but consistent: an "
                    "established operation with a modest web footprint and steady, "
                    "non-campaign publishing; nothing found contradicts the onboarding seed.",
                    "citations": ["https://www.example.com/"],
                }
            ],
            "failures": ["lane material was thin for this brand; synthesis is provisional"],
        },
        "weekly_plan": {
            "brand_id": "",
            "period_start": "2026-07-06T00:00:00+00:00",
            "period_end": "2026-07-12T23:59:59+00:00",
            "rationale": "Baseline week while research and audit finish filling the profile: one "
            "short video and two statics hold cadence without over-committing before benchmarks land.",
            "goals_snapshot": {},
            "items": [
                {
                    "content_type": "reel",
                    "platform": "instagram",
                    "topic": "Behind-the-scenes short introducing the team",
                    "count": 1,
                    "format_spec": {"length_sec": 20, "aspect": "9:16"},
                    "rationale": "Video baseline while profile research completes.",
                    "evidence": [{"url": "https://www.example.com/", "ref": "", "note": "baseline recommendation"}],
                    "predicted_metrics": {"views": 800, "likes": 40, "comments": 3},
                },
                {
                    "content_type": "image",
                    "platform": "instagram",
                    "topic": "Product/offer highlight with a single CTA",
                    "count": 2,
                    "format_spec": {"aspect": "4:5"},
                    "rationale": "Static baseline while profile research completes.",
                    "evidence": [{"url": "https://www.example.com/", "ref": "", "note": "baseline recommendation"}],
                    "predicted_metrics": {"likes": 35, "comments": 2, "engagement_rate": 0.03},
                },
            ],
        },
        "morning_brief": {
            "brand_id": "",
            "date": ANCHOR_ISO,
            "headline": "Profile is still filling in — hold a steady baseline cadence this week",
            "sections": [
                {
                    "title": "Status",
                    "body": "Research and audit are still populating this brand's profile. Until "
                    "benchmarks land, hold the baseline cadence and finish onboarding.",
                    "citations": [],
                }
            ],
            "recommended_actions": ["Complete onboarding so research and audit can populate the profile."],
        },
        "peer_digest": {
            "brand_id": "",
            "period": "2026-06-29/2026-07-05",
            "peers": [],
            "benchmarks": {"cadence_per_week": 3.0, "avg_engagement_rate": 0.03, "formats": {"reel": 0.5, "image": 0.5}},
            "observations": ["No tracked peers for this brand yet; benchmarks shown are conservative defaults."],
        },
        "goals": {
            "goals": [
                {"section": "goals", "platform": "instagram", "metric": "followers", "baseline": 1000,
                 "target": 2000, "timeframe_days": 90, "priority": 0.5,
                 "rationale": "Starter target pending baseline and peer benchmarks."}
            ],
            "narrative": "Starter negotiation: double the smallest channel in 90 days at a "
            "sustainable cadence; renegotiate once baseline and peer benchmarks exist.",
        },
        "press_events": {"events": []},
        # reviewer judge contract: {"score": float 0-1, "notes": str}
        "review_judge": {
            "score": 0.7,
            "notes": "Register is neutral and professional with no obvious tells; exemplar "
            "coverage for this brand is thin, so the score is conservative.",
        },
        "draft_post": "Small update from the workshop this week: one thing shipped, one thing "
        "learned, one thing coming next. More Friday.",
        "draft_templates": (
            "{topic}. We said we'd keep you posted, so here it is: the work is done and the "
            "date is set. All the details at the link.",
            "{topic}. Everything you actually need to know fits in one post, and this is it. "
            "Questions welcome in the comments.",
            "{topic}. We kept the plan simple on purpose. What ships this week speaks for "
            "itself; come take a look.",
        ),
        "revise_templates": (
            "{topic}. Plainer take: here's what's happening, when it happens, and how to be "
            "part of it. Details at the link.",
            "{topic}. Second pass, shorter: the essentials only, straight from the team. More "
            "on the page.",
        ),
        "brief_text": "Morning brief: the profile is still filling in. Hold the baseline "
        "cadence and finish onboarding to unlock researched recommendations.",
        "plan_narrative": "This week: one short video and two statics to hold the baseline "
        "while the profile fills.",
    },
}

BRANDS: dict[str, dict] = {
    "turtleback": TURTLEBACK,
    "james-prendamano": JAMES,
    "spaceport-america": SPACEPORT,
}
