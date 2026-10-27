#!/usr/bin/env python3
# 公開版說明：本檔複製自內部 rigor v3.1.2b 程式（內部 sha256 371349ebe04cc222430fb9a0ea7ce3554e52d46a09dd1bee037499b951de94a4），
# 只換人名、內部路徑、內部文件代號、個股帳本名、User-Agent 與寫帳資料夾防呆，判定邏輯未改。資料來源與授權見 repo 的 NOTICE.md。
"""rigor-rules v3.1 合成測試（v3 唯讀備份 tests/test_leak_v3.py）。原 61 個測試的斷言不變；v3.1 只改測試夾具：
休市表附官方來源 meta 與 --calendar-sha256、帳本代號對照 ledger-codes.json、inputs_meta 帶 dataset 欄、
錨點改由「核帳方 github-push 錨點＋三份證據快照」產生；臨時休市與 Stats 兩案改用 v3.1 必填參數。
計分案例在「錨點方案已採用」模式跑（子行程載入模組後把 ANCHOR_SCHEME_ADOPTED 設 True；正式 CLI 沒有任何參數能打開），
另有 TestB0 證明正式 CLI 一律判缺錨點。新增 N1–N8 與 A1–A7 反例（對應審查者對 v3 與錨點方案的審查意見，審查檔未公開）。
（以下為 v3 原說明）rigor-rules v3 合成測試（對應審查者對 v2 的 R1–R7、D1 意見，加連假後首日；審查檔未公開）。
全部在系統暫存資料夾跑，不碰正式檔。執行：python3 code/tests/test_leak.py -v
每個案例都 assert：score_prf 的逐日判定（合法或 missing）、n、理由，並用 recompute.py --compare 驗兩支結果一致。"""
import base64, datetime as dt, hashlib, http.server, itertools, json, os, re, shutil, subprocess, sys, tempfile, threading, time, unittest
from zoneinfo import ZoneInfo

def _rb(p):
    """讀整個檔（bytes），讀完就關（v3.1.1 修 ResourceWarning）。"""
    with open(p, "rb") as f: return f.read()

def _rt(p, enc="utf-8"):
    """讀整個檔（文字），讀完就關。"""
    with open(p, encoding=enc) as f: return f.read()

def _w(p, data, mode="w", enc=None):
    """寫整個檔，寫完就關。"""
    with open(p, mode, **({} if "b" in mode else {"encoding": enc})) as f: return f.write(data)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import fetch_anchor as FA  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")
SCORE = os.path.join(HERE, "score_prf.py")
RECOMP = os.path.join(HERE, "recompute.py")

# 測試夾具（只供測試）：1151026 補假一列與官方 DG11761 相符（v3.1.1 更正：2026-10-23 抓 https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=open_data，
# sha256 ef3a1238…，第 27 列「臺灣光復暨金門古寧頭大捷紀念日,1151026,一,補假」）。v3.1 說「假資料、待官方表」不對，見 rigor-rules v3.1.1 勘誤。
# v3.1.2 更正（C6，上一行是縮寫）：官方 DG11761 快照（sha256 ef3a1238…）資料第 26 列（檔案第 27 行）原文整行是
# 「"臺灣光復暨金門古寧頭大捷紀念日","1151026","一","臺灣光復暨金門古寧頭大捷紀念日為10月25日適逢星期日，於10月26日（星期一）補假。"」；夾具只取前三欄，說明欄簡寫成「補假」。
CAL = ("名稱,日期,星期,說明\n"
       "\"國曆新年開始交易日\",\"1150102\",\"五\",\"開始交易\"\n"
       "\"臺灣光復暨金門古寧頭大捷紀念日\",\"1151026\",\"一\",\"補假\"\n"
       "\"國慶日補假\",\"1151009\",\"五\",\"補假\"\n")

def sha(b): return hashlib.sha256(b).hexdigest()
def day8(v):
    s = re.sub(r"\D", "", str(v or "")); return s if len(s) == 8 else None
def canon(r): return json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def http_of(t): return t.astimezone(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
def T(day, hh, mm, ss=0): return dt.datetime(int(day[:4]), int(day[4:6]), int(day[6:]), hh, mm, ss, tzinfo=TPE)
def iso(t): return t.isoformat(timespec="seconds")

ADOPT = ("import sys, importlib.util as u; p = sys.argv[1]; sys.argv = [p] + sys.argv[2:]; "
         "s = u.spec_from_file_location('m', p); m = u.module_from_spec(s); s.loader.exec_module(m); "
         "m.ANCHOR_SCHEME_ADOPTED = True; import os; m.APPROVALS_SHA_FILE = os.environ.get('TEST_APPROVALS_SHA_FILE', m.APPROVALS_SHA_FILE); m.main()")
# v3.1.2b：上一行只在測試夾具裡把核可者的核證 sha 檔指到暫存資料夾；正式程式沒有任何參數或環境變數能改這個路徑
CODES = {"L1": "practice-calls.jsonl", "L2": "practice-calls-L2.jsonl", "L3": "shadow-calls.jsonl"}
CAL_URL = "https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=open_data"
_EID = itertools.count(70000000001)
def utc_z(v):
    t = FA.parse_http_date(v) if isinstance(v, str) and "," in v else dt.datetime.fromisoformat(v)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def run_cli(script, args, adopt=True, inject_outcomes=True):
    cmd = [sys.executable, "-c", ADOPT, script] if adopt else [sys.executable, script]
    args = list(args)
    # v3.1.2b-r5（依審查意見，審查檔未公開）：score_prf／recompute 的 --outcomes 改必填、沒有預設檔。
    # 舊夾具把 outcome 行寫在夾具 root 的 shadow-calls.jsonl，這裡改成「明確」帶 --outcomes 指到它；新測試用 inject_outcomes=False 驗必填。
    if inject_outcomes and script in (SCORE, RECOMP) and "--outcomes" not in args and "--root" in args:
        args = ["--outcomes", os.path.join(args[args.index("--root") + 1], "shadow-calls.jsonl")] + args
    # v3.1.2b-r6（依審查意見，審查檔未公開）：--outcomes 檔不准是影子帳本、不准混其他行，另要 --outcomes-sha256。
    # 舊夾具的 outcome 行和預測行混在同一本帳；這裡只在測試裡把其中的 outcome 行抽成獨立 outcome 帳（保留原檔的
    # 鏈斷點：原檔斷點之後的 outcome 行在新檔也斷），再補 sha。新測試用 inject_outcomes=False 驗拒算。
    if inject_outcomes and script in (SCORE, RECOMP) and "--outcomes" in args and "--outcomes-sha256" not in args:
        i = args.index("--outcomes") + 1
        args[i] = outcome_only(args[i])
        if os.path.isfile(args[i]):
            with open(args[i], "rb") as fh: args = ["--outcomes-sha256", sha(fh.read())] + args
    return subprocess.run(cmd + list(args), capture_output=True, text=True)

def outcome_args(root):
    """測試專用（v3.1.2b-r6）：夾具 root 的混合帳 → 獨立 outcome 帳＋sha 的命令列參數。"""
    p = outcome_only(os.path.join(root, "shadow-calls.jsonl"))
    with open(p, "rb") as fh: return ["--outcomes", p, "--outcomes-sha256", sha(fh.read())]

def outcome_only(src):
    """測試專用：把混合帳裡的 outcome 行抽成獨立 outcome 帳（重新串 sha 鏈；原檔鏈斷之後的行照樣斷）。"""
    if not os.path.isfile(src): return src
    with open(src, encoding="utf-8") as fh: raw = fh.read().splitlines()
    out, ok_lines, prev, new_prev, broken, mixed = [], 0, "0" * 64, "0" * 64, False, False
    for ln in raw:
        if not ln.strip(): continue
        try: d = json.loads(ln)
        except Exception: d = None
        if not broken:
            if not isinstance(d, dict): broken = True
            else:
                body = dict(d); s0 = body.pop("sha256", None)
                if body.get("prev_sha256") != prev or sha(canon(body).encode()) != s0: broken = True
                else: prev = s0
        if not isinstance(d, dict) or str(d.get("kind") or d.get("type") or "") != "outcome":
            mixed = True; continue
        r = {k: v for k, v in d.items() if k not in ("sha256", "prev_sha256")}
        if broken: r["prev_sha256"] = "斷"; r["sha256"] = "斷"
        else: r["prev_sha256"] = new_prev; r["sha256"] = sha(canon(r).encode()); new_prev = r["sha256"]
        out.append(json.dumps(r, ensure_ascii=False))
    if not mixed and not os.path.basename(src).lower().startswith("shadow"): return src
    dst = os.path.join(os.path.dirname(src), "outcomes-only-" + os.path.basename(src).replace("shadow", "s"))
    with open(dst, "w", encoding="utf-8") as fh: fh.write("".join(x + "\n" for x in out))
    return dst

class Root:
    """暫存根目錄：休市日曆、快照、帳本、錨點。"""
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="rigor-v3-test-")
        self.cal = self.write("snapshots/cal/pre-DG11761-x.csv", CAL.encode())
        self.cal_sha = sha(CAL.encode())
        self.write("snapshots/cal/pre-DG11761-x.meta.json", json.dumps({"url": CAL_URL, "sha256": self.cal_sha}).encode())
        self.write("ledger-codes.json", json.dumps(CODES).encode())
        self.write("closures/adhoc-closures.jsonl", b"")   # v3.1.2（C4）：--closures 預設開啟，夾具根目錄給空清單＋sha 檔
        self.write("closures/adhoc-closures.sha256", (sha(b"") + "  adhoc-closures.jsonl\n").encode())
        self.lines = {}
        self.vdir = tempfile.mkdtemp(prefix="rigor-v312b-verify-")   # v3.1.2b（B1）：核證檔和「核可者的 sha 檔」放在 root 以外
        self.approvals = os.path.join(self.vdir, "approvals", "closure-verifications.sha256")
        os.environ["TEST_APPROVALS_SHA_FILE"] = self.approvals   # 預設不存在＝全部待核
    def verify(self, rows, vb="查證者", tier="甲", src_date="2026-10-23（逐字核）", extra=None, path=None, sha_override=None):
        """查證者核證檔夾具：每列 {date, row_sha256, tier, url, source_date, verified_by}，再寫核可者的 sha 檔。回傳空參數清單（沒有命令列參數）。"""
        out = []
        for r in rows:
            x = {"date": r["date"], "row_sha256": sha(canon(r).encode()), "tier": tier, "verified_by": vb, "source_date": src_date,
                 "url": [u for u in (r.get("source_urls") or []) + [r.get("url")] if u] or ["https://news.example.org/x"]}
            x.update(extra or {}); out.append(json.dumps(x, ensure_ascii=False) + "\n")
        vp = path or os.path.join(self.vdir, "closure-verifications.jsonl")
        os.makedirs(os.path.dirname(vp), exist_ok=True)
        with open(vp, "wb") as fh: fh.write("".join(out).encode())
        os.makedirs(os.path.dirname(self.approvals), exist_ok=True)
        with open(self.approvals, "w", encoding="utf-8") as fh: fh.write(f"{sha_override or sha(_rb(vp))}  {vp}\n")
        return []
    def anchor(self, covers, when, day, ledger="L3", verified_by="查證者", ref_kind="refs/heads/main", line=None,
               drop=(), arc_time=None, write=True):
        """核帳方 github-push 錨點：Events API 回應、GH Archive 小時檔、commit 檔三份快照（合成）。"""
        eid = str(next(_EID)); head = sha(f"{eid}{covers}".encode())[:40]; ca = utc_z(when)
        ev = {"id": eid, "type": "PushEvent", "repo": {"name": "william9722359/Stock-bot"}, "created_at": ca,
              "payload": {"ref": ref_kind, "head": head}}
        arc = dict(ev, created_at=utc_z(arc_time) if arc_time else ca)
        txt = (line if line is not None else f"{day[:4]}-{day[4:6]}-{day[6:]}\t{covers}\t{ledger}") + "\n"
        cf = {"path": f"anchors/{day[:4]}.txt", "url": f"https://api.github.com/repos/william9722359/Stock-bot/contents/anchors/{day[:4]}.txt?ref={head}",
              "content": base64.b64encode(txt.encode()).decode(), "encoding": "base64"}
        evidence = []
        for kind, body in (("events-api", json.dumps([ev]).encode()), ("gharchive", (json.dumps(arc) + "\n").encode()),
                           ("commit-file", json.dumps(cf).encode())):
            if kind in drop: continue
            rel, hh = self.snap("anchor-ev", f"{kind}-{eid}.json", body)
            evidence.append({"kind": kind, "path": rel, "sha256": hh})
        a = {"covers_sha256": covers, "external_time": "2000-01-01T00:00:00Z", "type": "github-push", "ref": f"{eid}/{head}",
             "ledger": ledger, "trading_day": day, "evidence": evidence}
        if verified_by is not None: a["verified_by"] = verified_by
        if write:
            with open(os.path.join(self.root, "anchors.jsonl"), "a", encoding="utf-8") as f: f.write(json.dumps(a, ensure_ascii=False) + "\n")
        return a
    def write(self, rel, body):
        p = os.path.join(self.root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh: fh.write(body)
        return p
    def snap(self, day, name, body=b"x,y\n1,2\n"):
        self.write(f"snapshots/{day}/{name}", body)
        return f"snapshots/{day}/{name}", sha(body)
    def inp(self, day, fetched, cov, server=None, name="DG11596", body=None, **over):
        rel, h = self.snap(day, f"pre-{name}-{fetched.strftime('%H%M%S')}.csv", body or f"{name},{day},{fetched}\n".encode())
        it = {"fetched_at_tpe": iso(fetched), "server_date_http": http_of(server or fetched + dt.timedelta(seconds=1)),
              "snapshot_path": rel, "snapshot_sha256": h, "coverage_end_tpe": cov if isinstance(cov, (str, type(None))) else iso(cov),
              "request_started_tpe": iso(fetched - dt.timedelta(seconds=2)), "dataset": None}
        it.update(over)
        return {name: it}
    def call(self, day, rule="P1", call="漲", written=None, inputs=None, **extra):
        w = written if written is not None else iso(T(day, 8, 30))
        r = {"kind": "call", "trading_day": day, "rule": rule, "call": call, "written_at_tpe": w,
             "inputs_meta": inputs if inputs is not None else self.inp(day, T(day, 8, 20), T(day, 5, 0))}
        r.update(extra); return r
    def outcome(self, day, chg, actual=None, path=None, sha_override=None):
        roc = f"{int(day[:4]) - 1911}{day[4:]}"
        body = f"日期,成交股數,成交金額,成交筆數,發行量加權股價指數,漲跌點數\n\"{roc}\",\"1\",\"1\",\"1\",\"40000\",\"{chg}\"\n".encode()
        rel, h = self.snap(day, f"post-DG11672-{day}.csv", body)
        a = actual or ("漲" if chg > 0 else "跌" if chg < 0 else "平")
        return {"kind": "outcome", "trading_day": day, "actual": a, "change_pts": chg,
                "sources": {"DG11672": {"sha256": sha_override or h, "snapshot_path": rel}}}
    def ledger(self, name, recs, anchor_after=None, raw_inject=None, chain=True):
        """寫帳（sha 鏈）。anchor_after：{行索引(0 起): 外部時間} → 在那一行之後記一個錨點（covers 該行 sha）。"""
        p = os.path.join(self.root, name); prev = "0" * 64; out = []; anchors = []
        for i, r in enumerate(recs):
            if isinstance(r, str): out.append(r); continue
            r = dict(r)
            if chain:
                r["prev_sha256"] = prev; r["sha256"] = sha(canon(r).encode()); prev = r["sha256"]
            out.append(json.dumps(r, ensure_ascii=False))
            if anchor_after and i in anchor_after:
                anchors.append((r.get("sha256"), anchor_after[i], day8(r.get("trading_day")), {v: k for k, v in CODES.items()}.get(name, "L3")))
        if raw_inject:
            for idx, text in raw_inject: out[idx] = text
        with open(p, "w", encoding="utf-8") as fh: fh.write("\n".join(out) + "\n")
        for cov, when, day, led in anchors: self.anchor(cov, when, day, led)
        return p
    def score(self, files, since, until, extra=(), adopt=True):
        args = ["--root", self.root, "--calendar", self.cal, "--calendar-sha256", self.cal_sha, "--since", since, "--until", until] + list(extra) + list(files)
        r = run_cli(SCORE, ["--json"] + args, adopt)
        assert r.returncode == 0, r.stderr
        js = os.path.join(self.root, "score.json"); Root.write(self, "score.json", r.stdout.encode())
        c = run_cli(RECOMP, ["--compare", js] + args, adopt)
        assert c.returncode == 0 and c.stdout.startswith("一致"), (c.stdout, c.stderr)
        return json.loads(r.stdout)
    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True); shutil.rmtree(self.vdir, ignore_errors=True)

