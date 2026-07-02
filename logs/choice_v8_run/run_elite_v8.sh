#!/usr/bin/env bash
# Elite first-mull agreement for choice_v8, all sets x both event types.
# Absolute sanity check (agreement vs elite human decisions), tee'd per set.
set -o pipefail
cd /c/Users/basti/Documents/Mulligan_Coach
PY=".venv/Scripts/python.exe"
S="scripts/elite_first_mull_agreement.py"
LOGDIR="logs/choice_v8_run"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
for SET in TLA TMT SOS; do
  echo "==================== elite v8 ${SET} ===================="
  "$PY" "$S" --set "$SET" --choice-model-dir models/choice_v8 \
    --n-sims 300 --n-workers 8 2>&1 | tee "$LOGDIR/elite_v8_${SET}.log"
done
echo "ELITE_V8_DONE"
