#!/usr/bin/env python3
"""stockbot-backtest 開發期回測（750/20/20、purge 1；方法見 METHOD.md）。紙上計算、非投資建議。
不收參數；常數寫死在 common.py。只讀 data/dev 截斷檔。輸出寫 ROOT/results/<VERSION>/（open 'x'，不覆寫）。"""
import sys, os, json, math, csv, platform, datetime as dt
if __name__ == "__main__" and len(sys.argv) > 1:
    sys.stderr.write("拒絕：本程式不收任何參數\n"); sys.exit(2)
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, scipy
from scipy.stats import binom
import common as C

ETF_FEE, ETF_TAX, SLIPS = 0.001425, 0.001, (0.0, 0.0002)

def stats(yv, pv):
    """3×2 混淆矩陣（實際 漲/跌/平 × 預測 漲/跌）；平盤計入分母算錯；BA 只用漲、跌兩類 Recall。"""
    n = len(yv); cm = {(a, p): 0 for a in (1, 0, -1) for p in (1, 0)}
    for a, p in zip(yv, pv): cm[(a, p)] += 1
    o = {"n": n, "hit": (cm[(1, 1)] + cm[(0, 0)]) / n if n else None, "flat_days": cm[(-1, 1)] + cm[(-1, 0)],
         "cm": {f"實際{an}_預測{pn}": cm[(a, p)] for a, an in ((1, "漲"), (0, "跌"), (-1, "平")) for p, pn in ((1, "漲"), (0, "跌"))}}
    rec = {}
    for pos, nm in ((1, "up"), (0, "dn")):
        tp = cm[(pos, pos)]; fp = sum(cm[(a, pos)] for a in (1, 0, -1) if a != pos); fn = cm[(pos, 1 - pos)]
        pr = tp / (tp + fp) if tp + fp else None; rc = tp / (tp + fn) if tp + fn else None
        den = 2 * tp + fp + fn
        f1 = 2 * tp / den if den else None
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


def write_csv(folder, name, rows):
    if not rows:
        return
    with (folder / name).open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def status(model, i, dates, oi, deal, spot, prediction):
    if prediction is not None:
        return "VALID"
    if model == "M2" and i >= 22:
        return "INVALID"
    if model == "M3" and i >= 2 and all(d in oi for d in dates[i-2:i]):
        return "ABSTENTION"
    if i < {"M1": 2, "M2": 22, "M3": 2, "M4": 1, "M5": 270}.get(model, 0):
        return "WARM_UP"
    return "MISSING"


def probability_scores(rows):
    reports, curve = [], []
    for name, key in (("Fixed50", None), ("TrainPrior", "prior"), ("M2", "p2")):
        p = np.array([r[key] if key else .5 for r, y in rows])
        up = np.array([y == 1 for r, y in rows], dtype=float)
        clipped = np.clip(p, 1e-15, 1-1e-15)
        reports.append(dict(model=name, n=len(up), brier=np.mean((p-up)**2) if len(up) else None,
                            log_loss=-np.mean(up*np.log(clipped)+(1-up)*np.log1p(-clipped)) if len(up) else None))
        for b in range(10):
            sel = np.minimum((p*10).astype(int), 9) == b
            curve.append(dict(model=name, lower=b/10, upper=(b+1)/10, n=int(sel.sum()),
                              mean_p=p[sel].mean() if sel.any() else None,
                              frac_up=up[sel].mean() if sel.any() else None))
    return reports, curve