D = "20261027"
ANCH = {0: iso(T(D, 8, 40))}

class Base(unittest.TestCase):
    def setUp(self): self.R = Root()
    def tearDown(self): self.R.cleanup()
    def one(self, rec, rule="P1", extra=(), outcome_chg=10, anchor=None, rules=None):
        """一行預測＋outcome → 回傳 (該日預測判定, 結果 dict)。"""
        R = self.R
        outp = R.ledger("shadow-calls.jsonl", [rec, R.outcome(D, outcome_chg)], anchor_after=anchor if anchor is not None else ANCH)
        res = R.score([outp], D, D, list(extra) + ["--rules", rules or rule])[0]
        return res["rules"][rule]["days"][D][0], res

class TestBaseline(Base):
    def test_A_legal_night(self):
        p, res = self.one(self.R.call(D))
        self.assertEqual(p, "漲"); self.assertEqual(res["rules"]["P1"]["n"], 1); self.assertEqual(res["rules"]["P1"]["hits"], 1)
    def test_E_us_close_news_dst(self):
        # 美股收盤 10/26 16:00 美東（EDT, UTC−4）＝台北 10/27 04:00，用時區函式庫換算
        ny = dt.datetime(2026, 10, 26, 16, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(TPE)
        self.assertEqual(ny.hour, 4)
        p, _ = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 10), ny, name="news")))
        self.assertEqual(p, "漲")

class TestR1Anchor(Base):
    def test_no_server_date(self):
        p, res = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 5, 0), server_date_http=None)))
        self.assertEqual(p, "missing"); self.assertIn("輸入缺伺服器 Date 標頭", res["reasons"])
    def test_server_date_iso_not_http(self):
        p, _ = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 5, 0), server_date_http=iso(T(D, 8, 20)))))
        self.assertEqual(p, "missing")
    def test_server_skew_over_120s(self):
        p, res = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 5, 0), server=T(D, 8, 23))))
        self.assertEqual(p, "missing"); self.assertIn("伺服器 Date 與取得時間相差超過 120 秒", res["reasons"])
    def test_server_skew_exactly_120s_ok(self):
        p, _ = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 5, 0), server=T(D, 8, 22))))
        self.assertEqual(p, "漲")
    def test_server_date_after_cut(self):
        # 本機時鐘被撥慢：本機記 08:44:00，伺服器說 08:45:30
        p, res = self.one(self.R.call(D, written=iso(T(D, 8, 44, 30)), inputs=self.R.inp(D, T(D, 8, 44), T(D, 5, 0), server=T(D, 8, 45, 30))))
        self.assertEqual(p, "missing")
    def test_snapshot_missing(self):
        p, res = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 5, 0), snapshot_path="snapshots/none.csv")))
        self.assertEqual(p, "missing"); self.assertIn("快照檔不存在或 sha256 對不上", res["reasons"])
    def test_snapshot_sha_mismatch(self):
        p, _ = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 5, 0), snapshot_sha256="0" * 64)))
        self.assertEqual(p, "missing")
    def test_request_started_after_fetched(self):
        p, _ = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 5, 0), request_started_tpe=iso(T(D, 8, 21)))))
        self.assertEqual(p, "missing")
    def test_no_anchor(self):
        p, res = self.one(self.R.call(D), anchor={})
        self.assertEqual(p, "missing"); self.assertIn("沒有早於 08:45 的外部錨點", res["reasons"])
    def test_anchor_after_cut(self):
        p, _ = self.one(self.R.call(D), anchor={0: iso(T(D, 8, 45))})
        self.assertEqual(p, "missing")
    def test_anchor_http_date_format(self):
        p, _ = self.one(self.R.call(D), anchor={0: http_of(T(D, 8, 40))})
        self.assertEqual(p, "漲")
    def test_tampered_line_breaks_chain(self):
        R = self.R
        good = R.call(D); later = R.call("20261028", call="跌", written=iso(T("20261028", 8, 30)),
                                          inputs=R.inp("20261028", T("20261028", 8, 20), T("20261028", 5, 0)))
        p = R.ledger("shadow-calls.jsonl", [good, later, R.outcome(D, 10), R.outcome("20261028", -5)],
                     anchor_after={0: iso(T(D, 8, 40)), 1: iso(T("20261028", 8, 40))})
        lines = _rt(p).splitlines()
        rec = json.loads(lines[0]); rec["call"] = "跌"; lines[0] = json.dumps(rec, ensure_ascii=False)   # 改內容不重算 sha
        _w(p, "\n".join(lines) + "\n", "w", "utf-8")
        res = R.score([p], D, "20261028", ["--rules", "P1"])[0]
        self.assertEqual(res["chain_break_line"], 1)
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertEqual(res["rules"]["P1"]["days"]["20261028"][0], "missing")
        self.assertEqual(res["rules"]["P1"]["n"], 2)
    def test_rewritten_chain_loses_anchor(self):
        # 事後整份重寫並重算新鏈：舊錨點對不到新 sha → 判 missing
        R = self.R
        R.ledger("shadow-calls.jsonl", [R.call(D, call="跌"), R.outcome(D, 10)], anchor_after=ANCH)
        p = R.ledger("shadow-calls.jsonl", [R.call(D, call="漲"), R.outcome(D, 10)], anchor_after={})
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
    def test_backfilled_line_with_fake_time(self):
        # 09:00 後補寫但把時間填 08:40：錨點只有更早一行的 sha（補寫行在錨點之後）→ missing
        R = self.R
        first = R.call(D, rule="P2")
        fake = R.call(D, rule="P1")
        p = R.ledger("shadow-calls.jsonl", [first, fake, R.outcome(D, 10)], anchor_after={0: iso(T(D, 8, 40))})
        res = R.score([p], D, D, ["--rules", "P1,P2"])[0]
        self.assertEqual(res["rules"]["P2"]["days"][D][0], "漲")
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")

