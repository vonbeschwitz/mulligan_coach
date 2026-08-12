"""Patch existing feature caches after a set-vocabulary bump.

Thin CLI over :mod:`mulligan_coach_model.cache_patch`, applying its
``ACTIVE_MIGRATION`` (currently ``set_onehots_v2``: HOB appended to the
one-hot vocabulary, ``FEATURES_SEMANTICS_VERSION`` 3 -> 4). Rather than
re-simulating each format (many hours/set), this rewrites only the
``set_code_*`` columns of the already-materialised chunks — purely from
each chunk's stored ``expansion`` column — and bumps each shard's
``_meta.json``.

By default it scans BOTH cache roots:

* win-model caches:    ``data/processed/model_training/<SET>/<EVENT>/``
* choice-model caches: ``data/processed/choice_training/<SET>/<EVENT>/``

Always dry-run first and eyeball the report, then re-run without
``--dry-run`` to apply. Tee stdout to keep a record:

    .venv/Scripts/python.exe packages/model/scripts/patch_set_onehots.py \\
      --dry-run | tee logs/patch_set_onehots_dryrun.log

    .venv/Scripts/python.exe packages/model/scripts/patch_set_onehots.py \\
      | tee logs/patch_set_onehots_apply.log

Exit status is non-zero if any shard dir was REFUSED (unrecognised
version provenance), so a scripted migration fails loudly.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from mulligan_coach_model.cache_patch import format_report, patch_roots

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_ROOTS = [
    REPO_ROOT / "data" / "processed" / "model_training",
    REPO_ROOT / "data" / "processed" / "choice_training",
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--roots",
        nargs="+",
        type=Path,
        default=DEFAULT_ROOTS,
        help=(
            "Cache roots to scan for <SET>/<EVENT>/chunk_*.parquet shard dirs. "
            "Default: the win-model and choice-model training cache roots."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Report per-dir eligibility + per-expansion row counts without writing anything.",
    )
    args = ap.parse_args()

    report = patch_roots(list(args.roots), dry_run=args.dry_run)
    print(format_report(report))

    if report.any_refused:
        logging.error(
            "One or more shard dirs were REFUSED (unrecognised version provenance). "
            "Investigate before re-running; nothing was patched in those dirs."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
