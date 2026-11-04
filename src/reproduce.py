"""Isolated development-only replay. Read no mixed-period source archive."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

import common as C

ROOT = Path(C.ROOT)
BASE = "110ec47acc8bc92d00e6ed3190ad02f466e2df8a"
OLD = "results/v1.0.3b"
PAIRS = dict(TAIEX=C.TAIEX, INST=C.INST, SPOT=C.SPOT, ETF=C.ETF)
ATOL, RTOL = 1e-9, 1e-12


def blob(path):
    return subprocess.check_output(["git", "show", BASE + ":" + path], cwd=ROOT)


def released():
    files = subprocess.check_output(["git","ls-tree","-r","--name-only",BASE,"--",OLD],cwd=ROOT,text=True).splitlines()
    for path in files:
        if (ROOT/path).read_bytes() != blob(path):
            raise ValueError("Published file differs from Git blob: "+path)
    return files


def normalized(key, path):
    from datetime import date
    from decimal import Decimal
    fields = {"ETF":("open","close"),"TAIEX":("open","close"),
              "INST":("long_open_interest_balance_volume","short_open_interest_balance_volume","long_deal_volume","short_deal_volume"),
              "SPOT":("buy","sell")}[key]
    rows = []
    for row in json.loads(path.read_bytes())["data"]:
        day = date.fromisoformat(str(row["date"]).replace("/","-")).isoformat()
        if day > C.DEV_END:
            raise ValueError("Input is not a development-only export")
        r = {"date":day}
        for field, expected in {"ETF":{"stock_id":"0050"},"TAIEX":{"stock_id":"TAIEX"},"INST":{"futures_id":"TX"},"SPOT":{}}[key].items():
            if row[field] != expected:
                raise ValueError("Unexpected identifier")
            r[field] = expected
        if key=="INST":
            if row["institutional_investors"]!="外資": continue
            r["institutional_investors"]="外資"
        if key=="SPOT":
            if row["name"]!="Foreign_Investor": continue
            r["name"]="Foreign_Investor"
        for field in fields:
            value=Decimal(str(row[field]).replace(",",""))
            if not value.is_finite() or (key in ("ETF","TAIEX") and value<=0):
                raise ValueError("Invalid numeric field")
            if key=="INST" and value != value.to_integral_value():
                raise ValueError("Fractional contracts")
            r[field]=int(value) if key=="INST" else float(value)
        rows.append(r)
    rows.sort(key=lambda r:r["date"])
    if len({r["date"] for r in rows})!=len(rows): raise ValueError("Duplicate session")
    return rows


def compare_values(a,b):
    if [r["date"] for r in a]!=[r["date"] for r in b]:
        raise ValueError("Trading dates or missing observations differ")
    for x,y in zip(a,b):
        for k,v in x.items():
            equal = math.isclose(v,y[k],rel_tol=RTOL,abs_tol=ATOL) if isinstance(v,(float,int)) else v==y[k]
            if not equal: raise ValueError(f"Numerical difference {x['date']} {k}")


def run_one(folder, source, mode, reference=None):
    legacy = mode=="exact"
    src=folder/"src"
    src.mkdir(parents=True)
    for name in ("common.py","backtest.py","make_dev_slice.py","leak_test.py"):
        path="src/"+name
        (folder/path).write_bytes(blob(path) if legacy else (ROOT/path).read_bytes())
    (folder/"METHOD.md").write_bytes(blob("METHOD.md"))
    overrides={}
    declared={}
    if mode=="numerical":
        declared=json.loads((source/"development-inputs.json").read_bytes())
        for key,(path,want) in PAIRS.items():
            name=Path(path).name
            item=declared[name]
            if (not item.get("source") or not item.get("last") or item["last"]>C.DEV_END
                    or item["sha256"] in {v[1] for v in C.ORIG.values()}):
                raise ValueError("Requires independently isolated development-only exports")
    for key,(path,want) in PAIRS.items():
        name=Path(path).name
        dest=folder/("data/finmind" if key=="ETF" else "data/dev")/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        candidate=source/name
        if mode=="numerical":
            if C.sha(candidate)!=declared[name]["sha256"]: raise ValueError("Candidate export SHA mismatch")
            ref=reference/name
            if C.sha(ref)!=want: raise ValueError("Numerical reference slice does not match released SHA: "+name)
            values=normalized(key,candidate)
            compare_values(normalized(key,ref),values)
            dest.write_text(json.dumps({"data":values},ensure_ascii=False),encoding="utf8")
        else:
            if C.sha(candidate)!=want: raise ValueError("Development file SHA mismatch: "+name)
            shutil.copyfile(candidate,dest)
        overrides[key]=[str(dest),C.sha(dest)]
    env=dict(os.environ,PYTHONUTF8="1",OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1")
    def execute(name):
        if mode=="numerical":
            command=("import json,sys,runpy;sys.path.insert(0,'src');import common as c;"
                     "[setattr(c,k,v) for k,v in json.loads(sys.argv.pop()).items()];"
                     f"runpy.run_path('src/{name}',run_name='__main__')")
            args=["-c",command,json.dumps(overrides)]
        else:
            args=["src/"+name]
        return subprocess.check_output([sys.executable,"-B",*args],cwd=folder,env=env,encoding="utf8")
    leak=execute("leak_test.py").strip().splitlines()[-1]
    last=execute("backtest.py").strip().splitlines()[-1]
    if legacy: return folder/OLD
    output=folder/"output"
    shutil.move(last,output)
    from audit_outputs import audit
    audit(output)
    shutil.copyfile(leak,output/"leak_test.txt")
    return output


def hashes(folder):
    return {p.relative_to(folder).as_posix():C.sha(p) for p in folder.rglob("*") if p.is_file()}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=("public","exact","corrected","numerical"))
    p.add_argument("--inputs",type=Path,help="Folder containing ONLY four licensed bounded development exports")
    p.add_argument("--reference",type=Path,help="Four independently archived development reference files")
    p.add_argument("--custody",type=Path,help="Original-source digest JSON signed by an authorized archive custodian")
    p.add_argument("--keyring",type=Path,help="Independently trusted custodian GPG verification keyring")
    a=p.parse_args()
    files=released()
    if a.mode=="public":
        print(f"PASS {len(files)} published outputs match {BASE}");return
    if not a.inputs or a.mode=="numerical" and not a.reference: p.error("Missing private development inputs/reference")
    if a.mode=="exact":
        if not a.custody or not a.keyring: p.error("Exact requires externally verified original archive hashes")
        subprocess.run(["gpgv","--keyring",str(a.keyring.resolve()),str(a.custody)+".sig",str(a.custody)],check=True)
        originals={Path(path).name:want for path,want in [*C.ORIG.values(),C.ETF]}
        if json.loads(a.custody.read_bytes())!=originals: raise ValueError("Original snapshot custody hashes differ")
        import numpy,scipy
        h=json.loads(blob(OLD+"/run-header.json"))
        if (platform.python_version(),numpy.__version__,scipy.__version__)!=(h["python"],h["numpy"],h["scipy"]):
            raise ValueError("Exact environment mismatch")
        for n,want in h["program_sha"].items():
            if hashlib.sha256(blob("src/"+n)).hexdigest()!=want: raise ValueError("Frozen source mismatch")
    temp=Path(tempfile.mkdtemp(prefix="stockbot-reproduction-"))
    print("Private run directory",temp,flush=True)
    left=run_one(temp/"A",a.inputs,a.mode,a.reference)
    right=run_one(temp/"B",a.inputs,a.mode,a.reference)
    if hashes(left)!=hashes(right): raise ValueError("A/B output SHA mismatch")
    if a.mode=="exact":
        for name in files:
            if C.sha(left/Path(name).name)!=hashlib.sha256(blob(name)).hexdigest():raise ValueError("Published diff: "+name)
        print("All published bytes reproduced, pinned development inputs and signed original archive hashes verified.")
    if a.mode=="numerical":
        ref=run_one(temp/"reference",a.reference,"corrected")
        def predictions(folder):
            with (folder/"predictions.csv").open(encoding="utf8") as f: return list(csv.DictReader(f))
        expected,actual=predictions(ref),predictions(left)
        for r in expected+actual:
            if r["m2_prob_up"]: r["m2_prob_up"]=float(r["m2_prob_up"])
        compare_values(expected,actual)
        if (ref/"internal_actuals.csv").read_bytes()!=(left/"internal_actuals.csv").read_bytes():raise ValueError("Label differences")
    print("PASS",a.mode,"independent A/B output hashes",hashes(left))


if __name__=="__main__":
    main()