class TestR1Fetch(unittest.TestCase):
    """fetch_anchor：收完才記時間、存 Date 標頭、原始 sha256、快照；帳本時間自動產生。"""
    def test_fetch_records_after_read(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/csv"); self.end_headers()
                self.wfile.write(b"a,b\n"); self.wfile.flush(); time.sleep(1.5); self.wfile.write(b"1,2\n")
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        d = tempfile.mkdtemp(prefix="rigor-v3-fetch-")
        try:
            meta, body = FA.snapshot_one(f"http://127.0.0.1:{srv.server_port}/x", "local", d, "pre", None,
                                         (dt.datetime.now(TPE) + dt.timedelta(seconds=60)).isoformat(timespec="seconds"), (), d)
            self.assertEqual(body, b"a,b\n1,2\n")
            self.assertEqual(meta["snapshot_sha256"], sha(body))
            self.assertEqual(sha(_rb(os.path.join(d, meta["snapshot_path"]))), meta["snapshot_sha256"])
            self.assertIsNotNone(FA.parse_http_date(meta["server_date_http"]))
            t0 = dt.datetime.fromisoformat(meta["request_started_tpe"]); t1 = dt.datetime.fromisoformat(meta["fetched_at_tpe"])
            self.assertGreaterEqual((t1 - t0).total_seconds(), 1)   # 取得時間在收完之後
            self.assertIsNone(meta["coverage_end_tpe"])  # 給定的涵蓋時間（現在＋60 秒，r8：晚幾小時的偷看也抓得到）晚於取得時間 → 異常 → null
        finally:
            srv.shutdown(); srv.server_close(); shutil.rmtree(d, ignore_errors=True)
    def test_blocked_sources(self):
        for u in ("https://finance.yahoo.com/x", "https://www.cnbc.com/x", "https://www.twse.com.tw/zh/page.html", "https://www.tpex.org.tw/web/x"):
            self.assertFalse(FA.url_allowed(u)[0], u)
        self.assertTrue(FA.url_allowed("https://www.twse.com.tw/exchangeReport/FMTQIK?response=open_data")[0])
    def test_append_auto_time_and_chain(self):
        d = tempfile.mkdtemp(prefix="rigor-v3-led-"); p = os.path.join(d, "practice-calls.jsonl")
        try:
            FA.append_records(p, [{"kind": "call", "trading_day": D}, {"kind": "call", "trading_day": "20261028"}])
            self.assertEqual(FA.verify_chain(p), (True, None))
            self.assertIsNotNone(FA.strict_ts(FA.read_lines(p)[0]["written_at_tpe"]))
            with self.assertRaises(SystemExit):
                FA.append_records(p, [{"kind": "call", "written_at_tpe": "2026-10-27T08:40:00+08:00"}])
        finally:
            shutil.rmtree(d, ignore_errors=True)

class TestR2Deadline(Base):
    def test_T3_fetched_085959(self):
        p, _ = self.one(self.R.call(D, written=iso(T(D, 9, 0)), inputs=self.R.inp(D, T(D, 8, 59, 59), T(D, 5, 0))))
        self.assertEqual(p, "missing")
    def test_T7a_fetched_084459_ok(self):
        p, _ = self.one(self.R.call(D, written=iso(T(D, 8, 44, 59)), inputs=self.R.inp(D, T(D, 8, 44, 58), T(D, 5, 0), server=T(D, 8, 44, 58))))
        self.assertEqual(p, "漲")
    def test_T7b_fetched_0845(self):
        p, _ = self.one(self.R.call(D, written=iso(T(D, 8, 44, 59)), inputs=self.R.inp(D, T(D, 8, 45), T(D, 5, 0), server=T(D, 8, 44, 50))))
        self.assertEqual(p, "missing")
    def test_written_0845(self):
        p, res = self.one(self.R.call(D, written=iso(T(D, 8, 45))))
        self.assertEqual(p, "missing"); self.assertIn("寫入時間不早於 08:45", res["reasons"])
    def test_T7c_coverage_0845(self):
        p, _ = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 46), T(D, 8, 45))), anchor={0: iso(T(D, 8, 40))})
        self.assertEqual(p, "missing")
    def test_T7d_coverage_084459_ok(self):
        p, _ = self.one(self.R.call(D, written=iso(T(D, 8, 44, 59)),
                                    inputs=self.R.inp(D, T(D, 8, 44, 58), T(D, 8, 44, 58), server=T(D, 8, 44, 58))),
                        anchor={0: iso(T(D, 8, 44, 59))})
        self.assertEqual(p, "漲")
    def test_C_coverage_day_session(self):
        p, res = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 13, 45))))
        self.assertEqual(p, "missing")
    def test_shadow_deadline_constant(self):
        import shadow_run
        self.assertEqual(shadow_run.PRE_DEADLINE, dt.time(8, 45)); self.assertEqual(shadow_run.RIGOR, "v3")

class TestR3Time(Base):
    def test_T1_date_only_coverage(self):
        p, res = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), "2026-10-27")))
        self.assertEqual(p, "missing"); self.assertIn("輸入缺涵蓋結束時間（或來源異常）", res["reasons"])
    def test_T1b_compact_date(self):
        p, _ = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), "20261027")))
        self.assertEqual(p, "missing")
    def test_T2_naive_written(self):
        p, res = self.one(self.R.call(D, written="2026-10-27T01:30:00"))
        self.assertEqual(p, "missing"); self.assertIn("缺寫入時間或時間沒有時區", res["reasons"])
    def test_naive_fetched(self):
        p, _ = self.one(self.R.call(D, inputs=self.R.inp(D, T(D, 8, 20), T(D, 5, 0), fetched_at_tpe="2026-10-27T08:20:00")))
        self.assertEqual(p, "missing")
    def test_Z_time_ok(self):
        p, _ = self.one(self.R.call(D, written="2026-10-27T00:40:00Z"))
        self.assertEqual(p, "漲")
    def test_strict_ts_helpers(self):
        self.assertIsNone(FA.strict_ts("2026-10-27")); self.assertIsNone(FA.strict_ts("2026-10-27T08:00:00"))
        self.assertIsNone(FA.strict_ts("20261027T080000+0800"))
        self.assertIsNotNone(FA.strict_ts("2026-10-27T08:00:00+08:00"))

NIGHT_HDR = "日期,契約代號,到期月份(週別),開盤價,最高價,最低價,最後成交價,漲跌價,漲跌%,合計成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量\n"
def nrow(day, last, chg, sess, settle="NULL"):
    return f"{day},TX,202611,1,1,1,{last},{chg},0.1%,1,{settle},-,1,1,1,1,,{sess},\n"

class TestR4Coverage(unittest.TestCase):
    F = T(D, 8, 30)
    def test_T5a_dated_future_row_blocked(self):
        b = ("日期,商品名稱\n20261027,臺股期貨\n").encode()
        self.assertEqual(FA.coverage_end("DG11596", b, self.F)[0], None)
    def test_T5b_renamed_column(self):
        b = ("交易日期,商品名稱\n20261026,臺股期貨\n").encode()
        ce, basis = FA.coverage_end("DG11596", b, self.F)
        self.assertIsNone(ce); self.assertIn("找不到日期欄", basis)
    def test_T5c_datetime_in_date(self):
        b = ("日期,商品名稱\n2026-10-27T13:45:00,臺股期貨\n").encode()
        self.assertIsNone(FA.coverage_end("DG11596", b, self.F)[0])
    def test_blank_date_nonblank_row(self):
        b = ("日期,商品名稱\n20261022,臺股期貨\n,臺股期貨\n").encode()
        self.assertIsNone(FA.coverage_end("DG11596", b, self.F)[0])
    def test_valid_dated(self):
        b = ("日期,商品名稱\n20261022,臺股期貨\n\n").encode()
        self.assertEqual(FA.coverage_end("DG11596", b, self.F)[0], T("20261022", 23, 59, 59))
    def test_unknown_dataset_blocked(self):
        self.assertIsNone(FA.coverage_end("DG99999", "日期\n20261022\n".encode(), self.F)[0])
    def test_undated_uses_fetched(self):
        self.assertEqual(FA.coverage_end("DG11655", b"a\n1\n", self.F)[0], self.F)
    def test_null_coverage_scored_missing(self):
        R = Root()
        try:
            rec = R.call(D, inputs=R.inp(D, T(D, 8, 20), None))
            p = R.ledger("shadow-calls.jsonl", [rec, R.outcome(D, 1)], anchor_after=ANCH)
            res = R.score([p], D, D, ["--rules", "P1"])[0]
            self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        finally: R.cleanup()

class TestD1NightAndPostHoliday(unittest.TestCase):
    """D1 夜盤參考價自核＋連假後首日（依查證者內部查核紀錄：10/23 颱風休市含盤後、10/26 補假；紀錄未公開）。"""
    F = T(D, 8, 30)
    def test_night_ref_matches_same_file(self):
        b = (NIGHT_HDR + nrow("20261022", 46272, -741, "一般", 46234) + nrow("20261022", 47482, 469, "盤後")).encode()
        ce, basis = FA.coverage_end("DG11319", b, self.F)
        self.assertEqual(ce, T("20261022", 23, 59, 59)); self.assertIn("自核通過 1 列", basis)
    def test_night_ref_mismatch_blocked(self):
        b = (NIGHT_HDR + nrow("20261022", 46272, -741, "一般", 46234) + nrow("20261022", 47482, 400, "盤後")).encode()
        self.assertIsNone(FA.coverage_end("DG11319", b, self.F)[0])
    def test_post_holiday_night_row_with_prior_settlement(self):
        # 10/27 開盤前拿到標 20261027 的盤後列（＝10/22 15:00 起夜盤，參考價＝10/22 結算 46234）；先前快照有 10/22 結算 → 可證 → 10/27 05:00
        prior = (NIGHT_HDR + nrow("20261022", 46272, -741, "一般", 46234)).encode()
        b = (NIGHT_HDR + nrow("20261027", 46100, -134, "盤後")).encode()
        ce, _ = FA.coverage_end("DG11319", b, self.F, [prior])
        # v3.1 更正（交叉核對 1-6，rigor-rules 第 52 行）：此夜盤 10/22 15:00 開、10/23 05:00 收，不是標示日 10/27 05:00
        self.assertEqual(ce, T("20261023", 5, 0))
    def test_post_holiday_night_row_unprovable(self):
        # 沒有任何可核對的前一結算 → 證明不了 → null → missing
        b = (NIGHT_HDR + nrow("20261027", 46100, -134, "盤後")).encode()
        self.assertIsNone(FA.coverage_end("DG11319", b, self.F)[0])
    def test_post_holiday_night_row_wrong_reference(self):
        # 參考價對的是休市期間不存在的結算（例如有人把別段夜盤標成 10/27）→ 對不上 → null
        prior = (NIGHT_HDR + nrow("20261022", 46272, -741, "一般", 46234)).encode()
        b = (NIGHT_HDR + nrow("20261027", 46100, -500, "盤後")).encode()
        self.assertIsNone(FA.coverage_end("DG11319", b, self.F, [prior])[0])
    def test_real_raw_11319_passes(self):
        p = os.environ.get("RAW_11319_CSV", "")  # 實檔是期交所原始資料，不放進 repo；要跑本案例請自行下載後指定路徑
        if not os.path.exists(p): self.skipTest("沒有實檔")
        ce, basis = FA.coverage_end("DG11319", _rb(p), T("20261023", 11, 37, 12))
        self.assertEqual(ce, T("20261022", 23, 59, 59)); self.assertIn("自核通過 56 列", basis)
    def test_scoring_post_holiday(self):
        R = Root()
        try:
            pre_hol = R.inp(D, T(D, 8, 20), T("20261022", 23, 59, 59), name="DG11319")       # 假前夜盤檔：可證 → 合法
            unknown = R.inp(D, T(D, 8, 21), None, name="DG11319b")                             # 涵蓋時間不明 → missing
            recs = [R.call(D, rule="P1", inputs=pre_hol), R.call(D, rule="P2", call="跌", inputs=unknown),
                    R.call(D, rule="P3", inputs=dict(pre_hol, **unknown)), R.outcome(D, -20)]
            p = R.ledger("shadow-calls.jsonl", recs, anchor_after={2: iso(T(D, 8, 40))})
            res = R.score([p], "20261022", D, ["--rules", "P1,P2,P3"])[0]
            self.assertEqual(res["trading_days"], ["20261022", "20261023", D])   # 10/26 補假不在分母；10/23 颱風沒有官方快照就留在分母
            r = res["rules"]
            self.assertEqual(r["P1"]["days"][D], ["漲", "跌"]); self.assertEqual(r["P2"]["days"][D][0], "missing")
            self.assertEqual(r["P3"]["days"][D][0], "missing")
            for k in ("P1", "P2", "P3"): self.assertEqual(r[k]["n"], 3); self.assertEqual(r[k]["hits"], 0)
        finally: R.cleanup()

