# B-roll Generation Playbook

**Source:** shot-by-shot reverse-engineering of 4 "Agent Opus" reference reels
(housing market, AI-as-friend, term-limits/politics, education-in-AI-era),
2026-07. Transcribed (faster-whisper) + frames sampled every 1.5s and read
visually. This is the reference standard our cinematic b-roll should hit.

Most of this is now **encoded in the prompts** in `src/james_os/story_video.py`
(`_CINEMATIC_TREATMENT_SYSTEM` and the cinematic branch of `_insert_pick_system`).
This doc is the human-readable "why" behind those rules + the open gaps.

---

## 1. The doctrine (what defines the style)

- **100% generated b-roll under a voiceover.** No talking head ever appears in
  the references — the monologue plays continuously over a fully constructed
  film. (JAMES OS deliberately diverges: real James talking-head base + ~30%
  cutaways. A full-faceless mode is an *optional* future style, §3.7.)
- **One world, one grade, whole film.** Every shot lives in a single continuous
  universe; cuts between unrelated props never break the mood because location,
  lighting logic and palette are locked.
- **The metaphor in the sentence dictates the object in the frame.** Turn each
  key noun/verb into ONE literal physical prop and shoot it — the most literal
  object that embodies the phrase, not a mood shot of the topic.

## 2. Shot categories & mix (per ~60s / ~24 shots)

| Category | Share | What it is |
|---|---|---|
| character | 35–45% | the recurring figure or an archetype, lit by the scene's practical light |
| symbolic_prop | 25–35% | a noun rendered as a literal object (coin on edge, brass scale, gavel, ballot box, burning typewriter) |
| abstract_motion | 10–15% | connective tissue — motion-blur smears, a wandering luminous trace, particle bursts — used *between* ideas |
| data_viz | ~10% | the topic's numbers as an in-world graph whose SHAPE animates the words |
| environment | 5–10% | empty negative-space beats (bare corridor, vacant classroom) |
| text_as_prop | 5–10% | hard numbers/words rendered as objects in the scene |

## 3. Pacing & cadence

- **~2.5s average shot** (politics/education ~2.4s, housing ~3.0s). A cut lands
  on each new clause/idea.
- **Dwell on the money moment; cut fast on transitions.** Hold a key concept
  across 3–4 near-identical frames with only a slow push-in — and *progress the
  same subject through stages* (smoulder → flame → ash; stamp presses → lifts →
  reveals PARDON) rather than showing a hero visual once.
- **Motion-blur smears cover the cut** between two distinct ideas.

## 4. Rule-of-three, extended

- Rotate wide → medium → macro; never two consecutive same sizes; state the
  shot size explicitly in every prompt.
- **Escalate by tightening on the SAME subject** at emotional peaks: medium →
  close → extreme macro of a single eye (reserved for the turning line).
- **Scale humans DOWN against the data** for awe/dread: a tiny lone silhouette
  dwarfed by a giant screen or wall-sized glowing number.

## 5. Recurring-character convention (the hard part)

- **Lock ONE generated person and return to them** — same face, wardrobe, hair
  every appearance. Bookend with a portrait of that figure (open, peak, close).
- **Faceless bodies carry abstractions; the identifiable avatar carries the
  personal argument.** When a person is needed but identity shouldn't persist,
  return to a *type* (trader / analyst / hooded figure), not a continuous face.

## 6. Symbolic-prop lexicon (metaphor → object)

two sides of the coin → coin on edge, macro · weighing/demand → brass two-pan
scale w/ glowing cube · countercyclical → glowing Newton's cradle · not a
straight line → a wandering luminous trace · hits a floor & reverses → particle
line-graph dipping then rocketing · keep the lights on → bare desk lamp over
unpaid bills · decades come off the calendar → calendar pages peeling off a wall
· backroom deal → two silhouettes shaking hands in a smoky corridor · justice →
gavel in a shaft of light · the people you elect → lone VOTE ballot box in a
spotlight · a pardon/a pass → rubber stamp hitting a document, red PARDON ·
jobs won't exist → floating obsolete tools (typewriter, briefcase, CRT) catching
fire → ash · broken system → empty classroom / rows of unused desks · spark of
inspiration → extreme-macro eye, iris igniting electric blue.

_Every surface reflective; every light source practical-in-frame._

## 7. Text-as-prop (the ONLY on-screen text)

- **Zero burned-in captions/subtitles/lower-thirds.** All text is diegetic —
  part of an object (neon wall "6.3%", stamped "PARDON", "VOTE" on a ballot box,
  a HUD "HOUSING SPIKE / BASE").
- **Words that MUST read are short, UPPERCASE, on a clean high-contrast surface**
  so the model renders them reliably. Incidental text (code streams, background
  headlines) may stay garbled and read only as texture.

## 8. Grade & lighting

- One locked grade: crushed blacks, cool desaturated teal/cyan/blue midtones,
  heavy chiaroscuro from a **single hard source**, volumetric haze, shallow DOF,
  film grain.
- **Two-color emotional grammar:** cold teal = problem/data/fear; **one warm
  gold key light is RESERVED for the payoff beat.** Keep ~90% cold so the one
  warm beat lands. End on the thesis image, warmly lit.

---

## Capability status (vs JAMES OS)

**Encoded now (prompt):** storyboard treatment (world/figure/motif/palette/arc),
prop lexicon, figure-policy (locked vs archetype), payoff-beat warm-key
exception, text-as-prop reliability, macro-eye escalation, scale-down-vs-data,
hold-&-evolve, motion-smear connective beats, rule-of-three. Plus a plumbed
`uses_recurring_figure` insert flag.

**Open gaps (real engineering, NOT prompt):**
1. **Non-hero character consistency** — the biggest reason the references read
   as one film. Our per-shot gpt-image-1 calls don't lock a face across shots.
   Options: (a) at storyboard time mint a Soul/seed for the treatment's
   recurring figure and route every `uses_recurring_figure` insert through the
   same identity path `uses_hero` uses today (the elegant brand win: recurring
   figure = **James via Soul**); (b) generate the figure once and pass it as an
   img2img/edit reference to every later figure insert. The `uses_recurring_figure`
   flag is already plumbed for this.
2. **Full-faceless mode** (`broll_style="cinematic_faceless"`) — drop the
   talking-head base, drive the whole timeline off b-roll slots synced to the
   transcript audio at ~2.5s cadence. Optional style, not the default (James on
   camera is the differentiator).
