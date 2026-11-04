#!/usr/bin/env python3
"""v1.0.4 F：分類指標邊界 8 案（不需資料）。"""
import sys, os, unittest
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import importlib.util
spec = importlib.util.spec_from_file_location("bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "backtest.py"))
bt = importlib.util.module_from_spec(spec); sys.argv = sys.argv[:1]; spec.loader.exec_module(bt)
U = None
class T(unittest.TestCase):
    def up(self, y, p): o = bt.stats(y, p); return o["up_P"], o["up_R"], o["up_F1"], o
    def test_tp0(self):            # TP=0、FP>0、FN>0：P=R=F1=0（有定義的 0）
        self.assertEqual(self.up([1, 0], [0, 1])[:3], (0.0, 0.0, 0.0))
    def test_fp0(self):            # FP=0：P=1
        P, R, F, _ = self.up([1, 1, 0], [1, 0, 0]); self.assertEqual((P, R), (1.0, 0.5)); self.assertAlmostEqual(F, 2 / 3)
    def test_fn0(self):            # FN=0：R=1
        P, R, F, _ = self.up([1, 0], [1, 1]); self.assertEqual((P, R), (0.5, 1.0)); self.assertAlmostEqual(F, 2 / 3)
    def test_all_up(self):         # 全猜漲：dn 的 P 無法定義（TP+FP=0），dn R=0，dn F1=0（2TP+FP+FN>0）
        o = bt.stats([1, 0, 1], [1, 1, 1]); self.assertIsNone(o["dn_P"]); self.assertEqual(o["dn_R"], 0.0); self.assertEqual(o["dn_F1"], 0.0)
    def test_all_down(self):
        o = bt.stats([1, 0, 1], [0, 0, 0]); self.assertIsNone(o["up_P"]); self.assertEqual(o["up_R"], 0.0); self.assertEqual(o["up_F1"], 0.0)
    def test_perfect(self):
        o = bt.stats([1, 0, 1], [1, 0, 1]); self.assertEqual((o["up_P"], o["up_R"], o["up_F1"], o["hit"], o["BA"]), (1.0, 1.0, 1.0, 1.0, 1.0))
    def test_all_wrong(self):
        o = bt.stats([1, 0], [0, 1]); self.assertEqual((o["up_F1"], o["dn_F1"], o["hit"], o["BA"]), (0.0, 0.0, 0.0, 0.0))
    def test_empty(self):          # 無有效樣本：全部無法定義
        o = bt.stats([], []); self.assertEqual((o["hit"], o["up_P"], o["up_R"], o["up_F1"], o["BA"]), (None,) * 5)
    def test_only_up_class(self):  # 只有上漲日：dn R 分母 0 → 無法定義，dn F1 = 0（FP>0）
        o = bt.stats([1, 1], [1, 0]); self.assertIsNone(o["dn_R"]); self.assertEqual(o["dn_F1"], 0.0)
if __name__ == "__main__": unittest.main()