class TestR5Denominator(Base):
    def test_T4_four_days(self):
        R = self.R
        d1, d2, d3, d4 = "20261027", "20261028", "20261029", "20261030"
        late = R.call(d1, written=iso(T(d1, 9, 5)))
        fix = R.call(d1)                                   # 補寫的合法行：同日第二行，不採用
        draft = dict(R.call(d2, inputs=R.inp(d2, T(d2, 8, 20), T(d2, 5, 0)), written=iso(T(d2, 8, 30))), kind="draft")
        noout = R.call(d3, inputs=R.inp(d3, T(d3, 8, 20), T(d3, 5, 0)), written=iso(T(d3, 8, 30)))
        fmt = dict(R.call(d4, inputs=R.inp(d4, T(d4, 8, 20), T(d4, 5, 0)), written=iso(T(d4, 8, 30))), trading_day="2026-10-30")
        recs = [late, fix, R.outcome(d1, 5), draft, R.outcome(d2, 5), noout, fmt, R.outcome(d4, 5)]
        p = R.ledger("shadow-calls.jsonl", recs, anchor_after={1: iso(T(d1, 8, 40)), 3: iso(T(d2, 8, 40)),
                                                                5: iso(T(d3, 8, 40)), 6: iso(T(d4, 8, 40))})
        res = R.score([p], d1, d4, ["--rules", "P1"])[0]
        r = res["rules"]["P1"]
        self.assertEqual(r["n"], 4)
        self.assertEqual(r["days"][d1][0], "missing"); self.assertEqual(r["days"][d2][0], "missing")
        self.assertEqual(r["days"][d3], ["漲", None]); self.assertEqual(r["days"][d4], ["漲", "漲"])
        self.assertEqual(r["hits"], 1)
        self.assertIn(d3, res["unpaired_days"])
        self.assertIn("同日重複預測行（取第一行）", res["reasons"])
    def test_day_without_line_counts_wrong(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5), R.outcome("20261028", 5)], anchor_after=ANCH)
        r = R.score([p], D, "20261028", ["--rules", "P1"])[0]["rules"]["P1"]
        self.assertEqual(r["n"], 2); self.assertEqual(r["days"]["20261028"], ["missing", "漲"]); self.assertEqual(r["hits"], 1)
    def test_not_run_counts_wrong(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [{"kind": "not_run", "trading_day": D, "status": "not_run", "written_at_tpe": iso(T(D, 9, 1))},
                                           R.call(D), R.outcome(D, 5)], anchor_after={1: iso(T(D, 8, 40))})
        r = R.score([p], D, D, ["--rules", "P1"])[0]["rules"]["P1"]
        self.assertEqual(r["days"][D][0], "missing"); self.assertEqual(r["n"], 1)
    def test_bad_json_line(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        with open(p, "a", encoding="utf-8") as f: f.write("{壞行\n")
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertIn("JSON 壞行", res["reasons"]); self.assertEqual(res["chain_break_line"], 3)
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "漲")   # 壞行在後面，不影響之前已錨定的行
    def test_dry_run_in_formal(self):
        p, res = self.one(self.R.call(D, dry_run=True))
        self.assertEqual(p, "missing"); self.assertIn("正式檔有 dry_run 行", res["reasons"])
    def test_dry_run_file_flag(self):
        p, _ = self.one(self.R.call(D, dry_run=True), extra=["--dry-run-file"])
        self.assertEqual(p, "漲")
    def test_holiday_and_weekend_excluded(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        res = R.score([p], "20261023", D, ["--rules", "P1"])[0]
        self.assertEqual(res["trading_days"], ["20261023", D])
    def test_closure_with_evidence(self):
        R = self.R
        rel, h = R.snap("evidence", "typhoon-1023.html", b"notice")
        base = {"url": "https://www.twse.com.tw/zh/announcement/x", "approval_basis": "official_notice"}   # v3.1.2b 夾具：查證者外部核證檔、官方網域、當日 Date
        rows = [dict(base, date="20261023", snapshot_path=rel, snapshot_sha256=h, server_date_http="Fri, 23 Oct 2026 01:00:00 GMT"),
                dict(base, date="20261022", snapshot_path=rel, snapshot_sha256="0" * 64, server_date_http="Thu, 22 Oct 2026 01:00:00 GMT")]
        clo = R.write("closures.jsonl", "".join(json.dumps(x) + "\n" for x in rows).encode())
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        res = R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))] + R.verify(rows))[0]
        self.assertEqual(res["trading_days"], ["20261022", D])   # 有官方快照的 10/23 排除；sha 不對的 10/22 不排除
    def test_old_v2_format_missing(self):
        rec = {"kind": "call", "trading_day": D, "rule": "P1", "call": "漲", "written_at_tpe": iso(T(D, 8, 30)),
               "inputs_downloaded_at_tpe": {"DG11319": iso(T(D, 8, 20))}, "inputs_coverage_end_tpe": {"DG11319": iso(T(D, 5, 0))}}
        p, res = self.one(rec)
        self.assertEqual(p, "missing"); self.assertIn("缺 inputs_meta（v3 錨點欄位）", res["reasons"])

class TestR6Outcomes(Base):
    def test_actual_mismatch_unpaired(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, -5, actual="漲")], anchor_after=ANCH)
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertIn(D, res["unpaired_days"]); self.assertEqual(res["rules"]["P1"]["days"][D], ["漲", None])
        self.assertEqual(res["rules"]["P1"]["hits"], 0); self.assertEqual(res["rules"]["P1"]["n"], 1)
    def test_outcome_snapshot_missing(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5, sha_override="1" * 64)], anchor_after=ANCH)
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertIn(D, res["unpaired_days"])
    def test_practice_outcome_ignored(self):
        R = self.R
        shadow = R.ledger("shadow-calls.jsonl", [R.outcome(D, -5)])
        fake_out = {"kind": "outcome", "trading_day": D, "actual": "漲", "sources": {}}   # practice 檔自填的 outcome
        prac = R.ledger("practice-calls.jsonl", [R.call(D, rule="正式", call="漲"), fake_out], anchor_after=ANCH)
        res = R.score([prac], D, D, ["--rules", "正式", "--outcomes", shadow])[0]
        self.assertEqual(res["rules"]["正式"]["days"][D], ["漲", "跌"]); self.assertEqual(res["rules"]["正式"]["hits"], 0)

class TestStats(Base):
    def test_holm_and_small_sample_text(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D, rule="P1"), R.call(D, rule="P2", call="跌"), R.outcome(D, 5)], anchor_after={1: iso(T(D, 8, 40))})
        res = R.score([p], D, D, ["--rules", "P1,P2"])[0]["rules"]
        # v3.1.1（P-B3）：53.6% 預設拿掉，不再有 p／holm_p 欄；只有同期天天猜漲（本例 1/1）的 p 與 Holm
        for r in ("P1", "P2"): self.assertNotIn("p", res[r]); self.assertNotIn("holm_p", res[r])
        self.assertAlmostEqual(res["P1"]["p_same_period"], 1.0); self.assertAlmostEqual(res["P2"]["p_same_period"], 1.0)
        self.assertAlmostEqual(res["P1"]["holm_p_same_period"], 1.0)
        out = subprocess.run([sys.executable, SCORE, *outcome_args(R.root), "--root", R.root, "--calendar", R.cal, "--calendar-sha256", R.cal_sha, "--since", D, "--until", D, "--rules", "P1,P2", p],
                             capture_output=True, text=True).stdout
        self.assertIn("樣本太少，參考用", out); self.assertIn("不下有效或無效結論", out)

# ======================= v3.1 新增：B0、N1–N8、A1–A7=======================
D2 = "20261028"

def two_day(R, call2="漲", out2=-10, anchors=True):
    recs = [R.call(D), R.call(D2, call=call2, written=iso(T(D2, 8, 30)), inputs=R.inp(D2, T(D2, 8, 20), T(D2, 5, 0))),
            R.outcome(D, 10), R.outcome(D2, out2)]
    return R.ledger("shadow-calls.jsonl", recs, anchor_after={0: iso(T(D, 8, 40)), 1: iso(T(D2, 8, 40))} if anchors else None)

