"""Chain script: re-materialise feature caches and retrain both models.

Convenience wrapper around the four steps the docs spell out in
``packages/model/CLAUDE.md``:

1. ``materialize_feature_matrix.py`` for each ``--win-sets`` set.
   Overwrites the cache so the new simulator semantics propagate.
2. ``train_multi_set.py`` -> ``--win-output-dir``.
3. ``materialize_choice_features.py`` for each ``--choice-sets`` set.
   Reuses the freshly-materialised win cache for kept hands.
4. ``train_choice_model.py`` -> ``--choice-output-dir``.

Designed for long unattended runs: each sub-step logs to its own
file under ``logs/retrain_all_<timestamp>/`` and writes a single
top-level ``retrain.log`` capturing step boundaries + return codes.

Typical use:

    .venv/Scripts/python.exe \\
      packages/model/scripts/retrain_all.py \\
      --win-sets TLA TMT ECL \\
      --choice-sets TLA TMT \\
      --win-output-dir models/all3_v2 \\
      --choice-output-dir models/choice_v3 \\
      --n-workers 8

Resume semantics: if a materialisation step crashes, just re-run
with the same args — the chunk-level resume in each materialiser
picks up where it left off. ``--skip-existing`` is a top-level
shortcut to skip whole *steps* whose output already exists.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
VENV_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
SCRIPTS_DIR = REPO_ROOT / "packages" / "model" / "scripts"


def _run(label: str, cmd: list[str], log_path: Path) -> int:
    logging.info("[%s] -> %s", label, log_path)
    logging.info("[%s] cmd: %s", label, " ".join(str(c) for c in cmd))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, check=False)
    logging.info("[%s] exit=%d", label, proc.returncode)
    return proc.returncode


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win-sets", nargs="+", required=True)
    ap.add_argument("--choice-sets", nargs="+", required=True)
    ap.add_argument("--win-output-dir", type=Path, required=True)
    ap.add_argument("--choice-output-dir", type=Path, required=True)
    ap.add_argument("--n-workers", type=int, default=8)
    ap.add_argument("--n-sims-per-row", type=int, default=200)
    ap.add_argument(
        "--win-cache-root",
        type=Path,
        default=REPO_ROOT / "data" / "processed" / "model_training",
    )
    ap.add_argument(
        "--choice-cache-root",
        type=Path,
        default=REPO_ROOT / "data" / "processed" / "choice_training",
    )
    ap.add_argument(
        "--overwrite-caches",
        action="store_true",
        help="Drop existing chunks before materialising (use after a "
        "simulator-semantics change). Default resumes from whatever "
        "chunks exist.",
    )
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip a step if its output directory already has content. "
        "Useful for picking up after a crash mid-chain.",
    )
    ap.add_argument(
        "--allow-version-mismatch",
        action="store_true",
        help="Pass through to both training steps: train even if a shard's "
        "_meta.json pipeline versions differ from the live code. Default "
        "refuses the mix (the choice_v7 mixed-sim-version incident).",
    )
    ap.add_argument(
        "--log-root",
        type=Path,
        default=REPO_ROOT / "logs",
    )
    args = ap.parse_args()

    if not VENV_PY.exists():
        raise SystemExit(f"Expected venv python at {VENV_PY}")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = args.log_root / f"retrain_all_{stamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    top_log = log_dir / "retrain.log"
    logging.getLogger().addHandler(logging.FileHandler(top_log, encoding="utf-8"))
    logging.info("Logging step output to %s", log_dir)

    # ------ Step 1: win-model materialisation ------
    label = "win_materialize"
    cache_has_content = any(
        (args.win_cache_root / s / "PremierDraft").glob("chunk_*.parquet") for s in args.win_sets
    )
    if args.skip_existing and cache_has_content and not args.overwrite_caches:
        logging.info("[%s] skipping (cache exists; --skip-existing set)", label)
    else:
        cmd: list[str] = [
            str(VENV_PY),
            str(SCRIPTS_DIR / "materialize_feature_matrix.py"),
            "--sets",
            *args.win_sets,
            "--n-workers",
            str(args.n_workers),
            "--n-sims-per-row",
            str(args.n_sims_per_row),
            "--output-root",
            str(args.win_cache_root),
        ]
        if args.overwrite_caches:
            cmd.append("--overwrite")
        rc = _run(label, cmd, log_dir / f"{label}.log")
        if rc != 0:
            raise SystemExit(f"{label} exit {rc}; see {log_dir / f'{label}.log'}")

    # ------ Step 2: win-model training ------
    label = "win_train"
    if args.skip_existing and (args.win_output_dir / "xgboost.json").exists():
        logging.info("[%s] skipping (%s exists)", label, args.win_output_dir / "xgboost.json")
    else:
        cmd = [
            str(VENV_PY),
            str(SCRIPTS_DIR / "train_multi_set.py"),
            "--sets",
            *args.win_sets,
            "--model-training-dir",
            str(args.win_cache_root),
            "--output-dir",
            str(args.win_output_dir),
        ]
        if args.allow_version_mismatch:
            cmd.append("--allow-version-mismatch")
        rc = _run(label, cmd, log_dir / f"{label}.log")
        if rc != 0:
            raise SystemExit(f"{label} exit {rc}; see {log_dir / f'{label}.log'}")

    # ------ Step 3: choice-model materialisation ------
    label = "choice_materialize"
    choice_cache_has_content = any(
        (args.choice_cache_root / s / "PremierDraft").glob("chunk_*.parquet")
        for s in args.choice_sets
    )
    if args.skip_existing and choice_cache_has_content and not args.overwrite_caches:
        logging.info("[%s] skipping (cache exists; --skip-existing set)", label)
    else:
        cmd = [
            str(VENV_PY),
            str(SCRIPTS_DIR / "materialize_choice_features.py"),
            "--sets",
            *args.choice_sets,
            "--n-workers",
            str(args.n_workers),
            "--n-sims-per-row",
            str(args.n_sims_per_row),
            "--cached-features-root",
            str(args.win_cache_root),
            "--output-root",
            str(args.choice_cache_root),
        ]
        if args.overwrite_caches:
            cmd.append("--overwrite")
        rc = _run(label, cmd, log_dir / f"{label}.log")
        if rc != 0:
            raise SystemExit(f"{label} exit {rc}; see {log_dir / f'{label}.log'}")

    # ------ Step 4: choice-model training ------
    label = "choice_train"
    if args.skip_existing and (args.choice_output_dir / "xgboost.json").exists():
        logging.info("[%s] skipping (%s exists)", label, args.choice_output_dir / "xgboost.json")
    else:
        cmd = [
            str(VENV_PY),
            str(SCRIPTS_DIR / "train_choice_model.py"),
            "--sets",
            *args.choice_sets,
            "--choice-training-dir",
            str(args.choice_cache_root),
            "--output-dir",
            str(args.choice_output_dir),
        ]
        if args.allow_version_mismatch:
            cmd.append("--allow-version-mismatch")
        rc = _run(label, cmd, log_dir / f"{label}.log")
        if rc != 0:
            raise SystemExit(f"{label} exit {rc}; see {log_dir / f'{label}.log'}")

    # Shutil.disk_usage hint at the end so the operator notices if
    # the caches consumed an unexpected amount of space.
    total, _used, free = shutil.disk_usage(args.win_cache_root.anchor or "/")
    logging.info(
        "Done. Free disk on cache root volume: %.1f GiB / %.1f GiB.",
        free / 1024**3,
        total / 1024**3,
    )
    logging.info("Logs: %s", log_dir)


if __name__ == "__main__":
    main()
