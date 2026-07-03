# Going-public plan — 2026-07-01 (Claude Fable 5)

Plan for taking Mulligan Coach from friends-only distribution to public
availability. Companion to `docs/design_review_2026-07-01.md`.

**Current state:** main repo `vonbeschwitz/mulligan_coach` is PRIVATE; public
companion repo `vonbeschwitz/mulligan_coach_data` hosts auto-update artifacts
(manifest + ratings/parsed_cards/model zips under rolling tag `data-current`).
EXE = unsigned PyInstaller one-folder bundle (~325 MB), built on the dev
machine (`packages/overlay/packaging/build_distribution.py`, with an
xgboost→xgboost-cpu venv swap), zipped and sent to friends manually.
Data auto-updates; the EXE itself does not.

**Recommended scope:** ship the **overlay** publicly; keep the website
local-only (it's a dev/validation surface; hosting CPU-bound sims for
strangers is cost + abuse surface for little benefit). Windows-only initially.

---

## Owner decisions 2026-07-03

These supersede the corresponding workstream items below.

1. **No code signing at launch.** Accept SmartScreen/AV friction for the
   open beta; revisit signing if the tool gets traction (nothing is lost
   by waiting — SmartScreen reputation only starts accruing once a stable
   signing identity exists). Consequence: the EXE update channel stays
   **notify-only** — an unsigned binary that silently replaces its own
   executables is the most AV-suspicious pattern there is. The free
   mitigations stay in scope: CI builds from a clean environment,
   published SHA256 + VirusTotal link per release, SmartScreen
   walk-through in the install instructions, false-positive submissions
   to Microsoft.
2. **Maximize the data auto-update channel — new sets ship without an
   EXE rebuild.** Verified 2026-07-03: the overlay discovers sets
   dynamically (`card_index.py` globs `parsed_cards/*.json`; no
   hardcoded set list anywhere in shipped code), so publishing a new
   set's `parsed_cards` + `ratings` (+ later a retrained `choice_prod`)
   in the manifest reaches every install with zero user action. The
   residual EXE-only case is a new mechanic needing simulator code
   (à la SOS Prepared); mitigate by encoding such cards approximately
   with existing primitives so the set still works on old EXEs.
   **Known gap — fix before launch:** `load_parsed_cards`
   (`packages/cards/src/mulligan_coach_cards/store.py`) validates every
   card and one failure aborts the whole set, so a published encoding
   using a new enum value (e.g. a new `Mode.kind`) would brick the
   entire set on older EXEs. Fix: per-card try/except — skip + log
   unparseable cards and surface the count through the existing
   degradations mechanism.
3. **Feedback channels:** an in-app "Send feedback" action opening a
   pre-filled Google Form (app/model/data versions passed as URL
   parameters; pairs with the planned "Copy diagnostics" button), plus
   GitHub Issues with templates on the public `mulligan_coach_data`
   repo (works while the main repo stays private). No Discord at
   launch — it's a standing moderation commitment; revisit if a
   community forms.
4. **Install/usage counting — stay pull-only, no telemetry.** GitHub
   Releases per-asset `download_count` (via `gh api`) is the signal:
   EXE zip downloads ≈ cumulative installs; `manifest.json` downloads ≈
   active machines (the overlay fetches it at launch + every 6 h —
   cadence reviewed and confirmed 2026-07-03; with autostart default-on
   this tracks daily-active machines rather than play sessions).
   Gotcha: `gh release upload --clobber` deletes + re-creates the
   asset, **resetting its download count** — both publish scripts must
   snapshot counts into an append-only log *before* clobbering. Landing
   page gets a privacy-friendly counter (e.g. GoatCounter). No
   phone-home ping — it would dilute the "nothing leaves your PC"
   headline.
5. **Tray icon + launch balloon — DONE 2026-07-03.** System tray icon
   (permanent while the app runs; right-click menu: Start with Windows,
   Quit) fixes the "completely invisible when Arena isn't running"
   problem, and a "Mulligan Coach is running — the overlay will appear
   when you open MTG Arena" balloon shows on **manual** launches with
   Arena closed. Autostart launches (identified by the `--autostart`
   flag baked into the registry Run entry) stay silent so users aren't
   nagged at every login. See `packages/overlay/src/mulligan_coach_overlay/tray.py`.