class TestB0NotAdopted(Base):
    def test_B0_production_cli_always_missing(self):
        """正式 CLI（沒有打開開關）：就算錨點三份證據都齊、時間 08:40，一律判缺錨點；score 和 recompute 一致。"""
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)], anchor_after=ANCH)
        res = R.score([p], D, D, ["--rules", "P1"], adopt=False)[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertIn("外部錨點方案尚未採用（v3.1 B0）：一律判缺錨點", res["reasons"])
        self.assertIn("沒有早於 08:45 的外部錨點", res["reasons"])
    def test_B0_switch_is_code_constant_false(self):
        for f in (SCORE, RECOMP):
            src = _rt(f)
            self.assertRegex(src, r"(?m)^ANCHOR_SCHEME_ADOPTED = False\b")
            self.assertNotIn("--adopt", src)
    def test_B0_no_push_code(self):
        for f in (SCORE, RECOMP, os.path.join(HERE, "fetch_anchor.py")):
            src = _rt(f)
            for bad in ("git push", "GITHUB_STOCKBOT_TOKEN", "subprocess.run([\"git\"", "api.github.com/repos/william9722359/Stock-bot/git"):
                self.assertNotIn(bad, src, f)

class TestN(Base):
    def test_N1_self_written_local_anchor_rejected(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)])
        last = json.loads(_rt(p).splitlines()[0])["sha256"]
        R.write("anchors.jsonl", (json.dumps({"covers_sha256": last, "external_time": iso(T(D, 8, 40)), "type": "隨便寫", "ref": ""}) + "\n"
                                  + json.dumps({"covers_sha256": last, "external_time": iso(T(D, 8, 40)), "type": "github-push",
                                                "ref": "1/" + "a" * 40, "ledger": "L3", "trading_day": D, "verified_by": "查證者"}) + "\n").encode())
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertEqual(res["reasons"].get("錨點不採用：錨點 type 不是 github-push"), 1)
        self.assertEqual(res["reasons"].get("錨點不採用：錨點證據快照缺或 sha256 對不上"), 1)
    def test_N2_coverage_rederived_from_snapshot(self):
        R = self.R
        body = "日期,商品名稱,身份別,多空交易口數淨額\n20261027,臺股期貨,外資及陸資,-9999\n".encode()
        for ds in ("DG11596", None):
            R2 = Root()
            try:
                inp = R2.inp(D, T(D, 8, 20), T(D, 5, 0), body=body, dataset=ds)
                p = R2.ledger("shadow-calls.jsonl", [R2.call(D, inputs=inp), R2.outcome(D, 10)], anchor_after=ANCH)
                res = R2.score([p], D, D, ["--rules", "P1"])[0]
                self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing", ds)
                self.assertTrue(any(k.startswith("從快照重算涵蓋結束時間失敗") or k.startswith("涵蓋結束時間晚於取得時間") for k in res["reasons"]), res["reasons"])
            finally: R2.cleanup()
    def test_N2b_missing_dataset_field(self):
        R = self.R
        inp = R.inp(D, T(D, 8, 20), T(D, 5, 0)); list(inp.values())[0].pop("dataset")
        p, res = self.one(R.call(D, inputs=inp))
        self.assertEqual(p, "missing"); self.assertIn("inputs_meta 缺 dataset 欄", res["reasons"])
    def test_N3_coverage_override_with_dataset_refused(self):
        d = tempfile.mkdtemp(prefix="rigor-v31-n3-")
        try:
            with self.assertRaises(ValueError):
                FA.snapshot_one("https://www.taifex.com.tw/x", "DG11596", d, "pre", "DG11596", (dt.datetime.now(TPE) + dt.timedelta(seconds=60)).isoformat(timespec="seconds"), (), d)
            self.assertEqual(os.listdir(d), [])   # 拒絕發生在抓取之前
        finally: shutil.rmtree(d, ignore_errors=True)
    def test_N4_forged_closure_not_deducted(self):
        R = self.R
        p = two_day(R)
        rel, h = R.snap("ev", "fake-typhoon.html", b"<p>totally official</p>")
        forged = [{"date": D2, "snapshot_path": rel, "snapshot_sha256": h, "url": "https://example.com", "server_date_http": "Wed, 28 Oct 2026 01:00:00 GMT", "status": "verified", "verified_by": "查證者"},
                  {"date": D2, "snapshot_path": rel, "snapshot_sha256": h, "url": "https://www.twse.com.tw/x", "server_date_http": "Wed, 28 Oct 2026 01:00:00 GMT", "status": "verified", "verified_by": "股票機器人"},
                  {"date": D2, "snapshot_path": rel, "snapshot_sha256": h, "url": "https://www.twse.com.tw/x", "server_date_http": "Tue, 20 Oct 2026 01:00:00 GMT", "status": "verified", "verified_by": "查證者"}]
        for c in forged: c["approval_basis"] = "official_notice"   # v3.1.2b：三列都有查證者外部核證列，照樣擋
        clo = R.write("closures.jsonl", "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in forged).encode())
        res = R.score([p], D, D2, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))] + R.verify([forged[0], forged[2]]))[0]
        self.assertEqual(res["rules"]["P1"]["n"], 2); self.assertEqual(res["rules"]["P1"]["hits"], 1)
        self.assertEqual(res["reasons"]["臨時休市清單不合格行（不扣）"], 2)   # v3.1.2b：寫帳方那列沒有核證列 → 待核
        self.assertEqual(res["reasons"]["臨時休市列待查證者核證（pending_verify，不扣）"], 1)
        r = run_cli(SCORE, ["--root", R.root, "--calendar", R.cal, "--calendar-sha256", R.cal_sha, "--since", D, "--until", D2, "--closures", clo, p])
        self.assertNotEqual(r.returncode, 0); self.assertIn("closures-sha256", r.stderr)
    def test_N5_calendar_must_be_explicit_frozen_and_official(self):
        R = self.R
        p = two_day(R)
        planted = R.write("snapshots/zz/pre-DG11761-zz.csv", (CAL + "\"假的\",\"1151028\",\"三\",\"x\"\n").encode())
        cases = [["--since", D, "--until", D2],
                 ["--since", D, "--until", D2, "--calendar", planted],
                 ["--since", D, "--until", D2, "--calendar", planted, "--calendar-sha256", R.cal_sha],
                 ["--since", D, "--until", D2, "--calendar", planted, "--calendar-sha256", sha(_rb(planted))]]   # 沒有官方 meta
        for script in (SCORE, RECOMP):
            for c in cases:
                r = run_cli(script, ["--root", R.root] + c + [p])
                self.assertNotEqual(r.returncode, 0, (script, c)); self.assertIn("拒算", r.stdout + r.stderr)
    def test_N6_truncated_tail_invalidates_file(self):
        R = self.R
        p = two_day(R)
        lines = _rt(p).splitlines()
        _w(p, lines[0] + "\n", "w")   # 截帳尾：只留 10/27 call，10/28 call 與兩天 outcome 被截
        # v3.1.2b-r6（N1）：outcome 帳不准是 0 行，實際方向改由獨立 outcome 帳提供；本測試只驗截尾讓預測帳整檔無效
        outp = R.ledger("outcomes-n6.jsonl", [R.outcome(D, 10), R.outcome(D2, -10)])
        res = R.score([p], D, D2, ["--rules", "P1", "--outcomes", outp])[0]
        self.assertIn("錨點指向的行不存在＝帳本被截或改（整檔無效）", res["reasons"])
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertEqual(res["rules"]["P1"]["days"][D2][0], "missing")
        self.assertEqual(res["rules"]["P1"]["n"], 2)
        for script in (SCORE, RECOMP):
            r = run_cli(script, ["--outcomes", outp, "--root", R.root, "--calendar", R.cal, "--calendar-sha256", R.cal_sha, p])
            self.assertNotEqual(r.returncode, 0); self.assertIn("--since", r.stdout + r.stderr)
    def test_N6b_missing_tail_day_counts_wrong(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)], anchor_after=ANCH)
        res = R.score([p], D, "20261030", ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["n"], 4); self.assertEqual(res["rules"]["P1"]["hits"], 1)
        self.assertEqual(res["reasons"]["交易日沒有預測行（算錯）"], 3)
    def test_N7_fractional_seconds_consistent(self):
        R = self.R
        p, res = self.one(R.call(D, written="2026-10-27T08:44:59.500000+08:00", inputs=R.inp(D, T(D, 8, 44, 59), T(D, 5, 0), server=T(D, 8, 44, 59))),
                          anchor={0: iso(T(D, 8, 44, 59))})
        self.assertEqual(p, "漲")   # Root.score 內已斷言 recompute 一致
        R2 = Root()
        try:
            p2 = R2.ledger("shadow-calls.jsonl", [R2.call(D, written="2026-10-27T08:44:59.400000+08:00",
                                                         inputs=R2.inp(D, T(D, 8, 44, 59), T(D, 5, 0), server=T(D, 8, 44, 59),
                                                                       fetched_at_tpe="2026-10-27T08:44:59.500000+08:00")), R2.outcome(D, 10)],
                           anchor_after={0: iso(T(D, 8, 44, 59))})
            r2 = R2.score([p2], D, D, ["--rules", "P1"])[0]
            self.assertEqual(r2["rules"]["P1"]["days"][D][0], "missing")
            self.assertIn("輸入取得時間不早於寫入時間或 08:45", r2["reasons"])
        finally: R2.cleanup()
    def test_N8_anchor_row_in_ledger_ignored(self):
        R = self.R
        c = R.call(D)
        p = R.ledger("shadow-calls.jsonl", [c, R.outcome(D, 10)])
        first = _rt(p).splitlines()[0]
        rec = {"kind": "anchor", "anchored_line_sha256": sha(first.encode()), "commit_sha": "x" * 40,
               "server_time_utc": "2026-10-27T00:40:00Z", "server_time_tpe": iso(T(D, 8, 40)), "snapshot_path": "", "snapshot_sha256": ""}
        with self.assertRaises(SystemExit):
            FA.append_records(p, [rec])
        prev = json.loads(_rt(p).splitlines()[-1])["sha256"]
        r = dict(rec, prev_sha256=prev); r["sha256"] = sha(canon(r).encode())   # 繞過工具直接寫進鏈
        with open(p, "a", encoding="utf-8") as f: f.write(json.dumps(r, ensure_ascii=False) + "\n")
        res = R.score([p], D, D)[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertIn("帳本內的錨點行（不採用、不計）", res["reasons"])
        self.assertNotIn("預測日格式不明的行", res["reasons"])
        self.assertEqual(sorted(res["rules"]), ["P1", "P2", "P3", "P4", "P5"])

class TestSamePeriodBaseline(Base):
    def test_same_period_always_up_is_formal_baseline(self):
        """predict-algo §11：正式檢定以同期天天猜漲率為 H0；53.6% 只當對照並標舊數。"""
        R = self.R
        p = two_day(R, call2="跌", out2=-10)   # 實際 漲、跌 → 同期天天猜漲 1/2；P1 猜 漲、跌 → 2/2
        res = R.score([p], D, D2, ["--rules", "P1"])[0]["rules"]["P1"]
        self.assertAlmostEqual(res["always_up_rate"], 0.5); self.assertAlmostEqual(res["p_same_period"], 0.25)
        self.assertNotIn("p", res); self.assertNotIn("holm_p", res)
        import score_prf as S, recompute as RC                                  # noqa: E402
        with self.assertRaises(TypeError): S.binom_p(2, 2)                    # P-B3：沒有預設 p0
        with self.assertRaises(TypeError): RC.tail_p(2, 2)
        with self.assertRaises(ValueError): RC.tail_p(2, 2, None)
        with self.assertRaises(ValueError): S.binom_p(2, 2, None)
        self.assertAlmostEqual(S.binom_p(2, 2, 0.5), 0.25)
        out = subprocess.run([sys.executable, "-c", ADOPT, SCORE, *outcome_args(R.root), "--root", R.root, "--calendar", R.cal, "--calendar-sha256", R.cal_sha,
                              "--since", D, "--until", D2, "--rules", "P1", p], capture_output=True, text=True).stdout
        self.assertIn("H0 命中率 ≤ 同期間實際天天猜漲率", out); self.assertIn("55.65%（n=1213）", out); self.assertIn("53.00%（n=6887）", out)
        self.assertIn("53.6% 舊數、來源不合規", out); self.assertNotIn("長期基準 53.6%", out)

class TestIndexOnly(Base):
    def test_L2_ledger_not_scored(self):
        """2026-10-23 裁定：只算大盤指數；practice-calls-L2.jsonl 不計分（舊行保留），practice 與 shadow 照常。"""
        R = self.R
        q = R.ledger("practice-calls-L2.jsonl", [R.call(D, rule="正式"), R.outcome(D, 10)], anchor_after=ANCH)
        before = _rb(q)
        s = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)], anchor_after=ANCH)
        res = R.score([q, s], D, D, ["--rules", "P1"])
        self.assertIn("不計分", res[0]["excluded"]); self.assertNotIn("rules", res[0])
        self.assertEqual(res[1]["rules"]["P1"]["hits"], 1)
        self.assertEqual(_rb(q), before)

class TestA(Base):
    def test_A1_commit_nonce(self):
        d = tempfile.mkdtemp(prefix="rigor-v31-a1-"); p = os.path.join(d, "practice-calls.jsonl")
        try:
            out = FA.append_records(p, [{"kind": "call", "trading_day": D, "call": "跌"}, {"kind": "call", "trading_day": D2, "call": "跌"}])
            ns = [r["commit_nonce"] for r in out]
            for n in ns: self.assertRegex(n, r"^[0-9a-f]{64}$")
            self.assertNotEqual(ns[0], ns[1]); self.assertEqual(FA.verify_chain(p), (True, None))
            with self.assertRaises(SystemExit):
                FA.append_records(p, [{"kind": "call", "trading_day": D, "commit_nonce": "0" * 64}])
        finally: shutil.rmtree(d, ignore_errors=True)
    def test_A2_tail_sha_ok_whole_line_bytes_missing(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)])
        line = _rt(p).splitlines()[0]
        R.anchor(sha(line.encode()), iso(T(D, 8, 40)), D)          # 整行 bytes 的雜湊（舊稿定義）
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        os.remove(os.path.join(R.root, "anchors.jsonl"))
        R.anchor(json.loads(line)["sha256"], iso(T(D, 8, 40)), D)  # 鏈尾 sha256 欄（v3.1 定義）
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "漲")
    def test_A3_one_tail_covers_all_rules(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D, rule=r) for r in ("P1", "P2", "P3", "P4", "P5")] + [R.outcome(D, 10)],
                     anchor_after={4: iso(T(D, 8, 40))})
        res = R.score([p], D, D)[0]
        for r in ("P1", "P2", "P3", "P4", "P5"): self.assertEqual(res["rules"][r]["days"][D][0], "漲", r)
    def test_A4_second_value_same_key_is_multibet(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D, call="跌"), R.outcome(D, -10)], anchor_after=ANCH)
        R.anchor(sha(b"another chain tail"), iso(T(D, 8, 41)), D)
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertIn("同一（日期, 帳本）錨點多值＝多押嫌疑", res["reasons"])
    def test_A4_non_main_branch_rejected(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)])
        R.anchor(json.loads(_rt(p).splitlines()[0])["sha256"], iso(T(D, 8, 40)), D, ref_kind="refs/heads/bet-up")
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertIn("錨點不採用：Events API 快照找不到對應 main 的 PushEvent", res["reasons"])
    def test_A5_needs_gharchive_and_same_time(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)])
        s = json.loads(_rt(p).splitlines()[0])["sha256"]
        R.anchor(s, iso(T(D, 8, 40)), D, drop=("gharchive",))
        R.anchor(s, iso(T(D, 8, 40)), D, arc_time=iso(T(D, 8, 39)))
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertEqual(res["reasons"].get("錨點不採用：錨點證據快照缺或 sha256 對不上"), 1)
        self.assertEqual(res["reasons"].get("錨點不採用：Events API 與 GH Archive 的 created_at 缺或不一致"), 1)
    def test_A6_writer_cannot_verify(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)])
        s = json.loads(_rt(p).splitlines()[0])["sha256"]
        R.anchor(s, iso(T(D, 8, 40)), D, verified_by="股票機器人"); R.anchor(s, iso(T(D, 8, 40)), D, verified_by=None)
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertEqual(res["reasons"].get("錨點不採用：錨點沒有 verified_by 或由寫帳方自核"), 2)
    def test_A7_filename_in_public_line_rejected(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)])
        s = json.loads(_rt(p).splitlines()[0])["sha256"]
        R.anchor(s, iso(T(D, 8, 40)), D, line=f"2026-10-27\t{s}\tshadow-calls.jsonl")
        res = R.score([p], D, D, ["--rules", "P1"])[0]
        self.assertEqual(res["rules"]["P1"]["days"][D][0], "missing")
        self.assertIn("錨點不採用：anchors 檔有不合白名單格式的行", res["reasons"])

