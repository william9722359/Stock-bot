"""stockbot-backtest 共用：常數（寫死）、載入、模型。紙上計算、非投資建議。方法見 METHOD.md。"""
import json, hashlib, csv, io, os, sys
import numpy as np
from pathlib import Path

VERSION = "v1.0.3b"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FM = str(Path(__file__).resolve().parents[1] / "data" / "finmind") + "/"
DEV_DIR = os.path.join(ROOT, "data/dev")
# 原檔（只有 make_dev_slice.py 讀）
ORIG = {"finmind-TaiwanStockPrice-TAIEX": (FM + "finmind-TaiwanStockPrice-TAIEX.json", "ee85a934aa8ba0d99ea978bcbacc2d1c05aa4337d49fad54b9f880acff3af6a8"),
        "finmind-TaiwanFuturesInstitutionalInvestors-TX": (FM + "finmind-TaiwanFuturesInstitutionalInvestors-TX.json", "ba5b116daba9a357917336695956232ca4bd6cd2fe6d2a83f00f1ef05e992467"),
        "finmind-TaiwanStockTotalInstitutionalInvestors": (FM + "finmind-TaiwanStockTotalInstitutionalInvestors.json", "847865abfbdcedd6f538adaafd6efcd404f2f8c6d0e2e0f527f1ae83c48e7f5e")}
# 開發期副本（sha 待 make_dev_slice 產出後寫死）
TAIEX = (os.path.join(DEV_DIR, "finmind-TaiwanStockPrice-TAIEX.json"), "ffb8d6d3d7da818d85c89196705a198c4d4aaa3f6d16bf5b6bacace6cfe1350d")
INST = (os.path.join(DEV_DIR, "finmind-TaiwanFuturesInstitutionalInvestors-TX.json"), "238be435804cfa86ce6782102f5c407c94c7acfc80f610a7fa1db8d761d7a593")
SPOT = (os.path.join(DEV_DIR, "finmind-TaiwanStockTotalInstitutionalInvestors.json"), "807e695b5619b93db9634de4ee6e8cad66398e8c30eee732cf86150be7886f67")
ETF = (os.path.join(ROOT, "data/finmind/finmind-TaiwanStockPrice-0050-20030101-20241231.json"), "7419fbe175e520566b68d3f99262943279a1e3377e4573d23d4d201219327344")
METHOD = (os.path.join(ROOT, "METHOD.md"), "941e832df562a6854d880490e990bd1cd734356495c8d663006f83843a0c15a9")
DEV_END = "2024-12-31"        # 寫死；超過即 rc≠0
SCORE_START = "2020-01-02"    # 寫死
HOLDOUT_START = "2025-01-01"  # 寫死；本版不讀、不跑
# ===== 視窗：唯一一處。不開 CLI、不讀環境變數，外部不能覆蓋。 =====
# 750 日訓練窗、20 日測試、每次往前 20 日：
TRAIN = 750   # 訓練窗：750 個交易日，滾動
TEST = 20     # 測試窗：20 個交易日
STEP = 20     # 步長：20 個交易日（每步重估、整窗前移）
PURGE = 1     # 防漏：訓練窗與測試窗之間空 1 個交易日
# =====================================================================================
C_L2, IRLS_MAX, IRLS_TOL = 1.0, 100, 1e-10
P5_VOL, P5_PCT, P5_TH, P5_MA = 20, 250, 80.0, 60
MODELS = ["M0", "M1", "M2", "M3", "M4", "M5"]
MAIN = ["M0", "M1", "M2", "M5"]; SUB = ["M3", "M4"]

class Stop(Exception): pass

def die(msg):
    sys.stderr.write("STOP: " + msg + "\n"); sys.exit(1)

def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()

def check(pair):
    s = sha(pair[0])
    if s != pair[1]: die(f"sha 不符 {pair[0]} {s}")
    return s

def guard(dates):
    bad = [d for d in dates if d > DEV_END]
    if bad: die("資料越界：有列 > DEV_END")      # 不印日期或數值

def _load(pair):
    check(pair)
    rows = json.load(open(pair[0]))["data"]
    guard([r["date"] for r in rows])              # 載入後、任何處理前就擋
    return rows

def load_taiex():
    out = []
    for r in _load(TAIEX):
        try: o, c = float(r["open"]), float(r["close"])
        except Exception: die("TAIEX 格式壞")
        if not (o > 0 and c > 0): die("TAIEX 值壞")
        out.append((r["date"], o, c))
    ds = [x[0] for x in out]
    if ds != sorted(ds) or len(set(ds)) != len(ds): die("TAIEX 日期未排序或重複")
    return ds, [x[1] for x in out], [x[2] for x in out]

def load_etf():
    m = {}
    for r in _load(ETF):
        if r["stock_id"] != "0050": die("0050 檔 stock_id 不符")
        if r["date"] in m: die("0050 同日多列")
        m[r["date"]] = (float(r["open"]), float(r["close"]))
    return m

def load_inst():
    oi, deal = {}, {}
    for r in _load(INST):
        if r["futures_id"] != "TX": die("futures_id 非 TX")
        if r["institutional_investors"] != "外資": continue
        d = r["date"]
        if d in oi: die("外資同日多列")
        oi[d] = int(r["long_open_interest_balance_volume"]) - int(r["short_open_interest_balance_volume"])
        deal[d] = int(r["long_deal_volume"]) - int(r["short_deal_volume"])
    return oi, deal

def load_spot():
    fi, fd = {}, {}
    for r in _load(SPOT):
        tgt = fi if r["name"] == "Foreign_Investor" else (fd if r["name"] == "Foreign_Dealer_Self" else None)
        if tgt is None: continue
        if r["date"] in tgt: die("現貨同日同類多列")
        tgt[r["date"]] = float(r["buy"]) - float(r["sell"])
    return fi   # 只用 Foreign_Investor，不含外資自營商

