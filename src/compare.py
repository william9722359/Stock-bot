#!/usr/bin/env python3
"""v1.0.4 A：逐檔比 sha256。比較 C.out_dir()（新跑）與 results/<VERSION>（已公布，唯讀）中 git 追蹤的每一檔。
不收參數。印每檔 SAME/DIFF/缺；全部 SAME 才 rc=0。"""
import sys, os, hashlib, subprocess
if len(sys.argv) > 1: sys.exit(2)
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
out, pub = C.out_dir(), C.PUBLISHED
try: files = subprocess.run(["git", "-C", C.ROOT, "ls-files", "results/" + C.VERSION], capture_output=True, text=True, check=True).stdout.split()
except Exception: files = []
if not files: files = ["results/%s/%s" % (C.VERSION, f) for f in sorted(os.listdir(pub))] if os.path.isdir(pub) else []
if not files: print("沒有已公布結果可比（results/%s 不存在）" % C.VERSION); sys.exit(3)
h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
rc = 0
for f in files:
    b = os.path.relpath(f, "results/" + C.VERSION); a, o = h(os.path.join(out, b)), h(os.path.join(C.ROOT, f))
    s = "SAME" if a and a == o else ("缺" if a is None else "DIFF"); rc |= s != "SAME"
    print(s, b, (a or "-")[:8], (o or "-")[:8])
print("compare rc=%d out=%s" % (rc, out)); sys.exit(rc)
