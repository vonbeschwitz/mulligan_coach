#!/usr/bin/env bash
set -o pipefail
cd /c/Users/basti/Documents/Mulligan_Coach
LOGDIR="logs/choice_v8_run"
PY=".venv/Scripts/python.exe"
MAT="packages/model/scripts/materialize_choice_features.py"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1

echo "started $(date '+%F %T')" > "$LOGDIR/status.txt"

echo "[$(date '+%F %T')] step1 materialize Premier (TLA TMT)" >> "$LOGDIR/status.txt"
"$PY" "$MAT" --sets TLA TMT --event-type PremierDraft --cached-features-root "" --n-workers 8 --n-sims-per-row 200 > "$LOGDIR/mat_premier.log" 2>&1
rc=$?; echo "[$(date '+%F %T')] mat_premier rc=$rc" >> "$LOGDIR/status.txt"
[ $rc -ne 0 ] && { echo "ABORT premier" >> "$LOGDIR/status.txt"; exit $rc; }

echo "[$(date '+%F %T')] step2 materialize Trad (TLA TMT)" >> "$LOGDIR/status.txt"
"$PY" "$MAT" --sets TLA TMT --event-type TradDraft --cached-features-root "" --n-workers 8 --n-sims-per-row 200 > "$LOGDIR/mat_trad.log" 2>&1
rc=$?; echo "[$(date '+%F %T')] mat_trad rc=$rc" >> "$LOGDIR/status.txt"
[ $rc -ne 0 ] && { echo "ABORT trad" >> "$LOGDIR/status.txt"; exit $rc; }

echo "[$(date '+%F %T')] step3 tune_choice_v8" >> "$LOGDIR/status.txt"
"$PY" packages/model/scripts/tune_choice_v8.py > "$LOGDIR/train.log" 2>&1
rc=$?; echo "[$(date '+%F %T')] train rc=$rc" >> "$LOGDIR/status.txt"
[ $rc -ne 0 ] && { echo "ABORT train" >> "$LOGDIR/status.txt"; exit $rc; }

echo "[$(date '+%F %T')] DONE" >> "$LOGDIR/status.txt"