def feature_catalog(i, dates, oi, deal, spot):
    """Offsets are observed trading sessions counted backwards from target D."""
    specs=[("M0","constant","constant",0,0,True),("M1","previous_direction","TAIEX",2,1,True)]
    specs += [("M2",f"return_lag{k}","TAIEX",k+1,k,True) for k in range(1,6)]
    specs += [("M2","return20","TAIEX",21,1,True),("M2","vol20","TAIEX",21,1,True),
              ("M2","deviation_ma20","TAIEX",20,1,True),
              ("M3","oi_previous","TX foreign OI",1,1,dates[i-1] in oi),
              ("M3","oi_two_sessions_prior","TX foreign OI",2,2,dates[i-2] in oi),
              ("M4","net_deals","TX foreign net trades",1,1,dates[i-1] in deal),
              ("M4","net_cash","Foreign_Investor net spot",1,1,dates[i-1] in spot),
              ("M5","sigma20_rank250","TAIEX",270,1,True),("M5","ma60","TAIEX",60,1,True)]
    return [dict(date=dates[i],model=m,feature=f,source=s,feature_date=dates[i-end] if end else "",
                 coverage_start=dates[i-start] if start else "",coverage_end=dates[i-end] if end else "",
                 publication_time="",cutoff=dates[i]+"T08:45:00+08:00",
                 availability="VERIFIED" if m=="M0" else "UNVERIFIED" if present else "NOT AVAILABLE")
            for m,f,s,start,end,present in specs]


def block_interval(differences):
    # Resample calendar-contiguous circular blocks, retaining gaps as NaN.
    x=np.asarray(differences,float)
    if np.isfinite(x).sum()<40: return None,None
    rng=np.random.default_rng(104)
    estimates=[]
    for _ in range(2000):
        start=rng.integers(len(x),size=(len(x)+19)//20)
        sample=x[((start[:,None]+np.arange(20))%len(x)).ravel()[:len(x)]]
        if np.isfinite(sample).any(): estimates.append(np.nanmean(sample))
    return np.quantile(estimates,[.025,.975]).tolist()


def calibration_svg(rows):
    lines=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 380 340">',
           '<rect width="380" height="340" fill="white"/><path d="M45 20V280H305 M45 280L305 20" fill="none" stroke="#aaa"/>']
    for j,(model,color) in enumerate((("Fixed50","#a04b12"),("TrainPrior","#008478"),("M2","#403bb3"))):
        points=[(45+260*r["mean_p"],280-260*r["frac_up"],r["n"]) for r in rows if r["model"]==model and r["n"]]
        coords=" ".join(f"{x:.4f},{y:.4f}" for x,y,_ in points)
        lines.append(f'<polyline points="{coords}" stroke="{color}" fill="none"/>')
        for x,y,n in points: lines.append(f'<circle cx="{x:.4f}" cy="{y:.4f}" r="3" fill="{color}"><title>{model} n={n}</title></circle>')
        lines.append(f'<text x="{45+j*95}" y="325" fill="{color}">{model}</text>')
    lines.extend(['<text x="85" y="300">Mean predicted P(UP)</text>',
                  '<text transform="translate(18 237) rotate(-90)">Observed UP frequency</text></svg>'])
    return "\n".join(lines)


def etf_trade(o, c, slip):
    if not all(math.isfinite(x) and x > 0 for x in (o, c)):
        raise ValueError("invalid ETF price")
    buy = o*(1+slip)
    sell = c*(1-slip)
    costs = dict(buy_commission=buy*ETF_FEE, sell_commission=sell*ETF_FEE,
                 sell_tax=sell*ETF_TAX, buy_slippage=buy-o, sell_slippage=c-sell)
    return (sell-costs["sell_commission"]-costs["sell_tax"])/(buy+costs["buy_commission"])-1, costs


