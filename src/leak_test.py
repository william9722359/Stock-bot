#!/usr/bin/env python3
"""防偷看測試（v1.0.1：含 M2 機率、M3、M4、M5、輪次）：抽 50 個 T+1 目標日 k（seed 20261104），把 k 日（含）以後的收盤與法人值換亂數，重跑；k 日（含）以前的預測要逐筆相同。"""
import sys, os
if len(sys.argv) > 1: sys.exit(2)
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, common as C
dates, opens, closes = C.load_taiex(); oi, deal = C.load_inst(); spot = C.load_spot()
y, base, _ = C.run_models(dates, closes, oi, deal, spot)
rng = np.random.default_rng(20261104); keys = sorted(base)
ks = sorted(rng.choice(keys, size=50, replace=False).tolist()); fails = []
for k in ks:
    c2 = list(closes[:k]) + list(rng.uniform(1000, 30000, len(closes) - k))
    o2 = {d: (v if d < dates[k] else int(rng.integers(-1e5, 1e5))) for d, v in oi.items()}
    d2 = {d: (v if d < dates[k] else int(rng.integers(-1e5, 1e5))) for d, v in deal.items()}
    s2 = {d: (v if d < dates[k] else float(rng.normal())) for d, v in spot.items()}
    _, p, _ = C.run_models(dates, c2, o2, d2, s2)
    bad = [dates[i] for i in keys if i <= k and p[i] != base[i]]
    if bad: fails.append((dates[k], bad[:3]))
out = os.path.join(C.ROOT, "results", C.VERSION, "leak_test.txt"); os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "x") as fh:
    fh.write(("PASS" if not fails else "FAIL") + f" 50 個 k，失敗 {len(fails)}；k={[dates[k] for k in ks]}；{fails}\n")
print("PASS" if not fails else "FAIL", len(fails)); sys.exit(0 if not fails else 1)
