#!/usr/bin/env python3
"""stockbot-backtest 開發期回測（750/20/20、purge 1；方法見 METHOD.md）。紙上計算、非投資建議。
不收參數；常數寫死在 common.py。只讀 data/dev 截斷檔。輸出寫 C.out_dir()（預設 runs/<VERSION>，open 'x'，不覆寫；從不寫 results/）。"""
import sys, os, json, math, csv, platform, datetime as dt
if len(sys.argv) > 1:
    sys.stderr.write("拒絕：本程式不收任何參數\n"); sys.exit(2)
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, scipy
from scipy.stats import binom
import common as C

FUT_TAX = 0.00002   # 期交稅十萬分之二，每趟進出各一次（假設、待核）
ETF_FEE, ETF_TAX, LS_FEES, SLIPS, TARGET = 0.001425, 0.001, (0.0, 0.0001, 0.0005), (0.0, 0.0002), 0.586
PUBLIC_COLS = ["date", "m0_pred", "lr_pred", "lr_prob_up", "fold_id", "m3_pred", "m4_pred"]   # 公開預測檔 7 欄
CAL_BINS = 10

ZERO_DIVISION = "undefined"   # v1.0.4 F：分母為 0 → 無法定義（輸出「無法計算」），不當 0 分；分母 > 0 而值為 0 → 輸出 0
def prf(tp, fp, fn):
    """P=TP/(TP+FP)、R=TP/(TP+FN)、F1=2TP/(2TP+FP+FN)（與 2PR/(P+R) 在 P、R 皆有定義時相同）。分母為 0 → None。"""
    pr = tp / (tp + fp) if tp + fp > 0 else None
    rc = tp / (tp + fn) if tp + fn > 0 else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn > 0 else None
    return pr, rc, f1

def stats(yv, pv):
    """3×2 混淆矩陣（實際 漲/跌/平 × 預測 漲/跌）；平盤計入分母算錯；BA 只用漲、跌兩類 Recall。"""
    n = len(yv); cm = {(a, p): 0 for a in (1, 0, -1) for p in (1, 0)}
    for a, p in zip(yv, pv): cm[(a, p)] += 1
    o = {"n": n, "hit": (cm[(1, 1)] + cm[(0, 0)]) / n if n else None, "flat_days": cm[(-1, 1)] + cm[(-1, 0)],
         "cm": {f"實際{an}_預測{pn}": cm[(a, p)] for a, an in ((1, "漲"), (0, "跌"), (-1, "平")) for p, pn in ((1, "漲"), (0, "跌"))}}
    rec = {}
    for pos, nm in ((1, "up"), (0, "dn")):
        tp = cm[(pos, pos)]; fp = sum(cm[(a, pos)] for a in (1, 0, -1) if a != pos); fn = cm[(pos, 1 - pos)]
        pr, rc, f1 = prf(tp, fp, fn)
        rec[nm] = rc; o.update({nm + "_TP": tp, nm + "_FP": fp, nm + "_FN": fn, nm + "_P": pr, nm + "_R": rc, nm + "_F1": f1})
    o["BA"] = (rec["up"] + rec["dn"]) / 2 if rec["up"] is not None and rec["dn"] is not None else None
    return o

def mcn(a_ok, b_ok, one=True):
    b = sum(x and not z for x, z in zip(a_ok, b_ok)); c = sum(z and not x for x, z in zip(a_ok, b_ok))
    if b + c == 0: return b, c, None
    if one: return b, c, float(binom.sf(b - 1, b + c, 0.5))
    return b, c, min(1.0, 2 * float(min(binom.cdf(b, b + c, 0.5), binom.sf(b - 1, b + c, 0.5))))

def holm(ps):
    idx = sorted([i for i, p in enumerate(ps) if p is not None], key=lambda i: ps[i]); m = len(idx); out = [None] * len(ps); run = 0.0
    for k, i in enumerate(idx): run = max(run, min(1.0, (m - k) * ps[i])); out[i] = run
    return out

