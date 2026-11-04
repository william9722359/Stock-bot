#!/usr/bin/env python3
"""v1.0.1 截斷：讀原檔一次，只保留 date<=DEV_END 寫成 data/dev/*.json（open 'x'）。
只印：檔名、保留列數、副本 sha8、原檔 sha8。不印任何列、數值、丟棄列數或最大日期。"""
import sys, os, json, hashlib
if len(sys.argv) > 1: sys.exit(2)
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
os.makedirs(C.DEV_DIR, exist_ok=True)
for name, (src, want) in C.ORIG.items():
    raw = open(src, "rb").read(); s0 = hashlib.sha256(raw).hexdigest()
    if s0 != want: sys.stderr.write(f"STOP 原檔 sha 不符 {name}\n"); sys.exit(1)
    rows = [r for r in json.loads(raw)["data"] if r["date"] <= C.DEV_END]
    del raw
    out = json.dumps({"data": rows}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    with open(os.path.join(C.DEV_DIR, name + ".json"), "xb") as fh: fh.write(out)
    print(f"{name}.json 保留 {len(rows)} 列 副本 {hashlib.sha256(out).hexdigest()[:8]} 原檔 {s0[:8]}")