def trading(dates, etf, pred, idx):
    metrics, curve, annual = [], [], []
    for slip in SLIPS:
        for model in [*C.MODELS, "Cash", "BuyHoldPriceOnly"]:
            equity = peak = 1.
            drawdown = 0.
            returns, pnl, yearly = [], [], {}
            total_cost = dict.fromkeys(("buy_commission","sell_commission","sell_tax","buy_slippage","sell_slippage"), 0.)
            for j, i in enumerate(idx):
                day = dates[i]
                o, c = etf[day]
                growth, costs = etf_trade(o, c, slip)
                before = equity
                yearly.setdefault(day[:4], [before, before])
                if model == "BuyHoldPriceOnly":
                    if j == 0:
                        units = 1/(o*(1+slip)*(1+ETF_FEE))
                        for k in ("buy_commission","buy_slippage"):
                            total_cost[k] += units*costs[k]
                    equity = units*c
                    if j == len(idx)-1:
                        for k in ("sell_commission","sell_tax","sell_slippage"):
                            total_cost[k] += units*costs[k]
                        equity = units*c*(1-slip)*(1-ETF_FEE-ETF_TAX)
                        pnl.append(equity-1)
                elif model != "Cash" and pred[i][model] == 1:
                    units = equity/(o*(1+slip)*(1+ETF_FEE))
                    for k in costs:
                        total_cost[k] += units*costs[k]
                    equity *= 1+growth
                    pnl.append(equity-before)
                returns.append(equity/before-1)
                peak = max(peak, equity)
                drawdown = max(drawdown, 1-equity/peak)
                yearly[day[:4]][1] = equity
                curve.append(dict(model=model, slip=slip, date=day, equity=equity))
            sd = np.std(returns, ddof=1) if len(idx)>1 else 0
            loss = -sum(x for x in pnl if x<0)
            span = (dt.date.fromisoformat(dates[idx[-1]])-dt.date.fromisoformat(dates[idx[0]])).days+1
            metrics.append(dict(model=model, slip=slip, total_return=equity-1, CAGR=equity**(365.2425/span)-1,
                                Sharpe=np.mean(returns)/sd*math.sqrt(252) if sd else None, MDD=drawdown,
                                PF=sum(x for x in pnl if x>0)/loss if loss else None, **total_cost))
            annual.extend(dict(model=model, slip=slip, year=y, total_return=b/a-1) for y,(a,b) in yearly.items())
    return metrics, curve, annual