---

## Workstream A — Policy & legal (the gating risk)

1. **WotC position.** The existential risk: a real-time in-match keep/mull
   advisor. Precedent suggests tolerance (Untapped.gg draw-odds overlays,
   17Lands draft overlay, Arena Tutor), and the project already holds the
   community-norm line (read-only log parsing, no memory reads, no client
   interaction, no automation). Before launch: re-read the current MTGA
   Terms of Service / Code of Conduct + Wizards **Fan Content Policy**;
   write a one-page compliance note (docs/) stating the position and the
   red lines; be prepared to comply with a takedown. **Must stay free** —
   FCP requires fan content be free (donations are generally OK but verify
   current FCP text before adding even a Ko-fi link).
2. **Third-party data terms.** 17Lands public-data usage guidelines →
   attribution in app About + landing page (we *redistribute derived*
   ratings parquets to users). Scryfall API guidelines → hotlinking is
   permitted, keep attribution, no implied endorsement. MTGJSON fine.
3. **Branding.** Keep "Mulligan Coach"; add the standard FCP disclaimer
   ("Unofficial Fan Content permitted under the Fan Content Policy. Not
   approved/endorsed by Wizards. Portions of the materials used are
   property of Wizards of the Coast. ©Wizards of the Coast LLC.") in the
   app About panel, README, and landing page.
4. **Privacy.** Currently nothing leaves the user's PC (updates are
   pull-only) — make that a headline feature. Player.log contains
   opponent screen names: never auto-upload logs. Any future crash
   reporting must be opt-in + a short privacy note.

## Workstream B — Trust & install experience (the #1 practical blocker)

1. **Code signing.** *Deferred at launch (decision 2026-07-03, see
   above); revisit if the tool gets traction.* Unsigned PyInstaller
   EXEs → SmartScreen warnings + AV false positives. Options when
   revisited (verify current terms): Azure Trusted Signing (~$10/mo,
   now open to individual devs with ID verification) or an OV cert
   (Certum's open-source developer cert ~€69/yr if the repo goes open
   source; Sectigo etc. ~$70–200/yr). SmartScreen reputation accrues
   with a stable signing identity — starting late loses nothing but
   early friction.
2. **AV false positives.** Sign everything; build on CI from a clean
   environment; submit false positives to Microsoft/majors; publish a
   VirusTotal link per release.
3. **Installer.** Replace the raw 325 MB zip with an Inno Setup
   per-user installer (no admin rights); winget manifest later.
4. **EXE update channel — prerequisite for shipping at all.** Today only
   data auto-updates; a public user base on frozen EXEs means an Arena
   log-format change (they happen) bricks everyone simultaneously with no
   fix path. Minimum viable (launch bar): the overlay polls the
   `exe_version.json` sidecar that `publish_exe_release.py` already
   uploads to the `exe-latest` release, and shows "new version
   available" with a button that opens the download / runs the
   installer. **Notify-only until signed** (2026-07-03 decision) —
   unsigned silent self-update is an AV magnet; full self-update moves
   to Phase 2, gated on signing.

## Workstream C — Product readiness (maps to design-review items)

1. **Review #4 slot rename first** (`choice_v6` → `choice_prod` in
   `_frozen.py` + `_DEFAULT_CHOICE_MODEL_DIR` + publish scripts): must be
   in the FIRST public EXE so model updates never again require a rebuild.
2. **Review #2 (train/serve skew)** — set vocab + name-keyed stats:
   directly determines quality on the sets new users will play (MSH now).
3. **Degradation surfacing** — "MSH data preliminary (17Lands sample
   small)" banners; matters most exactly at set release when demand peaks.
