#!/bin/bash
# v1.0.4 A：兩個獨立副本各自完整重跑（leak_test＋backtest，各自輸出目錄），兩次逐檔相同才 rc=0；再各自和已公布 results/<VERSION> 比（若有）。
set -eu; export PYTHONDONTWRITEBYTECODE=1
R=$(cd "$(dirname "$0")/.." && pwd); T=$(mktemp -d -t bt-twice-XXXX)
for X in A B; do
  mkdir -p "$T/$X"; cp -r "$R/src" "$R/data" "$R/METHOD.md" "$T/$X/"
  ( cd "$T/$X" && BT_OUT="$T/$X/out" python3 src/leak_test.py >/dev/null && BT_OUT="$T/$X/out" python3 src/backtest.py >/dev/null )
done
rc=0
( cd "$T/A/out" && find . -type f | sort ) > "$T/list"
while read -r f; do cmp -s "$T/A/out/$f" "$T/B/out/$f" || { echo "DIFF A/B $f"; rc=1; }; done < "$T/list"
( cd "$T/B/out" && find . -type f | sort ) | cmp -s - "$T/list" || { echo "DIFF 檔案清單"; rc=1; }
echo "A/B 檔數 $(wc -l < "$T/list")"
if [ -d "$R/results" ] && [ -n "$(cd "$R" && git ls-files "results/$(cd "$R" && python3 -c 'import sys;sys.path.insert(0,"src");import common;print(common.VERSION)')" 2>/dev/null)" ]; then
  ( cd "$R" && BT_OUT="$T/A/out" python3 src/compare.py ) || rc=1
else echo "已公布結果不存在：只做 A/B 比對"; fi
echo "run_twice rc=$rc tmp=$T"; exit $rc
