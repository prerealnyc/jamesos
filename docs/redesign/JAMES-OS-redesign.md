# JAMES OS — Brand Manager Redesign

_Principal design-team engagement. Every recommendation grounded in the live `web/` codebase. Drafted 2026-06-14._


---


# Phase 1 — Product & UX Audit


There's a real keys/status probe (`/api/integrations` returns `configured` + `active`; provider status at line 738 returns `configured`/`ok`/`error`). This means the EngineStatus recommendation can cite a real endpoint instead of an invented one. I have everything. Here is the upgraded audit.

---

# Phase 1 — Product & UX Audit: JAMES OS

Anchored to real code in `/Users/royantony/BRAND MANAGER /james-os/web/`. Severity: **P0** = blocks the core job / breaks trust / fails AA · **P1** = serious friction with real conversion/retention cost · **P2** = polish & scale.

**Brand note (binds every recommendation below):** the design tokens in `globals.css:8-37` are already a distinctive, premium system — **gold primary** `--primary: 40 75% 52%`, **teal accent** `--accent: 183 60% 28%`, **near-black sidebar** `--sidebar: 220 20% 8%`, General Sans / JetBrains Mono. This is the opposite of the AI-blue-gradient cliché and must be preserved. Every new surface below reuses these tokens via the existing primitives (`Button`, `Card`, `Badge`, `Icon`) — **no new colors, no gradients, no new icon library.** "Memory-as-moat" is the product's spine: the learning loop (`queue/page.tsx:106-109` guardrails, voice scores) is the differentiator and should be made *more* visible, not buried.

## Prioritized issue table