def tm(r):
    r = np.asarray(r, float); E = np.cumprod(1 + r); neg = r[r < 0].sum(); sd = r.std(ddof=1)
    return (float(r[r > 0].sum() / abs(neg)) if neg < 0 else None, float(r.mean() / sd * math.sqrt(252)) if sd > 0 else None,
            float(E[-1] ** (252 / len(r)) - 1), float(np.max(1 - E / np.maximum.accumulate(np.concatenate([[1.0], E]))[1:])))

def ret(p, oc, kind, fee, slip):
    g = oc[1] / oc[0] - 1
    if kind == "ETF": return (g - 2 * ETF_FEE - ETF_TAX - 2 * slip) if p == 1 else 0.0
    return (g if p == 1 else -g) - 2 * FUT_TAX - fee - 2 * slip   # 期交稅×2（假設、待核）

def fmt(v):
    if v is None: return "無法計算"
    return f"{v:.6f}" if isinstance(v, float) else str(v)

def settle_days(dates):
    """台指期月結算日＝每月第三個星期三；非交易日順延到下一個 TAIEX 交易日（假設、待核）。"""
    ds = set(dates); out = set()
    for yy in range(int(dates[0][:4]), int(C.DEV_END[:4]) + 1):
        for mm in range(1, 13):
            d = dt.date(yy, mm, 1); w = 0
            while True:
                if d.weekday() == 2:
                    w += 1
                    if w == 3: break
                d += dt.timedelta(days=1)
            while d.isoformat() not in ds and d.isoformat() <= C.DEV_END: d += dt.timedelta(days=1)
            if d.isoformat() <= C.DEV_END: out.add(d.isoformat())
    return out

