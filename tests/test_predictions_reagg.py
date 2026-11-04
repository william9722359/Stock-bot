#!/usr/bin/env python3
"""v1.0.4 §9：逐筆 predictions.csv 重新彙總，須與 summary.csv／prf.csv／confusion.csv／yearly.csv 相符。
需本機實際漲跌（不公開）：從內部重算 y，不寫出。用法：BT_OUT=<輸出目錄> python3 tests/test_predictions_reagg.py"""
import sys, os, csv, unittest
sys.dont_write_bytecode = True
H = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(H, "..", "src"))
import common as C
OUT = C.out_dir()
def rd(n): return list(csv.DictReader(open(os.path.join(OUT, n), encoding="utf-8")))
class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dates, _, closes = C.load_taiex(); cls.y = {dates[i]: (1 if closes[i] > closes[i-1] else (0 if closes[i] < closes[i-1] else -1)) for i in range(1, len(dates))}
        cls.P = rd("predictions.csv"); cls.S = {(r["table"], r["model"]): r for r in rd("summary.csv")}
        cls.F = {(r["table"], r["model"]): r for r in rd("prf.csv")}
    def col(self, m, tbl):
        c = m.lower() + "_pred"; rows = [r for r in self.P if r[c] != ""]
        if tbl == "副表_M3": rows = [r for r in rows if r["m3_pred"] != ""]
        if tbl == "副表_M4": rows = [r for r in rows if r["m4_pred"] != ""]
        return [(int(r[c]), self.y[r["date"]]) for r in rows]
    def check(self, tbl, m):
        v = self.col(m, tbl); s = self.S[(tbl, m)]; f = self.F[(tbl, m)]
        self.assertEqual(len(v), int(s["n"]))
        self.assertAlmostEqual(sum(p == y for p, y in v) / len(v), float(s["hit"]), places=6)
        self.assertEqual(sum(p == 1 and y == 1 for p, y in v), int(f["up_TP"])); self.assertEqual(sum(p == 0 and y == 0 for p, y in v), int(f["dn_TP"]))
    def test_main(self):
        for m in C.MAIN: self.check("主表", m)
    def test_sub(self):
        for m in ("M0", "M1", "M2", "M3"): self.check("副表_M3", m)
        for m in ("M0", "M1", "M2", "M4"): self.check("副表_M4", m)
    def test_header(self):
        self.assertEqual(list(self.P[0].keys()), ["date", "fold_id", "m0_pred", "m1_pred", "m2_pred", "m2_prob_up", "m3_pred", "m4_pred", "m5_pred", "model_version"])
if __name__ == "__main__": unittest.main()
