#!/usr/bin/env python3
"""v1.0.4 B：Mode B 數值重現（Numerical Reproduction，飛行浩規格）。不保證逐位元一致，不等於 Exact Reproduction。
輸入：環境變數 BT_ALT_TAIEX＝另一來源的 TAIEX 日線 CSV（欄位 date,open,close；date 可為 YYYY-MM-DD 或 YYYY/MM/DD）。
做：統一日期／數值格式 → 比交易日集合與缺漏 → 比 open/close（容許相對誤差 TOL）→ 用另一來源重算標籤與 M0/M1/M2/M5（M3/M4 不依 TAIEX 收盤，沿用本 repo 法人檔）→ 比方向與 M2 機率。
只印彙總計數與最大誤差，不印行情、不印實際漲跌。輸出 mode_b.txt 到 C.out_dir()（open 'x'）。"""
import sys, os, csv
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
TOL = 1e-6          # 行情相對誤差容許
PTOL = 1e-9         # M2 機率絕對誤差容許
def norm_date(s): s = s.strip().replace("/", "-"); y, m, d = s.split("-"); return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
def main():
    alt = os.environ.get("BT_ALT_TAIEX")
    if not alt: C.die("需設 BT_ALT_TAIEX（另一來源 CSV）")
    A = {}
    for r in csv.DictReader(open(alt, encoding="utf-8")):
        d = norm_date(r["date"])
        if d <= C.DEV_END: A[d] = (float(str(r["open"]).replace(",", "")), float(str(r["close"]).replace(",", "")))
    dates, opens, closes = C.load_taiex(); B = dict(zip(dates, zip(opens, closes)))
    only_a, only_b = sorted(set(A) - set(B)), sorted(set(B) - set(A)); both = sorted(set(A) & set(B))
    rel = max((abs(A[d][k] - B[d][k]) / B[d][k] for d in both for k in (0, 1)), default=0.0)
    L = [f"Mode B（數值重現，非逐位元）alt={os.path.basename(alt)}", f"交易日：本 repo {len(B)}、另一來源 {len(A)}、共同 {len(both)}、只在另一來源 {len(only_a)}、只在本 repo {len(only_b)}",
         f"open/close 最大相對誤差 {rel:.3e}（容許 {TOL:g}）：{'PASS' if rel <= TOL else 'FAIL'}"]
    oi, deal = C.load_inst(); spot = C.load_spot()
    y1, p1, _ = C.run_models(dates, closes, oi, deal, spot)
    ad = sorted(A); y2, p2, _ = C.run_models(ad, [A[d][1] for d in ad], oi, deal, spot)
    i1 = {dates[i]: i for i in p1}; i2 = {ad[i]: i for i in p2}
    sc = [d for d in sorted(set(i1) & set(i2)) if C.SCORE_START <= d <= C.DEV_END]
    L.append(f"評分日：共同 {len(sc)}")
    L.append(f"標籤不一致天數 {sum(y1[i1[d]] != y2[i2[d]] for d in sc)}")
    for m in ("M0", "M1", "M2", "M3", "M4", "M5"):
        L.append(f"{m} 方向不一致天數 {sum(p1[i1[d]][m] != p2[i2[d]][m] for d in sc)}")
    pe = max((abs(p1[i1[d]]['p2'] - p2[i2[d]]['p2']) for d in sc if p1[i1[d]]['p2'] is not None and p2[i2[d]]['p2'] is not None), default=0.0)
    L.append(f"M2 機率最大絕對誤差 {pe:.3e}（容許 {PTOL:g}）：{'PASS' if pe <= PTOL else 'FAIL'}")
    L.append("注意：原始資料 SHA 不同不代表回測錯；本工具 PASS 也不等於 Exact Reproduction（Mode A）。")
    o = C.out_dir(); os.makedirs(o, exist_ok=True)
    with open(os.path.join(o, "mode_b.txt"), "x", encoding="utf-8") as fh: fh.write("\n".join(L) + "\n")
    print("\n".join(L))
if __name__ == "__main__": main()