def main():
    shas = {k: C.check(v) for k, v in dict(TAIEX_dev=C.TAIEX, INST_dev=C.INST, SPOT_dev=C.SPOT, ETF0050=C.ETF, METHOD=C.METHOD).items()}
    dates, opens, closes = C.load_taiex(); etf = C.load_etf(); oi, deal = C.load_inst(); spot = C.load_spot()
    y, pred, steps = C.run_models(dates, closes, oi, deal, spot)
    sc = [i for i in sorted(pred) if C.SCORE_START <= dates[i] <= C.DEV_END]; C.guard([dates[i] for i in sc])
    for i in sc:
        if dates[i] not in etf: C.die("0050 缺日")
    ks = C.sub_starts(dates, oi, deal, spot)
    tables = {"主表": (C.MAIN, sc),
              "副表_M3": (["M0", "M1", "M2", "M3"], [i for i in sc if ks["M3"] is not None and i >= ks["M3"]]),
              "副表_M4": (["M0", "M1", "M2", "M4"], [i for i in sc if ks["M4"] is not None and i >= ks["M4"]])}
    family = [("主表", "M1"), ("主表", "M2"), ("主表", "M5"), ("副表_M3", "M3"), ("副表_M4", "M4")]   # 5 個檢定一起 Holm
    scen = [("ETF只做多_手續費0.1425%x2_稅0.1%_滑價" + str(s), "ETF", 0, s) for s in SLIPS] + \
           [(f"多空_每趟費{f}_滑價{s}", "LS", f, s) for f in LS_FEES for s in SLIPS]
    stl = settle_days(dates)
    summ, trad, cmr, yr, prf, calib, sett, missing = [], [], [], [], [], [], [], []
    res = {}; briers = {}
    for g, (models, idx) in tables.items():
        for m in models:
            ev = [i for i in idx if pred[i][m] is not None]
            missing.append({"table": g, "model": m, "table_days": len(idx), "predicted": len(ev), "missing": len(idx) - len(ev),
                            "missing_ratio": (len(idx) - len(ev)) / len(idx) if idx else None})
            st = stats([y[i] for i in ev], [pred[i][m] for i in ev]); k = len(ev)
            cuts = [ev[:k // 3], ev[k // 3:2 * k // 3], ev[2 * k // 3:]]
            st["sub"] = [sum(y[i] == pred[i][m] for i in s) / len(s) for s in cuts]
            st["tr"] = {nm: tm([ret(pred[i][m], etf[dates[i]], kd, f, s) for i in ev]) for nm, kd, f, s in scen}
            st["ev"] = ev; res[(g, m)] = st
            summ.append({"table": g, "model": m, "first": dates[ev[0]], "last": dates[ev[-1]], "n": st["n"], "hit": st["hit"], "BA": st["BA"],
                         "flat_days": st["flat_days"], "sub1": st["sub"][0], "sub2": st["sub"][1], "sub3": st["sub"][2],
                         "sub_ranges": " | ".join(f"{dates[s[0]]}~{dates[s[-1]]}({len(s)})" for s in cuts), "vs58.6_pp": (st["hit"] - TARGET) * 100})
            cmr.append(dict({"table": g, "model": m}, **st["cm"]))
            prf.append(dict({"table": g, "model": m}, **{kk: st[kk] for kk in ("up_TP", "up_FP", "up_FN", "up_P", "up_R", "up_F1", "dn_TP", "dn_FP", "dn_FN", "dn_P", "dn_R", "dn_F1")}, n=st["n"], up_actual_n=st["up_TP"] + st["up_FN"], dn_actual_n=st["dn_TP"] + st["dn_FN"], up_pred_n=st["up_TP"] + st["up_FP"], dn_pred_n=st["dn_TP"] + st["dn_FP"], flat_days=st["flat_days"]))
            for yy in sorted({dates[i][:4] for i in ev}):
                s2 = stats([y[i] for i in ev if dates[i][:4] == yy], [pred[i][m] for i in ev if dates[i][:4] == yy])
                yr.append({"table": g, "model": m, "year": yy, "n": s2["n"], "Acc": s2["hit"], "BA": s2["BA"], "flat_days": s2["flat_days"]})
            if m == "M2":
                brier = float(np.mean([(pred[i]["p2"] - (1.0 if y[i] == 1 else 0.0)) ** 2 for i in ev])); briers[g] = brier
                for b in range(CAL_BINS):
                    lo, hi = b / CAL_BINS, (b + 1) / CAL_BINS
                    sel = [i for i in ev if lo <= pred[i]["p2"] < hi or (b == CAL_BINS - 1 and pred[i]["p2"] == 1.0)]
                    calib.append({"table": g, "model": m, "bin_lo": lo, "bin_hi": hi, "n": len(sel),
                                  "mean_prob_up": float(np.mean([pred[i]["p2"] for i in sel])) if sel else None,
                                  "frac_up": sum(y[i] == 1 for i in sel) / len(sel) if sel else None})
            else:
                calib.append({"table": g, "model": m, "bin_lo": "不適用", "bin_hi": "不適用", "n": "不適用", "mean_prob_up": "不適用（無機率輸出）", "frac_up": "不適用"})
            for nm, sel in (("結算日隔天", [i for i in ev if dates[i - 1] in stl]), ("非結算日隔天", [i for i in ev if dates[i - 1] not in stl])):
                s3 = stats([y[i] for i in sel], [pred[i][m] for i in sel]) if sel else {"n": 0, "hit": None, "BA": None}
                h0 = sum(y[i] == 1 for i in sel) / len(sel) if sel else None
                sett.append({"table": g, "model": m, "group": nm, "n": s3["n"], "hit": s3["hit"], "BA": s3["BA"], "M0_same_days_hit": h0})
            for nm, v in st["tr"].items():
                trad.append({"table": g, "model": m, "標的": "0050（FinMind 未還原權息）", "scenario": nm, "fees": "假設、待核",
                             "PF": v[0], "Sharpe": v[1], "CAGR": v[2], "MDD": v[3]})
    mc, ps = [], []
    for g, m in family:
        ev = res[(g, m)]["ev"]; b, c, p = mcn([y[i] == pred[i][m] for i in ev], [y[i] == 1 for i in ev]); ps.append(p); mc.append([g, m, len(ev), b, c, p])
    verdict = {}
    for row, q in zip(mc, holm(ps)):
        g, m = row[0], row[1]; row.append(q); s1 = res[(g, m)]; ev = s1["ev"]
        m0 = stats([y[i] for i in ev], [1] * len(ev)); k = len(ev)
        m0sub = [sum(y[i] == 1 for i in s) / len(s) for s in (ev[:k // 3], ev[k // 3:2 * k // 3], ev[2 * k // 3:])]
        key = "多空_每趟費0.0001_滑價0.0"; sh0 = tm([ret(1, etf[dates[i]], "LS", 0.0001, 0.0) for i in ev])[1]
        verdict[f"{g}|{m}"] = (s1["hit"] > m0["hit"] and q is not None and q < 0.05, sum(a > z for a, z in zip(s1["sub"], m0sub)) >= 2,
                               s1["tr"][key][1] is not None and sh0 is not None and s1["tr"][key][1] > sh0)
    mcr = [{"family": "Holm 5 檢定（r4c §7）", "table": r[0], "pair": f"{r[1]}>M0", "test": "單尾精確McNemar", "n": r[2], "b": r[3], "c": r[4], "p": r[5], "p_holm": r[6]} for r in mc]
    R = C.out_dir(); os.makedirs(os.path.join(R, "internal"), exist_ok=True)
    def wcsv(name, rows, head=None):
        with open(os.path.join(R, name), "x", newline="") as fh:
            if head: fh.write(head)
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); [w.writerow({k: fmt(v) for k, v in r.items()}) for r in rows]
    wcsv("summary.csv", summ); wcsv("trading.csv", trad); wcsv("mcnemar.csv", mcr); wcsv("confusion.csv", cmr)
    wcsv("yearly.csv", yr); wcsv("prf.csv", prf); wcsv("calibration.csv", calib); wcsv("settlement.csv", sett); wcsv("missing.csv", missing)
    K = ("fold_id", "train_start", "train_end", "train_n", "purge_dates", "test_start", "test_end", "test_n")
    wcsv("steps.csv", [dict(zip(K, s)) for s in steps])
    sub_rows = []
    for g in ("副表_M3", "副表_M4"):
        S = tables[g][1]; first = dates[S[0]]
        for s in steps:
            if s[6] < first: continue
            td = [dates[i] for i in S if s[5] <= dates[i] <= s[6]]
            sub_rows.append({"table": g, "fold_id": s[0], "M2_train_start": s[1], "M2_train_end": s[2], "purge_dates": s[4], "test_start": td[0], "test_end": td[-1], "test_n": len(td),
                             "note": "M3/M4 為規則、不訓練；第一個評分日由湊滿 750 天＋purge 1 算出；M2 欄為同輪 LR 訓練窗"})
    wcsv("steps_sub.csv", sub_rows)
    s3, s4 = set(tables["副表_M3"][1]), set(tables["副表_M4"][1])
    def cell(i, m, S):   # 主表日、未湊滿 750 天、當天 missing → 空字串
        if i not in S or pred[i][m] is None: return ""
        return pred[i][m]
    pub = [dict(zip(PUBLIC_COLS, (dates[i], pred[i]["M0"], pred[i]["M2"], pred[i]["p2"], pred[i]["round"], cell(i, "M3", s3), cell(i, "M4", s4)))) for i in sc]
    wcsv("predictions.csv", pub)   # 表頭即欄位清單（chk2 要求第一列逐字等於表頭），欄位說明見 REPRODUCE.md
    wcsv("internal/predictions_internal.csv", [{"date": dates[i], "round": pred[i]["round"], "model": m, "prob_up": (pred[i]["p2"] if m == "M2" else "不適用"),
            "pred": ("missing" if pred[i][m] is None else pred[i][m]), "actual": y[i]} for i in sc for m in C.MODELS])
    leak = os.path.join(R, "leak_test.txt"); leak_ok = open(leak).read().startswith("PASS") if os.path.exists(leak) else None
    hdr = {"version": C.VERSION, "label": "FinMind・回測練習（不算正式成績）；未審初版、紙上、非投資建議",
           "unseal": "開發期 2020–2024 評分；2025 起不讀",
           "holdout": "2025-01-01 以後本版不讀、不跑",
           "params": {"SCORE_START": C.SCORE_START, "DEV_END": C.DEV_END, "HOLDOUT_START": C.HOLDOUT_START, "TRAIN": C.TRAIN, "TEST": C.TEST, "STEP": C.STEP, "PURGE": C.PURGE,
                      "window_meaning": "750 日訓練窗、20 日測試、每次往前 20 日、purge 1", "C_L2": C.C_L2, "IRLS_MAX": C.IRLS_MAX, "IRLS_TOL": C.IRLS_TOL},
           "sha": shas, "program_sha": {f: C.sha(os.path.join(C.ROOT, "src", f)) for f in ("backtest.py", "common.py", "make_dev_slice.py", "leak_test.py")},
           "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__, "sklearn": "未安裝（M2 用 numpy IRLS 自寫）",
           "total_rows_taiex_dev": len(dates), "steps": len(steps), "first_oos": dates[min(pred)], "scoring_window_rows": len(sc),
           "first_scoring_day": {"主表": dates[sc[0]], "M3": dates[ks["M3"]] if ks["M3"] is not None else None, "M4": dates[ks["M4"]] if ks["M4"] is not None else None},
           "tables": {g: {"models": ms, "first": dates[ix[0]], "last": dates[ix[-1]], "n": len(ix)} for g, (ms, ix) in tables.items()},
           "M4_note": "M4 期貨腿＝外資多空交易口數淨額（不是 M3 的淨未平倉變化）；現貨腿只用 Foreign_Investor，不含外資自營商；單位待核；公布時刻待核",
           "M5": "已實作（見 METHOD.md §3）",
           "fees": "假設、待核（含期交稅十萬分之二×2、手續費、證交稅）",
           "settlement": "結算日＝第三個星期三、非交易日順延（假設、待核）；settlement.csv 為分表、不改判準",
           "target": "交易模擬標的 0050（FinMind、未還原權息），D 日開盤進、收盤出；方向指標用加權指數；預測與交易分開",
           "flat_rule": "平盤計入分母算錯；BA 只用漲、跌 Recall 平均；混淆矩陣 3×2",
           "brier_M2": None,
           "leak_test": "PASS" if leak_ok else ("FAIL" if leak_ok is False else "未判定")}
    hdr["brier_M2"] = briers
    sj = {"brier_M2": briers, "summary": summ, "mcnemar_holm5": mcr, "confusion_3x2": cmr, "yearly": yr, "prf": prf, "calibration": calib, "settlement": sett, "missing": missing,
          "trading": trad, "verdict_vs_M0": {k: {"條1": v[0], "條2": v[1], "條3": v[2], "條4": hdr["leak_test"], "結論": "贏" if all(v) and leak_ok else "沒有贏"} for k, v in verdict.items()}}
    with open(os.path.join(R, "run-header.json"), "x") as fh: json.dump(hdr, fh, ensure_ascii=False, indent=1, sort_keys=True)
    with open(os.path.join(R, "summary.json"), "x") as fh: json.dump(sj, fh, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    gaps = [(dates[k - 1], dates[k], (dt.date.fromisoformat(dates[k]) - dt.date.fromisoformat(dates[k - 1])).days) for k in range(1, len(dates))
            if (dt.date.fromisoformat(dates[k]) - dt.date.fromisoformat(dates[k - 1])).days > 10]   # 警示、不停機
    with open(os.path.join(R, "report.md"), "x") as fh:
        fh.write("# 回測報告 " + C.VERSION + "（FinMind・回測練習，不算正式成績；未審初版、紙上、非投資建議）\n\n```json\n" + json.dumps(hdr, ensure_ascii=False, indent=1, sort_keys=True) +
                 "\n```\n\n## Holm 5 檢定\n" + "\n".join(f"- {r['table']} {r['pair']} n={r['n']} b={r['b']} c={r['c']} p={fmt(r['p'])} p_holm={fmt(r['p_holm'])}" for r in mcr) +
                 "\n\n## 判定\n" + "\n".join(f"- {k}: {d}" for k, d in sj["verdict_vs_M0"].items()) +
                 f"\n\n## 警示：連續兩列相隔超過 10 個日曆天（TAIEX 開發期檔，共 {len(gaps)} 處；只警示、不停機）\n" + "\n".join(f"- {a} → {b}：{d} 天" for a, b, d in gaps) + "\n")
    print(json.dumps(hdr["first_scoring_day"], ensure_ascii=False))

if __name__ == "__main__":
    main()