4. **First-run experience:** detect Detailed Logs disabled (Arena →
   Options → Account) and walk the user through enabling it with
   screenshots; detect Arena missing; verify `arena_paths.py` /
   `arena_card_db.py` handle non-default installs (Epic Games Store
   version of MTGA uses a different path); DPI / multi-monitor sanity
   pass on the overlay window. *Partially done 2026-07-03:* tray icon +
   manual-launch balloon shipped (decision #5 above) — the "launched it
   and nothing appeared" confusion is covered; the tray menu is also
   the natural home for future items (Check for updates, Send
   feedback, Copy diagnostics, About/FCP disclaimer).
5. **Event-type honesty:** model is Premier-Draft-trained; users will run
   Sealed / Quick Draft / Bo3. Decide per event type: support, warn, or
   suppress. Minimum: detect event from the log and show a caveat.
6. **Diagnostics:** "Copy diagnostics" button (app version, model
   version, data versions, last error, log tail) + rotating local log
   file — makes GitHub issue reports actionable.
7. **Arena-update canary:** if Arena is foreground and no events parse
   for N minutes, surface "Arena update may have changed the log format —
   check for a Mulligan Coach update."

## Workstream D — Release engineering & ops

1. **CI build pipeline** (GitHub Actions, windows runner): uv sync →
   build_distribution → sign → installer → draft release + manifest
   update. Kills "built on my machine with the venv swapped."
2. **Open-source the main repo (recommended).** MIT already; trust matters
   enormously for "an EXE that reads your game logs"; the moat is
   ops + data + encodings, not code. Prerequisite hygiene sweep: secrets
   scan of full history, anonymize any captured log fixtures (screen
   names / clientMetadata), remove personal paths, prune stale branches.
   Alternative (keep source private, ship binaries via the data repo) is
   viable but weaker on trust and AV reputation.
3. **Versioning:** semver for the app + CHANGELOG; keep rolling
   `data-current` tag for data artifacts.
4. **Per-set runbook** (the recurring operational commitment, ~every 2
   months): encode new set (LLM sessions + audit) → refresh ratings →
   (later) retrain → publish. Automate the weekly ratings refresh +
   publish via scheduled GitHub Action; document the rest as a runbook.
   Formalize the pre-publish model quality gate (elite-agreement eval).
5. **Support channels** (per decision #3 above): in-app "Send feedback"
   → pre-filled Google Form; GitHub Issues + templates on the public
   `mulligan_coach_data` repo; FAQ. No Discord at launch. Expect
   Arena-update firefights; the canary + update channel are what make
   them survivable.
6. **Usage visibility** (per decision #4 above): snapshot per-asset
   `download_count` in both publish scripts before every `--clobber`;
   GoatCounter (or similar) on the landing page.

## Workstream E — Landing page & docs

GitHub Pages one-pager: what it does, GIF, download, install steps
(incl. SmartScreen note + Detailed Logs setup), FAQ ("Is this allowed?",
"What leaves my PC? Nothing.", "Which formats?"), FCP disclaimer,
17Lands + Scryfall attribution. Rewrite README for users; move dev docs
into docs/.

## Workstream F — Website (deferred)

Public hosting would need: process-pool workers for CPU-bound sims
(~1–2 core-seconds per recommend), request queue + per-IP rate limits,
bounded Scryfall cache, hosting + monitoring. Value is low next to the
overlay. Revisit only if there's demand for a no-install demo.

---

## Sequencing

* **Phase 0 — decisions (a week of evenings):** scope = overlay-only;
  open-source yes/no; read ToS/FCP/17Lands/Scryfall terms and write the
  compliance note. ~~Pick signing route~~ → decided 2026-07-03: skip
  signing at launch.
* **Phase 1 — make it shippable (~2–4 weeks part-time):** slot rename
  (done) + `exe_version.json` update-notification UI; per-card-tolerant
  `load_parsed_cards` (decision #2 gap); unsigned CI release pipeline;
  first-run wizard + degradation banners (tray icon + balloon done
  2026-07-03); Google Form feedback + Issues on the data repo;
  download-count snapshotting in both publishers; repo hygiene
  (+ open-source flip); landing page. Soft-launch as an open beta in
  one Limited community (e.g. a Draft/Limited Discord, r/lrcast)
  before any broad posting.
* **Phase 2 — scale comfort:** code signing (if traction) then full
  self-update; diagnostics button, AV submissions, winget, automated
  ratings refresh, event-type handling, Sealed decision, Discord if a
  community forms.

**Top 3 gating items:** (1) written policy/compliance position;
(2) CI release pipeline (signing deferred per 2026-07-03 decision);
(3) an EXE update channel — notify-only — so problems are fixable
after strangers install. Everything else can iterate post-launch.