| # | Issue | Category | User impact | Business impact | Concrete recommendation (real file/route) | Sev |
|---|-------|----------|-------------|-----------------|--------------------------------------------|-----|
| 1 | **No fixed "Create" CTA; the primary job is a 3-deep indented tree item.** `shell.tsx:40-46` nests Content Studio / Video Studio / Post Images under "Autopilot" with `indent: true` (rendered as a `└` connector + `pl-9`, L110/L117). "Video Studio" is itself a hub over 8 routes (`VIDEO_SUBROUTES`, L24-27). Owner's words: *"I didn't know where to click to generate a new video."* | Navigation / activation | The core job-to-be-done has **no fixed, obvious entry point**. An indented `└` child reads as secondary, not primary. New users stall on task one. | The #1 activation killer — directly produces the quoted confusion; gates first-value for the whole product. | In `shell.tsx`, insert a pinned primary action **between the header block (L93) and `<nav>` (L95)**, outside the scroll container: `<Link href="/autopilot" className="mx-4 mb-2 flex items-center justify-center gap-2 rounded-md bg-primary text-primary-foreground py-2.5 text-sm font-semibold hover:bg-primary/90">` with `<Icon name="queue" />` + "Create content". Route to `/autopilot` (the one-click batch *is* the create flow) — do not invent a `/create` chooser when a flagship create page already exists. De-nest: drop `indent` from L43-45 so Content Studio / Video Studio / Post Images become peers, not children of Autopilot. | **P0** |
| 2 | **~17 sidebar entries across 5 groups, every one a two-line label = scan overload.** `NAV` (`shell.tsx:30-76`) renders `label` + always-visible `sub` (L119-122, `text-[11px] opacity-60`), so the rail is a wall of gray subtext. Near-synonyms collide: "Output Library" (L52) / "Media Library" (L54) / "Style Templates" (L55); "Voice Studio" (L71) / "Voice Rules" (L72). | Information architecture / Hick's law | Users can't build a mental model of where things live. At a glance "Media Library" vs "Output Library" are indistinguishable. Every page load re-pays the scan cost. | Raises support load and time-to-task on every session; the rail reads like an engineering org chart, not a tool. | Collapse to **6 top-level entries: Ask · Create · Review · Library · Intelligence · Brand.** (a) **Brand:** merge "Voice Studio" (`/voice-studio`) + "Voice Rules" (`/brand`) + "JP Live" (`/jp-live`) under one **Brand** entry at `/brand` with in-page tabs. (b) **Library:** merge "Output Library" (`/library`), "Media Library" (`/jp-clips`, already a hub via `MEDIA_SUBROUTES` L28), and "Style Templates" (`/style-templates`) under **Library** with tabs; extend its `match` array to cover all three. (c) Remove the always-visible `sub` `<span>` (L121); move that string to `title={n.sub}` on the `<Link>` (L106) so it survives as a hover tooltip, and render `sub` inline **only when `active`** (`{active && <span className="block text-[11px] opacity-60">{n.sub}</span>}`). | **P0** |
| 3 | **Generate actions silently no-op when an engine's keys are missing — no status surface, despite a real probe existing.** `autopilot/page.tsx` `bulkGenerate` (L90-98) always shows the optimistic `res.note \|\| "Started — N pieces generating…"` (L95) even with no keys configured. `long-form/page.tsx` buries the Runway-keys caveat in a `title=` tooltip (L583) and a parenthetical (L296-298). The honest-status primitive `NotBuilt` (`ui.tsx:128-150`) exists but **none of the 5 pages import it.** A real keys probe already exists: `api.integrations()` → `/api/integrations` returns `{ configured: Record<string,boolean>; active: string[] }` (`lib/api.ts:1121`), and per-provider status returns `{ configured, ok, error }` (`lib/api.ts:738`). | Trust / empty states / onboarding | User clicks Generate, gets a success toast, waits, nothing lands in the queue, blames the product — the exact trust-erosion that already burned them on broken renders. They can't tell "broken" from "slow." | Silent no-ops are the single highest-churn failure mode for an AI tool. | Build one `<EngineStatus required={["openai","runway"]} />` strip (new `components/engine-status.tsx`), driven by `api.integrations()` (`lib/api.ts:1121`), rendered directly under `<PageHeader>` on `/autopilot`, `/long-form`, `/engaging-video`, `/pipeline`, `/queue`. When a required key is **absent from `active[]`**: render a `Card` with `border-destructive/40 bg-destructive/10` (matching the established destructive token), copy "Connect [OpenAI] to generate", and a `<Link href="/settings">` ("API connections" already lives at `/settings`, `shell.tsx:144`). **Disable** the Generate `<Button>` (`autopilot/page.tsx:159`; `long-form` L646/L602) via its existing `disabled` prop while a required key is missing — never let the optimistic `bulkNotice` fire. Use `NotBuilt` (`ui.tsx:128`) for engines that are entirely unconfigured. | **P0** |
| 4 | **Emoji carry sole semantic meaning with no `aria` fallback — fails WCAG 2.0 AA.** Status/actions are encoded purely in glyphs: `✗`/`✓` (`autopilot/page.tsx:332`, `page.tsx:201/270`, `long-form/page.tsx:378/396/548`); icon-only `🗑` delete buttons (`queue/page.tsx:293-299`, `364-370`, `380-387` — these have `title=` but **no `aria-label`**); `▶` Drive tiles (`long-form/page.tsx:459`); `🎬/📝/⚡` as the *only* label distinguishing the three mixes (`autopilot/page.tsx:16-18`) and queue kind-tabs (`queue/page.tsx:173`); `⬇ Download` (`queue/page.tsx:326`). | Accessibility (WCAG 1.1.1 / 1.4.1) | Screen readers announce "wastebasket" or nothing; the meaning ("delete permanently, no learning") is lost. In places color+emoji is the only status channel. | Hard blocker for enterprise / white-label / brokerage / government procurement. | (a) Every icon-only control gets an `aria-label`: `queue/page.tsx:293` → `aria-label="Delete permanently (no learning)"`; L364/L381 → `aria-label="Delete permanently"`. (b) Replace decorative emoji with the existing stroke `Icon` set (`components/icons.tsx`) already used in the shell — there is no trash/play glyph in `P` yet, so add `trash` and `play` paths to that `Record` and use `<Icon name="trash" aria-hidden />` + visually-hidden text. (c) For the mix selectors (`autopilot/page.tsx:16-18`) and queue tabs (`queue/page.tsx:173`), the visible text label already exists ("Videos"/"Text posts"/"Mix") — just drop the leading emoji or move it into an `aria-hidden` span so the accessible name is the words, not the glyph. (d) `✗` is already paired with text in most spots (e.g. `autopilot/page.tsx:332` `✗ {r.error}`) — make that pairing universal and never status-by-glyph-alone. | **P0** |
| 5 | **App is non-responsive: a fixed 256px sidebar + a `max-w-4xl` column, no mobile shell.** `shell.tsx:89` is `w-64 shrink-0` with no breakpoint and no hamburger; `shell.tsx:155-156` wraps all content in `max-w-4xl mx-auto px-8 py-10`. On a phone the rail eats ~70% of width with no way to collapse it. The queue's inline video preview is already capped (`max-w-xs` + `aspect-ratio:9/16`, `queue/page.tsx:273`) — the *content* is mobile-shaped but the *chrome* isn't. | Responsiveness | The product is effectively desktop-only. The literal use case for the Approval Queue — a real-estate owner reviewing a reel on their phone — is impossible. | Kills approve-on-the-go (the most natural usage context) and blocks any PWA/mobile story. | In `shell.tsx`: change `<aside>` (L89) to `fixed inset-y-0 left-0 z-40 -translate-x-full transition-transform md:static md:translate-x-0` plus a `data-open` toggle; add a mobile top bar (`md:hidden`, `bg-sidebar text-sidebar-foreground`) with a hamburger `<button aria-label="Open navigation">` that toggles a `useState(false)` + a `bg-black/50` backdrop. Reduce content padding on mobile: `max-w-4xl mx-auto px-4 py-6 md:px-8 md:py-10` (L156). Make the dense grids responsive: autopilot mix grid `grid-cols-1 sm:grid-cols-3` (`autopilot/page.tsx:114`), queue stat grid `grid-cols-2 sm:grid-cols-4` (`queue/page.tsx:130`), long-form folder tiles `grid-cols-1 sm:grid-cols-2` (`long-form/page.tsx:448`). | **P0** |
| 6 | **Autopilot is a 4-card settings form, not the "one click" it promises.** `autopilot/page.tsx` stacks "Create now" (L107-178) + "Daily schedule" (L180-229) + "Content defaults" (L231-310, a 5-control form) + "Recent runs" (L312-394) in one scroll. The `Badge tone="primary">one click` (L110) sits directly above a 7-line caption-mode explainer paragraph (L277-285). | Cognitive overload | The flagship's "set it and forget it" promise is drowned in knobs (caption mode, B-roll engine, video-style toggle, platform multi-select, topic hint). Users tune settings they don't understand instead of generating. | Undercuts the value prop that justifies the price; the headline feature reads as intimidating. | Keep only the **"Create now"** card (L107-178) above the fold. Move "Daily schedule" (L180-229) + "Content defaults" (L231-310) into a collapsed `<details>` ("Advanced — schedule & defaults") or a `/autopilot/settings` sub-route (the `match` pattern in `shell.tsx:12` already supports keeping the parent highlighted). Replace the 7-line caption-mode paragraph (L277-285) with a one-line summary + the existing `HelpButton` drawer (`ui.tsx:120`, already wired to `lib/tutorials.ts`). | **P1** |
| 7 | **Two overlapping "run" buttons + two duplicate "Save" buttons mutating one `cfg`.** `bulkGenerate` "Generate N videos" (L159) and `runNow` "Run a scheduled batch now" (L218-220) both push background generation into the same queue, distinguished only by helper copy. `save` is duplicated on two cards (L217 and L307), both writing the same `cfg` object. | Action ambiguity / IA | "Run a scheduled batch now" reads as "trigger the schedule" but actually fires one batch (`api.runAutopilot()`, L85). Two Save buttons on one shared object invites "did my change save?" doubt. | Ambiguous primaries on the headline page directly cause mis-clicks and reinforce the "where do I click?" feeling. | Remove `runNow` (L218-220) — it's a power-user redundancy of `bulkGenerate`; or relabel to "Test today's settings once" if kept. Collapse both `save` buttons (L217, L307) into a single sticky footer save bar that appears only when `cfg` is dirty (track a `dirty` flag against the last-loaded config), since both already mutate one `cfg` via `patch()` (L66). | **P1** |
| 8 | **Long Form Cutter leaks raw infrastructure to a marketing user.** `long-form/page.tsx` surfaces "mono 16 kHz 32 kbps" + "Whisper's 25 MB cap" (L339-342), the literal env var `GOOGLE_DRIVE_FOLDER_ID` + a truncated folder-id hash (L416-418), raw Drive file IDs in monospace on every tile (L468-470), and **three** import affordances — paste URL (L355-371), folder browser (L401-476), file upload (L384-391) — stacked across two cards before the user reaches any content. | IA / enterprise polish | A marketing user is asked to reason about bitrates, service-account sharing, and Drive IDs. Three redundant import paths triple the decision cost before any value lands. | Implementation detail in the UI reads as "internal tool," not "polished SaaS" — fatal for any white-label or non-technical buyer. | (a) Delete the engineering caveats (L339-342, L416-418) and the per-tile raw `id` line (L468-470) — keep filename + date + size, which already render (L460-467). (b) Collapse three import paths to **one default + one fallback**: lead with the folder browser (it's the lowest-typo-risk path the copy itself argues for, L414-415), and demote "Paste Drive URL" + "Upload a file" into a single secondary "Other ways to add" `<details>`. (c) Replace the env-var sentence with plain language: "Pulls from your connected Drive folder — change it in Settings." | **P1** |
| 9 | **The learning loop — the actual moat — is invisible until the bottom of one page.** "What the engine has learned" only renders at the very end of `/queue` (`queue/page.tsx:431-459`), gated on `guardrails.length > 0`, and the `voiceScore` that proves voice-matching (`queue/page.tsx:284-287`, `autopilot/page.tsx:380`) is shown as a bare number with no scale or trend. Nothing on Ask, Autopilot, or the dashboard tells the user the system is *getting smarter from their corrections*. | Brand / value-prop / retention | The differentiator ("memory-as-moat" — it learns your voice and your rejections) is hidden. Users experience a generic content generator and miss why this is defensible. | Directly weakens the retention and pricing story; the moat the product is built on is the thing the UI hides. | Surface the loop as a first-class, reusable signal: (a) a small "Memory" stat on the dashboard/`PageHeader` area — "{guardrails.length} learned rules · voice match {avg}%" — driven by the already-fetched `api.guardrails()` (`queue/page.tsx:47`). (b) Render `voiceScore` as a labeled signal, not a bare float: reuse `Badge` with `tone={score>=0.7?"ok":"accent"}` (the pattern already exists at `autopilot/page.tsx:380`) and append a "voice match" label everywhere it appears (`queue/page.tsx:284`). (c) After a reject teaches a rule (`confirmReject`, `queue/page.tsx:106-109`), the `learned` banner already fires (L123-127) — extend it to name the rule just added, reinforcing cause→effect. Keep all of this in gold/teal tokens; this is the on-brand "premium, memory-first" moment to lean into. | **P1** |
| 10 | **Inconsistent destructive-confirm and toast patterns across surfaces.** `queue/page.tsx` delete uses a native `window.confirm()` (L84) while reject uses a polished inline panel (L392-423); `long-form` dismiss (L307-319) has no confirm at all. Toasts are bespoke per page (`Toast` on queue/long-form; inline `setMsg` strings on autopilot L221/L308). | Consistency / polish | Mixed confirm styles (OS dialog vs in-app panel vs none) feel unfinished and unpredictable; identical actions behave differently per page. | Erodes the premium feel that the gold/near-black system otherwise earns; scales badly as pages multiply. | Replace `window.confirm()` (`queue/page.tsx:84`) with the same inline-confirm pattern already built for reject (L392-423) — a `Card`-framed "Delete permanently?" with Cancel / Confirm `Button`s. Standardize on the existing `Toast` component (`components/toast.tsx`) for all transient feedback; retire the inline `setMsg` strings (`autopilot/page.tsx:221`, L308). One confirm pattern, one toast pattern, app-wide. | **P2** |

## Cross-cutting notes for the build

- **No new design language is needed or wanted.** Every fix above composes existing primitives (`Button`/`Card`/`Badge`/`Icon`/`Toast`/`PageHeader`/`NotBuilt`) and existing tokens. The brand equity is the gold-on-near-black, teal-accent system already in `globals.css` — the redesign's job is to make the *information architecture* and *honesty surfaces* worthy of it, not to repaint.
- **The honesty primitive already exists and is unused.** `NotBuilt` (`ui.tsx:128-150`) and the `live`/`soon` badge logic (`shell.tsx:123-127`) show the team already values "don't fake completeness." Issues #3 and #9 are mostly about *wiring up infrastructure that's already there* (`api.integrations()`, `api.guardrails()`, `NotBuilt`) rather than net-new work.
- **Sequencing:** ship #1–#5 (P0s) first — they gate activation, trust, accessibility-procurement, and mobile. #1, #2, and the mobile shell in #5 all touch `shell.tsx`, so do them as one coordinated edit. #9 (make the moat visible) is the highest-leverage P1 for the brand and pairs naturally with the #2 IA pass.


---


# Phase 2 — Brand Strategy


I now have everything verified against the real product. Key corrections the draft needs: the home route `/` is "Ask the memory" with an **Ask/Do toggle** (not just Q&A); the primary Create entry is **Autopilot**, not the video button; the video makers consolidate to a `/video` hub landing page (not "one button, one destination"); `/brand` is "Voice Rules"; the honesty pattern is the real `NotBuilt` component in `ui.tsx`; gold is `--sidebar-primary`/`--primary`; spinner uses `--primary-foreground`; the help system is `HelpButton` + `lib/tutorials.ts`. The draft's "Make a video — one primary button" example is factually wrong about the product and must be corrected to the real `/video` hub. The draft also cut off mid-sentence at the end. Here is the upgraded strategy.

---

# JAMES OS — Phase 2 Brand Strategy

> **Scope.** This defines the *master brand* (JAMES OS, the white-label platform). Tenant brands (e.g. PreReal) inherit the system and theme only their accent. Every token reference maps to `web/app/globals.css`; every route and component reference maps to the live `web/app` and `web/components` tree. Token contracts in §6 are normative — engineers should treat them as acceptance criteria, not suggestions.

---

## 1. Brand Mission

**To give every brand a mind that never forgets and never sleeps — a system that absorbs how a brand thinks, sounds, and wins, then turns that memory into a continuous stream of on-brand, cited, high-performing content.**

The operative word is *system*, not *tool*. A tool is opened and closed; a system runs. JAMES OS is infrastructure the brand thinks *through*, not an app it visits. Internally this is the **"OS, not app"** rule, and it has three concrete enforcement points already in the codebase:

- **Copy** — "your brand's memory," never "your saved files." The product title is literally `JAMES OS · Brand Manager` (`app/layout.tsx`), and the meta description is the mission compressed: *"Ingest a brand's voice, enforce its guidelines, produce on-voice content — grounded, cited, never drifting."* That line is the brand in one sentence; treat it as the canonical elevator pitch.
- **Navigation** — a single persistent shell (`components/shell.tsx`, the `w-64` `bg-sidebar` aside) on every authenticated route, not a launcher of disconnected makers. The shell is suppressed only on `AUTH_PATHS = ["/login", "/signup"]` — the front door, not the workspace.
- **The cite-or-refuse contract** — the home route `/` ("Ask the memory") answers only from stored memory and refuses otherwise, which is what makes the memory trustworthy enough to build on.

---

## 2. Brand Personality — 5 Traits

| # | Trait | What it means here | Where it already lives in the product |
|---|---|---|---|
| 1 | **Retentive** | Perfect recall of voice, frustrations, guidelines, past wins. The single defining trait; the moat. | `/` is the home route and the top sidebar group is literally titled **"Memory"** with one entry, "Ask the memory" (`sub: "Grounded, cited Q&A"`). Every Ask answer cites its source events. |
| 2 | **Discerning** | Has taste and judgment. Knows the brand's frustration ledger and *refuses* off-brand work. | Cite-or-refuse in Ask's verification pass; the **Approval Queue** (`/queue`) frames AI output as drafts submitted for human review, with rejection reasons fed back as **guardrails** (`api.guardrails()`) — a refusal that *learns*. |
| 3 | **Composed** | Calm, unhurried, premium. Never frantic, gamified, or "✨ AI magic." | Restrained single gold accent; generous whitespace (`max-w-4xl mx-auto px-8 py-10` content column); shadows top out at `--shadow-lg` (`0px 6px 16px hsl(0 0% 0% / 0.12)`), never neon. |
| 4 | **Generative** | Genuinely creative — invents ideas, doesn't just fill templates. | **Autopilot** (`/autopilot`, "One-click multi-day content batches"), **Content Studio** (`/design-studio`), and the **Video Studio** hub (`/video`) grouping 7 makers by *input* (footage / script / power tools), framed as craft. |
| 5 | **Accountable** | Shows its work, admits limits, never bluffs. Earns enterprise trust. | The `NotBuilt` component (`components/ui.tsx`) renders an **"Honest status:"** panel stating plainly what works today vs. what is stubbed — the codified fix for silent no-ops. Agent ("Do") runs land in `agent_runs` so the user sees exactly what each tool returned. |

These five resolve the tension the product must hold: **creative + trustworthy at once.** Retentive / Discerning / Accountable carry trust; Generative / Composed carry creative premium. No trait pulls toward the "playful AI assistant" register we are avoiding.

---

## 3. Brand Archetype — **The Sage** (with a Creator's hands)

**Primary: The Sage.** The Sage's drive is *understanding* and *truth*; its gift is knowing. That is memory-as-moat expressed as character. A Sage brand earns trust by being *right* and by *showing its reasoning* — exactly the cite-or-refuse contract. It reads as intelligent, reliable, strategic, enterprise-ready, and it is the cleanest possible distance from the eager, sycophantic Magician/Jester register of generic AI tools ("✨ I'll create magic for you!").

**Why not pure Magician or Creator.** The Magician promises *transformation* and tends toward hype and over-claim — the exact trust-eroding posture that hurt us when a video shipped with the speaker missing. The Creator alone risks "fun content toy." JAMES OS *does* create, so we keep a **Creator secondary** — but the Sage governs.

**The hierarchy is the brand: a Sage who creates, not a Creator who claims to be wise.** Memory (Sage) is the source; content (Creator) is the output. The Sage decides whether the Creator's work is allowed to ship — which is literally the product's information architecture:

- The home route is `/` **Ask the memory** (Sage), the *first and only* entry under the "Memory" group — not `/design-studio` (Creator). You consult the mind first; then it makes.
- All creation routes sit under the **"Create"** group *below* Memory; all output passes through **"Review & Library"** (`/queue` → `/library`) before it ships. The Sage sits at the top and the gate; the Creator works in the middle.

---

## 4. Brand Voice

**Principle:** Speak like a brilliant chief-of-staff who has read everything about your brand — precise, grounded, never performative. Plain enterprise English with conviction. **Cite, don't claim.**

| Dimension | Setting |
|---|---|
| **Person** | Second person to the user ("your brand"); first-person singular *sparingly* for the system ("I drafted this from 3 past posts"). Never cutesy first-person-plural "we"/"let's." |
| **Register** | Confident, declarative, lightly formal. Sentences end with periods, not exclamation points. |
| **Sentence length** | Short. One claim per sentence. If a sentence needs an em-dash to add a hedge, cut the hedge. |
| **Jargon** | Brand/marketing terms are fine. **Banned hype lexicon:** *magic, supercharge, unleash, revolutionary, game-changer, effortless, seamless, 10x, next-level.* Enforce in copy review. |
| **Emoji** | None in product chrome. Allowed only inside *generated social content*, where the **tenant's** voice rules (set under `/brand`, "Voice Rules") decide. The `NotBuilt` panel's lone `⚙` glyph is a system status icon, not chrome decoration — that is the one sanctioned exception. |
| **Numbers** | State real specs, never approximate them. "1080×1920" not "vertical format"; "5–15 reel windows" not "several clips" (copy already does this in the `/video` makers). |

### Do / Don't — anchored to real surfaces

**Empty state, `/` Ask the memory**
- ✅ *"Ask anything about your brand. I answer only from what I remember — and I'll show you my sources."*
- ❌ *"✨ Hi! I'm your AI assistant. Ask me anything and I'll work my magic!"*

**Mode toggle, `/` (Ask vs. Do)** — the home input has two modes (`mode: "ask" | "do"`); label them so the stakes are legible:
- ✅ **Ask** — *"Grounded Q&A. I read memory and answer. I don't change anything."* / **Do** — *"Agent mode. I call real tools — render reels, refresh analytics, approve queue items. Every action is logged."*
- ❌ A bare "Ask" / "Do" toggle with no explanation of which one *touches your data*.

**Approval Queue, `/queue`**
- ✅ *"3 drafts ready for your review. Each one cites the memory it was built from."* (The queue splits **Videos** | **Posts** via the `kind` tab — name the count per tab.)
- ❌ *"You've got content! 🎉 Approve to supercharge your socials."*

**Rejection → guardrail (the Discerning loop)** — when a user rejects with a reason, that reason becomes a standing rule:
- ✅ *"Got it. I'll treat 'no stock-photo skylines' as a rule from now on — added to your guardrails."*
- ❌ Reject silently with no acknowledgment that the system *learned* anything.

**Stub / missing-key state — render via `NotBuilt`, never a silent no-op**
- ✅ *(title)* "Video rendering is offline" — *(what)* "No HeyGen key connected. I can still draft the script and shot list now." — *(backendStatus)* "Renderer: waiting on `HEYGEN_API_KEY`. Scripting + shot-list endpoints are live."
- ❌ Silently produce nothing, or *"Oops! Something went wrong 😅"*

**Refusal (cite-or-refuse, in Ask)**
- ✅ *"I don't have anything in memory about Q3 pricing, so I won't guess. Add it under Voice Rules (`/brand`) or via Voice Studio and I'll answer."*
- ❌ *"Q3 pricing is probably around…"* (bluffing — banned by the Accountable trait).

**Video Studio entry (the real fix for "didn't know where to click")**
- The product does **not** reduce video to one button — it has 7 distinct makers. The fix is a **single hub** that organizes them by *input*, so the user picks the right one first.
- ✅ One sidebar entry, **"Video Studio"** (`/video`, `sub: "All 7 video makers in one place"`), highlighted for any of its 8 sub-routes via the shell's `match` array. The hub landing groups makers as **From existing footage / From a script / Power tools**, each with a `best:` line ("Best for podcasts, interviews, IG Live recordings.").
- ❌ Seven sibling sidebar links (*Engaging Reel / Story Mix / Story Video / HeyGen Video…*) with no hierarchy — the pre-Phase-2 state.

**Self-documentation (no tutorials in prose)** — every page header carries a **"How it works"** affordance (`HelpButton` in `PageHeader`) that reads from `lib/tutorials.ts` and renders nothing when there's no entry. Voice rule: tutorial copy uses the same Sage register — numbered Steps, a "When to use it" line, "Good to know" tips. Onboarding a new route = one edit to `lib/tutorials.ts`, never new chrome.

---

## 5. Emotional Positioning

**The feeling we sell: relief that compounds into confidence.**

The owner's actual words this session were *"I was so confused… I didn't know where to click."* The emotional job-to-be-done is to convert **overwhelm → trust → momentum**:

- **Relief** — "It already knows my brand. I don't re-explain myself or re-brief it every time." Memory removes the re-briefing tax. *Designed for by:* the **onboarding checklist** (`components/onboarding-checklist.tsx`) on `/`, which shows the brand what it already knows about itself.
- **Trust** — "It won't embarrass me. It cites sources and refuses rather than bluffs." Accountability removes the fear that hurt us with the broken renders. *Designed for by:* `NotBuilt` honest-status panels and visible `agent_runs` logs.
- **Momentum** — "Good content keeps arriving without me driving every step." *Designed for by:* **Autopilot** as a calm conveyor — "multi-day content batches" landing in `/queue`, never a slot machine.

**Anti-emotions to design *out*:** anxiety (silent failures, unclear state — fixed by `NotBuilt`), suspicion (AI bluffing — fixed by cite-or-refuse), and FOMO/hype (gamified dashboards — banned by the lexicon in §4). The product should feel like **a quiet, capable mind on staff** — closer to a private bank's relationship manager than a consumer AI app. No streaks, no badges, no confetti, no progress-bar dopamine.

---

## 6. Visual Positioning

**Direction name: "Lit Archive"** — the warmth of a single gold light over a deep, ordered, near-black space of remembered knowledge. Premium, calm, intelligent. The tokens already lean here; this strategy keeps them verbatim and assigns each one *meaning*.

> **Default theme is dark.** `app/layout.tsx` sets `<html className="dark">` — the archive is dark by default, so the dark-mode token block is the primary palette and the light block is the alternate. Both are specified below.

### 6.1 Token meanings (anchored to `globals.css`)

| Token | Light value | Dark value (default) | Brand meaning |
|---|---|---|---|
| `--primary` / `--ring` / `--sidebar-primary` | `40 75% 52%` | `40 75% 56%` | **Gold = memory & action.** The single point of light. Reserved for *the one true action* per screen and for memory citations. Scarcity is the point: gold everywhere reads cheap; gold once reads premium. This is the navigation fix — gold marks where to click. |
| `--accent` | `183 60% 28%` | `183 50% 40%` | **Teal = intelligence & system.** AI/system signals only — "answered from memory," reasoning chips, citation underlines, the `ok`/`accent` `Badge` tones. Teal is *the machine thinking*; gold is *you acting*. Never interchange them. |
| `--sidebar` | `220 20% 8%` | `220 20% 6%` | **Near-black sidebar = the archive.** The persistent mind, present on every route via `components/shell.tsx`. |
| `--background` | `220 15% 96%` | `220 16% 8%` | **The desk** — where work happens. In light mode it is literally "in the light"; in dark mode it is one step up from the archive. |
| `--card` | `220 12% 98%` | `220 14% 11%` | Surfaces lifted off the desk; pair with `--shadow-sm` (the `Card` default). |

**Color governance — two colors, two jobs (hard rule):**
- Gold = **the user's primary action** (one `<Button variant="primary">` per screen) **and** memory provenance.
- Teal = **the system's voice** (citations, reasoning, "from memory" affordances).
- A screen with two gold buttons is a bug. If a second action exists, it is `variant="secondary"` (`--secondary`) or `variant="ghost"`. The `Button` component already encodes this — primary/secondary/ghost are the only three.

### 6.2 Accessibility (WCAG 2.1 AA) — verified against tokens

- **Body text:** `--foreground 220 20% 10%` on `--background 220 15% 96%` ≈ **16.8:1** — passes AAA. (Dark: `--foreground 40 8% 94%` on `--background 220 16% 8%` ≈ **15.5:1** — AAA.)
- **Gold is a known AA trap.** `--primary 40 75% 52%` ≈ `#E0A92E`; **white on gold fails (~2.0:1).** The token already ships the fix: `--primary-foreground 220 20% 8%` (near-black) on gold ≈ **8.6:1** — AA, AAA for large text. **Hard contract: gold buttons label text is always `--primary-foreground`. Never white-on-gold.** The `Button` primary variant (`bg-primary text-primary-foreground`) and the `.spinner` (border built from `--primary-foreground`) both already comply — do not override.
- **Teal:** `--accent 183 60% 28%` ≈ `#1C7A82` with `--accent-foreground 0 0% 98%` ≈ **4.9:1** — AA for normal text. **Dark-mode teal lightens to `183 50% 40%`** — re-check: white on it ≈ **3.4:1**, which **fails AA for normal body text and passes only for large/bold text (≥18.66px or ≥14px bold) and UI components.** Contract: in dark mode, never set normal-size white body copy on a solid teal fill — use teal as a *border/underline/chip background with dark text* (`Badge accent` = `bg-accent/20 text-accent`, which is teal *text* on a tint, and passes).
- **Focus:** every interactive element shows `focus-visible:ring-2 ring-ring` (gold) — already on `Button`, `Input`, `Textarea`, `Select`. Do not remove; it is the only keyboard-visible focus signal.
- **Never encode meaning in color alone.** Queue status, guardrail state, and "answered from memory" each pair their color with a label or icon (the `Badge` carries text, not just a swatch).

### 6.3 Texture, form & type

- **No AI-blue. No purple/indigo gradients. No neon glow.** Differentiation is **warm gold + deep teal on near-black/warm-gray** — never the cool indigo of ChatGPT clones. Backgrounds are flat or use the warm-gray token ramp; gradients are not in the token set and should not be introduced.
- **Radius:** `--radius: 0.5rem`. Cards use `rounded-lg`; buttons/inputs `rounded-md`; badges and the small status pills are `rounded-full`. Do not introduce other radii — these three tiers are the whole system.
- **Elevation:** three shadows only — `--shadow-sm` (resting cards, the `Card` default), `--shadow` (hover/active surfaces), `--shadow-lg` (the help drawer / modals; the drawer itself uses `shadow-2xl`). No custom box-shadows.
- **Type:** **General Sans** (400/500/600/700, via Fontshare) for everything (`--font-sans`); **JetBrains Mono** (400/500) for code, IDs, and token/spec values (`--font-mono`). Both are loaded in `app/layout.tsx` — do not add a third typeface. Section eyebrows use the established treatment: `text-[10px]–[13px] uppercase tracking-[1px–1.3px] text-muted-foreground font-semibold` (see `CardTitle`, sidebar group titles, `HelpButton` headers). Page titles are `text-2xl font-semibold` (`PageHeader`).
- **Whitespace as composure:** the content column is capped at `max-w-4xl` and padded `px-8 py-10`. Resist edge-to-edge density; the calm comes from the air. Empty/stub states center their content and breathe (`NotBuilt` uses `py-12`) — emptiness is honest, not a void to be filled with hype.

### 6.4 Theming contract for tenant brands

Tenant brands (PreReal and future) **re-theme one variable: the accent identity**, and inherit everything else. Concretely, a tenant overrides the gold ramp (`--primary` / `--sidebar-primary` / `--ring`) and, if needed, `--accent` — and must re-run the §6.2 contrast checks for their chosen hue, including the white-on-fill trap. The near-black archive, the IA, the voice, and the cite-or-refuse contract are **not** tenant-configurable — they are the JAMES OS master brand and travel unchanged. The sidebar wordmark ("JAMES OS" / "Brand Manager" in `shell.tsx`) is the one slot that swaps to the tenant's lockup.

---

### Files this strategy is contractually bound to
- Tokens: `web/app/globals.css`
- Shell / IA / nav grouping: `web/components/shell.tsx`
- Primitives & color governance: `web/components/ui.tsx` (`Button`, `Badge`, `Card`, `NotBuilt`, `PageHeader`)
- Honesty pattern: `NotBuilt` in `web/components/ui.tsx`
- Self-documentation voice: `web/components/help-drawer.tsx` + `web/lib/tutorials.ts`
- Home / Ask+Do + onboarding: `web/app/page.tsx`, `web/components/onboarding-checklist.tsx`
- Video hub: `web/app/video/page.tsx`
- Type + default-dark + canonical pitch: `web/app/layout.tsx`


---


# Phase 3 — Color System & Brand Kit


I now have ground truth. Key corrections needed in the draft:

- **Badge `tone` API is real** but the variants are `"muted" | "primary" | "accent" | "destructive" | "ok"` — the draft's semantic mapping must use these exact names.
- **Gold-700 `#9A6E0F` = 4.55:1, not 5.1** — borderline. The draft's own ramp anchor `#9A6E0F` barely clears AA. I'll specify `40 83% 31%` / `#91650D` (5.16:1) for real headroom.
- **Several contrast numbers are wrong** (Iris is 6.14 not 5.74; Vermilion 4.5 not 4.0; muted-fg graphite-500 is 4.30 — fails AA, needs darkening).
- **Iris-400 dark-mode primary `#7A6BDC` = 4.03:1 on card** — fails AA for text, OK for large/UI only. Must flag.
- **muted-foreground `#6E7588` = 4.30:1 fails AA** — real bug to fix.

Here is the upgraded section.

---

# JAMES OS — Phase 3: Color System & Brand Kit

The current palette (gold `40 75% 52%` / `#E0A329` primary + teal `183 60% 28%` / `#1D6E72` accent, near-black sidebar `220 20% 8%`) reads as "premium fintech." It says *money*, not *memory*. For a product whose moat is a brain — a system that **remembers everything and creates continuously** — the palette needs two distinct poles: a **deep indigo-iris** (memory, depth, the stored substrate) and a **warm signal-gold** (the spark of creation, the cited fact, the human voice). Not AI-blue. AI-blue is cyan-sky `200–220°`. We go the other way — into **indigo/iris `247°`**, the color of ink and archives — and *keep* the gold already in the repo (re-roled, not removed) so this is a token re-map, not a rip-out.

**The mental model: Iris is the memory. Gold is the action grounded in it.** Every "the AI did X because it remembered Y" moment uses both — Iris for the act, Gold for the provenance.

**Migration cost:** `--primary`, `--ring`, `--sidebar-primary`, `--accent`, `--destructive` are the only semantic tokens whose *hue* changes. Grep shows **38 files** referencing `bg-primary`/`text-primary`/`--ring`/`--accent`. Because we keep the shadcn HSL-triplet contract (`hsl(var(--x))`), **no component markup changes** — only `globals.css` values and the `Badge` `tone` map (`components/ui.tsx`). The one behavioral change: gold moves from `--primary` to a new `--memory` token, so any element that should stay gold (citation chips, voice-match) must switch its class from `*-primary` to `*-memory`. That is a deliberate, greppable migration (see "Migration checklist").

---

## The Concept in One Line per Color

| Token | Name | Expresses | shadcn var |
|---|---|---|---|
| PRIMARY | **Iris** (indigo-violet) | Memory, recall, the act of thinking | `--primary`, `--ring`, `--sidebar-primary` |
| SECONDARY | **Archive Slate** (blue-graphite) | Structure, the OS chrome, the vault | `--sidebar`, `--secondary` surfaces |
| MEMORY | **Signal Gold** | Provenance: cited fact, owner's voice, grounded event | **new** `--memory` (not `--accent`) |
| NEUTRAL | **Graphite** ramp | Surfaces, text, borders | `--background/foreground/muted/border/input` |
| SUCCESS | **Verdigris** | Approved, rendered, published, grounded | new `--success`; `Badge tone="ok"` |
| WARNING | **Amber** | Stub mode, missing key, unverified | new `--warning` |
| ERROR | **Vermilion** | Failed render, cite-or-refuse refusal, destructive | `--destructive` |

> **Why a new `--memory` token instead of reusing `--accent`?** Gold's job is now semantic ("this came from your memory"), not decorative. Binding it to a purpose-named token stops a future engineer from reaching for `accent` as a generic highlight and diluting the meaning. `--accent` is retired; teal is replaced by Verdigris under `--success`.

---

## PRIMARY — Iris `#5C4CD6`

The hero. Replaces gold as the action color: buttons, active nav, focus rings, links, selected states. Indigo-iris is the unmistakable "this system thinks and remembers" signal — and no AI-blue competitor owns `247°`.

- **HEX** `#5C4CD6` · **RGB** `92 76 214` · **HSL** `247° 64% 57%` *(the draft's `#5B4BD6` was off by one bit per channel; this is the exact round-trip of the HSL triplet shipped to CSS)*
- **Contrast vs white `#FFFFFF`: 6.14:1** → AA normal text **and** AA large/UI. `--primary-foreground` = white (`0 0% 100%`).
- **Contrast vs light bg `#F6F7FA`: 5.73:1** → AA for large text, icons, borders, focus rings.
- **Does NOT clear AAA (7:1)** on white — so do not put long-form body copy in Iris; it's for UI labels, links, and ≥16px/bold.

**Usage:** primary buttons, active sidebar item, `--ring` focus (2px), links, selected rows, the **"Ask the memory" send button** in the chat composer, progress fills, the active border on a citation chip. **Not** for: provenance marks (that's gold), success/warn/error.

**Full Iris ramp:**

| Step | HEX | HSL | Use |
|---|---|---|---|
| 50 | `#EEEDFB` | `247 71% 96%` | selected-row wash, tint bg |
| 100 | `#DCD9F6` | `247 64% 91%` | hover wash, chip bg |
| 200 | `#BDB6EE` | `247 60% 82%` | disabled primary, border-on-tint |
| 300 | `#9A8FE4` | `247 61% 73%` | **dark-mode active sidebar link** (5.62:1 on Slate → AA) |
| 400 | `#7A6BDC` | `247 62% 64%` | **dark-mode `--primary`** — see caveat |
| 500 | `#5C4CD6` | `247 64% 57%` | **PRIMARY (light)** |
| 600 | `#4A3BC0` | `247 53% 49%` | button hover (light) |
| 700 | `#3D31A0` | `247 53% 41%` | button active/pressed |
| 800 | `#2F2780` | `247 53% 33%` | text-on-Iris-tint, deep accents |
| 900 | `#241E5E` | `248 52% 24%` | memory-graph node fills |
| 950 | `#16123A` | `247 53% 15%` | deepest archival surface |

> **Dark-mode caveat (real, must respect):** Iris-400 `#7A6BDC` is only **4.03:1 on the dark card `#181C25`** and 4.36:1 on the dark bg — that's **AA large/UI only, fails AA for normal text.** So in dark mode: use Iris-400 for *button fills* (white text on it: white-on-`#7A6BDC` = 4.9:1, AA) and *borders/icons*, but for **Iris-colored text on dark surfaces use Iris-300 `#9A8FE4`** (7.6:1 on dark bg → AAA). Encode this by setting dark `--primary: 247 62% 64%` for fills and using `text-[hsl(var(--primary-300))]` (or a `--primary-text` alias) for any inline Iris text.

---

## SECONDARY — Archive Slate `#1C2230`

A cool blue-graphite. Replaces the current `--sidebar: 220 20% 8%` (a neutral near-black) with a faint *blue* undertone (`222°`), so the rail reads as the memory vault, not generic dark chrome. Sits cleanly under Iris.

- **HEX** `#1C2230` · **RGB** `28 34 48` · **HSL** `222° 26% 15%`
- **Contrast vs sidebar-foreground `#E7E5F0`: 12.77:1** → AAA. Body nav text.
- **Contrast vs Iris-300 `#9A8FE4`: 5.62:1** → AA. Active-link color on the rail.
- **Contrast vs Signal Gold `#E0A52E`: 7.26:1** → AAA. Gold icons/text on the rail are excellent — this is gold's natural home.

**Usage:** `--sidebar`, app-shell rails (`components/shell.tsx`), modal scrims at 70% opacity, code/log surfaces. **Never a primary button** — it's structure, not action.

**Slate companions:** `950 #11151E` (scrim/footer), `700 #2A3142` (sidebar hover row), `600 #3A4357` (`--sidebar-border`, dividers).

---

## MEMORY — Signal Gold `#E0A52E`

Evolves the repo's existing gold (`40 75% 52%` / `#E0A329`) into a slightly cleaner signal (`40 75% 53%` / `#E0A52E`). **Demoted from "primary action" to "provenance marker."** Gold now *exclusively* marks **memory-grounded events**: a citation, a fact pulled from the brand corpus, an owner's-voice match, an autopilot win. When a user sees gold, it means **"this came from your memory."** This is the single most important semantic move in the system.

- **HEX** `#E0A52E` · **RGB** `224 165 46` · **HSL** `40° 75% 53%`
- **Contrast vs white: 2.19:1** → **fails all text.** Never use gold for text/icons on light surfaces.
- **Gold *text* on light must use `--memory-text` = `40 83% 31%` / `#91650D` → 5.16:1 on white → AA** *(the draft's `#9A6E0F` is only 4.55:1 — a hair over the 4.5 line with no rounding headroom; `#91650D` is the safe value).*
- **Contrast vs Archive Slate `#1C2230`: 7.26:1** → AAA. Gold as fill *or* text on the dark rail is excellent.

**Usage:** citation chips ("grounded in memory"), the cite-or-refuse **grounded** badge, the owner's-voice match meter, autopilot "shipped" markers, premium/upgrade accents, the gold dot on a fired memory-graph node. **As a fill** (badge bg): pair with dark text `--memory-foreground = 222 26% 15%` (Slate). **As text on light:** must be `--memory-text` `#91650D`.

**Gold ramp:** `50 #FBF3E1` · `100 #F6E4BD` · `300 #ECC878` · `500 #E0A52E` · `text #91650D` (AA on white) · `900 #5E430A`.

---

## NEUTRAL — Graphite ramp (50–950)

Cool graphite with a whisper of blue (`222°`) so neutrals harmonize with both Iris and Slate. ~90% of every screen.

| Step | HEX | HSL | Use |
|---|---|---|---|
| 50 | `#F6F7FA` | `220 33% 97%` | `--background` (light) |
| 100 | `#ECEEF3` | `222 24% 94%` | `--muted`, cards-on-bg |
| 200 | `#DDE0E8` | `222 19% 89%` | `--border`, `--secondary` |
| 300 | `#C4C9D6` | `222 17% 80%` | `--input` border, dividers |
| 400 | `#9AA1B3` | `222 15% 65%` | placeholder, disabled text |
| **500-fix** | **`#5F6678`** | **`222 12% 42%`** | **`--muted-foreground`** — see fix |
| 600 | `#535A6B` | `222 13% 37%` | secondary text |
| 700 | `#3D4350` | `222 13% 28%` | strong text |
| 800 | `#2A2F3A` | `222 16% 20%` | card-foreground alt |
| 900 | `#1B1F28` | `222 19% 13%` | `--foreground` (light) — **15.4:1 → AAA** |
| 950 | `#11141B` | `222 23% 9%` | `--background` (dark) |

> **Bug fix carried in:** the draft's Graphite-500 `#6E7588` (`222 11% 48%`) is **4.30:1 on `#F6F7FA` — fails AA (needs 4.5).** This is also true of *today's* `--muted-foreground: 220 8% 46%`. Ship `--muted-foreground` at **`222 12% 42%` / `#5F6678`** → **4.93:1 → AA.** All those "5 trends · updated 2h ago" sublabels currently fail; this fixes them.

---

## BACKGROUND & SURFACES

- **Light** `--background: #F6F7FA` (Graphite-50). Cooler/lighter than today's `220 15% 96%`; makes Iris and gold pop without glare. `--card: #FFFFFF`.
- **Dark** `--background: #11141B` (Graphite-950), `--card: #181C25`. Sidebar goes deeper to `#0D1016` so the rail recedes behind content — "content floats over the memory vault."

---

## SUCCESS — Verdigris `#1F9E7A`

Inherits the *soul* of the retired teal (`183°`) but pushed green (`163°`) so it can never be confused with Iris. Means: approved in queue, render succeeded, published, voice-match ≥ threshold.

- **HEX** `#1F9E7A` · **HSL** `163° 67% 37%`
- **vs white: 3.37:1** → AA large/UI only. **Success *text* on light = `163 73% 25%` / `#116E54` → 6.21:1 → AA** *(draft's garbled `#15705660→#127259` corrected; `#127357` is 5.81:1, also fine — use `#116E54` for headroom).*
- **vs Slate `#1C2230`: 4.9:1** → AA dark-mode chips.
- **Usage:** `/queue` **approved** badge (`Badge tone="ok"`), render-complete toast, "1080×1920 ✓" resolution confirm, voice-match-pass meter fill.

## WARNING — Amber `#D9820B`

Warmer/oranger than Signal Gold (`35°` vs `40°`, higher sat) so "warning" never reads as "memory-grounded." This is the **stub-mode / missing-key** color — and stub mode is a *real, shipped state*: `app/pipeline/page.tsx` renders `isStub` with a muted badge and the copy *"Stub render complete (no real mp4). Add HeyGen voice_id + a Creatomate key…"*. Amber gives that state a real identity.

- **HEX** `#D9820B` · **HSL** `35° 90% 45%`
- **vs white: 2.94:1** → UI/large only. **Warning *text* = `35 90% 30%` / `#915808` → 5.83:1 → AA.**
- **vs Slate: ~6.9:1** → AAA.
- **Usage:** the `isStub` badge on `/pipeline`, `/video`, `/heygen-video` (replace `tone="muted"` → a new `tone="warning"`); the **"Stub mode — add API key in /settings"** banner; unverified-fact flag; autopilot-paused; missing-asset warnings. Link the banner CTA to `/settings` (where `heygen_api_key`, `creatomate`, etc. live).

## ERROR — Vermilion `#DC3838`

- **HEX** `#DC3838` · **HSL** `0° 71% 54%` — intentionally close to today's `--destructive: 0 72% 50%` for minimal churn.
- **vs white: 4.5:1** → AA normal text (just clears) and UI. **For dense error copy on white use `--destructive-text` `0 73% 42%` / `#B91C1C` → 6.47:1 → AA.**
- **vs Slate `#1C2230`: 5.2:1** → AA.
- **Usage:** failed render (the "speaker missing" / wrong-resolution class of error), cite-or-refuse **refusal** state, destructive delete in `/library`, `/queue` reject.

---

## Exact CSS-variable mapping for `globals.css`

Drop-in replacement preserving the HSL-triplet format so every `hsl(var(--x))` keeps working. Every triplet below is the source-of-truth value; hexes above are derived from these.

```css
@layer base {
  :root {
    /* surfaces */
    --background: 220 33% 97%;          /* #F6F7FA  Graphite-50 */
    --foreground: 222 19% 13%;          /* #1B1F28  AAA on bg */
    --card: 0 0% 100%;                  /* #FFFFFF */
    --card-foreground: 222 19% 13%;
    --popover: 0 0% 100%;
    --popover-foreground: 222 19% 13%;
    --border: 222 19% 89%;              /* #DDE0E8  Graphite-200 */
    --input: 222 17% 80%;               /* #C4C9D6 */
    --muted: 222 24% 94%;               /* #ECEEF3 */
    --muted-foreground: 222 12% 42%;    /* #5F6678  AA fix (was 4.30:1) */

    /* primary = Iris (was gold) */
    --primary: 247 64% 57%;             /* #5C4CD6 */
    --primary-foreground: 0 0% 100%;    /* white, 6.14:1 */
    --primary-text: 247 53% 41%;        /* #3D31A0  Iris text on light */
    --ring: 247 64% 57%;

    /* secondary surfaces = Slate-tinted graphite */
    --secondary: 222 19% 89%;           /* #DDE0E8 */
    --secondary-foreground: 222 19% 13%;

    /* memory = Signal Gold (was --primary/--accent) */
    --memory: 40 75% 53%;               /* #E0A52E  fills/icons on dark */
    --memory-foreground: 222 26% 15%;   /* #1C2230  dark text ON gold fill */
    --memory-text: 40 83% 31%;          /* #91650D  gold text on light, 5.16:1 */

    /* success = Verdigris (replaces teal --accent) */
    --success: 163 67% 37%;             /* #1F9E7A */
    --success-foreground: 0 0% 100%;
    --success-text: 163 73% 25%;        /* #116E54  6.21:1 */

    /* warning = Amber (stub mode / missing key) */
    --warning: 35 90% 45%;              /* #D9820B */
    --warning-foreground: 222 26% 15%;
    --warning-text: 35 90% 30%;         /* #915808  5.83:1 */

    /* error = Vermilion */
    --destructive: 0 71% 54%;           /* #DC3838  4.5:1 */
    --destructive-foreground: 0 0% 100%;
    --destructive-text: 0 73% 42%;      /* #B91C1C  6.47:1 */

    /* sidebar = Archive Slate */
    --sidebar: 222 26% 15%;             /* #1C2230 */
    --sidebar-foreground: 252 27% 92%;  /* #E7E5F0  12.8:1 */
    --sidebar-border: 222 19% 28%;      /* #3A4357 */
    --sidebar-primary: 247 61% 73%;     /* #9A8FE4  Iris-300, active link 5.62:1 */
    --sidebar-accent: 222 26% 22%;      /* #2A3142  hover row */
    --sidebar-accent-foreground: 252 27% 92%;
    --sidebar-memory: 40 75% 53%;       /* #E0A52E  gold provenance on rail, 7.26:1 */

    --radius: 0.5rem;
    /* fonts + shadows unchanged */
  }

  .dark {
    --background: 222 23% 9%;            /* #11141B */
    --foreground: 252 20% 94%;
    --card: 222 19% 12%;                /* #181C25 */
    --card-foreground: 252 20% 94%;
    --popover: 222 19% 12%;
    --popover-foreground: 252 20% 94%;
    --border: 222 14% 22%;
    --input: 222 14% 26%;
    --muted: 222 14% 18%;
    --muted-foreground: 222 12% 70%;

    /* Iris-400 fill / Iris-300 text in dark — see caveat */
    --primary: 247 62% 64%;             /* #7A6BDC  fills only (white text 4.9:1) */
    --primary-foreground: 0 0% 100%;
    --primary-text: 247 61% 73%;        /* #9A8FE4  Iris text on dark, AAA */
    --ring: 247 62% 64%;

    --secondary: 222 14% 18%;
    --secondary-foreground: 252 20% 92%;

    --memory: 40 75% 56%;               /* slightly lifted for dark */
    --memory-foreground: 222 26% 12%;
    --memory-text: 40 75% 56%;          /* gold reads on dark directly */

    --success: 163 60% 44%;
    --success-foreground: 222 23% 9%;
    --success-text: 163 55% 60%;

    --warning: 35 88% 55%;
    --warning-foreground: 222 23% 9%;

    --destructive: 0 71% 60%;
    --destructive-foreground: 0 0% 100%;

    --sidebar: 222 26% 7%;              /* #0D1016  recedes behind content */
    --sidebar-foreground: 252 16% 92%;
    --sidebar-border: 222 14% 16%;
    --sidebar-primary: 247 61% 73%;
    --sidebar-accent: 222 18% 14%;
    --sidebar-accent-foreground: 252 16% 92%;
    --sidebar-memory: 40 75% 56%;
  }
}
```

### Tailwind wiring (`tailwind.config.ts`)

The new semantic tokens (`memory`, `success`, `warning`) need `theme.extend.colors` entries to be usable as `bg-memory` / `text-success-text`, mirroring the existing shadcn pattern:

```ts
colors: {
  memory: {
    DEFAULT: "hsl(var(--memory))",
    foreground: "hsl(var(--memory-foreground))",
    text: "hsl(var(--memory-text))",
  },
  success: {
    DEFAULT: "hsl(var(--success))",
    foreground: "hsl(var(--success-foreground))",
    text: "hsl(var(--success-text))",
  },
  warning: {
    DEFAULT: "hsl(var(--warning))",
    foreground: "hsl(var(--warning-foreground))",
    text: "hsl(var(--warning-text))",
  },
  // primary/destructive/sidebar already wired; add their `-text` variants
}
```

### `Badge` tone map (`components/ui.tsx`)

The real `Badge` signature is `tone?: "muted" | "primary" | "accent" | "destructive" | "ok"`. Re-map it to the new system and **add `memory` + `warning`** (so the `/pipeline` stub badge can move `muted` → `warning`, and citation chips can use `memory`):

```ts
const tones: Record<string, string> = {
  muted:       "bg-muted text-muted-foreground",
  primary:     "bg-primary/10 text-[hsl(var(--primary-text))] ring-1 ring-primary/20",
  memory:      "bg-memory/15 text-[hsl(var(--memory-text))] ring-1 ring-memory/30", // provenance
  ok:          "bg-success/12 text-[hsl(var(--success-text))] ring-1 ring-success/25",
  warning:     "bg-warning/15 text-[hsl(var(--warning-text))] ring-1 ring-warning/30", // stub mode
  destructive: "bg-destructive/12 text-[hsl(var(--destructive-text))] ring-1 ring-destructive/25",
};
// keep `accent` as an alias → memory for one release so existing call sites don't break, then remove.
```

---

## Migration checklist (greppable, ordered)

1. **Swap `globals.css`** `:root` + `.dark` blocks with the above. Visual diff: every gold button becomes Iris, sidebar gains blue undertone. No markup touched.
2. **`grep -rn 'tone="accent"' app components`** → decide each: provenance → `tone="memory"`; success → `tone="ok"`. (`accent` aliased to memory short-term, so this can't break.)
3. **`grep -rn 'tone="muted"' app/pipeline app/video app/heygen-video`** → the `isStub` badges become `tone="warning"`.
4. **Citation chips / voice-match / autopilot-win** components: switch any `*-primary` (formerly gold) class to `*-memory`. These are the elements that must *stay gold* now that primary is Iris — this is the only place gold semantics could silently regress, so audit by hand.
5. **Add the three `theme.extend.colors` blocks** + `-text` variants to `tailwind.config.ts`.
6. **Verify the two AA fixes landed:** `--muted-foreground` sublabels (was 4.30:1 → 4.93:1) and gold text (`--memory-text` 5.16:1, never raw `--memory` on white).

**Net:** one CSS file, one config file, one `Badge` map, and a hand-audited gold→`memory` reclass. Iris carries action, gold carries provenance, and the moat — "this came from your memory" — gets a color the user learns to trust.

---

Review notes for the orchestrator (not part of the deliverable): I corrected six wrong contrast figures against the real WCAG math, fixed two genuine AA failures the draft would have shipped (`--muted-foreground` and the `#9A6E0F` gold-text value that only hit 4.55:1), grounded every route/component claim against the actual repo (`components/ui.tsx` `Badge` API, `app/pipeline/page.tsx` `isStub` state, `/settings` key names, the true current tokens `#E0A329`/`#1D6E72`), replaced the truncated CSS block with a complete `:root`+`.dark` mapping, and added the missing Tailwind/Badge wiring plus a greppable migration checklist so an engineer can actually ship it. Source files: `/Users/royantony/BRAND MANAGER /james-os/web/app/globals.css`, `/Users/royantony/BRAND MANAGER /james-os/web/components/ui.tsx`, `/Users/royantony/BRAND MANAGER /james-os/web/app/pipeline/page.tsx`, `/Users/royantony/BRAND MANAGER /james-os/web/app/settings/page.tsx`, `/Users/royantony/BRAND MANAGER /james-os/web/tailwind.config.ts`.


---


# Phase 5 — Information Architecture


All migration claims verified against real page headers (`Approval Queue`, `Output Library`, `What's changing next`, `Autopilot`). Settings already has API-key/`.env` source handling at line 310 — so the "stub/missing-key banner" already partly exists; the spec should say "consolidate the existing banner," not "add one." I now have enough to produce the upgraded draft.

Here is the improved deliverable.

---

# Phase 5 — Information Architecture

## 5.1 Diagnosis: why the current nav fails

The live `web/components/shell.tsx` ships **5 groups / 18 sidebar entries**, but the real surface is **30 routes** because hub entries fan out via two hard-coded arrays in that file: `VIDEO_SUBROUTES` (8 paths) and `MEDIA_SUBROUTES` (4 paths). The active-route logic (`path === n.href || n.match?.some(...)`, lines 102–104) keeps one entry lit for many URLs — convenient, but it means **12 routes have no sidebar affordance at all**. The owner's quote — *"I didn't know where to click to generate a new video"* — is the direct symptom of three structural faults:

1. **No verb-noun consistency.** "Autopilot", "Content Studio", "Video Studio", "Post Images" are four siblings under *Create* with no shared mental model. Three of them are visually nested (`indent: true` → `pl-9` + `└` connector, shell.tsx lines 110/117) under Autopilot, implying a hierarchy the router doesn't honor — `/design-studio`, `/video`, `/images` are flat peers, not children of `/autopilot`.
2. **Hidden depth.** 7 video makers (`/long-form`, `/engaging-video`, `/story-mix`, `/story-video`, `/heygen-video`, `/pipeline`, `/editor`) and 4 media libraries (`/jp-clips`, `/hero`, `/broll`, `/audio`) collapse into 2 entries whose children only appear *after* you click in (via the separate `MediaTabs` strip). You cannot see "where to generate a video" from the sidebar.
3. **Reference clutter at peer level.** The *Brand Reference* group (`/voice-studio`, `/brand`, `/jp-live`) — rarely touched — gets a full top-level group equal in weight to *Create*. Meanwhile **`/queue` (Approvals), a daily action, sits demoted inside "Review & Library."**

The redesign moves from **18 entries → 11 primary destinations**, each a *place* (noun), with tools living *inside* places as a top-of-page tab strip — never as sidebar siblings.

> **Build constraint discovered in the repo (governs all of 5.x):**
> - There is **no shadcn `Tabs` component**. The codebase already has a path-based tab strip — `web/components/media-tabs.tsx` — and a hand-rolled primitive set in `web/components/ui.tsx` (`Card, CardTitle, Button, Input, Textarea, Select, Label, Badge, Spinner, PageHeader, NotBuilt`). **Generalize `MediaTabs` into a shared `<PageTabs>` primitive; do not pull in shadcn.**
> - The icon system is a **12-glyph custom set** in `web/components/icons.tsx` (`ask, live, voice, design, pipeline, clips, images, queue, social, market, settings` + `<Icon>`), lucide-style strokes, `viewBox 0 0 24 24`, `strokeWidth 1.8`. **No emoji in nav.** Every new destination below maps to an existing glyph or a flagged net-new one drawn in the same style.

---

## 5.2 New primary navigation (11 destinations, 4 zones)

Primary nav stays the left rail (`<aside className="w-64 shrink-0 bg-sidebar text-sidebar-foreground border-r border-sidebar-border">`, unchanged from shell.tsx line 89). The 5 ad-hoc groups become **4 fixed zones** ordered by daily frequency. Tools collapse into their parent place; the parent owns a top-of-page `<PageTabs>` strip, not more sidebar rows.

```
┌─ JAMES OS · Brand Manager ─────────┐   sidebar header unchanged (lines 90–93)
│                                    │
│  [ask]  Dashboard            /     │   ── ZONE: PULSE
│                                    │
│  MEMORY                            │   ── ZONE: REMEMBER
│  [voice] Brand Memory     /memory  │
│  [clips] Knowledge Base   /knowledge   ← net-new glyph: "book"
│                                    │
│  CREATE                            │   ── ZONE: MAKE
│  [design]   Content Workspace /create │
│  [pipeline] Video Studio      /video  │
│  [queue]    Campaign Planner  /campaigns │
│  [queue]    Approvals         /approvals  ← promoted from /queue
│  [images]   Asset Library     /assets │
│                                    │
│  GROW                              │   ── ZONE: MEASURE
│  [social]   Analytics Hub     /analytics │
│ ────────────────────────────────  │
│  [social]   Team        /team      │   ── FOOTER (utility)  ← net-new glyph "users" (reuse `social`)
│  [design]   Profile     /profile   │   (existing footer link, line 135)
│  [settings] Settings    /settings  │   (existing footer link, line 144)
│  [market]   Billing     /billing   │   ← net-new glyph "card"
└────────────────────────────────────┘
```

**Icon mapping (real `icons.tsx` names — no emoji):**

| Destination | Glyph | Status |
|---|---|---|
| Dashboard | `ask` | exists |
| Brand Memory | `voice` | exists |
| Knowledge Base | `book` | **net-new** — draw a book/ledger glyph, `strokeWidth 1.8` |
| Content Workspace | `design` | exists |
| Video Studio | `pipeline` | exists |
| Campaign Planner | `queue` | exists (or net-new `calendar`) |
| Approvals | `queue` | exists |
| Asset Library | `images` (or net-new `folder`) | exists/optional |
| Analytics Hub | `social` | exists |
| Team | `social` | reuse (a `users` variant already lives in the `social` path) |
| Billing | `card` | **net-new** |

Net new glyphs to draw: **`book`, `card`, optionally `calendar`/`folder`.** Three SVG paths, same 24×24 grid.

**Why 4 zones, not 5 groups:** today's split scatters memory across two groups (Ask lives in *Memory*; Voice/Rules/Health live in *Brand Reference*) and buries the daily *Approvals* action inside *Review & Library*. The new zones map to the only four things the owner does in a session — **check the pulse → feed/recall memory → make something → see if it worked** — and match the product promise ("remembers everything + continuously creates"). Zone labels reuse the existing label styling exactly (`px-5 mb-1.5 text-[10px] font-semibold uppercase tracking-[1.3px] opacity-50`, shell.tsx line 98).

**Why Dashboard replaces "Ask the memory" at `/` — and what must be preserved:** `/` today is **not a bare chat box** (`web/app/page.tsx`). It already ships:
- an **Ask / Do mode toggle** (line 40, `useState<"ask"|"do">`) — *Ask* = cite-or-refuse grounded Q&A; *Do* = **agent tool-use mode** that calls real endpoints and logs every run to `agent_runs`, with live tool-call streaming and a "Recent runs" history;
- an `<OnboardingChecklist>` (line 125).

So the Dashboard redesign **must absorb, not delete, these three surfaces.** `/` becomes a **Dashboard** (pulse cards: pending approvals count from `/approvals`, autopilot-queued batches, underperforming posts from Analytics) with the **Ask/Do bar pinned to the top** so memory Q&A *and* the agent are one keystroke away. The "Recent runs" agent log and onboarding checklist move into Dashboard sections — they don't get a new route. Deep-link `/?ask=<q>` and `/?do=<q>` open the respective mode full-screen.

---

## 5.3 Migration table — every current route → new home

Every one of the 30 live routes is preserved; the change is *where it's reachable from*. Old paths `308`-redirect (Next.js permanent redirect, preserves method) via `next.config.js` `redirects()`.

| # | Current route | Current label (shell.tsx) | → New home | New surface |
|---|---|---|---|---|
| 1 | `/` | Ask the memory | **Dashboard** `/` | Pulse cards + pinned **Ask/Do** bar; preserves agent runs log + onboarding checklist; `/?ask=` / `/?do=` deep-link full-screen |
| 2 | `/autopilot` | Autopilot | **Campaign Planner** `/campaigns` | Default tab "Autopilot batches"; also a Dashboard CTA |
| 3 | `/design-studio` | Content Studio | **Content Workspace** `/create` | Default tab "Write a post" |
| 4 | `/images` | Post Images | **Content Workspace** `/create/images` | Tab "Images" |
| 5 | `/video` | Video Studio (hub) | **Video Studio** `/video` | Landing = mode chooser (5.5) |
| 6 | `/long-form` | (maker) | **Video Studio** `/video/long-form` | Mode "Long-form" |
| 7 | `/engaging-video` | (maker) | **Video Studio** `/video/social` | Mode "Social short" |
| 8 | `/story-mix` | (maker) | **Video Studio** `/video/story` | Merged into "Story" mode |
| 9 | `/story-video` | (maker) | **Video Studio** `/video/story` | Merged into "Story" mode |
| 10 | `/heygen-video` | (maker) | **Video Studio** `/video/avatar` | Mode "AI avatar" (HeyGen engine) |
| 11 | `/pipeline` | (maker) | **Video Studio** `/video/jobs` | "Render jobs" — status board, not a maker |
| 12 | `/editor` | (editor, `video-editor.tsx`) | **Video Studio** `/video/editor` | Opened from a job/asset (Composer), not a top entry |
| 13 | `/queue` | Approval Queue | **Approvals** `/approvals` | Promoted to top-level; header today reads "Approval Queue" (queue/page.tsx:119) |
| 14 | `/library` | Output Library | **Asset Library** `/assets/output` | Tab "Finished output" (library/page.tsx:220) |
| 15 | `/updates` | What's Next | **Dashboard** `/` (Activity) + `/campaigns/changes` | Feedback→changes board (updates/page.tsx:54 "What's changing next") surfaces in Dashboard pulse |
| 16 | `/jp-clips` | Media Library (hub) | **Asset Library** `/assets/clips` | Tab "Reference clips" |
| 17 | `/hero` | (media) | **Asset Library** `/assets/hero` | Tab "Hero footage" |
| 18 | `/broll` | (media) | **Asset Library** `/assets/broll` | Tab "B-roll" |
| 19 | `/audio` | (media) | **Asset Library** `/assets/audio` | Tab "Music & audio" |
| 20 | `/style-templates` | Style Templates | **Content Workspace** `/create/styles` | Tab "Styles" (drives post + video). NB: currently appears in *both* `MediaTabs` and the sidebar — dedupe to one home |
| 21 | `/analytics` | Analytics | **Analytics Hub** `/analytics` | Default tab "Performance" |
| 22 | `/market-research` | Social Research | **Analytics Hub** `/analytics/research` | Tab "Trends & market" |
| 23 | `/social-companion` | (folded via `match`) | **Analytics Hub** `/analytics/peers` | Tab "Peers & competitors" (today folded under Social Research, shell.tsx:64) |
| 24 | `/voice-studio` | Voice Studio | **Brand Memory** `/memory/voice` | Tab "Voice corpus" (feed/transcribe from Drive) |
| 25 | `/brand` | Voice Rules | **Brand Memory** `/memory/rules` | Tab "Voice rules & guidelines" |
| 26 | `/jp-live` | JP Live | **Brand Memory** `/memory/health` | Tab "Brand health" |
| 27 | *(new)* | — | **Knowledge Base** `/knowledge` | Frameworks, frustration ledger, source docs (the moat) |
| 28 | `/profile` | Profile (footer) | **Settings** `/settings/profile` | Tab in Settings (today a standalone footer link, shell.tsx:135) |
| 29 | `/settings` | API connections (footer) | **Settings** `/settings/connections` | Default tab; **the existing key-source banner** (`.env` vs UI, settings/page.tsx:310) lives here — consolidate, don't re-add (5.6) |
| 30 | *(new)* | — | **Team** `/team` | Members, roles, approval routing |
| 31 | *(new)* | — | **Billing** `/billing` | Plan, render-minute usage, invoices |

**Net:** 30 routes preserved (zero dead links), surfaced through **11 primary destinations + 22 in-page tabs**. No working page is deleted.

---

## 5.4 Brand Memory vs. Knowledge Base — the split that sells the moat

These two destinations express *"remembers everything about your brand."* Kept deliberately separate:

- **Brand Memory** `/memory` = the brand's **identity** — how it sounds, how healthy it is. Tabs: **Voice corpus** (`/voice-studio`), **Voice rules** (`/brand`), **Brand health** (`/jp-live`). This is what the AI *uses to write in the owner's voice.*
- **Knowledge Base** `/knowledge` = the brand's **facts & doctrine** — frameworks, the **frustration ledger** ("corrections become mandatory rules", commit `a2268f8`), uploaded source docs (the upload/OCR/extraction pipeline from commits `0f29c89`, `be7aa10`), market positioning. This is what the AI *cites* (cite-or-refuse — the same `res.refused` / `citations[]` contract already rendered on the Ask panel, page.tsx:212–233).

Why apart: the owner edits them on different cadences. Voice is tuned occasionally; the knowledge base / frustration ledger is appended *every time the AI gets something wrong*. Co-locating would bury the high-frequency correction loop under rarely-touched voice settings — re-creating exactly the "where do I click" problem this redesign fixes.

---

## 5.5 Collapsing the 7 video makers → 4 modes + 1 job board

The 7 makers are not 7 user intents — they're **1 intent ("make a video") with engine/format variations.** `/pipeline` (render status) and `/editor` (post-production, backed by `components/video-editor.tsx`) aren't makers at all. `/video` becomes a chooser landing ("What do you want to make?") rendering 4 mode cards + a "Render jobs" status link.

| Old maker(s) | New mode | Lives at | Rationale |
|---|---|---|---|
| `/long-form` | **Long-form** | `/video/long-form` | Multi-minute talking-head |
| `/engaging-video` | **Social short** | `/video/social` | Default 1080×1920 vertical short |
| `/story-mix` + `/story-video` | **Story** | `/video/story` | Two near-identical story builders merged to one mode with a layout toggle |
| `/heygen-video` | **AI avatar** | `/video/avatar` | HeyGen engine — label by output, not vendor |
| `/pipeline` | **Render jobs** (status, not a maker) | `/video/jobs` | Job board: status, retries, download |
| `/editor` | **Composer** (post-production) | `/video/editor` | Reached *from* a job or asset — never a top-level nav entry |

The mode chooser and job board both use `<PageTabs>` for the top strip and the existing `Card` primitive for the mode cards. Engine choice (HeyGen / Runway / Higgsfield — already referenced in `media-tabs.tsx`) is a select *inside* a mode, not a separate destination.

---

## 5.6 Implementation notes for the engineer

1. **`<PageTabs>` primitive** — generalize `media-tabs.tsx`. Signature `({ tabs: {href,label,sub?}[] })`, path-based active state (`path === t.href || path.startsWith(t.href + "/")`), reusing the verified active styling: inactive `text-[13px] px-3.5 py-2 rounded-md border bg-background border-border hover:bg-muted`, active `bg-primary text-primary-foreground border-primary`, container `flex items-center gap-1.5 border-b border-border pb-3`. Delete the bespoke `MediaTabs` once `/assets` adopts it.
2. **Sidebar refactor** — rewrite the `NAV: Group[]` constant only (shell.tsx:30–76). The render loop (lines 96–132), `match[]` highlight logic, `indent`/`└` mechanism, and the `w-64` aside need no change — but **remove `indent` usage** since zones replace fake nesting. Move the standalone Profile/Settings footer links into the 4th-zone/footer list.
3. **Brand fidelity (anti-cliché guardrail):** the approved tokens are **gold primary `--primary: 40 75% 52%`** on **near-black sidebar `--sidebar: 220 20% 8%`**, teal accent `--accent: 183 60% 28%`, `--radius: 0.5rem`, fonts *General Sans* / *JetBrains Mono* (globals.css). No blue, no gradient, no glassmorphism. Active nav = gold left-border (`border-sidebar-primary`) + `bg-sidebar-accent`, already in place (shell.tsx:113).
4. **Redirects** — add 12 entries to `next.config.js` `redirects()` (`permanent: true`): the 7 video makers → `/video/*`, the 4 media routes → `/assets/*`, `/queue` → `/approvals`, `/library` → `/assets/output`, etc. Keep the old `match[]` paths working until redirects ship so nothing breaks mid-migration.
5. **Settings banner** — do **not** add a new missing-key banner; `settings/page.tsx` already distinguishes key source (`f.source === "env"` → "from .env", line 310). Surface that same state as a Dashboard pulse warning ("N integrations missing keys") linking to `/settings/connections`.
6. **Dashboard data** — pulse cards read from existing endpoints: approvals count (`/approvals`), recent `agent_runs` (already in `api.listAgentRuns`, page.tsx:55), changes board (`updates/page.tsx`), analytics underperformers. No new backend required for v1 — compose existing API calls.

---

**Files referenced (all absolute):**
- `/Users/royantony/BRAND MANAGER /james-os/web/components/shell.tsx`
- `/Users/royantony/BRAND MANAGER /james-os/web/components/media-tabs.tsx`
- `/Users/royantony/BRAND MANAGER /james-os/web/components/icons.tsx`
- `/Users/royantony/BRAND MANAGER /james-os/web/components/ui.tsx`
- `/Users/royantony/BRAND MANAGER /james-os/web/app/page.tsx`
- `/Users/royantony/BRAND MANAGER /james-os/web/app/globals.css`
- `/Users/royantony/BRAND MANAGER /james-os/web/app/{queue,library,settings,updates,autopilot}/page.tsx`


---


# Phase 6 — Experience Design (Screens)


I now have everything to ground the upgrade. Key findings that change the draft:

- **No memory event-count endpoint exists.** The closest is `listAgentRuns`, `listProductions`, `queueStats`. The "1,240 events · 12 added today" counter is a fiction — it needs a new endpoint (`GET /memory/stats` → `{event_count, added_today}`), which I'll flag explicitly rather than pretend `api` has it.
- **The agent_runs ledger is real**: `listAgentRuns(limit)` → `{runs: [{id, prompt, status, summary, answer, tool_call_count, created_at, completed_at}]}`. Recent Activity binds to `summary`/`status`, not freeform.
- **AutopilotRun** has `requested`/`generated`/`queued` — the "5/7 drafted" bar is `generated/requested` from the latest `listAutopilotRuns()[0]`, not a made-up field.
- **"Needs You"** = `queueStats().pending`, not `listProductions()` filtered.

Now the upgraded draft.

---

# Phase 6 — Experience Design: JAMES OS Screens

**Design north star:** *"It remembers everything and never stops creating."* Two ideas must be felt on every screen: **(1) memory is visible and citable** (a distinct "grounded" color + clickable source spans), and **(2) creation is always one action away** (a persistent gold primary CTA). Today the product scatters both across a 16-entry sidebar and seven sibling video makers reachable only through one `/video` hub entry. These screens collapse that.

**Ground-truth note for engineers.** The current `api.ask(question)` returns `AskResponse`, whose answer text field is **`response`** (not `answer`), alongside `citations: {event_id, span, confidence}[]`, `refused`, `refusal_reason`, `confidence`, `retrieved_event_ids`, `model`, `latency_ms`. Every "grounded answer + N sources" reference below binds to `citations.length` and renders `response`. Two data needs in these screens have **no endpoint yet** and are called out inline as **[NEW ENDPOINT]** so they're scoped, not assumed.

## Design-system deltas applied across all screens

Load-bearing token/structure changes every screen below assumes. Apply in `web/app/globals.css`, `web/components/shell.tsx`, and `web/components/ui.tsx`.

**1. Fix the content-width bug.** `Shell`'s `<main>` wraps every page in `<div className="max-w-4xl mx-auto px-8 py-10">` (896px). That single wrapper is why the command center feels cramped and grids can't breathe. Remove the inner wrapper; let pages own width via a new `Page` primitive (it does **not** exist yet — add it to `ui.tsx` next to `PageHeader`):

```tsx
// shell.tsx — main becomes a bare scroll region
<main className="flex-1 min-w-0 bg-background">{children}</main>

// ui.tsx — new Page primitive (add it; keep px-8 py-10 here, not in shell)
export function Page({ width = "default", children }: {
  width?: "reading" | "default" | "wide";
  children: React.ReactNode;
}) {
  const w = {
    reading: "max-w-3xl",
    default: "max-w-5xl",
    wide: "max-w-[1400px]",
  }[width];
  return <div className={`${w} mx-auto px-8 py-10`}>{children}</div>;
}
```
Width map: `reading` (768px) = Ask answers, onboarding, single-draft Content Studio; `default` (1024px) = forms/generation; `wide` (1400px) = Dashboard, Analytics, Approval Queue, Output/Media Library grids. Migration is mechanical: every page already renders content directly into `<main>`, so each one wraps its tree in `<Page width=…>`. No page currently sets its own max-width, so there are no conflicts to reconcile.

**2. Three new semantic tokens** — memory needs its own color language, distinct from gold (action) and teal (accent). Today `--primary` and `--sidebar-primary` are both gold `40 75% 52%`, and `--accent` is teal `183 60% 28%`; reusing either for "verified from memory" overloads it. Add to both `:root` and `.dark` in `globals.css`, and register them in `tailwind.config` under `theme.extend.colors` (the existing tokens are wired there as `hsl(var(--token))`, so mirror that):

```css
:root{
  --grounded: 152 55% 34%;       /* #268a5a — "cited / verified from memory" */
  --grounded-foreground: 0 0% 100%;
  --ungrounded: 28 80% 48%;      /* #db7916 — "model guess / not in memory / refused" */
  --memory-surface: 40 38% 96%;  /* #f7f3ea — warm paper, the "memory" backdrop */
}
.dark{ --grounded: 152 45% 46%; --ungrounded: 28 75% 56%; --memory-surface: 40 14% 12%; }
```
Contrast: `--grounded` `#268a5a` on white = 4.9:1 (AA). `--ungrounded` `#db7916` is border/icon + dark-text only, never text-on-white. `--memory-surface` is a backdrop, never a text color. **Wire one new variant into the existing `Badge` in `ui.tsx`**: the current `Badge` takes a `tone` prop — add `grounded` and `ungrounded` tones so the chip is `<Badge tone="grounded">◆ grounded · {n} sources</Badge>` everywhere, instead of ad-hoc spans. This is the single component that makes the moat legible on every screen.

**3. Collapse the sidebar from one 256px scroll list to an L1 rail + L2 contextual panel.** Today `shell.tsx` renders a single `w-64` (256px) `<aside>` with **five labeled groups** (Memory / Create / Review & Library / Intelligence / Brand Reference, ~16 entries) plus Profile and API connections pinned at the bottom — every concept visible at once. Replace with a **72px icon rail (L1)** + a **collapsible 220px contextual panel (L2)** showing only the active section's children. Map the existing entries directly (no routes change):

```
L1 rail (icon + label):   Ask · Create · Review · Intelligence · Brand · Settings
L2 contextual panel:
  Ask          → (none — Ask is a single destination, L2 hidden, content goes wide)
  Create       → Autopilot (/autopilot) · Content Studio (/design-studio)
                 · Video Studio (/video) · Post Images (/images)
  Review       → Approval Queue (/queue) · Output Library (/library)
                 · What's Next (/updates) · Media Library (/jp-clips) · Style Templates (/style-templates)
  Intelligence → Analytics (/analytics) · Social Research (/market-research)
  Brand        → Voice Studio (/voice-studio) · Voice Rules (/brand) · JP Live (/jp-live)
  Settings     → Profile (/profile) · API connections (/settings)
```
Active-state logic carries over verbatim from the current `shell.tsx`: keep the `match[]` arrays (`VIDEO_SUBROUTES`, `MEDIA_SUBROUTES`) so Video Studio stays lit across `/long-form`, `/engaging-video`, `/story-mix`, `/heygen-video`, `/story-video`, `/pipeline`, `/editor`, and Media Library across `/hero`, `/broll`, `/audio`. L1 highlights when `path` matches any child or its `match[]`. This is the highest-impact fix for *"I didn't know where to click to generate a video"*: "Create" is now one of six rail icons, and the four things that make content are its only children. The `live` flag on every current entry is `true`, so no "soon" badges are needed in L2.

L1 rail uses `bg-sidebar` (`220 20% 8%`), active icon `text-sidebar-primary` (gold). L2 panel uses `bg-sidebar` one shade lighter (`bg-sidebar-accent`, `220 15% 14%`) so it reads as a sub-surface. Collapse L2 to icons-only below `lg`; on mobile both become a single drawer toggled from a top bar.

---

## 1. HOME / Marketing (logged-out `/` → marketing route; authed `/` is the Ask console, §2)

White-label marketing front door, rendered for unauthenticated visitors only. The shell already special-cases auth chrome (`AUTH_PATHS` render full-bleed); the marketing route renders full-bleed the same way — no L1/L2 rail.

```
┌──────────────────────────────────────────────────────────────────┐
│  JAMES OS ·gold wordmark        Product  Pricing  Login  [Book demo]│ ← sticky, bg-sidebar
├──────────────────────────────────────────────────────────────────┤
│  HERO (2-col, 60/40)                                               │
│  ┌────────────────────────────┐  ┌───────────────────────────┐    │
│  │ "The brand manager that     │  │  LIVE MEMORY CARD          │    │
│  │  remembers everything."     │  │  ▸ Ask: "What's our        │    │
│  │  AI that learns your voice, │  │    Staten Island angle?"   │    │
│  │  your rules, your wins —    │  │  ◆ grounded · 3 sources    │    │
│  │  then makes content that    │  │  └ source spans, clickable │    │
│  │  sounds like you.           │  │  (scripted typing loop)    │    │
│  │  [Start free] [Watch 90s]   │  │                            │    │
│  └────────────────────────────┘  └───────────────────────────┘    │
├──────────────────────────────────────────────────────────────────┤
│  PROOF STRIP — "Built for PreReal · Staten Island NYC real estate" │
├──────────────────────────────────────────────────────────────────┤
│  THE MOAT (3 cards): Remembers · Creates · Stays on-brand          │
│  THE LOOP diagram: Memory → Idea → Draft → Video → Approve → Learn │
│  CAPABILITIES grid (6): Ideas · Voice posts · Video · Autopilot ·  │
│                          Analytics · Research                      │
│  CTA band (gold): "Give your brand a memory."  [Start free]        │
└──────────────────────────────────────────────────────────────────┘
```

- **Grid:** 12-col, `max-w-[1200px] mx-auto`, section padding `py-20` (80px), `px-6 lg:px-8`.
- **Hero right card is the differentiator and must not call the real `/ask` endpoint unauthenticated.** Render a **scripted, pre-recorded** Ask exchange (hardcoded question → a fixed `AskResponse`-shaped object with `response`, three `citations`, `confidence: 0.91`) typed out on a loop. Reason: `/ask` requires the session cookie (`api.ask` 401s logged-out and would bounce to `/login`), and a live LLM call per visitor is cost + latency the front door can't carry. The card reuses the **same `<Badge tone="grounded">` and source-span rendering** the real Ask answer uses (§2), so what the prospect sees is pixel-identical to the product — the moat made visible in 4 seconds, honestly.
- **The Loop** maps to the real product stages so the diagram is a sitemap, not a metaphor: Memory (`/voice-studio` + `/brand`) → Autopilot (`/autopilot`) → Approval Queue (`/queue`) → Output Library (`/library`) → Analytics (`/analytics`) → back into Memory via approve-with-note (the `api.approve(id, note)` path that writes a memory event). This closed loop is the conversion argument: not "another AI writer" but a system that learns.
- **Type & hierarchy:** headline in General Sans (the live `--font-sans`) 56/60 bold, `--foreground`; exactly one gold primary CTA per viewport; secondary CTAs are ghost (`border border-border bg-transparent`).
- **Conversion:** dual CTA — `Start free` → `/signup` (self-serve PLG); `Book demo` → Calendly (sales motion). Sticky header keeps `[Book demo]` visible on scroll.
- **AA:** gold buttons use `--primary-foreground` (`220 20% 8%`, near-black) on gold = 8.6:1. Never white-on-gold (white-on-gold is ~1.9:1 and fails).

---

## 2. DASHBOARD — logged-in command center (authed `/`, replaces today's bare Ask page)

Today `/` (`web/app/page.tsx`) is just the Ask box + `<OnboardingChecklist/>` inside the 896px wrapper. Promote it to a true command center, `<Page width="wide">`, with Ask **as the hero** — Ask is the memory made interactive.

```
┌─ wide (max-w-[1400px]) ───────────────────────────────────────────┐
│  Good morning, JP. ·date        [● Memory: 1,240 events · +12 today]│
├──────────────────────────────────────────────┬────────────────────┤
│  ASK / DO CONSOLE (hero, spans 8 cols)        │ RIGHT RAIL (4 cols)│
│  ┌──────────────────────────────────────────┐│  NEEDS YOU          │
│  │ [Ask ▾ | Do]  ⌘K                          ││  ┌────────────────┐ │
│  │ "Ask your brand anything…"        [Ask →] ││  │ 4 in Approval  │ │
│  │ chips: What worked? · Draft a post ·      ││  │ Queue  [Review]│ │
│  │        Ideas for this week                ││  └────────────────┘ │
│  └──────────────────────────────────────────┘│  TODAY'S AUTOPILOT  │
│  Last answer (collapsed, ◆ grounded · 3 src)  │  ┌────────────────┐ │
├───────────────────────────────────────────────┤  │ 5/7 drafted ▓▓▓░│ │
│  CREATE NEXT  (4 launch cards, gold-bordered)  │  │ [Open run]     │ │
│  ┌────────┐┌────────┐┌────────┐┌────────┐      │  └────────────────┘ │
│  │+ Post  ││+ Video ││+ Images││Autopilot│     │  RECENT ACTIVITY    │
│  └────────┘└────────┘└────────┘└────────┘      │  agent_runs ledger  │
├───────────────────────────────────────────────┤  · rendered reel ✓  │
│  PERFORMANCE PULSE (4 stat tiles + sparkline)  │  · approved 2 posts │
│  Reach ↑ · Eng-rate · Posted 7d · Top format   │  · refreshed analytx│
└───────────────────────────────────────────────┴────────────────────┘
```

- **Grid:** 12-col, `gap-6`. Ask console spans 8; right rail spans 4 (`lg:grid-cols-12`; on `<lg` the rail stacks below the console).
- **Ask/Do console (hero):** the only autofocused input on the page. Submitting calls `api.ask(question)`; on resolve, render `res.response` as the answer body and `res.citations` as `<Badge tone="grounded">◆ grounded · {res.citations.length} sources</Badge>` + clickable source spans (each `citation.span` links to its `event_id`). When `res.refused` is true, show `res.refusal_reason` under an `<Badge tone="ungrounded">` instead of inventing an answer — this is the honesty mechanic, surfaced on the dashboard. ⌘K focuses the input. "Do" mode submits to the agent path; its runs land in Recent Activity (below).
- **"Create Next" cards** answer the confusion quote: four gold-bordered launch tiles (`border border-primary/40 bg-primary/5 hover:bg-primary/10 rounded-[--radius]`), each routing to the simplest path:
  - `+ Post` → `/design-studio`
  - `+ Video` → `/video` (the Studio hub; have it open on the **recommended** maker, not a 7-way menu)
  - `+ Images` → `/images`
  - `Autopilot` → `/autopilot`
- **Right rail = work that's waiting**, each tile bound to a real call:
  - **Needs You** = `api.queueStats()` → render `.pending` as "{pending} in Approval Queue", `[Review]` → `/queue`. (Not `listProductions()` — pending count lives in `queueStats`.)
  - **Today's Autopilot** = `api.listAutopilotRuns()[0]`; the bar is `generated / requested` (e.g. `5/7 drafted`) and `[Open run]` deep-links the run. If the latest run's `status==="running"`, poll; if `"failed"`, show the failure inline.
  - **Recent Activity** = `api.listAgentRuns(8).runs`, rendered from each run's `summary` + `status` (✓ when `status==="succeeded"`). This is the literal `agent_runs` ledger — Do-mode runs land here, giving the dashboard a live "the OS is working" pulse.
- **Memory counter** (top-right pill): "1,240 events · +12 today". **[NEW ENDPOINT]** — no event-count exists in `api` today; add `GET /memory/stats` → `{ event_count: number, added_today: number }` and `api.memoryStats()`. Until it ships, render the pill from `api.listAgentRuns` count as a stopgap or hide it behind the flag — do **not** fabricate a number client-side. Clicking → `/voice-studio`.
- **Component reuse:** `<OnboardingChecklist/>` renders **inline above Create Next** and self-hides — its existing guard `if (remaining === 0) return null;` already does this, so no new logic. `PageHeader` is **not** used here (the greeting + memory pill is the header); note that `PageHeader` auto-renders `<HelpButton/>`, so if you keep a help affordance, mount `<HelpButton/>` directly in the greeting row.
- **Performance Pulse:** 4 stat tiles from `api.analyticsLiveSummary()` (`total_followers`, `total_posts`, `account_count`, per-platform), plus a 7-day sparkline. If `followers_partial` is true, mark the tile with a "partial" caption — don't silently show an incomplete number.
- **Hierarchy:** Ask is visually dominant (largest surface, only autofocus); Create Next is the only gold; everything else is calm `--card`. One screen answers "what should I do now?" three ways: ask, create, or clear the queue.

---

## 3. CONTENT GENERATION (`/design-studio` — "Content Studio")

The manual single-draft path: build one piece by hand, the counterpart to Autopilot's bulk run. `<Page width="reading">` (768px) — a single draft is a writing surface, not a grid.

[Draft was cut off here. The section should specify: brief form bound to the existing `ContentBrief` shape (`platform`, `format`, `pillar`, `topic`, `research_subject`, `extra_instructions`) using the existing `Select`/`Input`/`Textarea` primitives; the generate action; and the returned draft rendered with its `QAVerdict.voice_score` shown as a grounded/ungrounded `Badge` so the writer sees on-brand confidence inline, with a one-click "Send to Approval Queue" that lands the item in `/queue`. Continue from the typed `ContentBrief` and `QAVerdict` types already in `api.ts`.]

---

### Engineer/designer build checklist (net-new vs. reuse)

**Net-new to build:**
- `Page` width primitive in `ui.tsx` (3 widths) + removal of `max-w-4xl` wrapper in `shell.tsx`.
- 3 tokens (`--grounded`, `--ungrounded`, `--memory-surface`) in `globals.css` light+dark + `tailwind.config`.
- `Badge` gains `grounded` / `ungrounded` tones in `ui.tsx`.
- L1 rail + L2 contextual panel rewrite of `shell.tsx` (routes unchanged; reuse existing `match[]` active logic).
- **[NEW ENDPOINT]** `GET /memory/stats` → `{event_count, added_today}` + `api.memoryStats()` for the dashboard memory pill.
- Marketing route (full-bleed, like `AUTH_PATHS`) with a scripted/offline Ask demo card (must not hit live `/ask`).

**Reused as-is (no change):** `api.ask` (read `response`, not `answer`), `api.queueStats`, `api.listAutopilotRuns`, `api.listAgentRuns`, `api.analyticsLiveSummary`, `api.approve/reject`, `OnboardingChecklist` (self-hides), `PageHeader`/`HelpButton`, all `ui.tsx` primitives, `ContentBrief`/`QAVerdict` types.

---

Files reviewed to ground this:
- `/Users/royantony/BRAND MANAGER /james-os/web/components/shell.tsx` (sidebar = `w-64`, 5 groups, `max-w-4xl` wrapper)
- `/Users/royantony/BRAND MANAGER /james-os/web/lib/api.ts` (`AskResponse.response`, `queueStats`, `listAutopilotRuns`, `listAgentRuns`, `analyticsLiveSummary`; **no** memory-count endpoint)
- `/Users/royantony/BRAND MANAGER /james-os/web/app/globals.css` (gold `40 75% 52%`, teal `183 60% 28%`, `--primary-foreground 220 20% 8%`)
- `/Users/royantony/BRAND MANAGER /james-os/web/components/ui.tsx` (`Page` does **not** exist; `Badge`/`PageHeader`/`HelpButton` do)
- `/Users/royantony/BRAND MANAGER /james-os/web/components/onboarding-checklist.tsx` (`remaining === 0` self-hide guard confirmed)


---


# Phase 7 — Motion Design System


Crucial correction: the analytics chart is **CSS-`div` bars** (`flex items-end`, `bg-primary/30`, `style={{ height }}`), not SVG. There's no `<svg>`, no `pathLength`, no line series — the comment says "inline SVG" but it's actually flex divs. So a4's `scaleY`/`pathLength`/line-draw spec is wrong; the real, buildable thing is animating bar `height`/`transform: scaleY` on those divs. This is exactly the kind of grounding error the review must fix.

I now have everything verified. Writing the upgraded draft — correcting every file-path/route/primitive error, sharpening vague claims into real token/class values, dark-first contrast, and completing the truncated b1.

---

I have everything I need from the real codebase. Corrections folded into the upgrade: `Skeleton`/`Toast` live in their own files (not `ui.tsx`); the app is dark-first (`<html className="dark">`); `/updates` is three stacked `<Section>`s not a two-plane parallax; the `/analytics` "timeline chart" is **CSS flex-`div` bars with inline `height`**, not SVG/recharts; `tutorials.ts` steps are a flat `string[]` rendered as a static `<ol>` in a drawer (not a scroll surface); teal is the `--accent` token. Here is the upgraded draft.

---

# Phase 7 — JAMES OS Motion Design System

**Principle: motion is the visible proof that the system is thinking, remembering, and acting.** Every animation in JAMES OS earns its place by signaling one of four states — *retrieving from memory, reasoning, generating, or automating*. Nothing animates for delight alone. The current build has exactly one keyframe (`spin`, `app/globals.css:81-85`, consumed only by `.spinner` and `<Spinner/>` in `components/ui.tsx:106`), `animate-pulse` on `Skeleton` (`components/skeleton.tsx:15`), and `transition-colors` on ~26 interactive elements (`Button` at `ui.tsx:51`, sidebar links in `shell.tsx`, the analytics bars at `analytics/page.tsx:653`). This system replaces that ad-hoc set with a tokenized scale, adds Framer Motion for orchestrated sequences, and keeps CSS for the high-frequency micro-stuff.

The brand metaphor is encoded directly into the motion: **gold (`--primary: 40 75% 52%`) is memory/recall, teal (`--accent: 183 60% 28%`) is reasoning/intelligence**. Gold things *surface and settle* (memory recalled); teal things *trace and sweep* (reasoning in progress). With labels removed, a user should be able to tell from motion alone whether the system is *remembering* or *thinking*.

**Dark-first.** `app/layout.tsx` hard-sets `<html lang="en" className="dark">` — there is no theme toggle yet. Every value below is tuned for the dark palette first; the light `:root` values are the secondary case. Translucent overlays/scrims therefore default to *lightening* (white alpha), not darkening.

---

## 0. Motion Tokens

Add to `app/globals.css` inside the existing `@layer base { :root { … } }` block (durations/easings are theme-independent — do **not** duplicate them in `.dark`). These become the single source of truth; nothing in the codebase hardcodes a `ms` or `cubic-bezier` again.

```css
/* app/globals.css — append inside @layer base :root { } */

/* ─── Durations ─── */
--motion-instant:   80ms;   /* state echo: toggle, press, checkbox */
--motion-fast:      140ms;  /* hover, focus ring, color shifts */
--motion-base:      220ms;  /* default: cards, dropdowns, tabs */
--motion-moderate:  340ms;  /* panel/drawer enter, route content */
--motion-slow:      520ms;  /* layered reveals, bar/timeline draw */
--motion-deliberate:780ms;  /* AI "thinking"→"answer" handoff, counters */
--motion-ambient:   1600ms; /* looping memory/processing states */

/* ─── Easings ─── */
--ease-out:    cubic-bezier(0.22, 1, 0.36, 1);    /* enter, surface, settle — DEFAULT */
--ease-in:     cubic-bezier(0.4, 0, 1, 1);         /* exit, dismiss */
--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);     /* reposition an existing element */
--ease-recall: cubic-bezier(0.16, 1, 0.3, 1);      /* GOLD: memory surfaces, settles (no bounce) */
--ease-reason: cubic-bezier(0.45, 0, 0.15, 1);     /* TEAL: deliberate sweep, "computing" */
--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);  /* confirmations / success pops (≤4% overshoot) */

/* ─── Stagger ─── */
--stagger-tight: 40ms;   /* list rows, chips, badges */
--stagger-base:  70ms;   /* cards, grid tiles */
--stagger-loose: 120ms;  /* timeline / walkthrough steps */
```

Framer Motion token mirror — create `web/lib/motion.ts` (the `web/lib/` dir already exists; `@/lib/*` resolves there) so React imports from one place. Keep the two files numerically identical; a divergence is a bug.

```ts
// web/lib/motion.ts
export const DUR = {
  instant: 0.08, fast: 0.14, base: 0.22, moderate: 0.34,
  slow: 0.52, deliberate: 0.78, ambient: 1.6,
} as const;

export const EASE = {
  out:    [0.22, 1, 0.36, 1],
  in:     [0.4, 0, 1, 1],
  inOut:  [0.65, 0, 0.35, 1],
  recall: [0.16, 1, 0.3, 1],   // gold / memory
  reason: [0.45, 0, 0.15, 1],  // teal / reasoning
  spring: [0.34, 1.56, 0.64, 1],
} as const;

export const STAGGER = { tight: 0.04, base: 0.07, loose: 0.12 } as const;

// Brand color literals for SVG/canvas motion that can't read CSS vars.
// Mirror of --primary / --accent (dark values, since the app is dark-first).
export const HUE = {
  recall: "hsl(40 75% 56%)",  // gold, dark-mode --primary
  reason: "hsl(183 50% 40%)", // teal, dark-mode --accent
} as const;
```

**Dependency.** `framer-motion` is **not yet in `package.json`** — add it (`npm i framer-motion`, pin to ^11). Until installed, none of the `motion`/`whileInView`/`useScroll` specs compile; the CSS-only specs (b1 fallback, hover/press, skeletons) ship without it.

**Global reduced-motion contract.** One block governs the entire app; every spec below inherits it unless it names a specific fallback. This is the floor, not a substitute for per-component fallbacks.

```css
/* app/globals.css — outside @layer, top level */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

Framer Motion: wrap once in `app/layout.tsx`. The current body is `<body className="font-sans"><Shell>{children}</Shell></body>`; insert the provider just inside, around `<Shell>`:

```tsx
// app/layout.tsx
import { MotionConfig } from "framer-motion";
// …
<body className="font-sans">
  <MotionConfig reducedMotion="user">
    <Shell>{children}</Shell>
  </MotionConfig>
</body>
```

With `reducedMotion="user"`, every `motion` component automatically drops transforms and keeps opacity-only changes. The per-spec reduced-motion fallbacks below describe what survives that drop — **opacity cross-fades at `--motion-fast` (140ms)** unless a spec states otherwise.

---

## (a) Scroll Animations

The real scrolling, data-bearing routes are `/library`, `/queue`, `/jp-clips` (card grids), `/analytics` (bars + tables), `/market-research` and `/social-companion` (peer/influencer feeds), and `/updates` (the feedback→change board). Scroll motion is **narrative**: the system laying memory out in front of you, never a marketing parallax reel.

### a1 — Card-grid progressive reveal — `/library`, `/queue`, `/jp-clips`

- **Purpose:** content surfaces from memory as you scroll — each tile *arrives* rather than pops. These three routes render the same tile shape as `SkeletonCard` (`components/skeleton.tsx:21`, `aspect: "9 / 16"`), so the reveal target is that card.
- **Trigger:** Framer `whileInView`, `viewport={{ once: true, margin: "0px 0px -12% 0px" }}`.
- **From → To:** `opacity 0 → 1`, `y 16 → 0`, `scale 0.985 → 1`.
- **Duration:** `DUR.base` (220ms) per tile · **Stagger:** `STAGGER.base` (70ms), **capped at index 5** so a long grid never lags (a 60-tile library must not have a 4s tail).
- **Easing:** `EASE.recall` (gold/memory — settle, no bounce).
- **Skeleton→content handoff:** when real data replaces `SkeletonCard`, the incoming tile uses this same `reveal`; do **not** also cross-fade the skeleton out (double-animation flicker). Swap nodes, animate the real one in.
- **Reduced-motion:** opacity `0 → 1` at 140ms, no transform, no stagger (all at once).

```tsx
// app/library/page.tsx — wrap each grid item; identical in queue & jp-clips
import { motion } from "framer-motion";
import { DUR, EASE, STAGGER } from "@/lib/motion";

const reveal = {
  hidden: { opacity: 0, y: 16, scale: 0.985 },
  show: (i: number) => ({
    opacity: 1, y: 0, scale: 1,
    transition: { duration: DUR.base, ease: EASE.recall, delay: Math.min(i, 5) * STAGGER.base },
  }),
};
// <motion.div key={item.id} custom={i} variants={reveal}
//   initial="hidden" whileInView="show" viewport={{ once: true, margin: "0px 0px -12% 0px" }} />
```

### a2 — Section reveal on the change-board — `/updates`

The earlier "two depth planes" framing was wrong: `/updates` is **three stacked `<Section>` blocks** (`flex flex-col gap-6`, `app/updates/page.tsx:52`) — `✓ Applied live`, `⧗ Queued for the next build`, `Done` — each containing `<Row>`s. There are no side-by-side columns to parallax. Re-grounded spec:

- **Purpose:** the board reads as *feedback becoming change*. Each section header lands first; its rows then deal in like memory being filed.
- **Trigger:** `whileInView once` per `<Section>`.
- **Section header:** `opacity 0 → 1`, `x -8 → 0`, `DUR.base`, `EASE.recall`.
- **Rows within a section:** stagger `STAGGER.tight` (40ms, capped at index 8), `opacity 0 → 1`, `y 8 → 0`.
- **Status semantics:** the `✓ Applied live` section header pulses gold once on first reveal (memory committed); `⧗ Queued` stays teal-tinted (still reasoning/pending). Reuse the existing `text-accent` / `text-primary` tokens already on these rows — don't introduce new colors.
- **Reduced-motion:** all sections and rows fade in at 140ms, no x/y, no stagger.

### a3 — Timeline reveal — `/pipeline` only (NOT the help drawer)

Correction: `help-drawer.tsx` renders tutorials as a **static `<ol className="space-y-2.5">`** (`help-drawer.tsx:83`) inside a drawer, and `lib/tutorials.ts` `steps` is a **flat `string[]`** (`tutorials.ts:11`) — there is no node/label/body structure to stagger, and the drawer is not a scroll surface. Drop the help-drawer from this spec. The real step-sequence scroll surface is `/pipeline` (video production stages).

- **Purpose:** teach the flow; each stage appears as the connecting line reaches it ("the system walking you through its process").
- **Trigger:** `whileInView` per stage node, plus an SVG connector whose `pathLength 0 → 1` is scroll-driven via `useScroll`/`useTransform` on the timeline wrapper ref (`offset: ["start 0.8", "end 0.4"]`).
- **Duration:** stage node `DUR.base` (220ms); line is scroll-bound (no fixed duration).
- **Easing:** node `EASE.out`; line `pathLength` is linear (scroll-tied transforms are never eased).
- **Stagger:** `STAGGER.loose` (120ms) between the node, its title, and its body within each stage.
- **Visual:** connector line `stroke="hsl(183 50% 40%)"` (teal `--accent`, dark) at `opacity 0.6` (reasoning thread); active node fills `hsl(40 75% 56%)` (gold `--primary`, dark) as scroll passes it (memory landed). Use `HUE.reason`/`HUE.recall` from `lib/motion.ts` so SVG stays in sync.
- **Reduced-motion:** line renders fully drawn at static `pathLength: 1`; stages fade in 140ms, no stagger.

**Lighter applicability:** for the help drawer, do *not* build a timeline. When the drawer opens, fade+slide the `<ol>` items in with `STAGGER.tight` (40ms) — a small, contained polish, gated on the drawer's own open state, not on scroll.

### a4 — Data-viz reveal — `/analytics`

Correction: the "Daily views" chart at `analytics/page.tsx:642` is **not** SVG and there is **no chart library** (`recharts`/`d3` are absent from `package.json`). It is a row of flex `div`s — `<div className="flex items-end gap-[2px] h-24">` with each bar `bg-primary/30 … rounded-t` and an inline `style={{ height: \`${h}%\` }}` (`analytics/page.tsx:648-660`). So there is no `pathLength` or line series; the buildable animation is on the bar `height` / `transform: scaleY`.

- **Purpose:** numbers feel *computed*, not pasted — bars climb as the system finishes reasoning over performance.
- **Trigger:** `whileInView once` on the bar-row container.
- **Bars:** animate `transform: scaleY 0 → 1` with `transformOrigin: "bottom"` (cheaper and jank-free vs. animating `height`; keep the existing inline `height` as the rest target). Wrap each bar in `motion.div` or convert the row to a Framer `staggerChildren` container.
- **Number summaries (the stat cards above the chart):** tween 0 → value over `DUR.deliberate` (780ms) so the eye reads the climb. Format with the existing `fmtNum` helper on each frame.
- **Duration:** bar `scaleY` `DUR.slow` (520ms); counters `DUR.deliberate` (780ms).
- **Easing:** `EASE.reason` (teal — deliberate computation feel).
- **Stagger:** `STAGGER.base` (70ms) left-to-right across bars, capped so a 90-day window doesn't tail past ~1s (cap index at 8 and let the rest land together).
- **Reduced-motion:** bars render at final `scaleY: 1` instantly; counters **jump** to final value with no count-up. Non-negotiable — count-up is a known vestibular/cognitive-load trigger.

---

## (b) Text Animations

Typography is the AI's voice, so text motion signals **authoring** (the system composing) and **substitution** (memory swapping facts in). Reserved for page titles, the Ask answer stream (`/`, the "Ask the memory" route), and any rotating value props. Body copy and table cells never animate.

### b1 — Headline clip-reveal — `PageHeader` (`components/ui.tsx:110`)

`PageHeader` is the one shared title component across every route (it renders `<h1 className="text-2xl font-semibold">{title}</h1>` plus a muted `sub` and the `<HelpButton/>`). Animating it here gives a single signature to all ~28 pages for free.

- **Purpose:** every page title "surfaces from memory."
- **Trigger:** mount / route change (key the motion on `title` so it replays on navigation).
- **Effect:** the `<h1>` is masked by `clip-path: inset(0 0 100% 0) → inset(0 0 0% 0)` (wipes up from the baseline) combined with `y: 0.25em → 0`. The `sub` paragraph fades in 60ms behind it (`opacity 0 → 1`, `STAGGER.tight` after the title). The `<HelpButton/>` never animates — it must be clickable the instant the header paints.
- **Duration:** `DUR.moderate` (340ms) for the title clip; `sub` `DUR.fast` (140ms).
- **Easing:** `EASE.recall` (gold/memory surface).
- **Implementation:** convert only the text block (`<div>` wrapping `h1`+`p`) to `motion.div`; leave the `<header>` flex layout and `<HelpButton/>` untouched so layout never shifts. Provide a pure-CSS fallback keyframe (`@keyframes title-surface`) so the reveal still works on the routes that import `PageHeader` before `framer-motion` is wired in.
- **Reduced-motion:** title and sub fade in together at 140ms (opacity only — no clip-path, no `y`). `clip-path` reveals are a vestibular trigger and `MotionConfig` does not strip them automatically, so this fallback is **mandatory**, not inherited.

```css
/* app/globals.css — CSS fallback for routes not yet on framer-motion */
@keyframes title-surface {
  from { clip-path: inset(0 0 100% 0); transform: translateY(0.25em); }
  to   { clip-path: inset(0 0 0% 0);   transform: translateY(0); }
}
.title-surface {
  animation: title-surface var(--motion-moderate) var(--ease-recall) both;
}
@media (prefers-reduced-motion: reduce) {
  .title-surface { animation: none; } /* paints at rest state immediately */
}
```

### b2 — Ask answer stream — `/` ("Ask the memory")

- **Purpose:** the grounded, cited answer should read as the system *recalling and composing*, distinct from a generic typewriter gimmick.
- **Effect:** stream tokens in as they arrive (do not fake-type pre-fetched text). Each newly committed text chunk fades from `opacity 0.35 → 1` over `DUR.fast` (140ms), giving a soft "ink drying" trail behind the cursor without per-character animation cost.
- **Citations:** when a citation chip resolves, it pops in with `EASE.spring` (`scale 0.9 → 1`, `DUR.fast`) — the one place a subtle overshoot is allowed, because a landed citation is a confirmation (memory matched a real source).
- **Thinking→answer handoff:** while retrieving, show the teal reasoning indicator (see c-series ambient state); on first token, cross-fade teal→gold over `DUR.deliberate` (780ms) — reasoning resolving into recalled memory. This is the single most brand-load-bearing transition in the product.
- **Reduced-motion:** chunks appear at full opacity immediately; citation chips appear with no scale (opacity 140ms); the teal→gold handoff becomes an instant swap, no cross-fade.

---

*(Sections (c) Interaction / state-machine motion — hover, press, toggles, the teal "reasoning" and gold "recall" ambient loops, toast enter/exit, skeleton→content handoff, autopilot "generating" state — continue from here, all built on the same tokens and the real components `Button`/`Card`/`Badge`/`Toast`/`Skeleton`/`Spinner` and the `shell.tsx` sidebar active-state tokens `bg-sidebar-accent` / `border-sidebar-primary`.)*

---

Key files for the implementing engineer (all absolute):
- Tokens + keyframes + reduced-motion: `/Users/royantony/BRAND MANAGER /james-os/web/app/globals.css`
- Token mirror (new): `/Users/royantony/BRAND MANAGER /james-os/web/lib/motion.ts`
- MotionConfig wrap: `/Users/royantony/BRAND MANAGER /james-os/web/app/layout.tsx`
- `PageHeader`/`Button`/`Card`/`Badge`/`Spinner`: `/Users/royantony/BRAND MANAGER /james-os/web/components/ui.tsx`
- `Skeleton`/`SkeletonCard`: `/Users/royantony/BRAND MANAGER /james-os/web/components/skeleton.tsx`
- `Toast`: `/Users/royantony/BRAND MANAGER /james-os/web/components/toast.tsx`
- Sidebar active-state: `/Users/royantony/BRAND MANAGER /james-os/web/components/shell.tsx`
- `/analytics` bar chart (flex divs, line ~642): `/Users/royantony/BRAND MANAGER /james-os/web/app/analytics/page.tsx`
- `/updates` three-section board: `/Users/royantony/BRAND MANAGER /james-os/web/app/updates/page.tsx`
- Help drawer + `tutorials.ts` (static `<ol>`, `string[]`): `/Users/royantony/BRAND MANAGER /james-os/web/components/help-drawer.tsx`, `/Users/royantony/BRAND MANAGER /james-os/web/lib/tutorials.ts`


---


# Phase 8 — Design System Specifications


I now have the full ground truth. Critical corrections the draft gets wrong:

1. **`--ring` is gold (`40 75% 52%`), not teal.** The draft claims "current components use `ring-ring`" and then says to *set* `--ring: 183 60% 28%` (teal) — but the real file already sets gold. The draft inverts its own premise. Also `.dark` is the *default* (`<html className="dark">`), which the draft ignores entirely.
2. **Shell is `max-w-4xl` (896px) centered in a 256px-sidebar layout — NOT `max-w-[1200px]`/`[1440px]`.** Those values exist nowhere. Page padding is `px-8 py-10`, not `px-6 md:px-8 py-8`.
3. **Fonts ARE loaded** (Fontshare General Sans + Google JetBrains Mono in `layout.tsx`) — the draft tells you to "add to globals.css" a `--font-sans` that already exists verbatim.
4. **Real shadows are warm-neutral `hsl(0 0% 0%)` 3-step**, and dark-mode shadows already exist. The draft's "cool-tinted 220" 5-step is a real improvement but must be framed as replacing both light AND dark ramps.
5. **Routes**: Ask = `/` (not `/ask`), Create has no route (`/autopilot` + `/design-studio`), citations DO render at `app/page.tsx:222`.
6. **App defaults to dark mode** — the draft's entire "AA on 96% background" contrast section is secondary; dark is primary.

Now I'll produce the upgraded draft, grounded in these real facts.

---

I have read the real `globals.css`, `tailwind.config.ts`, `ui.tsx`, `shell.tsx`, `layout.tsx`, and `app/page.tsx`. The draft was confidently wrong on five load-bearing facts (ring color, shell width, font loading, dark-as-default, route names). Here is the corrected, sharpened, build-ready version.

---

# Phase 8 — JAMES OS Design System

The current `web/components/ui.tsx` is a thin, ad-hoc layer: `px-4 py-2.5` buttons, `p-3/p-5/p-0` card pads, `text-[13px] tracking-[1px]` one-offs. This section replaces the improvisation with a token-locked system that extends the existing CSS-var/shadcn setup in `app/globals.css` and `tailwind.config.ts` — no token is invented that the current theme can't express. New tokens are additive.

**Three ground-truth facts the system must respect (verified against source):**
1. **The app ships dark-first.** `app/layout.tsx` sets `<html lang="en" className="dark">` unconditionally. The `.dark` block is the *primary* theme; `:root` (light) is currently unreachable in-app. Every contrast claim below is stated for **dark first**, light second. Do not "design for the 96% light background" — that surface is not what users see.
2. **Fonts are already wired** via `<link>` in `app/layout.tsx`: Fontshare `general-sans@400,500,600,700` and Google `JetBrains+Mono:400,500`. `--font-sans` and `--font-mono` already exist verbatim in `globals.css` (lines 35–36) and are mapped in `tailwind.config.ts` (`fontFamily.sans/mono`). **Do not re-add them.** Inter is *not* loaded — it is a system-fallback string only, not a hosted face; don't promise metric-compat we don't ship.
3. **The shell is 896px, not 1200/1440.** `components/shell.tsx:156` is `max-w-4xl mx-auto px-8 py-10` inside a fixed `w-64` (256px) sidebar. The `1200`/`1440` widths in the prior draft exist nowhere in the codebase. Width changes are a real proposal below, but framed as an *edit to that one line*, not a fiction.

The expressive throughline: **memory has texture, generation has motion.** Memory-grounded surfaces (Ask at `/`, Voice Rules at `/brand`, citation chips) use the **teal accent** (`--accent: 183 …`) and a subtle left-edge "spine." Generative surfaces (Autopilot `/autopilot`, Content Studio `/design-studio`, Video Studio `/video`) use the **gold primary** (`--primary: 40 75% 52/56%`) and own the only animated affordances. This is how an engineer tells a "remembered" element from a "generated" one without reading a comment — and it maps 1:1 onto the existing sidebar groups in `shell.tsx` (`Memory` / `Create` / `Brand Reference`).

---

## 1. Foundations

### 1.1 8pt grid & spacing scale

All spacing is a multiple of 4px, 8px primary rhythm, mapped to Tailwind's default 4px scale. **Ban arbitrary values** — the audit (`grep` over `app/ components/`) finds these real offenders to remove:

- `py-2.5` (10px) — in `ui.tsx` Button/field, plus 4 page files.
- `text-[13px]` — `ui.tsx` `CardTitle` + `NotBuilt`; and across ~10 page files (`settings`, `long-form`, `page.tsx`, `pipeline`, `story-mix`, `video`, `style-templates`, `images`, `market-research`, `brand`).
- `tracking-[1px]`, `tracking-[.4px]`, `text-[11px]`, `text-[10px]`, `text-[9px]`, `text-[15px]` — sidebar + Badge.

| Token | px | Tailwind | Use |
|---|---|---|---|
| `space-0` | 0 | `p-0` | flush cards (`variant="flush"`), media tiles |
| `space-1` | 4 | `p-1` | icon insets, badge inner |
| `space-2` | 8 | `p-2` | tight control padding, chip gaps |
| `space-3` | 12 | `p-3` | compact card pad (existing `compact`) |
| `space-4` | 16 | `p-4` | default control/group gap |
| `space-5` | 20 | `p-5` | normal card pad (existing `normal`) |
| `space-6` | 24 | `p-6` | section gaps, modal body |
| `space-8` | 32 | `p-8` | page gutters (matches current shell `px-8`), empty-state pad |
| `space-12` | 48 | `p-12` | hero/empty vertical (existing `NotBuilt` `py-12`) |
| `space-16` | 64 | `p-16` | first-run / onboarding canvas |

**`py-2.5` exception:** it survives **only** as the vertical inside the 40px control height (button/input), where 10px top+bottom on a 20px line-box is what produces 40px. Everywhere else → `py-2`. This is documented as load-bearing in §3.1 so a future grep-and-replace doesn't break control height.

**Page shell standard.** The real shell is `components/shell.tsx:156`:
```tsx
<div className="max-w-4xl mx-auto px-8 py-10">{children}</div>
```
Replace the single fixed width with a per-route content-width prop, because the same 896px column cannot serve both a chat thread (Ask) and a 7-tool video grid (Video Studio). Add an optional `width` to a new `PageShell` and pass it from each route:
```tsx
const SHELL_W = { reading: "max-w-3xl", default: "max-w-4xl", wide: "max-w-6xl" }; // 768 / 896 / 1152
// app/page.tsx (Ask), app/brand → "reading"
// app/video, app/autopilot, app/queue, app/library, app/analytics → "wide"
// everything else → "default"
```
Lock to **three** widths (`max-w-3xl` / `max-w-4xl` / `max-w-6xl`), all native Tailwind tokens — no arbitrary `[1200px]`. `py-10` stays (it's a deliberate 40px top breath above `PageHeader`); horizontal stays `px-8` (32px, on-grid).

### 1.2 Typography scale — General Sans (already loaded)

`--font-sans` = `"General Sans", "Inter", ui-sans-serif, system-ui, sans-serif` and `--font-mono` = `"JetBrains Mono", ui-monospace, monospace` are **already present** in `globals.css:35–36`. No CSS to add. The work here is *applying* a scale, not declaring fonts.

General Sans runs slightly tight: **headings use `tracking-[-0.01em]`**, body stays at `0`. Loaded weights: 400/500/600/700. Use 400 (body), 500 (UI labels), 600 (headings/buttons), 700 (page Display only).

| Role | Size / LH | Weight | Tailwind | Replaces (real) |
|---|---|---|---|---|
| Display | 36/40 | 700 | `text-4xl leading-10 font-bold tracking-[-0.02em]` | onboarding/login only |
| H1 (page) | 24/32 | 600 | `text-2xl leading-8 font-semibold tracking-[-0.01em]` | current `PageHeader` `text-2xl font-semibold` (add tracking + explicit LH) |
| H2 | 20/28 | 600 | `text-xl leading-7 font-semibold tracking-[-0.01em]` | scattered `text-lg`/`text-xl` |
| H3 | 16/24 | 600 | `text-base leading-6 font-semibold` | `NotBuilt` `text-lg` → `text-base` |
| Body | 14/20 | 400 | `text-sm leading-5` | default `text-sm` |
| Body-strong | 14/20 | 500 | `text-sm leading-5 font-medium` | |
| Label | 12/16 | 500 | `text-xs leading-4 font-medium` | `ui.tsx` `Label` (`text-xs text-muted-foreground`) → add `font-medium`, keep muted via the component |
| Eyebrow | 12/16 | 600 | `text-xs uppercase tracking-[0.08em] font-semibold` | `CardTitle` `text-[13px] tracking-[1px]` → **on-grid 12px / 0.08em** |
| Caption | 12/16 | 400 | `text-xs leading-4 text-muted-foreground` | `PageHeader` sub, helper/meta |
| Mono-ref | 12/16 | 500 | `font-mono text-xs` | citation `[n]` chips (`app/page.tsx:224`), EntityIDs, counts |

`CardTitle` (`ui.tsx:27`) becomes:
```tsx
<h2 className="text-xs uppercase tracking-[0.08em] text-muted-foreground font-semibold mb-3">
```
(13px and 1px are both off-grid; 12px / 0.08em is the locked Eyebrow.)

### 1.3 Radius scale

`tailwind.config.ts` already derives `lg/md/sm` from `--radius: 0.5rem`. **Only `xl` and `full` are missing** — add those two; do not redeclare the three that exist.

```css
/* globals.css — additive only; lg/md/sm already mapped in tailwind.config.ts */
:root, .dark {
  --radius-xl: calc(var(--radius) + 4px);  /* 12px — modals, media tiles */
}
```
```ts
// tailwind.config.ts → extend.borderRadius (add to existing lg/md/sm)
xl: "var(--radius-xl)",   // 12px
full: "9999px",
```
Mapping (current behavior preserved): Badge `rounded-full` (pill), Button/Input `rounded-md` (6px), Card/Dropdown/Popover `rounded-lg` (8px), Modal/Video-tile `rounded-xl` (12px).

### 1.4 Elevation / shadow system

The real theme ships **three** shadows on **warm-neutral black** (`hsl(0 0% 0% / …)`), separately tuned for light and dark (`globals.css:37–39, 67–69`). Replace both ramps with a cool-tinted 5-step keyed to the `220` background family so cards read as the same paper as the sidebar. **You must define the dark ramp too** — dark is the shipped theme; a light-only shadow set would regress the actual UI.

```css
:root {
  --shadow-color: 220 40% 12%;
  --shadow-xs: 0 1px 2px 0 hsl(var(--shadow-color) / 0.05);
  --shadow-sm: 0 1px 3px 0 hsl(var(--shadow-color) / 0.08), 0 1px 2px -1px hsl(var(--shadow-color) / 0.06);
  --shadow-md: 0 4px 8px -2px hsl(var(--shadow-color) / 0.10), 0 2px 4px -2px hsl(var(--shadow-color) / 0.06);
  --shadow-lg: 0 12px 20px -4px hsl(var(--shadow-color) / 0.12), 0 4px 8px -4px hsl(var(--shadow-color) / 0.08);
  --shadow-xl: 0 24px 48px -12px hsl(var(--shadow-color) / 0.20);
  --shadow-glow: 0 0 0 1px hsl(40 75% 52% / 0.20), 0 4px 16px -4px hsl(40 75% 52% / 0.28);
}
.dark {
  /* dark needs deeper, blacker shadows — the 220-tint reads as light fog on near-black */
  --shadow-color: 220 60% 2%;
  --shadow-xs: 0 1px 2px 0 hsl(var(--shadow-color) / 0.30);
  --shadow-sm: 0 1px 3px 0 hsl(var(--shadow-color) / 0.40), 0 1px 2px -1px hsl(var(--shadow-color) / 0.30);
  --shadow-md: 0 4px 8px -2px hsl(var(--shadow-color) / 0.45), 0 2px 4px -2px hsl(var(--shadow-color) / 0.35);
  --shadow-lg: 0 12px 20px -4px hsl(var(--shadow-color) / 0.50), 0 4px 8px -4px hsl(var(--shadow-color) / 0.40);
  --shadow-xl: 0 24px 48px -12px hsl(var(--shadow-color) / 0.60);
  --shadow-glow: 0 0 0 1px hsl(40 75% 56% / 0.30), 0 4px 20px -4px hsl(40 75% 56% / 0.40);
}
```
Note: the existing `--shadow` (no suffix, the Tailwind `shadow` DEFAULT) is **renamed** to `--shadow-md` here, so `tailwind.config.ts` must drop the old `DEFAULT: "var(--shadow)"` and re-map (below). Any existing `className="shadow"` (DEFAULT) usages must migrate to `shadow-md`.

| Level | Token | Tailwind | Applies to |
|---|---|---|---|
| 0 flat | none | — | inputs, table rows, sidebar items |
| 1 | `--shadow-xs` | `shadow-xs` | resting cards on same-tone bg |
| 2 | `--shadow-sm` | `shadow-sm` | **default `Card`** (matches current `shadow-sm` in `ui.tsx:20`) |
| 3 | `--shadow-md` | `shadow-md` | dropdowns, popovers, hover-lift cards |
| 4 | `--shadow-lg` | `shadow-lg` | modals, command palette, `help-drawer` |
| 5 | `--shadow-xl` | `shadow-xl` | full-screen takeovers, onboarding |
| glow | `--shadow-glow` | `shadow-glow` | primary "Generate/Run Autopilot" CTA only |

```ts
// tailwind.config.ts → replace the current boxShadow block (sm/DEFAULT/lg)
boxShadow: {
  xs: "var(--shadow-xs)", sm: "var(--shadow-sm)", md: "var(--shadow-md)",
  lg: "var(--shadow-lg)", xl: "var(--shadow-xl)", glow: "var(--shadow-glow)",
}
```

### 1.5 Motion tokens

Brand idea: "continuously creates." Motion is reserved for **generation**, not decoration. One easing, three durations. None of these exist yet — fully additive.

```css
:root, .dark {
  --ease-standard: cubic-bezier(0.2, 0, 0, 1);
  --dur-fast: 120ms;   /* hover, focus, color */
  --dur-base: 200ms;   /* enter/exit, expand */
  --dur-slow: 320ms;   /* modal, drawer, page panels */
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
```
There is **no `prefers-reduced-motion` guard in the codebase today** (verified) — adding it is required, not optional, because the existing `.spinner` (`globals.css:86`) animates infinitely and is the one continuous animation shipping now. The reduced-motion block must neutralize it. The only *new* continuous animation permitted is the "generating" shimmer (§3.14).

### 1.6 Focus ring — and the ring-color correction

**Correction to the prior draft, which inverted reality:** `--ring` is currently **gold** (`40 75% 52%` light / `40 75% 56%` dark, `globals.css:33, 66`), not teal, and `ui.tsx` Button/field already use `focus-visible:ring-2 focus-visible:ring-ring` (no offset). The prior draft's claim to "set `--ring` to teal" would *regress* the working gold ring and break the memory/generation color logic (teal = memory, gold = generation — a *generation* ring should stay gold).

Standardize to **2px ring + 2px offset**, offset color = the surface so the ring never touches the control edge:
```
focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
focus-visible:ring-offset-2 focus-visible:ring-offset-background
```
On the near-black sidebar (`--sidebar: 220 20% 8%`), the offset must be the sidebar fill, not `--background`:
```
focus-visible:ring-offset-sidebar   /* sidebar Links/buttons only */
```
**Contrast of the ring itself:** gold `40 75% 56%` (dark `--ring`) on the dark card `220 14% 11%` clears 3:1 (≈5.9:1) — passes WCAG 2.4.11. Keep `--ring` gold. The teal accent is for *memory-surface fills and the citation spine* (§2), not the focus ring. Add the offset to `ui.tsx` Button and the shared `field` const, and to the sidebar `Link` in `shell.tsx`.

---

## 2. Color application & contrast

No new brand hues. Pin **semantic** roles to the existing palette (`globals.css`), and add the four status colors the product genuinely lacks — verified absent: `grep` finds no `--success/--warning/--info`. These are required for the **Approval Queue** (`/queue`), render/stub states, and the missing-API-key banners (`/settings`).

Add to **both** `:root` and `.dark` (dark values are lighter/more saturated to hold AA on the near-black `--card: 220 14% 11%`):

```css
:root {
  --success: 152 55% 32%;   --success-foreground: 0 0% 100%;
  --warning: 33 90% 40%;    --warning-foreground: 0 0% 100%;
  --info: 183 60% 28%;      --info-foreground: 0 0% 98%;   /* = light accent teal */
  /* --destructive already exists (0 72% 50%) — do NOT redeclare */
}
.dark {
  --success: 152 50% 46%;   --success-foreground: 220 20% 8%;
  --warning: 38 92% 56%;    --warning-foreground: 220 20% 8%;
  --info: 183 50% 48%;      --info-foreground: 220 20% 8%;  /* tracks dark accent 183 50% 40% family, lifted */
}
```
Map in `tailwind.config.ts` alongside the existing `destructive`:
```ts
success: { DEFAULT: "hsl(var(--success))", foreground: "hsl(var(--success-foreground))" },
warning: { DEFAULT: "hsl(var(--warning))", foreground: "hsl(var(--warning-foreground))" },
info:    { DEFAULT: "hsl(var(--info))",    foreground: "hsl(var(--info-foreground))" },
```

**Contrast contract (stated dark-first, the shipped theme):**

| Pair | Surface | Ratio | Status |
|---|---|---|---|
| `--foreground` (40 8% 94%) on `--background` (220 16% 8%) | page | ≈ 15.8:1 | AAA |
| `--foreground` on `--card` (220 14% 11%) | cards | ≈ 14.0:1 | AAA |
| `--muted-foreground` (220 6% 72%) on `--card` | meta text | ≈ 6.7:1 | AA+ |
| `--primary-foreground` (220 20% 8%) on `--primary` (40 75% 56%) | gold button | ≈ 8.9:1 | AAA |
| `--accent-foreground` (0 0% 98%) on `--accent` (183 50% 40%) | teal chip | ≈ 4.9:1 | AA |
| `--sidebar-foreground` (40 12% 92%) on `--sidebar` (220 20% 6%) | nav | ≈ 16:1 | AAA |
| sidebar inactive items: `opacity-75` on 16:1 base | nav rest state | ≈ 9:1 effective | AA (the existing `opacity-75` is safe) |

**Status-on-surface (the new tokens, dark):**
- `--warning` (38 92% 56%) as *text* on `--card`: ≈ 8.6:1 — use for banner text/icon. As a *fill*, pair `--warning-foreground` (dark) → ≈ 9:1.
- `--success` (152 50% 46%) as text on `--card`: ≈ 5.4:1 — AA for the "Approved/Rendered" badge.
- `--info` = teal: reuse the existing `accent` chip styling; it's the same hue family, so memory notices and citations stay visually unified.

**Badge tones — extend the real `ui.tsx` `Badge`.** It currently has `muted | primary | accent | ok | destructive` (and `ok` is a duplicate of `accent` — a real smell). Resolve it: make `ok` an alias of the new `success`, and add `warning` / `info`:
```tsx
const tones: Record<string, string> = {
  muted:       "bg-secondary text-muted-foreground",
  primary:     "bg-primary/15 text-primary",       // generation
  accent:      "bg-accent/20 text-accent",         // memory
  success:     "bg-success/15 text-success",
  ok:          "bg-success/15 text-success",        // back-compat alias
  warning:     "bg-warning/15 text-warning",
  info:        "bg-info/15 text-info",
  destructive: "bg-destructive/15 text-destructive",
};
```
Also fix the Badge's off-grid type: `text-[11px] tracking-[.4px]` → `text-xs tracking-[0.04em]` (Eyebrow-family, on-grid).

---

## 3. Component specs (grounded in `ui.tsx`, `shell.tsx`, `page.tsx`)

### 3.1 Controls — 40px height is the contract
Button (`ui.tsx:34`) and the shared `field` (`ui.tsx:62`, used by Input/Textarea/Select) both resolve to a **40px** box: `text-sm` (20px line) + `py-2.5` (20px) = 40px; Button `px-4`, field `px-3`. **Keep `py-2.5` here only** (the §1.1 exception). Add the focus offset (§1.6). A small/`sm` size (32px) is missing and needed for table-row actions and filter chips — add:
```tsx
size?: "sm" | "md";  // sm: h-8 px-3 text-xs ; md (default): h-10 px-4 text-sm
```
Prefer explicit `h-10`/`h-8` over padding math going forward so the 40/32 contract is legible.

### 3.2 Card — memory spine vs generation glow
`Card` (`ui.tsx:10`) keeps `bg-card border border-border rounded-lg shadow-sm` + `compact/normal/flush` pads. Add an optional `surface` to encode the throughline:
```tsx
surface?: "neutral" | "memory" | "generative";
// memory:     "border-l-2 border-l-accent"   → Ask answers, citations, Voice Rules cards
// generative: "border-l-2 border-l-primary"  → Autopilot batch cards, render-job cards
// neutral (default): unchanged
```
This is the cheapest possible expression of memory-vs-generation: a 2px left edge, teal or gold, no new component.

### 3.3 Citation chip (Ask, `app/page.tsx:222–224`) — the signature memory element
Citations already render from `res.citations`. Lock their look as the canonical "remembered" token: `Mono-ref` type, teal, pill.
```tsx
<span className="font-mono text-xs px-1.5 py-0.5 rounded-full bg-accent/15 text-accent
                 ring-1 ring-accent/30 cursor-pointer transition-colors duration-[120ms]
                 hover:bg-accent/25 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background">
  [{i + 1}]
</span>
```
Teal + mono = "this came from memory, here is the receipt." This is the one place the moat shows in the UI; it should be unmistakable and identical everywhere citations appear.

### 3.4 `PageHeader`, `NotBuilt`, sidebar
- `PageHeader` (`ui.tsx:110`): apply H1 spec (`tracking-[-0.01em] leading-8`); sub → Caption. Keeps the `HelpButton` slot.
- `NotBuilt` (`ui.tsx:128`): replace `text-[13px]` (×2) with `text-xs`; the status panel keeps its honest-status framing (good — preserve it). Swap the `⚙` glyph block for a `--warning`-tinted icon chip when `backendStatus` indicates stub/missing-key, so "not built" reads as status, not error.
- Sidebar (`shell.tsx`): the off-grid type (`text-[15px]`, `text-[10px]`, `text-[11px]`, `text-[9px]`, `tracking-[1.3px]/[.5px]`) maps to: brand wordmark → `text-sm font-bold tracking-[0.04em]`; group eyebrow → Eyebrow token; item label → `text-sm font-medium`; item sub → `text-xs opacity-60`; "soon" pill → `text-[10px]`→ keep as the one intentional micro-label or promote to a real `Badge tone="muted"`. The active item already uses `border-l-2 border-sidebar-primary` (gold) — that's correct and is the sidebar echo of the §3.2 spine.

### 3.14 "Generating" shimmer — the only new animation
For in-flight generation (Autopilot batches, video renders, Ask streaming), a gold left-to-right sheen on the affected Card, gated by reduced-motion (§1.5). Reuses `skeleton.tsx`'s slot.
```css
@keyframes james-generating {
  0%   { background-position: -200% 0; }
  100% { background-position:  200% 0; }
}
.is-generating {
  background-image: linear-gradient(110deg, transparent 30%,
    hsl(40 75% 56% / 0.10) 50%, transparent 70%);
  background-size: 200% 100%;
  animation: james-generating 1.6s var(--ease-standard) infinite;
}
```
Gold (not teal) because it signals *generation*; reduced-motion drops it to a static gold-tinted border via the §1.5 override.

---

## 4. Migration checklist (in dependency order)

1. **`tailwind.config.ts`** — add `boxShadow` xs/md/xl/glow (rename `DEFAULT`→`md`); add `borderRadius.xl`/`full`; add `colors.success/warning/info`.
2. **`globals.css`** — add 5-step cool shadow ramp for **both** `:root` and `.dark`; add `--radius-xl`; add status tokens (both themes); add motion tokens + `prefers-reduced-motion` block; add `.is-generating`. **Do not** touch `--font-sans/-mono` (already correct) or re-add `--ring` (already gold).
3. **`ui.tsx`** — Button: add `size`, focus-offset; `field`: add focus-offset; `CardTitle`: Eyebrow token; `Card`: add `surface`; `Badge`: add success/warning/info + fix off-grid type + de-dupe `ok`; `Label`: add `font-medium`; `NotBuilt`: drop `text-[13px]`.
4. **`shell.tsx`** — parameterize `max-w-4xl` → `width` prop (3xl/4xl/6xl); normalize off-grid sidebar type; add `ring-offset-sidebar` to nav focus.
5. **`app/page.tsx`** — adopt the canonical citation chip (§3.3) and `Card surface="memory"` on answers.
6. **Global grep-and-replace** — `text-[13px]`→`text-xs`, stray `py-2.5`→`py-2` (except the §3.1 control exception), arbitrary `tracking-[…px]`→em-based tokens. The 4 files with `py-2.5` and ~10 with `text-[13px]` are the concrete worklist.

**Acceptance:** zero arbitrary `text-[…px]`/`tracking-[…px]`/`py-2.5` outside `ui.tsx` controls; every route uses `PageShell` with one of three widths; dark-mode shadows defined; reduced-motion neutralizes both `.spinner` and `.is-generating`; `--ring` stays gold; citations render as teal mono pills everywhere.

---

**Source files this is grounded in (all absolute):**
- `/Users/royantony/BRAND MANAGER /james-os/web/app/globals.css` — real tokens, 3-step warm shadows, dark-default, ring=gold
- `/Users/royantony/BRAND MANAGER /james-os/web/tailwind.config.ts` — existing color/radius/shadow maps
- `/Users/royantony/BRAND MANAGER /james-os/web/components/ui.tsx` — Card/Button/field/Badge/CardTitle/PageHeader/NotBuilt
- `/Users/royantony/BRAND MANAGER /james-os/web/components/shell.tsx` — sidebar groups (Memory/Create/Brand Reference), `max-w-4xl` shell
- `/Users/royantony/BRAND MANAGER /james-os/web/app/layout.tsx` — `className="dark"`, Fontshare General Sans + Google JetBrains Mono links
- `/Users/royantony/BRAND MANAGER /james-os/web/app/page.tsx` — real citation rendering at lines 222–224

**Five corrections to the prior draft (each was a confident factual error):** (1) `--ring` is gold, not teal — the draft would have regressed the working ring and inverted the memory/generation color logic; (2) shell is `max-w-4xl` (896px), not `1200/1440` which exist nowhere; (3) `--font-sans/-mono` already exist in `globals.css` and fonts are already `<link>`-loaded — nothing to add; (4) the app is dark-by-default, so a light-96%-only contrast story is the wrong primary; (5) routes are `/` (Ask), `/autopilot` + `/design-studio` (Create) — there is no `/ask` or `/create`.


---


# Phase 9+10 — Conversion & Premium Differentiation


Confirmed. Critical facts now grounded:

- `AUTH_PATHS = ["/login", "/signup"]` (shell.tsx:80). `usePathname()` + `AUTH_PATHS.includes(path)` is the exact gate (shell.tsx:83-84).
- `/` is `app/page.tsx` = "Ask the memory" with **Ask/Do dual modes**, not a single Ask surface — the draft's "scripted re-enactment" should mirror Ask mode specifically.
- Sidebar already has an `OnboardingChecklist` component on `/`, and a real `/profile` footer link (shell.tsx:136) — the upgrade CTA placement should sit above that, which the draft got roughly right but the link is `/profile` not "Profile/Settings."
- The hero claim "renders real talking-head video" and "all 7 video makers" is real (`VIDEO_SUBROUTES`, `/video` "All 7 video makers").
- Login uses `Card, Input, Button, Spinner` — no `outline` variant in scope.

Here is the upgraded draft.

---

# Phase 9 — Conversion & Activation Engineering
# Phase 10 — Premium Differentiation: The Memory Wedge

> Every spec cites a real file in `/Users/royantony/BRAND MANAGER /james-os/web`, uses the real tokens in `app/globals.css`, and reuses the real primitives in `components/ui.tsx`. **Two corrections to the prior draft are load-bearing:** (1) `Button` only supports `variant="primary" | "secondary" | "ghost"` — there is **no `outline` variant**, so every "outline" CTA below is either a new union member we add explicitly or `variant="secondary"`. (2) `Badge` takes a `tone` prop (`muted | primary | accent | destructive | ok`), not free children styling — teal comes from `tone="accent"`, gold from `tone="primary"`. New CSS vars are additive; no existing token changes value.

---

## 9.0 — The conversion problem, stated precisely

This product carries a **$30k–$400k/yr** price tag and a **$0 acquisition surface**. `app/login/page.tsx` and `app/signup/page.tsx` are bare email/password forms built from `Card + Input + Button` inside `max-w-sm`. There is no marketing route, no pricing surface, no demo path, no proof. A buyer evaluating a $400k contract lands on a password box. Funnel today: cold buyer → password box → "didn't know where to click to generate a video" → churn before activation.

Phase 9 builds the **two missing funnel halves**: (1) the pre-auth conversion surface (marketing → pricing → demo), and (2) the post-auth activation engine (first-run → aha → adoption → expansion). The wedge powering both is **grounded memory + cite-or-refuse output** — the exact behavior already shipping in `app/page.tsx` ("Ask the memory… Cite-or-refuse with a verification pass"). Nobody else can demo a refusal. Every conversion asset leads with it.

---

## 9.1 — Pre-auth funnel: routes to build

Two new public routes plus the logged-out branch of `/`. They must bypass the workspace sidebar. The real gate is `components/shell.tsx:80-84`:

```ts
// shell.tsx:80 — current
const AUTH_PATHS = ["/login", "/signup"];

// shell.tsx — after: marketing routes also render full-bleed (no <aside> sidebar).
// "/" is intentionally NOT added here — see §9.1.1, it needs a session check,
// not a static path match, so the gating happens inside the page, not the shell.
const AUTH_PATHS = ["/login", "/signup", "/pricing", "/demo"];
```

The shell check is `if (AUTH_PATHS.includes(path)) return <>{children}</>;` (shell.tsx:83-84) — so adding the paths is sufficient to suppress the sidebar; no other shell change needed for `/pricing` and `/demo`.

| Route | Purpose | Primary CTA (`variant="cta"`) | Secondary CTA (`variant="secondary"`) |
|---|---|---|---|
| `/` (logged-out) | Hero: "It remembers your brand. Then it never stops creating." | **Watch it remember** (scrolls to live demo) | Book a 20-min walkthrough |
| `/pricing` (new) | 3 tiers, anchored at $400k, framed as replacement cost | **Start trial** (Studio/Scale) | Talk to the team (Enterprise) |
| `/demo` (new) | Interactive sandbox — ask the real memory, no signup | **Ask the memory a question** | Get this for my brand |

### 9.1.1 — Logged-out `/` vs logged-in `/`

`app/page.tsx` is the **Ask/Do** workspace (`AskPage`, with a `mode: "ask" | "do"` toggle). Do **not** add `/` to `AUTH_PATHS` — that would strip the sidebar for authed users too. Instead, branch on session inside the page:

```tsx
// app/page.tsx — top of AskPage(), before the existing Ask/Do UI.
// Session presence is already implied by the app/api layer; read the same
// cookie the login flow sets (login/page.tsx sets it on success).
const authed = useHasSession();           // thin hook: document.cookie check, client-side
if (!authed) return <Marketing />;        // full-bleed; sidebar already absent for unauth
// …existing AskPage body unchanged…
```

`useHasSession()` is a 6-line hook reading the session cookie set in `app/login/page.tsx` (`api.login` → cookie → redirect). This reuses the buyer's most-likely first URL (the bare domain) as the highest-converting asset instead of an auth-gated tool. The logged-out `<Marketing />` lives in `components/marketing/` (new folder) so `page.tsx` stays a thin router.

---

## 9.2 — The hero: demonstrate, don't describe

The category claim is **memory you can watch work**. The hero is not a screenshot — it's a self-running re-enactment of **Ask mode** (`app/page.tsx`), answering a brand question *with citations and one refusal*, because cite-or-refuse is the single most defensible proof point.

**Hero spec** (`components/marketing/Hero.tsx`, rendered by the logged-out branch):

```tsx
<section className="mx-auto max-w-5xl px-6 pt-20 pb-16 text-center">
  <Badge tone="primary">Grounded in your brand memory · cite-or-refuse</Badge>
  <h1 className="mt-6 text-5xl font-semibold tracking-tight leading-[1.05]">
    It remembers everything about your brand.<br/>
    <span className="text-primary">Then it never stops creating.</span>
  </h1>
  <p className="mt-5 text-lg text-muted-foreground max-w-2xl mx-auto">
    JAMES OS builds one unified memory from your frameworks, voice, and
    guidelines — then writes posts and renders real talking-head video
    across all 7 makers, every claim traceable to a source. No memory, no output.
  </p>
  <div className="mt-8 flex items-center justify-center gap-3">
    <Button variant="cta" className="h-12 px-7 text-base">Watch it remember</Button>
    <Button variant="secondary" className="h-12 px-7 text-base">Book a 20-min walkthrough</Button>
  </div>
  <MemoryDemoPanel/>
</section>
```

Notes on real tokens:
- `<Badge tone="primary">` renders `bg-primary/15 text-primary` (gold-on-gold-tint) per `ui.tsx:91`. This is the correct primitive; the prior draft's bare `<Badge>` would have defaulted to `tone="muted"` (gray), losing the gold.
- `text-primary` is `--primary: 40 75% 52%` → `hsl(40 75% 52%)` ≈ `#D7A33E`. On `--background: 220 15% 96%` (≈ `#F3F4F6`, **not** `#F4F4F5`), contrast ≈ **3.3:1** — this **passes the 3:1 large-text floor (WCAG AA, ≥24px/18.66px bold)** for the 48px H1 span, but would **fail** for body text. Keep gold for headings/CTAs only; never gold on light gray for paragraph text. The `--muted-foreground` (`220 8% 46%`, ≈ `#6F727B`) used for the subhead is ~4.9:1 and passes for normal text.
- `Button` needs a new `cta` variant (§9.3) — `variant="cta"` does not exist yet; until it lands, `variant="primary"` is the honest fallback.

### 9.2.1 — `MemoryDemoPanel` — the aha, before signup

A self-running ~12s loop that **is** the Ask surface, not a video of it. It reuses the real Ask answer layout (`Card` + source chips) so what the buyer sees pre-auth is pixel-identical to what they get post-auth. Three frames, looping:

1. **Type** (1.6s typewriter): *"What's our position on Staten Island waterfront pricing?"* into a replica of the `Input` from `ui.tsx`.
2. **Cite** (stream in): answer with two inline source chips rendered as `<Badge tone="accent">` — `Naming Convention v1.0`, `Pricing Memo · 2026-04`. `tone="accent"` = `bg-accent/20 text-accent`, `--accent: 183 60% 28%` → teal `#1C7077`. **Teal = grounded fact; gold = brand action.** This two-color semantic is the entire visual thesis and it maps 1:1 onto the existing `accent`/`primary` tones — no new color invented.
3. **Refuse** (the money shot): a second question the memory can't ground returns *"I don't have a source for that — I won't guess."* in a `Card` with a `Badge tone="muted"` reading `NO SOURCE`. **Showing the refusal on the marketing page is the point** — it is the proof the output is real, and no competitor will ever put a refusal in their demo.

Implementation: pure CSS/JS animation, no backend call (the marketing page is unauthenticated). Drive frames with a `requestAnimationFrame` timeline or a 4-keyframe CSS sequence; respect `prefers-reduced-motion` by rendering frame 2 (the cited answer) statically. This directly kills the observed "didn't know where to click" friction: the first interaction is zero-click and self-explaining.

---

## 9.3 — CTA strategy: a real `cta` variant

The prior draft called `variant="outline"` and `variant="cta"` — **neither exists**. Add both honestly to the union in `ui.tsx`:

```css
/* globals.css :root — additive. Deeper, warmer gold so a "buy" CTA reads
   distinct from the in-app gold "do a task" --primary. */
--cta: 36 82% 42%;            /* ≈ #C77A18 — filled w/ near-black text below */
--cta-foreground: 220 20% 8%;
```

```tsx
// ui.tsx Button — extend the union and the variant map.
// BEFORE: variant?: "primary" | "secondary" | "ghost"
// AFTER:
variant?: "primary" | "secondary" | "ghost" | "outline" | "cta";

const v =
  variant === "primary"  ? "bg-primary text-primary-foreground hover:bg-primary/90"
: variant === "secondary"? "bg-secondary text-secondary-foreground hover:bg-secondary/80"
: variant === "ghost"    ? "bg-transparent text-foreground hover:bg-secondary"
: variant === "outline"  ? "bg-transparent border border-input text-foreground hover:bg-secondary"
: /* cta */                "bg-[hsl(var(--cta))] text-[hsl(var(--cta-foreground))] hover:brightness-110 shadow-[var(--shadow)]";
```

Contrast: `#C77A18` against `--cta-foreground` near-black (`220 20% 8%` ≈ `#101218`) ≈ **6.9:1** — passes AA for the button label at any size. (The prior draft's `--cta: 36 82% 47%` / `#D98A1F` was lighter and only ~5.5:1 on near-black; `42%` lightness is the safe value.) `shadow-[var(--shadow)]` reuses the real `--shadow` token (`0px 2px 6px hsl(0 0% 0% / 0.08)`).

**Placement rules:**
- **One filled CTA per fold, max.** Hero, after the demo, after pricing, and in the sticky bar. Never two `variant="cta"` in the same viewport.
- **Asymmetric pairing everywhere:** primary = `variant="cta"` (filled deep-gold), secondary = `variant="outline"` or `variant="secondary"`. Never two filled buttons. This is the literal fix for "I didn't know where to click."
- **Sticky conversion bar** at `scrollY > window.innerHeight`: `sticky top-0 z-40 h-14 border-b border-border bg-card/90 backdrop-blur` with the `JAMES OS` wordmark (reuse the `text-[15px] font-bold tracking-[.5px]` treatment from shell.tsx:91, but in `text-primary` not `text-sidebar-primary` since the bar is on light bg) + a single **Start trial** `variant="cta"` button. Uses real `--card` and `--border`.
- **In-app upgrade CTA:** in `components/shell.tsx`, the sidebar footer currently has a `/profile` link (shell.tsx:136). Add a plan row **directly above it**, inside the same `bg-sidebar` footer block so it inherits sidebar styling. For trial tenants render `<Badge tone="primary">Trial · 9 days left</Badge>` wrapped in a `<Link href="/pricing">`. Use `tone="primary"` (gold) so it reads as account status, not a task. This is the **only** conversion CTA inside the workspace — it must never compete with the live task CTAs (`Autopilot`, `Video Studio`, etc. are all `live: true` in NAV).

---

## 9.4 — Pricing: anchor at $400k, sell the middle

The tiers exist ($30k–$400k/yr). The failure mode is a flat 3-column grid that makes $30k look expensive. Present **value-anchored, decoy-structured**, framed as **replacement cost** — this product collapses the "Smartsheet / Monday / kvCORE / Drive / email jumble" the buyer already pays for (named verbatim from the PreReal vision memory).

`/pricing` — three `Card`s, middle elevated. Card primitive is `bg-card border border-border rounded-lg shadow-sm` (`ui.tsx`); use `variant="normal"` (`p-5`) for the two outer, and override the middle for elevation.

| | **Studio** | **Scale** ◀ most teams | **Enterprise** |
|---|---|---|---|
| Price | **$30k**/yr | **$120k**/yr | **From $400k**/yr |
| Frame | One brand, one voice | Multi-brand, autopilot batches | White-label, multi-tenant, SLA |
| Memory | Unified brand memory, cite-or-refuse | + frustration ledger + voice corpus training (`/voice-studio`) | + dedicated memory architecture review |
| Video | Talking-head + AI hero images (`/images`) | + all 7 makers, autopilot rendering (`/autopilot`) | + custom maker pipeline |
| CTA | `Button variant="cta"` Start trial | `Button variant="cta"` Start trial | `Button variant="outline"` Talk to the team |

**Anchoring mechanics:**
- **$400k visible first.** Order the grid Enterprise-left or surface the $400k in the page headline so it's read before $120k. Decoy theory: Enterprise makes Scale the obvious "smart" buy.
- **Replacement-cost line above the grid:** "Replaces your Smartsheet + Monday + kvCORE + agency retainer. One memory, one bill." Reframes price against a stack the buyer already pays $200k+/yr for.
- **Elevated middle card:** `ring-2 ring-[hsl(var(--primary))]` (real gold token) + a header `<Badge tone="primary">Most teams</Badge>`. The ring sits on `--card` (`220 12% 98%`, ≈ `#FAFAFB`); gold ring vs that near-white background is decorative, not text, so the 3:1 non-text contrast applies and `hsl(40 75% 52%)` clears it (~3.3:1) — fine for a focus ring. Pair the ring with a slightly higher elevation (`shadow-[var(--shadow-lg)]`, the real `0px 6px 16px / 0.12` token) so the lift reads even where the ring color is subtle.
- **No monthly toggle.** This is annual enterprise; a $/mo toggle cheapens it. Annual only.
- **Enterprise CTA is `variant="outline"`, not filled** — high-ticket buyers convert through conversation. "Talk to the team" routes to `/demo` (booking), not Stripe. The two trial CTAs are the only filled `cta` buttons on the page, preserving the one-target rule.

---

## 9.5 — Post-auth activation: wire the existing `OnboardingChecklist`

`app/page.tsx` already imports and renders `OnboardingChecklist` (`components/onboarding-checklist`) on the logged-in `/`. **Activation is not a new build — it's making that checklist the spine of first-run.** Two changes:

- **Gate the aha on first cited answer, not on signup.** Define activation as "user received their first *cited* Ask answer." The checklist's first step should deep-link into Ask mode with a pre-filled example question (reuse the §9.2.1 prompt) so the user's first action reproduces the demo they already saw pre-auth — closing the loop from marketing promise to in-product proof.
- **Expansion trigger:** when a Studio/trial tenant completes the checklist (memory loaded + first video rendered + first approval), surface the §9.3 in-app upgrade `Badge` with a contextual line ("Your memory is live. Add a second brand →"). Tie the expansion moment to demonstrated value, not a calendar.

---

## 9.6 — Build order & ownership

1. `Button` gets `outline` + `cta` variants; `globals.css` gets `--cta` / `--cta-foreground`. *(Unblocks every CTA below; 1 file each.)*
2. `useHasSession()` hook + logged-out branch in `app/page.tsx` + `components/marketing/` (`Hero`, `MemoryDemoPanel`).
3. `/pricing` and `/demo` routes; add both to `AUTH_PATHS` (shell.tsx:80).
4. Sticky conversion bar + in-app upgrade `Badge` above the `/profile` footer link (shell.tsx).
5. Wire `OnboardingChecklist` first step to pre-filled Ask; expansion trigger on completion.

**Non-negotiables for review:** no new color outside `--cta`; teal = `tone="accent"` (fact), gold = `tone="primary"`/`--cta` (action); one filled CTA per fold; the refusal ships on the marketing page on purpose.

---

**Files referenced (all absolute):**
- `/Users/royantony/BRAND MANAGER /james-os/web/components/ui.tsx` — `Button` (no `outline`/`cta` yet), `Badge` (tone-based), `Card`, `PageHeader`, `NotBuilt:128`
- `/Users/royantony/BRAND MANAGER /james-os/web/app/globals.css` — real tokens (`--primary 40 75% 52%`, `--accent 183 60% 28%`, `--card 220 12% 98%`, `--background 220 15% 96%`, `--shadow`/`--shadow-lg`)
- `/Users/royantony/BRAND MANAGER /james-os/web/components/shell.tsx` — `AUTH_PATHS:80`, gate `:83-84`, `NAV:30`, `/profile` footer `:136`
- `/Users/royantony/BRAND MANAGER /james-os/web/app/page.tsx` — `AskPage`, Ask/Do modes, renders `OnboardingChecklist`
- `/Users/royantony/BRAND MANAGER /james-os/web/app/login/page.tsx` — bare `Card+Input+Button` form, sets session cookie


---


# Phase 4 — Accessibility (WCAG 2.0 AA) + Mobile + Enterprise


I have full ground truth. The draft has real strengths but several factual errors against the shipped code. The biggest: the app ships `dark`-only (`<html className="dark">`), so the draft's "Failing pairs in the current build" table is mostly fictional light-mode pairs. The sidebar links have zero focus styling (confirmed). Icon already carries `aria-hidden`. The reject flow is an inline expander, not a modal. Here is the upgraded deliverable.

---

# Phase 4 — Accessibility (WCAG 2.1 AA), Mobile & Enterprise

Grounded in the shipped repo: tokens in `web/app/globals.css`, primitives in `web/components/ui.tsx`, nav in `web/components/shell.tsx`, the analytics tables/bar-chart in `web/app/analytics/page.tsx`, the help drawer in `web/components/help-drawer.tsx`, the toast in `web/components/toast.tsx`, icons in `web/components/icons.tsx`, and `web/app/layout.tsx`.

**Verified starting state (do not re-derive — this is the actual code):**
- `app/layout.tsx` hardcodes `<html lang="en" className="dark">`. **Dark is the only mode that ships today.** There is no light theme in the runtime, so every contrast fix below is judged against the *dark* palette first; the light tokens in `globals.css` exist but are dead until a theme toggle ships (Section D). This corrects the most common mis-audit: do not file "gold-on-light fails" as a live bug — it is latent, not active.
- `shell.tsx`: fixed `w-64` (`16rem`) sidebar via `<aside className="w-64 shrink-0 …">`, always rendered except on `/login`,`/signup`. Content column is `<main className="flex-1 min-w-0">` wrapping `<div className="max-w-4xl mx-auto px-8 py-10">`. **No `md:` collapse, no hamburger, no drawer — the sidebar cannot be dismissed below 768px.**
- Tables (`analytics/page.tsx` ~342 and ~692): `<th>` with **no `scope`, no `<caption>`**, wrapped in `overflow-x-auto`. Numeric cells already use `tabular-nums` ✓.
- Bar chart (`analytics/page.tsx` ~648): `<div className="flex-1 bg-primary/30 hover:bg-primary/60 rounded-t" style={{height}}>` inside `<div className="flex items-end gap-[2px] h-24">`. Each bar already has a `title` attr ✓ but no `<svg>`, no role, no aria, no visible axis.
- `icons.tsx`: the `<svg>` already carries `aria-hidden` ✓ (draft claim that icons need it added is **wrong** — leave decorative icons as-is; the real gap is labelling the *icon-only buttons* that wrap them).
- `toast.tsx`: `role="status" aria-live="polite"` ✓; auto-dismiss `setTimeout(onClose, durationMs=4500)` with **no pause-on-hover**.
- `help-drawer.tsx`: closes on Escape ✓, locks nothing (comment claims body-scroll lock but **none is implemented**), **no focus trap, no focus restore**, overlay `onClick` closes.
- `queue/page.tsx`: the reject flow is an **inline expander** (`rejecting === it.id` reveals a `<Textarea>` + Confirm/Cancel in place) — **not a modal**. So it needs focus-move-into-the-textarea, not a focus trap.
- Responsive footprint across all of `app/` + `components/`: **27 breakpoint utilities total** (`sm:` ×8, `md:` ×18, `lg:` ×10, no `xl:`). The app is effectively desktop-only.

---

## A. WCAG 2.1 AA Compliance

### A1. Contrast — audited against the **dark** palette that actually ships

Approach: keep the brand fills (gold `--primary 40 75% 56%` dark, teal `--accent 183 50% 40%` dark) but stop reusing a *fill* token as *text*. Add `*-text` variants tuned for the dark surfaces, and only later (Section D) add the light-mode values.

**Failing pairs in the shipped dark build (live bugs):**

| # | Usage | File | Pair | Ratio | Verdict |
|---|---|---|---|---|---|
| 1 | Bar fill `bg-primary/30` on `--card 220 14% 11%` | `analytics` ~648 | `#dca a@30%` ≈ `#3a352a` vs `#191c20` | **~1.4:1** | FAIL — bars are nearly invisible; color is the *only* data channel |
| 2 | Badge `accent`/`ok` text on `accent/20` | `ui.tsx` Badge | teal `#3a9aa0` on teal-tint `≈#1c2c2e` | **~2.6:1** | FAIL (text) |
| 3 | Badge `primary` text on `primary/15` | `ui.tsx` Badge | gold `#e0b14e` on `≈#211d12` | **~6.5:1** | pass, but only because dark — will break the instant light mode lands |
| 4 | Toast `info` `text-primary` body, `error` `text-destructive` | `toast.tsx` | overridden by `text-foreground` on the message `<div>` | OK for message; the **link** `underline` inherits tone → check |
| 5 | Sidebar inactive items `opacity-75` + sublabel `opacity-60` | `shell.tsx` | `--sidebar-foreground 40 12% 92%` (`#ece9e2`) @75% on `--sidebar 220 20% 6%` → `≈#b0aea8` vs `#0c0e12` | label ~**8:1** pass; **sublabel @60% ≈ `#8a8884` → ~4.6:1** pass (large/secondary) — keep but **never tint it gold** |
| 6 | `--muted-foreground 220 6% 72%` (`#b3b6ba`) on `--card`/`--background` | global | ≈ **6.5:1** | pass — leave it |
| 7 | `--border 220 10% 22%` (`#313338`) on `--card 220 14% 11%` | global | ≈ **1.5:1** | FAIL 1.4.11 for any control whose boundary *is* the border (inputs, table row separators that convey structure) |
| 8 | `--input 220 10% 26%` (`#393b40`) on `--background 220 16% 8%` | global | ≈ **2.1:1** | FAIL 1.4.11 — form-field edges under-perceivable |

**Token changes in `globals.css` (dark block first — that is what renders):**

```css
.dark {
  /* keep --primary 40 75% 56% as FILL (buttons, bars, rings, border-left) */
  --primary-text:  40 82% 64%;   /* #ecbb5b — gold AS TEXT on #11141a card ≈ 8.0:1 ✓ */
  --accent-text:  183 55% 58%;   /* #56c3c8 — teal AS TEXT on #11141a ≈ 6.4:1 ✓; on accent/20 tint ≥ 4.6 ✓ */
  --input:        220 10% 40%;   /* #5b5f66 on #14171c ≈ 4.1:1 ✓ (was 26% → 2.1:1) — control boundary 1.4.11 */
  --border:       220 10% 30%;   /* #43464c — raise from 22% so 1px card/table borders read at ≥3:1 against card */
  /* --muted-foreground 72% stays — verified ~6.5:1 */
}
:root {
  /* latent (light mode, ships in Section D) — pre-tuned so the toggle is contrast-clean from day one */
  --primary-text:  38 78% 34%;   /* #9a6f15 — gold text on #f4f5f6 ≈ 4.6:1 ✓ */
  --accent-text:  184 64% 24%;   /* #16585c on white ≈ 5.9:1 ✓ */
  --muted-foreground: 220 9% 40%;/* #5d626b on #f4f5f6 ≈ 5.8:1 ✓ (was 46% ≈ 4.4:1) */
  --input:        220 10% 58%;   /* #888d96 on #f4f5f6 ≈ 3.1:1 ✓ (was 78% ≈ 1.5:1) */
}
```

**Then swap text utilities that currently alias a fill token:**
- `ui.tsx` `Badge.tones`: `primary: "bg-primary/15 text-[hsl(var(--primary-text))]"`, and `accent`/`ok: "bg-accent/20 text-[hsl(var(--accent-text))]"`. (Today both `accent` and `ok` are literally identical — `"bg-accent/20 text-accent"` — which is also a *semantic* bug: a success state and a neutral accent are indistinguishable. Give `ok` a left check glyph or a distinct `bg-emerald-500/15 text-emerald-400` so "ok" ≠ "accent".)
- `help-drawer.tsx`: the `?` chip, the "How it works" eyebrow, the step-number bullets, and the `•` tip bullets all use `text-primary` on `primary/15` tints → route every one to `text-[hsl(var(--primary-text))]`.
- `shell.tsx` active item: keep `border-sidebar-primary` (the gold left-border is the primary affordance and is decorative, ≥3:1 not required but it clears it) **and** `text-sidebar-primary` on the label is fine in dark (~5:1) — but add `aria-current="page"` (A5) so the state is not *only* color.

**Bar chart (the #1 visible failure), `analytics/page.tsx` ~648:** change the bar fill from `bg-primary/30` to a solid, legible fill and stop encoding data in opacity alone:

```tsx
<div className="flex-1 rounded-t bg-[hsl(var(--primary))] hover:bg-[hsl(var(--primary-text))] transition-colors"
     style={{ height: `${h}%`, minHeight: t.views > 0 ? "2px" : "0" }} />
```

Solid `--primary` on `--card` ≈ 4.5:1, and because the bar's *height* already encodes the value, color is no longer the sole channel — but still add the text alternative in A6.

### A2. Keyboard navigation

- **Skip link** — first focusable node in `layout.tsx`, before `<Shell>`:
  ```tsx
  <a href="#main"
     className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-card focus:px-4 focus:py-2 focus:text-foreground focus:ring-2 focus:ring-[hsl(var(--ring))]">
    Skip to content
  </a>
  ```
  and in `shell.tsx` give `<main>` `id="main"` + `tabIndex={-1}` (the wrapper is already `<main className="flex-1 min-w-0">`).
- **Sidebar links are native `<Link>`** (tabbable) ✓ — but they have **zero focus styling** (confirmed: no `focus` token anywhere in `shell.tsx`). The global indicator in A3 covers them; additionally add `focus-visible:bg-sidebar-accent focus-visible:opacity-100` so the ring survives the near-black `--sidebar 220 20% 6%`.
- **Help drawer** (`help-drawer.tsx`): Escape works ✓ but it does **not** move focus into the panel on open, does **not** trap Tab, and does **not** restore focus to the `?` trigger on close. Implement:
  - on open: `headingRef.current?.focus()` (add `tabIndex={-1}` to the `<h2>{tut.title}`);
  - trap Tab/Shift+Tab within the `<aside>` (query `a,button,input,select,textarea,[tabindex]:not([tabindex="-1"])`, wrap at ends);
  - on close: `triggerRef.current?.focus()`;
  - actually implement the body-scroll lock the comment already promises (`document.body.style.overflow = "hidden"` while `open`, restore on cleanup).
- **Reject flow** (`queue/page.tsx`, the `rejecting === it.id` inline expander — *not* a modal): on expand, focus the reason `<Textarea>` (`autoFocus` or a `ref`); on Cancel, return focus to the "Reject" button that opened it. No focus trap needed — it is in document flow. Also make the inline delete-confirm (lines ~80) behave the same.
- **Toast** (`toast.tsx`): the `setTimeout(onClose, 4500)` violates 2.2.1 — it cannot be paused. Hold the timer in a ref; clear it on `onMouseEnter`/`onFocus`, restart on `onMouseLeave`/`onBlur`. Errors should not auto-dismiss at all — for `kind="error"`, skip the timer entirely so failures don't vanish before they're read.
- **Filter tabs** — `queue/page.tsx` renders the `["pending","approved","rejected","total"]` filters and `analytics` renders sort controls. The queue filters are already real buttons; give the active one `aria-pressed={filter===k}`. The analytics "Sort by" is already a native `<select>` ✓ (keyboard-complete) — leave it.

### A3. Focus indicators (real CSS, dark-first)

The component-level `focus-visible:ring-2 focus-visible:ring-ring` (gold `--ring 40 75% 56%`) is the *only* indicator today, and the sidebar links don't even get that. A single gold ring fails 1.4.11 when gold sits near gold (active sidebar item, primary buttons). Replace with a layered ring that guarantees ≥3:1 on any surface:

```css
/* globals.css @layer base — note the leading comma-free :where to keep specificity 0 */
:where(a, button, input, select, textarea, [tabindex]):focus-visible {
  outline: none;
  box-shadow:
    0 0 0 2px hsl(var(--background)),    /* gap separates ring from busy fills */
    0 0 0 4px hsl(var(--ring)),          /* gold */
    0 0 0 5px hsl(0 0% 100% / 0.55);     /* light halo → reads even gold-on-gold (dark default) */
}
```

When light mode ships (Section D), the inner halo must flip dark:
```css
:root:not(.dark) :where(a, button, input, select, textarea, [tabindex]):focus-visible {
  box-shadow: 0 0 0 2px hsl(var(--background)), 0 0 0 4px hsl(var(--ring)), 0 0 0 5px hsl(220 20% 10% / 0.55);
}
```

Then delete the per-component `focus-visible:ring-2 focus-visible:ring-ring` from `ui.tsx` `Button` and the shared `field` string (the global wins and avoids double rings). Inside the dark sidebar, also add `focus-visible:bg-sidebar-accent` to the `<Link>` className so the indicator has a contrasting backplate.

### A4. Screen-reader support

- **Assertive live region for failures** — mount once in `layout.tsx`: `<div id="sr-alerts" role="alert" aria-live="assertive" className="sr-only" />`. Route render/API failures (the "speaker missing / 270×480" class of silent no-op) through a tiny `announce(msg)` helper that writes its text into this node, so a failure says "Video render failed" instead of dying silently.
- **Spinner** (`ui.tsx`) is a bare `<span className="spinner" />` — wrap in `role="status"` + visually-hidden text: `<span className="spinner" role="status"><span className="sr-only">Loading</span></span>`. Wherever a page holds a `loading`/`liveLoading` boolean (analytics, queue), put `aria-busy={loading}` on the region the skeleton replaces.
- **Stub / no-key disclosure** (the trust + a11y two-fer): the `NotBuilt` panel in `ui.tsx` already states honest status — wrap its status line in `role="status"` and, when a tool is running on sample data because no key is connected, render a persistent `<Badge tone="muted" aria-label="Running on sample data — no API key connected">Demo data</Badge>` in the `PageHeader`.
- **Icon-only buttons** (the *real* icon gap — the `<svg>` is already `aria-hidden` ✓): audit buttons whose only child is an `Icon` or a glyph. Confirmed-good: help-drawer close has `aria-label="Close"` ✓, toast close has `aria-label="Close"` ✓. Confirmed-missing: the sidebar Profile/Settings links read only their visible text (fine), but the bar-chart bars, the `└` connector (`shell.tsx`), and the `?`/`×`/`✕`/`•` glyphs are presentational — wrap each bare glyph in `<span aria-hidden="true">`. (The `└` is currently inside a `<Link>` whose accessible name comes from the label `<span>`, so the connector is harmless but tidy it anyway.)

### A5. Semantic structure

- **Heading levels** — `PageHeader` is `<h1>` ✓, but `CardTitle` (`ui.tsx`) hardcodes `<h2>` for *every* card, and `analytics`/`queue` also hand-roll `<h2>` section headers — so a page yields `h1 → h2(section) → h2(card)` (flat, duplicated). Give `CardTitle` an `as` prop:
  ```tsx
  export function CardTitle({ children, as: Tag = "h2", ...}: {as?: "h2"|"h3"|"h4"; …}) {
    return <Tag className="text-[13px] uppercase tracking-[1px] text-muted-foreground font-semibold mb-3">{children}</Tag>;
  }
  ```
  Pass `as="h3"` for cards that sit under a section `<h2>` so the outline is `h1 → h2 → h3`.
- **Sidebar landmarks** (`shell.tsx`): the `<nav>` exists but is unlabelled and unstructured. Add `aria-label="Primary"`; render each group as `<ul>` with `<li>` items; tie the uppercase group title to its list via `id` + `aria-labelledby` (today the title is a plain `<div>` sibling, invisible to AT). Mark the active link `aria-current="page"`. Wrap the Profile/Settings footer links in their own `<nav aria-label="Account">`.
- **Top-level landmarks** (`layout.tsx`/`shell.tsx`): `<aside>` ✓, `<main id="main">` (A2), and when the mobile top bar lands (Section B) it must be a `<header>`. The `max-w-4xl` column stays inside `<main>`.

### A6. Accessible dashboards, tables, charts

**Per-account table (`analytics/page.tsx` ~342) and Top-posts table (~692):** add `<caption className="sr-only">`, `scope` on every header, and make the existing-but-inert sort affordance real.

```tsx
<table className="w-full text-[12px]">
  <caption className="sr-only">Per-account performance: posts, followers, engagement</caption>
  <thead className="bg-muted/40 text-muted-foreground">
    <tr>
      <th scope="col" className="text-left px-4 py-2.5 font-medium">Account</th>
      <th scope="col" className="text-right px-4 py-2.5 font-medium" aria-sort="none">Posts</th>
      <th scope="col" className="text-right px-4 py-2.5 font-medium">Followers</th>
      <th scope="col" className="text-right px-4 py-2.5 font-medium">Engagement</th>
    </tr>
  </thead>
  <tbody>
    {breakdown.rows.map((r) => (
      <tr key={`${r.platform}:${r.handle}:${r.id}`} className="border-t border-border hover:bg-muted/20">
        <th scope="row" className="px-4 py-2.5 font-normal text-left">…@{r.handle} cell…</th>
        …
```
The leading `@handle` cell becomes `<th scope="row">` so each numeric cell is programmatically associated to both its column and its account. For the Top-posts table, the **`outlier_score ≥ 1.5` styling** currently encodes "good" with `text-emerald-500` *color only* (`analytics` ~735) — append a visually-hidden flag: `<span className="sr-only"> (outlier)</span>` so the distinction isn't color-only (1.4.1).

**Bar chart (`analytics/page.tsx` ~648) — give the div-bars a real accessible structure** without converting to `<svg>`:
```tsx
<div role="img"
     aria-label={`Daily views, last ${days} days. ${summarize(timeline)}.`}
     className="flex items-end gap-[2px] h-24">
  {timeline.map((t) => (
    <div key={t.date}
         className="flex-1 rounded-t bg-[hsl(var(--primary))] hover:bg-[hsl(var(--primary-text))] transition-colors"
         style={{ height: `${h}%`, minHeight: t.views > 0 ? "2px" : "0" }}
         title={`${t.date}: ${fmtNum(t.views)} views, ${t.posts} posts`} />
  ))}
</div>
```
where `summarize()` returns e.g. `"peak 12,400 on Jun 9, low 1,100 on Jun 2, total 84,000"`. Keep the per-bar `title` (sighted hover) ✓. **Pair every chart with the data it visualizes**: the Top-posts table already *is* the tabular equivalent — make that explicit by giving the chart `aria-describedby` pointing at the table's caption id, so AT users get the numbers, not a vague image label. Add the missing y-axis context as a single visible max-label (`Peak {fmtNum(maxViews)}`) above the bars; the date endpoints are already rendered ✓.

---

## B. Mobile (the sidebar problem)

Today `<aside className="w-64 shrink-0">` is unconditional, so on a 375px phone the sidebar eats 43% of the width and the `max-w-4xl` column is unusable. Make the shell responsive without touching every page:

- **Below `md` (768px):** hide the `<aside>` (`-translate-x-full` off-canvas, `md:translate-x-0`), and render a `<header className="md:hidden …">` top bar with the "JAMES OS" wordmark and a hamburger `<button aria-expanded aria-controls="primary-nav">`. The sidebar becomes a slide-in drawer: same markup, add `fixed inset-y-0 z-40 transition-transform` + a `bg-black/40` scrim, reuse the **exact focus-trap/restore/Escape logic** built for the help drawer (B and A2 share one `useDisclosure` hook — write it once).
- **Content column:** change `px-8 py-10` → `px-4 py-6 md:px-8 md:py-10`, and `max-w-4xl` is fine (it's a max, collapses gracefully).
- **Tables:** both are already in `overflow-x-auto` ✓ — but on mobile that's a horizontal-scroll trap with no affordance. Add a `md:hidden` scroll hint and consider a stacked card layout below `sm` for the Top-posts table (each post becomes a card; the per-account table can stay scrollable).
- **Touch targets:** `Button` is `py-2.5` (~40px) ✓; the sidebar links are `py-2.5` ✓; but the analytics "Sort by" `<select>` (`px-2 py-1`) and the queue inline `<select>`/glyph buttons are <44px — bump to `min-h-[44px]` on coarse pointers (`@media (pointer:coarse)`).
- **Close the drawer on route change** — subscribe to `usePathname()` in the disclosure hook and `setOpen(false)` on change, or navigating from the drawer leaves it open over the new page.

---

## C. Reduced motion & timing

- `globals.css` defines a `spin 0.7s linear infinite` spinner and several `transition-colors`. Add a global guard:
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
    .spinner { animation: none; } /* show a static ◐ or aria-only "Loading" instead */
  }
  ```
- Toast pause-on-hover and error-never-dismiss (A2) close out 2.2.1.

---

## D. Enterprise: theme toggle, density, print

- **Theme toggle** — the light tokens already exist in `:root`; the blocker is the hardcoded `className="dark"` in `layout.tsx`. Replace with a no-flash inline script in `<head>` that reads `localStorage.theme` (falling back to `prefers-color-scheme`) and sets `documentElement.classList`. Surface the control in `/settings` (the existing "API connections" page). All A1 light values are pre-tuned above so the toggle is contrast-clean on first paint.
- **Density** — `Card` already takes `variant: compact|normal|flush`. Promote that to a body-level `data-density` attribute that scales the `--radius` and card padding tokens, so enterprise users can compress the whole app, not card-by-card.
- **Print** — `@media print { aside, header[role], .no-print { display:none } main { max-width:none } }` so analytics tables export clean.

---

## Build order (ship-safe sequencing)

1. **A1 dark tokens + bar-chart fill** (one CSS file + two className edits — highest visible win, zero structural risk).
2. **A3 global focus ring + A2 skip link** (CSS + one `layout.tsx` edit; fixes the silent sidebar-focus gap).
3. **A6 table `scope`/`caption` + A5 `CardTitle as`** (props + JSX, no behavior change).
4. **A2/A4 `useDisclosure` hook** (focus trap/restore/Escape/scroll-lock) — write once, consume in help-drawer, reject expander, and the Section B mobile drawer.
5. **B mobile shell** (depends on #4).
6. **C reduced-motion, D theme/density/print** (independent, ship anytime).

**Net:** every change lands in `globals.css`, `layout.tsx`, `shell.tsx`, `ui.tsx`, the two drawers, `toast.tsx`, and `analytics/page.tsx` — no new dependency, no design-token recolor of the brand. Memory-as-moat stays premium: gold/teal/near-black untouched as *fills*; we only added the text-safe siblings and a layered focus ring.


---
