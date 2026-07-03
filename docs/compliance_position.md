# Compliance position — Mulligan Coach public release

Written 2026-07-03 (Claude Fable 5). This is the "written policy/compliance
position" — release gate #1 in `docs/ROADMAP.md` (Step 7) and Workstream A of
`docs/going_public_plan.md`. All source documents below were re-read on
2026-07-03. Re-verify them before launch if more than ~3 months pass, and
after any Arena Terms update.

## What the tool does (the facts this position rests on)

* A Windows overlay that reads MTG Arena's `Player.log` — a plain text file
  Arena only writes in detail when the user opts in via the game's own
  **"Detailed Logs (Plugin Support)"** setting (Options → Account).
* It uses only information the player is already entitled to see: their own
  opening hand, their own decklist, play/draw, and mulligan count. It shows
  win-probability estimates and per-card 17Lands stats. It never surfaces
  hidden information (no opponent data, no library order, nothing).
* Read-only: no game-memory access, no client modification, no injected
  input, no automation. Nothing leaves the user's PC — updates are pull-only.
* Free of charge, no account, no paywall of any kind.

## Position

**Mulligan Coach operates in the same read-only, own-information-only class
as 17Lands, Untapped.gg, and Arena Tutor — a class WotC has knowingly
tolerated for years and actively accommodates via the "Plugin Support" log
setting. We hold that line strictly, and if WotC ever objects anyway, we
comply first and discuss second.**

The supporting reasoning, per document:

1. **Wizards Terms of Use** (updated 2025-12-10). §2.2(i) prohibits
   "unauthorized means, process, or software that accesses, collects, reads,
   intercepts, monitors, data scrapes"; §2.2(v) prohibits software "not
   expressly authorized by Wizards that grants any user an advantage over
   other players." No companion app is "expressly authorized," so this whole
   tool category exists on tolerance, not permission. The tolerance case:
   (a) Arena ships an opt-in setting *named* "Detailed Logs (Plugin
   Support)" — a log file whose stated purpose is third-party plugin
   support is the closest thing to an authorized read channel that exists;
   (b) the read-only log-parsing class has operated openly at large scale
   for years without enforcement — WotC action has targeted client
   modification, memory reading, and automation; (c) the tool computes only
   over the player's own visible information — it is a fast calculator, not
   an information leak. Even WotC's competitive-event T&Cs (Arena Open,
   version dated 2025-07-24, checked 2026-07-03) prohibit only "any attempt
   to hack or otherwise modify the MTG Arena client" plus generic cheating.
2. **Honest residual risk.** §2.2(v) is broad enough that WotC could decide
   any in-match advisor "grants an advantage" — no argument fully closes
   that. The mitigation is not a legal theory; it is the red lines below
   plus the takedown protocol. We do not build anything whose viability
   depends on WotC not noticing.
3. **Wizards Code of Conduct.** Prohibits "intentional hacking/modding of
   the game" and bug exploitation. We do neither.
4. **Fan Content Policy.** Fan content must be free (no paywalls, surveys,
   subscriptions, or registration gates) — the tool is and stays free. The
   FCP grants use of card names/characters/art but **not** WotC logos or
   trademarks, explicitly including mana symbols and planeswalker symbols.
   WotC reserves the right to "stop or restrict your use of Wizards' IP at
   any time — for any reason or no reason"; that is what the takedown
   protocol answers. Note: the current FCP expressly permits Patreon-style
   donations and platform ad revenue — a donation link would be compliant,
   but we launch without one (re-verify the FCP text if that changes).

## Red lines (never cross; each is load-bearing for the position)

1. **Read-only, forever.** Parse `Player.log` only. No memory reads, no
   client files beyond the log and Arena's local `Raw_CardDatabase`, no
   process inspection, no injected input, no automation of any game action.
2. **Own information only.** Never surface anything the player couldn't see
   by looking at their screen and remembering their own deck.
3. **Free.** No paywall, no gated features, no required accounts.
4. **No WotC trademarks.** No MTG/planeswalker logos, no mana-symbol glyphs
   (the overlay renders costs as plain text like "1G" — keep it that way),
   original art only for the app icon and branding.
5. **Nothing phones home.** No telemetry, no log upload (`Player.log`
   contains opponent screen names). Any future crash reporting must be
   opt-in with a plain-language privacy note.
6. **Takedown compliance is unconditional** — see protocol below.

## Obligations per data source

### 17Lands (usage guidelines at 17lands.com/usage_guidelines, read 2026-07-03)

We use two distinct channels with different rules:

* **Public datasets** (game/replay CSVs, training only) — released under
  **CC BY 4.0**, explicitly *out of scope* of the usage guidelines.
  Obligation: license attribution (landing page + README + model docs).