class TestV31Crosscheck(Base):
    """交叉核對（查證者對 v3 的交叉核對意見，紀錄未公開）：夜盤結束時刻、只休夜盤。"""
    def setUp(self):
        super().setUp()
        import recompute as RC
        self.RC = RC
    def test_night_end_is_start_day_plus_one_0500(self):
        # 10/27 標示的盤後列＝10/22 15:00 開始；10/23 下載時已收盤 → 不可判缺（舊規則 bound=10/27 05:00 > 取得時間 → 誤判）
        prior = (NIGHT_HDR + nrow("20261022", 46272, -741, "一般", 46234)).encode()
        b = (NIGHT_HDR + nrow("20261027", 46100, -134, "盤後")).encode()
        got = T("20261023", 12, 0)
        ce, basis = FA.coverage_end("DG11319", b, got, [prior])
        self.assertEqual(ce, T("20261023", 5, 0), basis)
        self.assertEqual(self.RC.own_coverage("DG11319", b, got, [prior]), (T("20261023", 5, 0), False))
    def test_night_end_ordinary_day(self):
        # 平常日：標示 10/22 的盤後列，參考價＝10/21 結算 → 10/22 05:00（兩支一致）
        prior = (NIGHT_HDR + nrow("20261021", 46900, 10, "一般", 47013)).encode()
        b = (NIGHT_HDR + nrow("20261022", 47482, 469, "盤後")).encode()
        got = T("20261022", 8, 30)
        self.assertEqual(FA.coverage_end("DG11319", b, got, [prior])[0], T("20261022", 5, 0))
        self.assertEqual(self.RC.own_coverage("DG11319", b, got, [prior])[0], T("20261022", 5, 0))
    def test_night_end_unprovable_start_stays_conservative(self):
        # 開始日證不出來 → 用標示日 05:00 上界；10/23 下載早於 10/27 05:00 → 兩支都判缺
        b = (NIGHT_HDR + nrow("20261027", 46100, -134, "盤後")).encode()
        self.assertIsNone(FA.coverage_end("DG11319", b, T("20261023", 12, 0))[0])
        self.assertEqual(self.RC.own_coverage("DG11319", b, T("20261023", 12, 0), [])[1], True)
    def _closure(self, base, **kw):
        return json.dumps(dict(base, **kw), ensure_ascii=False) + "\n"
    def test_night_only_closure_before_1400(self):
        R = self.R
        rel, h = R.snap("evidence", "night-1022.html", b"notice")
        base = {"url": "https://www.taifex.com.tw/cht/x", "snapshot_path": rel, "snapshot_sha256": h}
        row = dict(base, date="20261023", announced_date="20261022", session="night", announced_at_tpe="2026-10-22T11:00:00+08:00",
                   server_date_http="Thu, 22 Oct 2026 03:00:00 GMT")   # v3.1.2b 裁定 4：date＝目標交易日，announced_date＝宣布日
        clo = R.write("closures.jsonl", self._closure(row).encode())
        R.anchor(sha(b"live-start"), iso(T("20261022", 8, 0)), "20261022", ledger="L1")   # v3.1.2b-r1：即時預測起始日由錨點推（10/22）
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        res = R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))] + R.verify([row]))[0]
        self.assertIn("20261022", res["trading_days"])                       # 日盤照開，留在分母
        self.assertEqual(res["night_closed_next_day"], {"20261023": "20261022"})   # 次一交易日沒有夜盤
    def test_night_only_closure_after_1400_not_taken(self):
        R = self.R
        rel, h = R.snap("evidence", "night-1022.html", b"notice")
        base = {"url": "https://www.taifex.com.tw/cht/x", "snapshot_path": rel, "snapshot_sha256": h}
        row = dict(base, date="20261023", announced_date="20261022", session="night", announced_at_tpe="2026-10-22T14:30:00+08:00",
                   server_date_http="Thu, 22 Oct 2026 07:00:00 GMT")
        clo = R.write("closures.jsonl", self._closure(row).encode())
        R.anchor(sha(b"live-start"), iso(T("20261022", 8, 0)), "20261022", ledger="L1")   # v3.1.2b-r1：起始日 10/22 ≤ 目標日 10/23
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        res = R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))] + R.verify([row]))[0]
        self.assertIn("20261022", res["trading_days"])
        self.assertEqual(res["night_closed_next_day"], {})
        self.assertIn("只休夜盤行沒有 14:00 前的宣布時間（不採計，夜盤照常）", res["reasons"])
    def test_night_only_historical_row_needs_only_announced_date(self):
        # v3.1.2b 裁定 4（r1：起始日不寫死，由錨點推）：目標日早於即時預測起始日的歷史回測列不套 14:00 宣布規則，只要 announced_date；沒有 announced_date 不採計
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        row = {"url": "https://news.example.org/x", "date": "20261022", "announced_date": "20261021", "session": "night", "level": "新聞"}
        clo = R.write("closures.jsonl", self._closure(row).encode())
        res = R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))] + R.verify([row]))[0]
        self.assertIn("20261022", res["trading_days"]); self.assertEqual(res["night_closed_next_day"], {"20261022": "20261021"})
        row2 = {k: v for k, v in row.items() if k != "announced_date"}
        clo = R.write("closures.jsonl", self._closure(row2).encode())
        res = R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))] + R.verify([row2]))[0]
        self.assertEqual(res["night_closed_next_day"], {})
        self.assertEqual(res["reasons"]["只休夜盤行沒有合格的 announced_date（不採計，夜盤照常）"], 1)

class TestV311PrevSnapshot(unittest.TestCase):
    """v3.1.1 第 5 項：抓 DG11319 盤後列前，前一交易日快照要在手；缺的時候明確報錯，不准默默判缺。"""
    def setUp(self):
        self.R = Root(); self.snap = os.path.join(self.R.root, "snapshots")
    def tearDown(self): self.R.cleanup()
    def put(self, day, body):
        return self.R.write(f"snapshots/{day}/pre-DG11319-{day}T083000.csv", body.encode())
    def cli(self, *args):
        return run_cli(os.path.join(HERE, "fetch_anchor.py"), list(args), adopt=False)
    def test_unproven_listed(self):
        b = (NIGHT_HDR + nrow("20261027", 46100, -134, "盤後")).encode()
        self.assertEqual(FA.night_unproven(b), ["20261027"])
        prior = (NIGHT_HDR + nrow("20261022", 46272, -741, "一般", 46234)).encode()
        self.assertEqual(FA.night_unproven(b, [prior]), [])
    def test_check_prev_ordinary_ok(self):
        self.put("20261021", NIGHT_HDR + nrow("20261021", 46900, 10, "一般", 47013))
        code, msg = FA.check_prev("20261022", self.R.cal, self.R.cal_sha, [self.snap])
        self.assertEqual(code, 0, msg); self.assertIn("20261021", msg)
    def test_check_prev_missing_is_loud(self):
        self.put("20261020", NIGHT_HDR + nrow("20261020", 46900, 10, "一般", 47013))   # 只有更早的，前一交易日 10/21 缺
        r = self.cli("check-prev", "--day", "20261022", "--calendar", self.R.cal, "--calendar-sha256", self.R.cal_sha, "--ref-dir", self.snap, "--root", self.R.root)   # v3.1.2：用暫存 root 的空清單，不靠正式清單
        self.assertEqual(r.returncode, FA.PREV_EXIT); self.assertIn("缺前一交易日 20261021 的 DG11319 快照", r.stderr)
    def test_check_prev_crosses_closure(self):
        # 夾具休市表：10/26 補假（與官方 DG11761 相符）；10/23 全日臨時休市由 --closures 給 → 10/27 的前一交易日是 10/22
        self.put("20261022", NIGHT_HDR + nrow("20261022", 46272, -741, "一般", 46234))
        clo = self.R.write("closures.jsonl", '{"date": "20261023", "session": "all", "level": "官方", "status": "pending_verify"}\n'.encode())
        r = self.cli("check-prev", "--day", "20261027", "--calendar", self.R.cal, "--calendar-sha256", self.R.cal_sha, "--ref-dir", self.snap,
                     "--closures", clo, "--closures-sha256", sha(_rb(clo)))
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("20261022", r.stdout)
        r2 = self.cli("check-prev", "--day", "20261027", "--calendar", self.R.cal, "--calendar-sha256", self.R.cal_sha, "--ref-dir", self.snap,
                      "--root", self.R.root)   # v3.1.2：預設清單（夾具根目錄是空清單）
        self.assertEqual(r2.returncode, FA.PREV_EXIT); self.assertIn("缺前一交易日 20261023", r2.stderr)
    def test_check_prev_holiday_day_is_loud(self):
        # 當天本身休市（10/26 官方補假）→ 明講不是交易日，不默默往前找
        r = self.cli("check-prev", "--day", "20261026", "--calendar", self.R.cal, "--calendar-sha256", self.R.cal_sha, "--ref-dir", self.snap, "--root", self.R.root)   # v3.1.2：用暫存 root 的空清單，不靠正式清單
        self.assertEqual(r.returncode, FA.PREV_EXIT); self.assertIn("20261026 不是交易日", r.stderr)
    def test_check_prev_bad_calendar(self):
        code, msg = FA.check_prev("20261022", self.R.cal, "0" * 64, [self.snap])
        self.assertEqual(code, FA.PREV_EXIT); self.assertIn("sha256", msg)
        code, msg = FA.check_prev("20270104", self.R.cal, self.R.cal_sha, [self.snap])
        self.assertEqual(code, FA.PREV_EXIT); self.assertIn("待官方表", msg)
    def test_fetch_dg11319_requires_ref_dir(self):
        r = self.cli("fetch", "--name", "x", "--dataset", "DG11319", "--url", "https://www.taifex.com.tw/x", "--dir", self.snap)
        self.assertNotEqual(r.returncode, 0); self.assertIn("拒抓：DG11319 要先取前一交易日快照", r.stderr)
    def test_snapshot_without_prev_says_so(self):
        body = (NIGHT_HDR + nrow("20261027", 46100, -134, "盤後")).encode()
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/csv"); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        try:
            meta, _ = FA.snapshot_one(f"http://127.0.0.1:{srv.server_port}/x", "DG11319", self.snap, "pre", "DG11319", None, (), self.R.root)
            self.assertEqual(meta["night_start_unproven"], ["20261027"])
            self.assertIn("前一交易日快照缺或對不上", meta["coverage_basis"])
            prior = self.put("20261022", NIGHT_HDR + nrow("20261022", 46272, -741, "一般", 46234))
            meta2, _ = FA.snapshot_one(f"http://127.0.0.1:{srv.server_port}/x", "DG11319", self.snap, "pre", "DG11319", None, (), self.R.root, [prior])
            self.assertEqual(meta2["night_start_unproven"], [])
            self.assertNotIn("前一交易日快照缺", meta2["coverage_basis"])
        finally:
            srv.shutdown(); srv.server_close()