# ---------- 模型：所有函式只拿 [:i]（≤T，T=目標日前一交易日） ----------
def feats(c_past):
    if len(c_past) < 22: return None
    c = np.asarray(c_past[-22:], dtype=float); r = c[1:] / c[:-1] - 1.0
    return [r[-1], r[-2], r[-3], r[-4], r[-5], c[-1] / c[-21] - 1.0,
            float(np.std(r[-20:], ddof=1)), c[-1] / float(np.mean(c[-20:])) - 1.0]

def m1(c_past): return 1 if c_past[-1] >= c_past[-2] else 0

def m3(d_past, oi):
    t, t1 = d_past[-1], d_past[-2]
    if t not in oi or t1 not in oi: return None
    dl = oi[t] - oi[t1]
    return 1 if dl > 0 else (0 if dl < 0 else None)

def p4u(d_past, deal, spot):
    t = d_past[-1]
    if t not in deal or t not in spot: return None
    f, s = deal[t], spot[t]
    if f < 0 and s < 0: return 0
    return 1   # 同正 → 漲；方向不一致或任一為 0 → 漲（M4）

def p5u(c_past):
    if len(c_past) < P5_VOL + P5_PCT: return None    # 不足 270 筆 → missing（M5）
    c = np.asarray(c_past, dtype=float); lr = np.log(c[1:] / c[:-1])
    vols = np.array([np.std(lr[k - P5_VOL:k], ddof=1) for k in range(len(lr) - P5_PCT + 1, len(lr) + 1)])
    pct = 100.0 * np.sum(vols <= vols[-1]) / P5_PCT
    ma = float(np.mean(c[-P5_MA:]))
    return 0 if (pct >= P5_TH and c[-1] < ma) else 1

def lr_fit(X, y):
    # StandardScaler 等價：mu/sd 只用本輪訓練窗 X fit
    mu = X.mean(0); sd = X.std(0); sd[sd == 0] = 1.0
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd]); w = np.zeros(Z.shape[1])
    P = np.eye(Z.shape[1]) / C_L2; P[0, 0] = 0.0
    for _ in range(IRLS_MAX):
        p = 1 / (1 + np.exp(-(Z @ w)))
        step = np.linalg.solve(Z.T @ (Z * (p * (1 - p))[:, None]) + P, Z.T @ (p - y) + P @ w)
        w = w - step
        if np.max(np.abs(step)) < IRLS_TOL: break
    return w, mu, sd

def lr_prob(mdl, f):
    w, mu, sd = mdl
    return float(1 / (1 + np.exp(-(np.concatenate([[1.0], (np.asarray(f) - mu) / sd]) @ w))))

def first_full(dates, has):
    """某模型輸入序列湊滿 TRAIN 天的第一個評分日：第 TRAIN 個「有特徵」的日子之後再空 PURGE 天。"""
    ok = [i for i in range(len(dates)) if has(i)]
    k = ok[TRAIN - 1] + PURGE + 1
    return k if k < len(dates) else None

def run_models(dates, closes, oi, deal, spot):
    """walk-forward 750/20/20、purge 1：網格從 SCORE_START 起，每步用 t0−1−PURGE 以前最後 TRAIN 個特徵齊全列重訓 M2。
    規則模型（M0/M1/M3/M4/M5）不訓練，只在同一網格逐日出預測。回傳 y、pred、steps（含輪次編號）。"""
    guard(dates)
    n = len(dates)
    y = [None] + [1 if closes[i] > closes[i-1] else (0 if closes[i] < closes[i-1] else -1) for i in range(1, n)]
    F = [feats(closes[:i]) for i in range(n)]
    valid = [i for i in range(n) if F[i] is not None]
    t0 = next(i for i in range(n) if dates[i] >= SCORE_START)
    pred, steps, rnd = {}, [], 0
    while t0 < n:
        rnd += 1
        tr = [i for i in valid if i <= t0 - 1 - PURGE][-TRAIN:]
        assert len(tr) == TRAIN and max(tr) <= t0 - 1 - PURGE
        test = list(range(t0, min(t0 + TEST, n)))
        mdl = lr_fit(np.array([F[i] for i in tr]), np.array([1.0 if y[i] == 1 else 0.0 for i in tr]))
        for i in test:
            dp = dates[:i]; cp = closes[:i]
            assert dp[-1] < dates[i]
            p2 = lr_prob(mdl, F[i]) if F[i] is not None else None
            pred[i] = {"M0": 1, "M1": m1(cp), "M2": (None if p2 is None else (1 if p2 >= 0.5 else 0)), "p2": p2,
                       "M3": m3(dp, oi), "M4": p4u(dp, deal, spot), "M5": p5u(cp), "round": rnd}
        steps.append((rnd, dates[tr[0]], dates[tr[-1]], len(tr), ";".join(dates[tr[-1]+1:t0]), dates[test[0]], dates[test[-1]], len(test)))
        t0 += STEP
    return y, pred, steps

def sub_starts(dates, oi, deal, spot):
    """M3：D−1、D−2 都有 OI 才有特徵；M4：D−1 兩腿都有才有特徵。各自湊滿 TRAIN 天的第一個評分日（程式算，不手填）。"""
    k3 = first_full(dates, lambda i: i >= 2 and dates[i-1] in oi and dates[i-2] in oi)
    k4 = first_full(dates, lambda i: i >= 1 and dates[i-1] in deal and dates[i-1] in spot)
    return {"M3": k3, "M4": k4}
