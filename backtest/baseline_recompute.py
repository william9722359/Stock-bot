#!/usr/bin/env python3
"""Recompute the "always guess up" (天天猜漲) baseline from FinMind TAIEX daily data.

資料來源：FinMind（TaiwanStockPrice, data_id=TAIEX），原始出處臺灣證券交易所。練習用，不是正式成績。
FinMind 授權：非商業、不得再散布原始資料、需標示「FinMind」。本程式不附原始資料。

定義：交易日 t 的收盤 C_t 相對前一筆 C_{t-1}：C_t > C_{t-1} 為漲（天天猜漲答對）；
C_t <= C_{t-1}（含平盤）為錯。n = 有前一筆收盤可比的交易日數。

用法：python3 baseline_recompute.py FINMIND_TAIEX_JSON [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""
import argparse, hashlib, json, math, sys

def _rb(p):
    """讀整個檔（bytes），讀完就關（v3.1.1 修 ResourceWarning）。"""
    with open(p, "rb") as f: return f.read()

def _rt(p, enc="utf-8"):
    """讀整個檔（文字），讀完就關。"""
    with open(p, encoding=enc) as f: return f.read()

def _w(p, data, mode="w", enc=None):
    """寫整個檔，寫完就關。"""
    with open(p, mode, **({} if "b" in mode else {"encoding": enc})) as f: return f.write(data)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    raw = _rb(a.path)
    rows = json.loads(raw)["data"]
    rows = [r for r in rows if r.get("stock_id") == "TAIEX"]
    rows.sort(key=lambda r: r["date"])
    dates = [r["date"] for r in rows]
    if len(set(dates)) != len(dates):
        sys.exit("重複日期，停止")
    up = flat = down = 0; mism = 0; first = last = None
    for prev, cur in zip(rows, rows[1:]):
        d = cur["date"]
        if a.start and d < a.start: continue
        if a.end and d > a.end: continue
        diff = round(cur["close"] - prev["close"], 2)
        if cur.get("spread") is not None and abs(diff - cur["spread"]) > 0.011:
            mism += 1
        if diff > 0: up += 1
        elif diff == 0: flat += 1
        else: down += 1
        first = first or d; last = d
    n = up + flat + down
    print(f"input_sha256 {hashlib.sha256(raw).hexdigest()}")
    print(f"rows_in_file {len(rows)} file_first {dates[0]} file_last {dates[-1]}")
    print(f"period {first}..{last} n {n} up {up} flat {flat} down {down}")
    if n:
        p = up / n
        se = math.sqrt(p * (1 - p) / n)
        print(f"always_up_hit_rate {p:.6f} ({p*100:.2f}%)  approx_95ci {100*(p-1.96*se):.2f}%..{100*(p+1.96*se):.2f}%")
    print(f"spread_vs_close_diff_mismatch {mism}")

if __name__ == "__main__":
    main()