class TestV312(Base):
    """v3.1.2：A／C4／C7／N4——臨時休市列由查證者核證填 verified_by；寫帳方名字正規化後前綴／包含比對；匯入查證者清單；--closures 預設開啟。"""
    REAL = os.environ.get("WATCH_DESK_ROOT", "")   # 公開版：正式清單與官方休市表快照不放進 repo，要跑下面兩個案例請自己指定
    CAL_REAL = os.path.join(REAL, "calendar-fetch/v311-check/pre-DG11761-20261023T122321.csv")
    def _row(self, **kw):
        rel, h = self.R.snap("evidence", "typhoon-1023.html", b"notice")
        base = {"date": "20261023", "session": "all", "level": "官方", "url": "https://www.taifex.com.tw/cht/11/announcement",
                "snapshot_path": rel, "snapshot_sha256": h, "server_date_http": "Fri, 23 Oct 2026 01:00:00 GMT",
                "status": "pending_verify", "verified_by": "", "approval_basis": "official_notice"}
        base.update(kw); return base
    def _score(self, rows, records=None, **vkw):
        """v3.1.2b：records 預設＝清單列本身（查證者外部核證檔有對應列）；給 [] ＝外部檔沒有任何列。"""
        R = self.R
        clo = R.write("clo.jsonl", "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows).encode())
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        va = R.verify(rows if records is None else records, **vkw)
        return R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))] + va)[0]
    def test_writer_name_variants_blocked(self):
        import score_prf as S, recompute as RC
        for v in ("股票機器人（依官方公告）", "股票機器人(依官方公告)", "Stock-Bot 2", "ＳＴＯＣＫＢＯＴ", "anchor bot", "stockbot助手",
                  "股票 機器人", "我是股票機器人", "（查證者）股票機器人", "stock_bot(x", "", "  ", None):
            self.assertTrue(S.is_writer(v), v); self.assertTrue(RC.not_verifier(v), v)
        for v in ("查證者", "查證者（核證）", "審查者"):
            self.assertFalse(S.is_writer(v), v); self.assertFalse(RC.not_verifier(v), v)
    def test_verified_closure_deducted(self):
        self.assertEqual(self._score([self._row()])["trading_days"], ["20261022", D])
    def test_writer_plus_suffix_not_deducted(self):
        res = self._score([self._row(verified_by="股票機器人（依官方公告）")], records=[self._row()])   # v3.1.2b：手改列 → 列 sha 對不上
        self.assertEqual(res["trading_days"], ["20261022", "20261023", D])
        self.assertEqual(res["reasons"]["臨時休市列沒有對得上的核證列（日期＋列 sha，待核）"], 1)
    def test_pending_verify_not_deducted(self):
        res = self._score([self._row(status="verified", verified_by="查證者")], records=[])   # 手填 verified＋查證者、外部檔沒有對應列 → 待核
        self.assertIn("20261023", res["trading_days"])
        self.assertEqual(res["reasons"]["臨時休市列待查證者核證（pending_verify，不扣）"], 1)
    def test_official_notice_needs_all_three(self):
        for kw, miss in (({"url": "https://example.com/x"}, "官方網址"), ({"snapshot_sha256": "0" * 64}, "快照 sha"),
                         ({"server_date_http": "Thu, 22 Oct 2026 01:00:00 GMT"}, "當日伺服器 Date"), ({"server_date_http": None}, "當日伺服器 Date")):
            res = self._score([self._row(**kw)])
            self.assertIn("20261023", res["trading_days"], kw)
            self.assertEqual(res["reasons"]["approval_basis=official_notice 缺" + miss + "（拒絕）"], 1, kw)
    def test_unverified_level_not_deducted(self):
        res = self._score([self._row(level="unverified")])
        self.assertIn("20261023", res["trading_days"])
    def test_default_closures_on_and_sha_checked(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        R.write("closures/adhoc-closures.jsonl", (json.dumps(self._row(), ensure_ascii=False) + "\n").encode())
        args = ["--root", R.root, "--calendar", R.cal, "--calendar-sha256", R.cal_sha, "--since", "20261022", "--until", D, "--rules", "P1", "--json", p]
        r = run_cli(SCORE, args)   # 清單改了、sha 檔沒改 → 兩支都拒算
        self.assertNotEqual(r.returncode, 0); self.assertIn("adhoc-closures", r.stderr)
        self.assertNotEqual(run_cli(RECOMP, args[:-2] + [p]).returncode, 0)
        R.write("closures/adhoc-closures.sha256", (sha(_rb(os.path.join(R.root, "closures/adhoc-closures.jsonl"))) + "  adhoc-closures.jsonl\n").encode())
        res = R.score([p], "20261022", D, ["--rules", "P1"] + R.verify([self._row()]))[0]   # 沒帶 --closures 也套用
        self.assertEqual(res["trading_days"], ["20261022", D])
        os.remove(os.path.join(R.root, "closures/adhoc-closures.sha256"))
        self.assertNotEqual(run_cli(SCORE, args).returncode, 0)
    def test_real_list_imported(self):
        if not (os.environ.get("WATCH_DESK_ROOT") and os.environ.get("ADHOC_CSV")): self.skipTest("公開版：需要未公開的正式清單與來源 CSV")
        import csv as _csv
        p, s = FA.default_closures(self.REAL)
        self.assertEqual(sha(_rb(p)), s)
        rows = [json.loads(l) for l in _rb(p).decode().splitlines() if l.strip()]
        src = os.environ.get("ADHOC_CSV", "")
        self.assertTrue(sha(_rb(src)).startswith("4b0f3d15"))
        with open(src, encoding="utf-8-sig", newline="") as f: csv_rows = list(_csv.DictReader(f))
        want = {r["日期"].replace("-", ""): (i, r["分級"]) for i, r in enumerate(csv_rows, 2) if r["分級"] in ("官方", "新聞")}
        self.assertEqual(len(want), 41)
        self.assertEqual({x["date"]: (x["source_csv"]["line"], x["level"]) for x in rows}, want)
        self.assertEqual(sorted(x["level"] for x in rows).count("新聞"), 34); self.assertEqual([x["level"] for x in rows].count("官方"), 7)
        for d in ("19990104", "19990701", "19991229"): self.assertNotIn(d, {x["date"] for x in rows})   # unverified 不收
        for x in rows:
            self.assertEqual((x["status"], x["verified_by"]), ("pending_verify", ""), x["date"])   # 清單本身不手填；v3.1.2b 起 verified 由程式算
        # v3.1.2b 裁定 3：只有查證者核證檔（核可者的 sha 檔指定、sha 相符）列出且（日期, 列 sha）對得上的列算 verified，其餘維持待核
        import score_prf as S
        if not os.path.isfile(S.APPROVALS_SHA_FILE): self.skipTest("沒有核可者的核證 sha 檔")
        recs, note = S.load_verify("/nonexistent-root-for-test")
        self.assertIsNone(note)
        got = {x["date"] for x in rows if S.closure_verified(x, recs, x["date"])[0] is not None}
        self.assertEqual(got, {d for d, _ in recs})
        self.assertEqual(len(got), 32)   # 查證者 closure-verifications-v1（sha 見內部 MANIFEST，未公開）：甲級 32 列，列 sha 全對得上；乙級 9 列不在檔內＝待核
        j = [x for x in rows if x["date"] == "20260710"]
        self.assertEqual(len(j), 1); self.assertEqual(j[0]["source_csv"]["line"], 44); self.assertEqual(j[0]["session"], "all")
        self.assertEqual(sha(_rb(j[0]["snapshot_path"])), "e5873b1ea8b10d185e0a7a89332cb59a7913ffc2268861146012aef11b79075e")
        k = [x for x in rows if x["date"] == "20261023"][0]
        self.assertEqual(k["approval_basis"], "official_notice"); self.assertNotIn("approved_by", k)
    def test_check_prev_20260713_gives_20260709(self):
        if not os.environ.get("WATCH_DESK_ROOT"): self.skipTest("公開版：需要未公開的正式清單")
        cal_sha = sha(_rb(self.CAL_REAL))
        p, s = FA.default_closures(self.REAL)
        self.assertEqual(FA.prev_trading_day(self.CAL_REAL, cal_sha, "20260713", FA.closure_days(p, s)), "20260709")
        self.assertEqual(FA.prev_trading_day(self.CAL_REAL, cal_sha, "20260713", ()), "20260710")   # 沒套清單會錯成 07-10
        snap = os.path.join(self.R.root, "snapshots")
        self.R.write("snapshots/20260709/pre-DG11319-20260709T083000.csv", (NIGHT_HDR + nrow("20260709", 46000, 10, "一般", 46010)).encode())
        r = run_cli(os.path.join(HERE, "fetch_anchor.py"), ["check-prev", "--day", "20260713", "--calendar", self.CAL_REAL,
                    "--calendar-sha256", cal_sha, "--ref-dir", snap, "--root", self.REAL], adopt=False)   # 沒帶 --closures：預設套用 --root 底下的清單
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("前一交易日 20260709", r.stdout)
    def test_check_prev_skips_unverified_level(self):
        clo = self.R.write("c.jsonl", '{"date": "20261023", "session": "all", "level": "unverified"}\n'.encode())
        self.assertEqual(FA.closure_days(clo, sha(_rb(clo))), set())
        with self.assertRaises(ValueError): FA.closure_days(clo, "0" * 64)

class TestV312b(Base):
    """v3.1.2b B1（審查意見改法 1～3＋5；改法 4 不採；協調助理轉達（非本人核可）的裁定 1–3；審查檔未公開）：
    verified 只由查證者核證檔（日期＋列 sha）算；核證檔的 sha 只從核可者寫的 sha 檔讀。"""
    _row = TestV312._row; _score = TestV312._score   # 共用夾具，不繼承 TestV312 的測試（不重跑、不湊數）
    WRITER_VARIANTS = ("股票機器人", "股票機器人（依官方公告）", "Stock-Bot 2", "", "股票\u200b機器人", 
                       "股票\u00ad機器人", "股票\u2060機器人", "股票\u200c機器人", "stoc\u03babot", "\u0455tockbot", "查證者", "查證者（核證）")
    def test_review_counterexamples_row_edit_not_deducted(self):
        # 審稿反例：寫帳方只改清單列 status＝verified、verified_by＝各種寫法（含直接寫「查證者」）→ 列 sha 變了，核證檔對不上 → 不扣
        for v in self.WRITER_VARIANTS:
            res = self._score([self._row(status="verified", verified_by=v)], records=[self._row()])
            self.assertIn("20261023", res["trading_days"], repr(v))
            self.assertEqual(res["reasons"]["臨時休市列沒有對得上的核證列（日期＋列 sha，待核）"], 1, repr(v))
    def test_record_verified_by_must_be_tanzi(self):
        for v in ("股票\u200b機器人", "股票機器人", "查證者（核證）", ""):
            res = self._score([self._row()], vb=v)
            self.assertIn("20261023", res["trading_days"], repr(v)); self.assertEqual(res["reasons"]["核證列 verified_by 不是查證者（待核）"], 1)
    def test_forged_whole_row_not_deducted(self):
        # 查證者補充意見（未公開）：寫帳方整列偽造（平常交易日、假官方網址、自製快照、自填 Date、verified_by＝查證者）→ 核證檔沒有 → 不扣
        rel, h = self.R.snap("evidence", "fake.html", b"fake")
        row = {"date": "20261022", "session": "all", "level": "官方", "url": "https://www.twse.com.tw/fake", "snapshot_path": rel,
               "snapshot_sha256": h, "server_date_http": "Thu, 22 Oct 2026 01:00:00 GMT", "status": "verified", "verified_by": "查證者",
               "approval_basis": "official_notice"}
        res = self._score([row], records=[])
        self.assertEqual(res["trading_days"], ["20261022", "20261023", D])
    def test_writer_made_own_verify_file_and_sha_not_counted(self):
        # 錯題本 v3.1.2b：寫帳方自己做核證檔、自己算 sha、放在 root 裡（或自己的 approvals/）→ 程式只讀核可者的 sha 檔 → 待核
        R = self.R
        row = self._row()
        own = R.write("evidence/closure-verifications.jsonl", json.dumps({"date": "20261023", "row_sha256": sha(canon(row).encode()),
                      "tier": "甲", "verified_by": "查證者", "source_date": "x", "url": ["https://x"]}, ensure_ascii=False).encode())
        R.write("approvals/closure-verifications.sha256", f"{sha(_rb(own))}  {own}\n".encode())
        clo = R.write("clo.jsonl", (json.dumps(row, ensure_ascii=False) + "\n").encode())
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        res = R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))])[0]
        self.assertIn("20261023", res["trading_days"]); self.assertEqual(res["reasons"]["核可者的核證 sha 檔不存在（全部待核）"], 1)
        R.verify([row], path=own)   # 就算核可者的 sha 檔指到 root 裡的檔也不收
        res = R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))])[0]
        self.assertIn("20261023", res["trading_days"]); self.assertEqual(res["reasons"]["核證檔在 watch-desk 根目錄裡（寫帳方範圍，全部待核）"], 1)
        for script in (SCORE, RECOMP):   # 命令列沒有任何參數能指定核證檔或 sha
            r = run_cli(script, ["--help"]); self.assertNotIn("verify", r.stdout); self.assertNotIn("approvals", r.stdout)
    def test_external_record_present_verified_by_code(self):
        res = self._score([self._row()])   # 清單列 pending_verify、verified_by 空白；核證檔有對應甲級列 → 程式算成 verified
        self.assertEqual(res["trading_days"], ["20261022", D])
        self.assertEqual(res["reasons"]["臨時休市列依查證者核證檔算成 verified"], 1)
    def test_tier_b_stays_pending(self):
        res = self._score([self._row()], tier="乙")
        self.assertIn("20261023", res["trading_days"]); self.assertEqual(res["reasons"]["核證列不是甲級（待核）"], 1)
    def test_sha_mismatch_or_missing_file_all_pending(self):
        for kw, note in (({"sha_override": "0" * 64}, "核證檔 sha256 與核可者的 sha 檔不符（全部待核）"),):
            res = self._score([self._row()], **kw)
            self.assertIn("20261023", res["trading_days"]); self.assertEqual(res["reasons"][note], 1)
        R = self.R
        R.verify([self._row()]); os.remove(os.path.join(R.vdir, "closure-verifications.jsonl"))
        clo = R.write("clo.jsonl", (json.dumps(self._row(), ensure_ascii=False) + "\n").encode())
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH)
        res = R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))])[0]
        self.assertIn("20261023", res["trading_days"]); self.assertEqual(res["reasons"]["核證檔不存在（全部待核）"], 1)
    def test_news_grade_needs_url_and_verified_date(self):
        row = {"date": "20261023", "session": "all", "level": "新聞", "source_urls": ["https://news.example.org/a"], "status": "pending_verify", "verified_by": ""}
        self.assertEqual(self._score([row])["trading_days"], ["20261022", D])   # 裁定 2：新聞級不要快照、不要當日 Date
        res = self._score([row], src_date="")
        self.assertIn("20261023", res["trading_days"]); self.assertEqual(res["reasons"]["新聞級缺核過的日期（不扣）"], 1)
        res = self._score([row], extra={"url": ["http://news.example.org/a"]})
        self.assertIn("20261023", res["trading_days"]); self.assertEqual(res["reasons"]["新聞級缺網址（不扣）"], 1)
    def test_official_level_without_same_day_date(self):
        # 裁定 2：官方級要當日來源；拿不到當日伺服器 Date 時，查證者瀏覽器核對的公告頁日期＋截圖 sha 可替代（例：07-10 idx=17137 公告日 2026/07/09）
        R = self.R
        shot = os.path.join(R.vdir, "shot.webp")
        with open(shot, "wb") as fh: fh.write(b"screenshot")
        bc = {"url": "https://www.taifex.com.tw/cht/11/newsDetail?idx=1", "notice_date": "2026/10/22", "screenshot_path": "shot.webp",
              "screenshot_sha256": sha(b"screenshot")}
        row = self._row(server_date_http="Sat, 24 Oct 2026 01:00:00 GMT", approval_basis="")
        res = self._score([row])
        self.assertIn("20261023", res["trading_days"]); self.assertEqual(res["reasons"]["官方級缺當日伺服器 Date（不扣）"], 1)
        self.assertEqual(self._score([row], extra={"browser_check": bc})["trading_days"], ["20261022", D])
        res = self._score([row], extra={"browser_check": dict(bc, screenshot_sha256="0" * 64)})
        self.assertIn("20261023", res["trading_days"])
        res = self._score([row], extra={"browser_check": dict(bc, notice_date="2026/10/24")})
        self.assertIn("20261023", res["trading_days"])

