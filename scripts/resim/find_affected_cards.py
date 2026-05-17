"""Diff pre-audit vs post-audit parsed_cards JSONs to identify cards
whose simulator-visible representation changed.

Many of the recent card-audit fixes touched role-feature flags that
the model reads but the simulator does NOT (is_mass_removal,
combat_trick on instants, etc.). Re-simulating those rows would be
wasted compute. This script projects each card onto only the fields
the simulator actually consumes, then diffs the projections.

Output: ``scripts/resim/affected_cards.json`` mapping
``set_code -> [card_name, ...]`` of cards whose sim-relevant fields
differ. Used by ``invalidate_affected_rows.py`` to filter the
materialised feature parquets.

The "sim-relevant" field list was derived by grepping
``packages/simulation/src`` for accesses to ``card.parsed.*`` and
``role_features.*``. See the module-level constant below.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

# Pre-audit commit (parent of the squash-merge that landed the 137 card fixes).
DEFAULT_BASE_REF = "ea32f3c"
DEFAULT_HEAD_REF = "HEAD"

# Top-level ParsedCard fields the simulator reads. Anything outside
# this set is irrelevant for the simulator (though it may matter for
# the XGBoost model — that's handled by re-deriving features at
# train time, which is cheap).
_SIM_TOP_LEVEL_FIELDS: tuple[str, ...] = (
    "types",
    "subtypes",
    "supertypes",
    "mana_cost",
    "mana_abilities",
    "enter_condition",
    "modes",
)

# RoleFeatures fields read along the training-materialisation simulator
# path. ``feature_matrix.py:build_row`` calls ``simulate(hand, library,
# ...)``, which only consults role_features inside ``policy_land.py``'s
# L1 spell-quality tiering and the burn-damage check. The bottoming
# heuristic's wider ``_EARLY_PLAY_ROLES`` set (is_counterspell, is_bounce,
# is_top_library, is_removal_aura) is NOT exercised here because the
# training path doesn't run ``simulate_mulligan_from_deck`` — 17Lands
# hands are passed in directly. Inference (overlay / website) does use
# bottoming, but inference runs live and has no cached parquet to
# invalidate.
_SIM_ROLE_FEATURE_FIELDS: tuple[str, ...] = (
    "is_creature",
    "removal_destroy_or_exile",
    "removal_burn_damage",
)


def _project(card: dict[str, Any]) -> dict[str, Any]:
    """Return a dict containing only the simulator-relevant fields.

    The projection is the canonical comparison key — two cards with
    the same projection produce identical simulator behaviour
    regardless of what else differs between them.
    """
    proj: dict[str, Any] = {f: card.get(f) for f in _SIM_TOP_LEVEL_FIELDS}
    rf = card.get("role_features") or {}
    proj["role_features"] = {f: rf.get(f) for f in _SIM_ROLE_FEATURE_FIELDS}
    return proj


def _canonical(obj: Any) -> str:
    """Canonical JSON serialisation for comparison (sorted keys)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _load_json_at_ref(ref: str, path: str) -> list[dict[str, Any]]:
    """``git show <ref>:<path>`` parsed as JSON.

    Uses subprocess rather than dulwich/gitpython to keep the script
    dependency-light; the cards data is small enough that the extra
    process invocation cost is negligible.
    """
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return json.loads(result.stdout)


def diff_one_set(set_code: str, *, base_ref: str, head_ref: str) -> list[str]:
    """Return sorted list of card names whose sim-relevant projection
    differs between ``base_ref`` and ``head_ref``.

    A card present in only one ref is treated as "changed" (the set
    of cards in scope shouldn't shift between commits, but we don't
    want a stray new card to silently skip re-simulation).
    """
    path = f"data/processed/parsed_cards/{set_code}.json"
    base = _load_json_at_ref(base_ref, path)
    head = _load_json_at_ref(head_ref, path)

    base_by_name = {c["name"]: _canonical(_project(c)) for c in base}
    head_by_name = {c["name"]: _canonical(_project(c)) for c in head}

    changed: list[str] = []
    for name in set(base_by_name) | set(head_by_name):
        if base_by_name.get(name) != head_by_name.get(name):
            changed.append(name)
    changed.sort()
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-ref", default=DEFAULT_BASE_REF)
    ap.add_argument("--head-ref", default=DEFAULT_HEAD_REF)
    ap.add_argument(
        "--sets",
        nargs="+",
        default=["TLA", "ECL", "TMT"],
        help="Set codes to diff. Defaults to all three Premier-Draft sets.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "scripts" / "resim" / "affected_cards.json",
    )
    args = ap.parse_args()

    per_set: dict[str, list[str]] = {}
    for set_code in args.sets:
        affected = diff_one_set(set_code, base_ref=args.base_ref, head_ref=args.head_ref)
        per_set[set_code] = affected
        print(f"{set_code}: {len(affected)} sim-relevant change(s)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "base_ref": args.base_ref,
                "head_ref": args.head_ref,
                "sim_top_level_fields": list(_SIM_TOP_LEVEL_FIELDS),
                "sim_role_feature_fields": list(_SIM_ROLE_FEATURE_FIELDS),
                "affected": per_set,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
