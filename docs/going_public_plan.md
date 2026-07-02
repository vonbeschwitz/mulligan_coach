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

1. **Code signing.** Unsigned PyInstaller EXEs → SmartScreen warnings +
   AV false positives; kills adoption at stranger-scale. Options to
   evaluate (verify current terms): Azure Trusted Signing (~$10/mo, now
   open to individual devs with ID verification) or an OV cert
   (Certum's open-source developer cert ~€69/yr if the repo goes open
   source; Sectigo etc. ~$70–200/yr). SmartScreen reputation accrues
   with a stable signing identity.
2. **AV false positives.** Sign everything; build on CI from a clean
   environment; submit false positives to Microsoft/majors; publish a
   VirusTotal link per release.
3. **Installer.** Replace the raw 325 MB zip with an Inno Setup
   per-user installer (no admin rights); winget manifest later.
4. **EXE update channel — prerequisite for shipping at all.** Today only
   data auto-updates; a public user base on frozen EXEs means an Arena
   log-format change (they happen) bricks everyone simultaneously with no
   fix path. Minimum viable: add `app_version` + download URL to the
   manifest (additive, no schema bump per forward-compat rules) and show
   "new version available" in the overlay. Full self-update can come later.

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
   pass on the overlay window.
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
5. **Support channels:** GitHub Issues (+ templates) and/or a small
   Discord; FAQ. Expect Arena-update firefights; the canary + update
   channel are what make them survivable.

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
  compliance note; pick signing route.
* **Phase 1 — make it shippable (~2–4 weeks part-time):** slot rename +
  manifest `app_version` check; signing + CI release pipeline; first-run
  wizard + degradation banners; repo hygiene (+ open-source flip);
  landing page; support channel. Soft-launch as an open beta in one
  Limited community (e.g. a Draft/Limited Discord, r/lrcast) before any
  broad posting.
* **Phase 2 — scale comfort:** diagnostics button, AV submissions,
  winget, automated ratings refresh, event-type handling, full
  self-update, Sealed decision.

**Top 3 gating items:** (1) written policy/compliance position;
(2) code signing + CI release pipeline; (3) an EXE update channel so
problems are fixable after strangers install. Everything else can
iterate post-launch.