class TestV312bR1(Base):
    """v3.1.2b-r1 裁定 4（轉達，非本人核可）：即時預測起始日不寫死；＝第一個有 anchors/ 紀錄、GitHub 伺服器端 push 時間早於當日 08:45 的交易日。
    沒有這種錨點、或 GitHub 時間缺 → 尚未開始，只休夜盤列一律當歷史回測列（只要 announced_date）。全部離線夾具，不連網。"""
    NOTE = "即時預測起始日：{}（只認 GitHub 伺服器端 push 時間早於當日 08:45 的 anchors 紀錄）"
    def _late_row(self):
        R = self.R
        rel, h = R.snap("evidence", "night-1022.html", b"notice")
        return {"url": "https://www.taifex.com.tw/cht/x", "snapshot_path": rel, "snapshot_sha256": h, "date": "20261023",
                "announced_date": "20261022", "session": "night", "announced_at_tpe": "2026-10-22T14:30:00+08:00",
                "server_date_http": "Thu, 22 Oct 2026 07:00:00 GMT"}
    def _run(self, row, anchor_ledger=True):
        R = self.R
        clo = R.write("closures.jsonl", json.dumps(row, ensure_ascii=False).encode() + b"\n")
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 5)], anchor_after=ANCH if anchor_ledger else None)
        return R.score([p], "20261022", D, ["--rules", "P1", "--closures", clo, "--closures-sha256", sha(_rb(clo))] + R.verify([row]))[0]
    def test_no_anchor_means_not_started(self):
        res = self._run(self._late_row(), anchor_ledger=False)
        self.assertEqual(res["reasons"][self.NOTE.format("尚未開始")], 1)
        self.assertEqual(res["night_closed_next_day"], {"20261023": "20261022"})   # 尚未開始＝歷史回測列，14:30 宣布也採計
    def test_hardcoded_date_not_in_code(self):
        for f in ("score_prf.py", "recompute.py"):
            with open(os.path.join(HERE, f), encoding="utf-8") as fh: src = fh.read()
            self.assertNotRegex(src, r"LIVE_FROM\s*=")
            self.assertNotIn('"20261023"', src)
    def test_locally_claimed_time_not_live(self):
        # 行內 external_time 寫得早、本機宣稱的日期也不算；GitHub 伺服器端時間 10/22 09:00（晚於 08:45）→ 10/22 不算起始日
        R = self.R
        a = R.anchor(sha(b"claim"), iso(T("20261022", 9, 0)), "20261022", ledger="L1", write=False)
        a["external_time"] = "2026-10-21T00:00:00Z"; a["live_from"] = "20261022"
        with open(os.path.join(R.root, "anchors.jsonl"), "a", encoding="utf-8") as fh: fh.write(json.dumps(a, ensure_ascii=False) + "\n")
        res = self._run(self._late_row(), anchor_ledger=False)
        self.assertEqual(res["reasons"][self.NOTE.format("尚未開始")], 1)
    def test_missing_github_time_not_live(self):
        R = self.R
        R.anchor(sha(b"nogh"), iso(T("20261022", 8, 0)), "20261022", ledger="L1", drop=("gharchive",))   # GH Archive 缺＝伺服器時間核不了
        R.anchor(sha(b"diff"), iso(T("20261022", 8, 0)), "20261022", ledger="L1", arc_time=iso(T("20261022", 9, 0)))   # 兩邊時間不一致
        res = self._run(self._late_row(), anchor_ledger=False)
        self.assertEqual(res["reasons"][self.NOTE.format("尚未開始")], 1)
        self.assertEqual(res["night_closed_next_day"], {"20261023": "20261022"})
    def test_weekend_anchor_not_live(self):
        R = self.R
        R.anchor(sha(b"sat"), iso(T("20261017", 8, 0)), "20261017", ledger="L1")   # 週六
        res = self._run(self._late_row(), anchor_ledger=False)
        self.assertEqual(res["reasons"][self.NOTE.format("尚未開始")], 1)
    def test_github_anchor_before_0845_starts_live(self):
        R = self.R
        R.anchor(sha(b"live"), iso(T("20261022", 8, 44)), "20261022", ledger="L1")
        res = self._run(self._late_row(), anchor_ledger=False)
        self.assertEqual(res["reasons"][self.NOTE.format("20261022")], 1)
        self.assertEqual(res["night_closed_next_day"], {})   # 起始日 10/22 ≤ 目標日 10/23 → 14:30 宣布不採計
        self.assertIn("只休夜盤行沒有 14:00 前的宣布時間（不採計，夜盤照常）", res["reasons"])

class TestV312bR5(Base):
    """v3.1.2b-r5（依審查意見，審查檔未公開）：
    --outcomes 必填、檔案必須存在；沒有預設讀 root/shadow-calls.jsonl。"""
    def _args(self, p, extra=()):
        R = self.R
        return ["--root", R.root, "--calendar", R.cal, "--calendar-sha256", R.cal_sha, "--since", D, "--until", D, "--rules", "P1"] + list(extra) + [p]
    def test_outcomes_required_even_if_shadow_file_exists(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)], anchor_after=ANCH)
        self.assertTrue(os.path.exists(os.path.join(R.root, "shadow-calls.jsonl")))
        for script in (SCORE, RECOMP):
            r = run_cli(script, self._args(p), inject_outcomes=False)
            self.assertNotEqual(r.returncode, 0, script); self.assertIn("--outcomes 必填", r.stderr, script)
    def test_outcomes_file_must_exist(self):
        R = self.R
        p = R.ledger("shadow-calls.jsonl", [R.call(D), R.outcome(D, 10)], anchor_after=ANCH)
        for script in (SCORE, RECOMP):
            r = run_cli(script, self._args(p, ["--outcomes", os.path.join(R.root, "outcomes-taiex.jsonl")]), inject_outcomes=False)
            self.assertNotEqual(r.returncode, 0, script); self.assertIn("--outcomes 檔不存在", r.stderr, script)
    def test_no_shadow_default_in_source(self):
        for f in (SCORE, RECOMP):
            src = _rt(f)
            self.assertNotIn('os.path.join(root, "shadow-calls.jsonl")', src, f)
            self.assertNotRegex(src, r"outcomes\s+or\s+", f)
    def test_separate_outcome_file_scores_practice_ledger(self):
        R = self.R
        outp = R.ledger("outcomes-taiex.jsonl", [R.outcome(D, -10)])
        prac = R.ledger("practice-calls.jsonl", [R.call(D, rule="正式", call="跌")], anchor_after=ANCH)
        self.assertFalse(os.path.exists(os.path.join(R.root, "shadow-calls.jsonl")))
        res = R.score([prac], D, D, ["--rules", "正式", "--outcomes", outp])[0]   # Root.score 內已斷言 recompute 一致
        self.assertEqual(res["rules"]["正式"]["days"][D], ["跌", "跌"]); self.assertEqual(res["rules"]["正式"]["hits"], 1)

class TestV312bR6(Base):
    """v3.1.2b-r6（依審查意見，審查檔未公開）：
    --outcomes 不准是影子帳本或混合帳、不准 0 行、要 --outcomes-sha256；有未配對日時同期基準無法計算，不當 0.0%。"""
    def _args(self, p, outp, osha, until=D):
        R = self.R
        a = ["--root", R.root, "--calendar", R.cal, "--calendar-sha256", R.cal_sha, "--since", D, "--until", until, "--rules", "P1", "--outcomes", outp]
        return a + (["--outcomes-sha256", osha] if osha else []) + [p]
    def test_shadow_or_mixed_outcomes_refused(self):
        R = self.R
        prac = R.ledger("practice-calls.jsonl", [R.call(D, rule="P1")], anchor_after=ANCH)
        mixed = R.ledger("shadow-calls.jsonl", [R.call(D, rule="P1"), R.outcome(D, 10)])          # 審查反例：影子帳混 outcome
        calls_only = R.ledger("shadow-only.jsonl", [R.call(D, rule="P1")])                         # 審查反例：只有影子預測行
        named = R.ledger("shadow-outcomes.jsonl", [R.outcome(D, 10)])                              # 只有 outcome 行，但檔名是 shadow
        renamed = R.ledger("outcomes-x.jsonl", [R.call(D, rule="P1"), R.outcome(D, 10)])           # 改名也擋：內容混了預測行
        ruled = R.ledger("outcomes-r.jsonl", [dict(R.outcome(D, 10), rule_id="P1")])               # outcome 行帶規則欄
        empty = R.write("outcomes-empty.jsonl", b"")
        good = R.ledger("outcomes-taiex.jsonl", [R.outcome(D, 10)])
        cases = [(mixed, "shadow 開頭"), (calls_only, "shadow 開頭"), (named, "shadow 開頭"), (renamed, "不是"),
                 (ruled, "帶規則欄"), (empty, "沒有任何 outcome 行")]
        for script in (SCORE, RECOMP):
            for f, msg in cases:
                r = run_cli(script, self._args(prac, f, sha(_rb(f))), inject_outcomes=False)
                self.assertNotEqual(r.returncode, 0, (script, f)); self.assertIn(msg, r.stderr, (script, f))
            r = run_cli(script, self._args(prac, good, None), inject_outcomes=False)
            self.assertNotEqual(r.returncode, 0, script); self.assertIn("--outcomes-sha256 必填", r.stderr, script)
            r = run_cli(script, self._args(prac, good, "0" * 64), inject_outcomes=False)
            self.assertNotEqual(r.returncode, 0, script); self.assertIn("不符", r.stderr, script)
            r = run_cli(script, self._args(prac, good, sha(_rb(good))), inject_outcomes=False)
            self.assertEqual(r.returncode, 0, (script, r.stderr))
    def test_unpaired_day_baseline_not_zero(self):
        R = self.R
        c = R.call(D, call="漲"); c.pop("rule")
        prac = R.ledger("practice-calls.jsonl", [c])                         # 沒有規則欄、沒有錨點＝尚未開始＝試跑
        outp = R.ledger("outcomes-taiex.jsonl", [R.outcome(D, 10)])          # 只有 D 有實際方向，D2 未配對
        res = R.score([prac], D, D2, ["--outcomes", outp])[0]               # Root.score 內已斷言 recompute 一致
        self.assertEqual(list(res["rules"]), ["試跑"])
        r0 = res["rules"]["試跑"]
        self.assertIn(D2, res["unpaired_days"])
        self.assertIsNone(r0["always_up_rate"]); self.assertIsNone(r0["p_same_period"]); self.assertIsNone(r0["holm_p_same_period"])
        out = run_cli(SCORE, ["--root", R.root, "--calendar", R.cal, "--calendar-sha256", R.cal_sha, "--since", D, "--until", D2,
                              "--outcomes", outp, prac]).stdout
        self.assertIn("同期天天猜漲 無法計算（試跑基準）", out); self.assertIn("試跑檢定", out); self.assertIn("試跑｜n=2", out)
        self.assertNotIn("正式", out); self.assertNotIn("基準 0.0%", out)

if __name__ == "__main__":
    unittest.main()
