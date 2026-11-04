#!/bin/bash
# 驗收 4：同一版跑兩次，輸出逐位元相同（含與正式輸出比對）。
set -eu; export PYTHONDONTWRITEBYTECODE=1
R=$(cd "$(dirname "$0")/.." && pwd); T=$(mktemp -d -t bt-twice-XXXX)
for X in A B; do
  mkdir -p "$T/$X/results"; cp -r "$R/src" "$R/data" "$R/METHOD.md" "$T/$X/"; V=$(python3 -c "import sys;sys.path.insert(0,'$R/src');import common;print(common.VERSION)"); mkdir -p "$T/$X/results/$V"; cp "$R/results/$V/leak_test.txt" "$T/$X/results/$V/"
  python3 "$T/$X/src/backtest.py" >/dev/null
done
rc=0
for b in summary.csv trading.csv mcnemar.csv steps.csv steps_sub.csv confusion.csv yearly.csv prf.csv calibration.csv settlement.csv missing.csv predictions.csv internal/predictions_internal.csv summary.json run-header.json report.md; do f=results/$V/$b
  a=$(sha256sum < "$T/A/$f"); b=$(sha256sum < "$T/B/$f"); o=$(sha256sum < "$R/$f")
  [ "$a" = "$b" ] && [ "$a" = "$o" ] || { echo "DIFF $f"; rc=1; }
done
echo "run_twice rc=$rc tmp=$T"; exit $rc
