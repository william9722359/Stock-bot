"""update_scorecard.py 測試：n=0、n<30、分母為 0、重跑相同、只改標記之間、拒收非正式帳本、HTML 與 README 同表。"""
import json, os, subprocess, sys, tempfile, unittest
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"   # 子程序（score_prf）也不寫 __pycache__
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_scorecard as U  # noqa: E402
import test_leak as TL  # noqa: E402
SC = os.path.join(HERE, "update_scorecard.py")

README = "# 標題\n\n前文不動。\n\n" + U.BEGIN + "\n舊內容\n" + U.END + "\n\n後文不動。\n"

def cnt(tp, fp, fn): return {"tp": tp, "fp": fp, "fn": fn}
def res(n, hits, up_hits, up, down, rng=("20261026", "20261030"), name="practice-calls.jsonl", lines=None):
    return [{"file": "/x/" + name, "exists": True, "lines": n if lines is None else lines, "range": list(rng),
             "rules": {"P1": {"n": n, "hits": hits, "always_up_hits": up_hits, "up": up, "down": down}}}]
NONE = [{"file": "/x/practice-calls.jsonl", "exists": False}]

class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="scorecard-test-")
        self.readme = self.put("README.md", README)
    def put(self, name, body):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f: f.write(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))
        return p
    def run_sc(self, week, cum, *extra):
        w, c = self.put("w.json", week), self.put("c.json", cum)
        return subprocess.run([sys.executable, SC, "--week", w, "--cum", c, "--readme", self.readme,
                               "--html", os.path.join(self.d, "s.html")] + list(extra), capture_output=True, text=True)
    def text(self, name="README.md"):
        with open(os.path.join(self.d, name), encoding="utf-8") as f: return f.read()
    def rows(self):
        t = self.text(); blk = t[t.index(U.BEGIN):t.index(U.END)]
        return [l for l in blk.splitlines() if l.startswith("| 當週") or l.startswith("| 累計")]

class TestScorecard(Base):
    def test_n0_blank_no_numbers(self):
        r = self.run_sc(NONE, NONE); self.assertEqual(r.returncode, 0, r.stderr)
        t = self.text(); self.assertIn("尚無正式預測、待更新", t)
        for l in self.rows():
            cells = [c.strip() for c in l.strip("|").split("|")]
            self.assertEqual(cells[1:11], [""] * 10)          # 表格留空
            self.assertFalse(any(ch.isdigit() for ch in "".join(cells[1:11])))
        self.assertNotIn("%", t[t.index(U.BEGIN):t.index(U.END)])
    def test_n0_from_empty_range(self):
        r = self.run_sc(res(0, 0, 0, cnt(0, 0, 0), cnt(0, 0, 0)), res(0, 0, 0, cnt(0, 0, 0), cnt(0, 0, 0)))
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("尚無正式預測、待更新", self.text())
    def test_small_n_note(self):
        wk = res(5, 3, 2, cnt(2, 1, 0), cnt(1, 0, 1))
        cu = res(12, 7, 6, cnt(4, 2, 2), cnt(3, 2, 2), rng=("20261012", "20261030"))
        r = self.run_sc(wk, cu); self.assertEqual(r.returncode, 0, r.stderr)
        w, c = self.rows()
        self.assertIn("| 當週 | 20261026–20261030 | 5 | 60.0% | 40.0% | 66.7% | 100.0% | 80.0% | 100.0% | 50.0% | 66.7% | 樣本太少，只供參考 |", w)
        self.assertIn("| 12 | 58.3% | 50.0% |", c); self.assertIn("樣本太少，只供參考", c)
        self.assertIn("n<30 的列：樣本太少，只供參考。", self.text())
    def test_n30_no_small_note(self):
        cu = res(30, 18, 16, cnt(10, 5, 6), cnt(8, 6, 5), rng=("20260901", "20261030"))
        r = self.run_sc(res(5, 3, 2, cnt(2, 1, 0), cnt(1, 0, 1)), cu); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.rows()[1].rstrip().endswith("|  |"))
    def test_zero_denominator(self):
        wk = res(3, 2, 2, cnt(2, 1, 0), cnt(0, 0, 1))   # 從沒猜跌：跌 Precision 分母 0
        r = self.run_sc(wk, wk); self.assertEqual(r.returncode, 0, r.stderr)
        cells = [c.strip() for c in self.rows()[0].strip("|").split("|")]
        self.assertEqual(cells[8:11], ["無法計算", "0.0%", "無法計算"])
    def test_rerun_identical_and_only_between_markers(self):
        wk = res(5, 3, 2, cnt(2, 1, 0), cnt(1, 0, 1))
        self.run_sc(wk, wk); a, h1 = self.text(), self.text("s.html")
        r = self.run_sc(wk, wk); self.assertIn("README 未變", r.stdout)
        self.assertEqual(a, self.text()); self.assertEqual(h1, self.text("s.html"))
        self.assertTrue(a.startswith("# 標題\n\n前文不動。\n\n" + U.BEGIN)); self.assertTrue(a.endswith(U.END + "\n\n後文不動。\n"))
        self.assertEqual(self.run_sc(wk, wk, "--check").returncode, 0)
    def test_html_same_cells(self):
        wk = res(5, 3, 2, cnt(2, 1, 0), cnt(1, 0, 1)); self.run_sc(wk, wk)
        h = self.text("s.html")
        for l in self.rows():
            for c in [c.strip() for c in l.strip("|").split("|")]: self.assertIn(f"<td>{c}</td>", h)
    def test_refuse_non_formal(self):
        for name in ("shadow-calls.jsonl", "practice-calls-L2.jsonl"):
            r = self.run_sc(res(5, 3, 2, cnt(2, 1, 0), cnt(1, 0, 1), name=name), NONE)
            self.assertNotEqual(r.returncode, 0); self.assertIn("拒跑", r.stderr)
        dry = [{"file": "/x/dry-run-1/practice-calls.jsonl", "exists": False}]
        self.assertNotEqual(self.run_sc(dry, NONE).returncode, 0)
        two = res(5, 3, 2, cnt(2, 1, 0), cnt(1, 0, 1)); two[0]["rules"]["P2"] = two[0]["rules"]["P1"]
        self.assertNotEqual(self.run_sc(two, two).returncode, 0)
        self.assertEqual(self.text(), README)            # 拒跑時 README 不動
    def test_bad_markers(self):
        self.put("README.md", "沒有標記\n")
        r = self.run_sc(NONE, NONE); self.assertNotEqual(r.returncode, 0)
    def test_end_to_end_with_score_prf(self):
        """真跑 score_prf --json（合成帳本）→ update_scorecard：猜漲中 1 天，跌類全部分母 0。"""
        R = TL.Root()
        try:
            D = TL.D
            p = R.ledger("practice-calls.jsonl", [R.call(D)], anchor_after=TL.ANCH)
            R.ledger("shadow-calls.jsonl", [R.outcome(D, 10)])
            js = R.score([p], D, D, ["--rules", "P1"])
            r = self.run_sc(js, js); self.assertEqual(r.returncode, 0, r.stderr)
            cells = [c.strip() for c in self.rows()[0].strip("|").split("|")]
            self.assertEqual(cells[2:], ["1", "100.0%", "100.0%", "100.0%", "100.0%", "100.0%",
                                         "無法計算", "無法計算", "無法計算", "樣本太少，只供參考"])
        finally: R.cleanup()

if __name__ == "__main__":
    unittest.main()
