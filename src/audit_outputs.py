"""Reaggregate exported calls using a separately kept private development label file."""
import csv
from pathlib import Path
import sys
from backtest import stats
import common as C


def audit(folder):
    def read(name):
        with (folder/(name+".csv")).open(encoding="utf8") as f:
            return list(csv.DictReader(f))
    calls=read("predictions")
    actual=read("internal_actuals")
    days=[r["date"] for r in calls]
    C.guard(days)
    if days!=sorted(set(days)) or days!=[r["date"] for r in actual]:
        raise ValueError("Prediction/label date mismatch")
    tables={k:read(k) for k in ("summary","yearly","confusion","prf")}
    for name,table in tables.items():
        keys=set()
        for row in table:
            m=row["model"]
            key=(row["table"],m,row.get("year",""))
            if key in keys: raise ValueError("Duplicate summary row")
            keys.add(key)
            pairs=[(int(label["actual"]),int(r[m.lower()+"_pred"])) for r,label in zip(calls,actual)
                   if r[m.lower()+"_pred"] and r["date"].startswith(row.get("year",""))
                   and (row["table"]=="full" or all(r[k.lower()+"_pred"] for k in C.MODELS))]
            s=stats([a for a,p in pairs],[p for a,p in pairs])
            expected={"n":len(pairs),**s["cm"],**s,"accuracy":s["hit"],"balanced_accuracy":s["BA"],"flat":s["flat_days"]}
            for k,v in row.items():
                if k in expected and v!=("" if expected[k] is None else str(expected[k])):
                    raise ValueError(f"{name}/{key}/{k} differs")
        years=sorted({d[:4] for d in days}) if name=="yearly" else [""]
        if keys!={(table,m,y) for table in ("full","common") for m in C.MODELS for y in years}:
            raise ValueError("Incomplete aggregate")


if __name__=="__main__":
    audit(Path(sys.argv[1]))
    print("PASS reaggregated direction metrics")
