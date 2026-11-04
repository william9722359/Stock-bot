#!/usr/bin/env python3
"""v1.0.4：StandardScaler 只用訓練窗、0050 交易成本、封存段（2025 起）未讀取。"""
import sys, os, json, unittest, importlib.util
import numpy as np
sys.dont_write_bytecode = True
H = os.path.dirname(os.path.abspath(__file__)); S = os.path.join(H, "..", "src"); sys.path.insert(0, S)
import common as C
spec = importlib.util.spec_from_file_location("bt", os.path.join(S, "backtest.py")); bt = importlib.util.module_from_spec(spec); sys.argv = sys.argv[:1]; spec.loader.exec_module(bt)
class T(unittest.TestCase):
    def test_scaler_train_only(self):   # mu/sd 等於傳入訓練矩陣的 mean/std，與任何測試列無關
        rng = np.random.default_rng(0); X = rng.normal(size=(50, 3)); y = (rng.random(50) > .5).astype(float)
        _, mu, sd = C.lr_fit(X, y); self.assertTrue(np.allclose(mu, X.mean(0)) and np.allclose(sd, X.std(0)))
        _, mu2, _ = C.lr_fit(X, y); self.assertTrue(np.array_equal(mu, mu2))
    def test_etf_cost(self):            # 開 100 收 101：做多＝1% − 2×0.1425% − 0.1% − 2×滑價；不做＝0
        self.assertAlmostEqual(bt.ret(1, (100.0, 101.0), "ETF", 0, 0.0), 0.01 - 0.00285 - 0.001)
        self.assertAlmostEqual(bt.ret(1, (100.0, 101.0), "ETF", 0, 0.0002), 0.01 - 0.00285 - 0.001 - 0.0004)
        self.assertEqual(bt.ret(0, (100.0, 101.0), "ETF", 0, 0.0002), 0.0)
    def test_holdout_not_in_inputs(self):   # 回測讀的四個檔最大日期 ≤ 2024-12-31
        for pair in (C.TAIEX, C.INST, C.SPOT, C.ETF):
            j = json.load(open(pair[0], encoding="utf-8")); r = j["data"] if isinstance(j, dict) else j
            self.assertLessEqual(max(x["date"] for x in r), C.DEV_END, pair[0])
        for p in ("backtest.py", "leak_test.py", "common.py"):   # 只有 make_dev_slice 碰原檔（ORIG）
            if p != "common.py": self.assertNotIn("ORIG", open(os.path.join(S, p), encoding="utf-8").read())
if __name__ == "__main__": unittest.main()
