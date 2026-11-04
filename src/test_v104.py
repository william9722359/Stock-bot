"""All prices and institutional records here are synthetic; score dates stop in 2020."""
import contextlib
import csv
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import backtest as B
import common as C
from reproduce import compare_values, normalized, hashes
from audit_outputs import audit


def fixture():
    day=dt.date(2020,1,2)
    past=[]
    while len(past)<780:
        day-=dt.timedelta(days=1)
        if day.weekday()<5: past.append(day.isoformat())
    dates=list(reversed(past))
    day=dt.date(2020,1,2)
    while len(dates)<832:
        if day.weekday()<5: dates.append(day.isoformat())
        day+=dt.timedelta(days=1)
    i=np.arange(len(dates))
    closes=(100+i*.02+np.sin(i*.64)).tolist()
    oi={d:i for i,d in enumerate(dates)}
    oi[dates[782]]=oi[dates[781]]
    del oi[dates[784]]
    deal={d:1 if i%2 else -1 for i,d in enumerate(dates)}
    spot=deal.copy()
    del spot[dates[782]]
    etf={d:(50.,49+i%4) for i,d in enumerate(dates)}
    return dates,closes,oi,deal,spot,etf


class Tests(unittest.TestCase):
    def test_f1_edges(self):
        for actual,pred,up,dn in [([1,1],[1,1],1,None),([0,0],[0,0],None,1),
                                    ([1,0],[0,1],0,0),([0,1],[0,1],1,1),
                                    ([1,0],[1,1],2/3,0),([1,0],[0,0],0,2/3),([],[],None,None)]:
            with self.subTest(actual=actual,pred=pred):
                s=B.stats(actual,pred)
                self.assertEqual((s["up_F1"],s["dn_F1"]),(up,dn))
        self.assertIsNone(B.stats([0],[0])["up_P"])
        self.assertEqual(B.stats([1],[0])["up_R"],0)

    def test_boundaries_scaler_labels_and_one_direction(self):
        ds,cl,oi,deal,spot,_=fixture()
        calls=[]
        original=C.lr_fit
        def capture(X,y):
            model=original(X,y)
            calls.append((X.copy(),y.copy(),model))
            return model
        with patch.object(C,"lr_fit",capture):
            y,p,steps=C.run_models(ds,cl,oi,deal,spot)
        for k,(X,target,(_,mu,sd)) in enumerate(calls):
            t=780+20*k
            tr=list(range(t-751,t-1))
            self.assertEqual(steps[k][1:],(ds[tr[0]],ds[tr[-1]],750,ds[t-1],ds[t],ds[min(t+19,len(ds)-1)],min(20,len(ds)-t)))
            expected=np.array([C.feats(cl[:i]) for i in tr])
            np.testing.assert_array_equal(X,expected)
            np.testing.assert_array_equal(target,[cl[i]>cl[i-1] for i in tr])
            np.testing.assert_allclose(mu,expected.mean(0))
            np.testing.assert_allclose(sd,expected.std(0))
            self.assertEqual(p[t]["prior"],target.mean())
        self.assertEqual(y[781],int(cl[781]>cl[780]))
        for sign in (1,-1):
            with self.subTest(sign=sign):
                alt=[2000+sign*i for i in range(len(ds))]
                yy,pp,_=C.run_models(ds,alt,oi,deal,spot)
                self.assertTrue(all(yy[i]==int(sign>0) and pp[i]["M1"]==yy[i] for i in pp))
                self.assertTrue(all(pp[i]["M2"] is None for i in pp))
        with self.assertRaises(ValueError):
            C.run_models(ds[:-1],cl,oi,deal,spot)

    def test_rule_coverage_and_cost(self):
        ds,cl,oi,deal,spot,_=fixture()
        _,p,_=C.run_models(ds,cl,oi,deal,spot)
        self.assertIsNotNone(p[780]["M3"])
        self.assertEqual(B.status("M3",783,ds,oi,deal,spot,p[783]["M3"]),"ABSTENTION")
        self.assertEqual(B.status("M3",785,ds,oi,deal,spot,p[785]["M3"]),"MISSING")
        self.assertIsNone(p[783]["M4"])
        r,c=B.etf_trade(100,105,.0002)
        self.assertAlmostEqual(r,(105*.9998*(1-.001425-.001))/(100*1.0002*1.001425)-1)
        self.assertAlmostEqual(c["sell_tax"],105*.9998*.001)
        _,curve,_=B.trading([ds[0]],{ds[0]:(100,120)},{0:dict.fromkeys(C.MODELS,0)},[0])
        self.assertTrue(all(row["equity"]==1 for row in curve if row["model"]!="BuyHoldPriceOnly"))

    def test_numerical_format_dates_and_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"dev.json"
            p.write_text(json.dumps({"data":[dict(date="2020/01/02",stock_id="0050",open="1,010.00",close="1000.0")]}))
            a=normalized("ETF",p)
            b=[dict(date="2020-01-02",stock_id="0050",open=1010,close=1000)]
            compare_values(a,b)
            for key,v in [("date","2020-01-03"),("close",1001)]:
                with self.assertRaises(ValueError):compare_values(a,[dict(b[0],**{key:v})])

    def test_mixed_slicer_refuses_before_opening_data(self):
        import runpy
        with patch("builtins.open",side_effect=AssertionError("forbidden input read")),self.assertRaises(SystemExit):
            runpy.run_path(str(Path(__file__).with_name("make_dev_slice.py")),run_name="__main__")
        with self.assertRaises(SystemExit): C.guard(["2025-01-01"])

    def test_two_synthetic_runs_and_reaggregation(self):
        ds,cl,oi,deal,spot,etf=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            outputs=[]
            for name in ("A","B"):
                out=Path(tmp)/name
                out.mkdir()
                with patch("tempfile.mkdtemp",return_value=str(out)),patch.object(C,"check"), \
                     patch.object(C,"load_taiex",return_value=(ds,cl,cl)),patch.object(C,"load_etf",return_value=etf), \
                     patch.object(C,"load_inst",return_value=(oi,deal)),patch.object(C,"load_spot",return_value=spot), \
                     contextlib.redirect_stdout(io.StringIO()):
                    B.main()
                audit(out)
                outputs.append(hashes(out))
            self.assertEqual(*outputs)
            with (out/"probability.csv").open() as f: self.assertEqual(float(next(csv.DictReader(f))["brier"]),.25)
            s=(out/"summary.csv").read_text()
            (out/"summary.csv").write_text(s.replace("full,M0,52","full,M0,51",1))
            with self.assertRaises(ValueError): audit(out)

    def test_fifty_future_replacements_original_test(self):
        import runpy
        ds,cl,oi,deal,spot,_=fixture()
        with tempfile.TemporaryDirectory() as tmp,patch.object(C,"ROOT",tmp),patch.object(sys,"argv",["leak_test.py"]), \
             patch.object(C,"load_taiex",return_value=(ds,cl,cl)),patch.object(C,"load_inst",return_value=(oi,deal)), \
             patch.object(C,"load_spot",return_value=spot),self.assertRaises(SystemExit) as result:
            runpy.run_path(str(Path(__file__).with_name("leak_test.py")),run_name="__main__")
        self.assertEqual(result.exception.code,0)

    def test_two_isolated_process_trees_with_synthetic_numerical_exports(self):
        import reproduce as rep
        ds,cl,oi,deal,spot,etf=fixture()
        groups={"TAIEX":[dict(date=d,stock_id="TAIEX",open=c,close=c) for d,c in zip(ds,cl)],
                "ETF":[dict(date=d,stock_id="0050",open=o,close=c) for d,(o,c) in etf.items()],
                "INST":[dict(date=d,futures_id="TX",institutional_investors="外資",
                             long_open_interest_balance_volume=v,short_open_interest_balance_volume=0,
                             long_deal_volume=max(deal[d],0),short_deal_volume=max(-deal[d],0)) for d,v in oi.items()],
                "SPOT":[dict(date=d,name="Foreign_Investor",buy=max(v,0),sell=max(-v,0)) for d,v in spot.items()]}
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            source,ref=base/"candidate",base/"reference"
            source.mkdir();ref.mkdir()
            pairs,manifest={},{}
            for key,rows in groups.items():
                name=Path(rep.PAIRS[key][0]).name
                (ref/name).write_text(json.dumps({"data":rows}),encoding="utf8")
                (source/name).write_text(json.dumps({"data":rows[::-1]},indent=1),encoding="utf8")
                pairs[key]=(name,C.sha(ref/name))
                manifest[name]=dict(source="synthetic",last=ds[-1],sha256=C.sha(source/name))
            (source/"development-inputs.json").write_text(json.dumps(manifest))
            with patch.object(rep,"PAIRS",pairs):
                a=rep.run_one(base/"A",source,"numerical",ref)
                b=rep.run_one(base/"B",source,"numerical",ref)
            self.assertEqual(hashes(a),hashes(b))


if __name__=="__main__":unittest.main()
