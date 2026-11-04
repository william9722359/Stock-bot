#!/usr/bin/env python3
"""v1.0.4 C：標籤時間對齊與訓練窗口邊界（需本機 dev 資料；不寫出任何資料）。"""
import sys, os, unittest
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import common as C
class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, _, cls.c = C.load_taiex(); cls.oi, cls.deal = C.load_inst(); cls.spot = C.load_spot()
        cls.y, cls.p, cls.st = C.run_models(cls.d, cls.c, cls.oi, cls.deal, cls.spot)
    def test_label_alignment(self):   # y[D] 只由 close[D] 與 close[D−1] 決定
        for i in self.p:
            e = 1 if self.c[i] > self.c[i-1] else (0 if self.c[i] < self.c[i-1] else -1)
            self.assertEqual(self.y[i], e)
    def test_window_boundary(self):   # 每輪訓練 750 天、訓練末日與測試首日之間恰空 purge 1 天、測試 ≤20 天、輪次相接不重疊
        ix = {d: k for k, d in enumerate(self.d)}; prev_end = None
        for rnd, tr0, tr1, n, purge, te0, te1, tn in self.st:
            self.assertEqual(n, C.TRAIN); self.assertLessEqual(tn, C.TEST)
            self.assertEqual(ix[te0] - ix[tr1] - 1, C.PURGE) if purge else None
            self.assertLessEqual(ix[tr1], ix[te0] - 1 - C.PURGE)
            if prev_end: self.assertEqual(ix[te0], ix[prev_end] + 1)
            prev_end = te1
    def test_future_perturbation(self):   # 改動 D 及以後的收盤，D 的預測不變
        k = sorted(self.p)[len(self.p) // 2]; c2 = self.c[:k] + [x * 1.5 for x in self.c[k:]]
        _, p2, _ = C.run_models(self.d, c2, self.oi, self.deal, self.spot)
        for m in ("M0", "M1", "M2", "M3", "M4", "M5", "p2"): self.assertEqual(self.p[k][m], p2[k][m])
if __name__ == "__main__": unittest.main()