def main():
    from pathlib import Path
    import tempfile
    C.check(C.METHOD)
    dates, _, closes = C.load_taiex()
    oi, deal = C.load_inst()
    spot, etf = C.load_spot(), C.load_etf()
    y, pred, steps = C.run_models(dates, closes, oi, deal, spot)
    idx = sorted(pred)
    common = [i for i in idx if all(pred[i][m] is not None for m in C.MODELS)]
    folder = Path(tempfile.mkdtemp(prefix="stockbot-v104-"))
    summary, annual, matrix, prf, coverage, audit, public, private = [], [], [], [], [], [], [], []
    sources = {"M0":"constant", "M1":"TAIEX", "M2":"TAIEX", "M3":"TX foreign OI",
               "M4":"TX foreign net deals + Foreign_Investor net spot", "M5":"TAIEX"}
    for i in idx:
        public.append(dict(date=dates[i], fold_id=pred[i]["round"], model_version=C.VERSION,
                           **{m.lower()+"_pred":pred[i][m] for m in C.MODELS}, m2_prob_up=pred[i]["p2"]))
        private.append(dict(date=dates[i], actual=y[i]))
        for m in C.MODELS:
            state = status(m,i,dates,oi,deal,spot,pred[i][m])
            audit.append(dict(date=dates[i],model=m,source=sources[m],feature_date=dates[i-1] if m!="M0" else "",
                              coverage_end=dates[i-1] if m!="M0" else "", publication_time="",
                              cutoff=dates[i]+"T08:45:00+08:00",state=state,
                              availability="VERIFIED" if m=="M0" else "NOT AVAILABLE" if state=="MISSING" else "UNVERIFIED"))
    rule_starts = C.sub_starts(dates,oi,deal,spot)
    for m in C.MODELS:
        states = [r for r in audit if r["model"]==m]
        valid = [i for i in idx if pred[i][m] is not None]
        if m in rule_starts:
            pos = rule_starts[m]
            feature = dates[pos] if pos is not None else None
        else:
            feature = dates[{"M0":0,"M1":2,"M2":22,"M5":270}[m]]
        coverage.append(dict(model=m, sessions=len(idx), first_feature=feature, first_prediction=dates[valid[0]] if valid else None,
                             coverage=len(valid)/len(idx), no_call_rate=1-len(valid)/len(idx),
                             missing_rate=sum(r["state"]=="MISSING" for r in states)/len(idx),
                             conservative_hits=sum(pred[i][m]==y[i] for i in valid),
                             **{k:sum(r["state"]==k for r in states) for k in ("VALID","WARM_UP","MISSING","ABSTENTION","INVALID")}))
        for table, subset in (("full",valid),("common",common)):
            st = stats([y[i] for i in subset],[pred[i][m] for i in subset])
            ids = dict(table=table, model=m, n=len(subset))
            summary.append(dict(**ids,accuracy=st["hit"],balanced_accuracy=st["BA"],flat=st["flat_days"]))
            matrix.append(dict(**ids,**st["cm"]))
            prf.append(dict(**ids,**{k:v for k,v in st.items() if k.startswith(("up_","dn_"))}))
            for year in sorted({dates[i][:4] for i in idx}):
                ss=[i for i in subset if dates[i][:4]==year]
                sy=stats([y[i] for i in ss],[pred[i][m] for i in ss])
                annual.append(dict(table=table,model=m,year=year,n=len(ss),accuracy=sy["hit"],BA=sy["BA"]))
    pairs=[]
    for m in C.MODELS[1:]:
        sel=[i for i in idx if pred[i][m] is not None]
        b,c,p=mcn([pred[i][m]==y[i] for i in sel],[y[i]==1 for i in sel])
        low,high=block_interval([int(pred[i][m]==y[i])-int(y[i]==1) if pred[i][m] is not None else np.nan for i in idx])
        pairs.append(dict(model=m,n=len(sel),b=b,c=c,p=p,paired_block95_low=low,paired_block95_high=high))
    for row,q in zip(pairs,holm([r["p"] for r in pairs])):
        row["holm"]=q
    probabilities,cal=probability_scores([(pred[i],y[i]) for i in idx if pred[i]["p2"] is not None])
    trade,curve,years=trading(dates,etf,pred,idx)
    features=[r for i in idx for r in feature_catalog(i,dates,oi,deal,spot)]
    settlement=settle_days(dates)
    settlement_rows=[]
    for m in C.MODELS:
        for flag in (False,True):
            sel=[i for i in idx if (dates[i-1] in settlement)==flag and pred[i][m] is not None]
            st=stats([y[i] for i in sel],[pred[i][m] for i in sel])
            settlement_rows.append(dict(model=m,following_assumed_settlement=flag,n=len(sel),hit=st["hit"],BA=st["BA"]))
    (folder/"calibration.svg").write_bytes(calibration_svg(cal).encode())
    header=dict(version=C.VERSION,python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__,
                train=C.TRAIN,test=C.TEST,step=C.STEP,purge=C.PURGE,C_L2=C.C_L2,
                cutoff="08:45 Asia/Taipei",first=dates[idx[0]],last=dates[idx[-1]],
                publication_evidence="UNVERIFIED",input_hashes={k:getattr(C,k)[1] for k in ("TAIEX","INST","SPOT","ETF")},
                program_sha={n:C.sha(Path(__file__).with_name(n)) for n in ("backtest.py","common.py","leak_test.py")})
    (folder/"run-header.json").write_bytes(json.dumps(header,sort_keys=True,indent=2).encode())
    for name, rows in (("predictions",public),("internal_actuals",private),("coverage",coverage),("availability",features),
                       ("summary",summary),("yearly",annual),("confusion",matrix),("prf",prf),("mcnemar",pairs),("settlement",settlement_rows),
                       ("probability",probabilities),("calibration",cal),("trading",trade),("equity",curve),("trading_yearly",years),
                       ("steps",[dict(zip(("fold","train_start","train_end","train_n","purge","first","last","test_n"),s)) for s in steps])):
        write_csv(folder,name+".csv",rows)
    print(folder)


if __name__ == "__main__":
    main()