* **Card-ratings endpoint** (`www.17lands.com/card_ratings/data`) — this is
  the site's Card Data, which *is* in scope, and we redistribute derived
  (shrunk) ratings to users via the data repo. Obligations we adopt:
  * **Citation:** "you must make it clear that the data comes from 17Lands
    … this citation must not imply that 17Lands endorses your product or
    findings." Must be "clearly visible at the top level, not hidden in a
    footnote or mouse-over," with a link. → App About/tray + landing page.
    (Name stylized "17Lands", capital L.)
  * **New-set embargo:** tools showing data available on their site must
    wait "until the 12th day it has been released on MTG Arena (typically
    the second Monday after the set release)" (7 days for specialty sets).
    The overlay displays per-card OH-WR-derived stats, so we honor this:
    **do not publish a new set's ratings parquet to the data channel before
    day 12.** The existing "no 17Lands ratings yet" degradation covers the
    gap — the tool still works, cards just show without stats. Goes in the
    per-set runbook (Step 8).
  * **Scraping:** "Unless you've gotten explicit permission otherwise,
    automated scraping of our API is discouraged." Our cadence is a
    handful of requests per refresh with an identifying User-Agent —
    far from bulk scraping, but before Step 9's *scheduled* automation,
    ask on the 17Lands Discord and describe the tool. Good citizenship in
    a small ecosystem is worth more than the time saved not asking.
  * 17Lands "reserve[s] the right to request anyone stop using our data
    for any reason" — covered by the takedown protocol.

### Scryfall (API docs at scryfall.com/docs/api, read 2026-07-03)

Scryfall provides data free "for the primary purpose of creating additional
Magic software" — exactly our use. Obligations: attribution without implied
endorsement (no Scryfall logos); no paywalling Scryfall-derived data; don't
"simply repackage, republish, or proxy" (our parsed-card encodings are
transformed, value-added derivatives — fine); every API request must send
accurate `User-Agent` **and** `Accept` headers. Card-image rules (don't crop
copyright/artist line, don't distort/watermark) apply to the website's
hot-linked images; the shipped overlay displays no card images.

### MTGJSON

No restrictive terms for our use (Arena-ID mapping). Courtesy attribution
alongside the others.

## Takedown protocol

* **Contact surface:** the landing page and README list a contact email;
  the data-download User-Agent already carries it. Watch GitHub issues on
  the public repo(s) for platform notices.
* **If WotC objects** (email, DMCA, or platform notice): comply first,
  negotiate second. Within 48 hours: delete the `exe-latest` and
  `data-current` releases (kills downloads and the auto-update feed), post
  a notice on the landing page and README, reply confirming compliance.
  Only then discuss scope/remedies. Installed copies keep running locally —
  we cannot and will not build a remote kill switch (that would violate red
  line 5).
* **If 17Lands or Scryfall objects:** same protocol scoped to their data —
  e.g. pull the ratings assets from `data-current` and ship a release
  without them; the degradations mechanism keeps the tool functional.

## Pre-launch checklist (feeds Step 8)

- [ ] FCP disclaimer, verbatim, in app About/tray, README, landing page:
  *"Mulligan Coach is unofficial Fan Content permitted under the Fan
  Content Policy. Not approved/endorsed by Wizards. Portions of the
  materials used are property of Wizards of the Coast. ©Wizards of the
  Coast LLC."*
- [ ] Top-level 17Lands citation + link (About + landing page), phrased to
  avoid implied endorsement; Scryfall + MTGJSON attribution alongside.
- [ ] CC BY 4.0 attribution for the training datasets (landing page +
  README + model metadata docs).
- [ ] Fix the placeholder URL in `packages/data-download/src/
  mulligan_coach_data_download/http.py` `USER_AGENT` (`+https://github.com/;`
  → the real repo/landing URL).
- [ ] Confirm every Scryfall request path sends `User-Agent` + `Accept`
  (httpx sends `Accept: */*` by default — verify once, both in
  data-download and the website's live image fetch).
- [ ] Original-art app/tray icon; audit shipped UI for any WotC trademark
  (mana-symbol glyphs, logos) — currently none; keep it that way.
- [ ] Per-set runbook includes the 17Lands day-12 ratings embargo (day 7
  for specialty sets).
- [ ] Before Step 9's scheduled ratings automation: ask 17Lands on Discord.
- [ ] Landing-page FAQ: "Is this allowed?" (summarize this position),
  "What leaves my PC? Nothing.", and a note that players in Arena
  Opens/Qualifiers are responsible for event-specific rules (current
  Arena Open T&Cs don't restrict overlays, but check per event).

## Sources (all read 2026-07-03)

* Wizards Terms of Use (updated 2025-12-10):
  <https://company.wizards.com/en/legal/terms>
* Wizards Code of Conduct:
  <https://company.wizards.com/en/legal/code-conduct>
* Wizards Fan Content Policy:
  <https://company.wizards.com/en/legal/fancontentpolicy>
* Arena Open T&Cs (2025-07-24 version):
  <https://magic.wizards.com/en/news/mtg-arena/arena-open-terms-and-conditions>
* 17Lands usage guidelines: <https://www.17lands.com/usage_guidelines>
  (site is a JS app; text extracted from the page's script bundle)
* 17Lands FAQ / public datasets: <https://www.17lands.com/faq>,
  <https://www.17lands.com/public_datasets>
* Scryfall API docs & data guidelines: <https://scryfall.com/docs/api>
