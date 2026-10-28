# 每日練習 r10（公開版）：只讀既有檔，輸出只寫 --outdir（預設本資料夾）。FinMind 只當歷史題庫；當天資料只用證交所 MI_INDEX、BFI82U 與期交所三大法人。
import json, random, math, sys, hashlib, os, argparse, datetime, zoneinfo, urllib.request, urllib.parse, io, contextlib, csv
H=os.path.dirname(os.path.abspath(__file__))
ap=argparse.ArgumentParser(); ap.add_argument('--date'); ap.add_argument('--outdir',default=H)
ap.add_argument('--rounds',default=H+'/rounds.txt')
ap.add_argument('--mi-file','--twse-json',dest='mi_file',help='離線測試用：MI_INDEX 原始回應檔')
ap.add_argument('--bfi-file',help='離線測試用：BFI82U 原始回應檔')
ap.add_argument('--tf-file',help='離線測試用：期交所 futContractsDateDown 原始回應檔')
ap.add_argument('--fmtqik-dir',default=H+'/fmtqik',help='FMTQIK 月表快取／離線注入資料夾（FMTQIK-YYYYMM-asof-YYYYMMDD.raw.txt）')
ap.add_argument('--graded-log',default=H+'/graded-log.jsonl')
ap.add_argument('--pred',default=H+'/data/predictions.jsonl',help='歷史題庫（P4、P5 每日表態，jsonl）'); ap.add_argument('--taiex-json',default=H+'/data/finmind-TaiwanStockPrice-TAIEX.json',help='歷史大盤收盤（使用者自行依 FinMind 條款取得，非商業）')
ap.add_argument('--twse-base',default='https://www.twse.com.tw'); ap.add_argument('--taifex-base',default='https://www.taifex.com.tw')
a=ap.parse_args()
def die(m,code=2): print(m); sys.exit(code)
RULES=H+'/drill-rules-v9-public.md'; LOCK=RULES+'.sha256'
sha=lambda p: hashlib.sha256(open(p,'rb').read()).hexdigest()
rt=open(RULES).read()
if sha(RULES)!=open(LOCK).read().split()[0] or ('drill_r10.py sha256：'+sha(__file__)) not in rt:
    die('凍結規則檔或腳本 sha256 不符，中止')
EXP=sha(RULES); TPE=zoneinfo.ZoneInfo('Asia/Taipei')
DATE=a.date or datetime.datetime.now(TPE).strftime('%Y%m%d')
SEED=int(DATE); N=60; GEN='# genesis 20261028'
OUTJ=os.path.join(a.outdir,f'drill-out-{DATE}.json'); OUTM=os.path.join(a.outdir,f'drill-out-{DATE}.md')
# B7：rounds.txt 必須存在且首行為 genesis
if not os.path.exists(a.rounds): die('rounds.txt 不存在，中止：'+a.rounds)
RL=open(a.rounds).read().splitlines()
if not RL or RL[0].strip()!=GEN: die('rounds.txt 是空檔或首行不是「'+GEN+'」，中止')
excl=set(); prior=[]; same=None
for l in RL[1:]:
    if not l.strip() or l.startswith('#'): continue
    d,h,p=l.split()
    if d==DATE: same=(h,p); continue
    if d>DATE: continue
    if not os.path.exists(p) or sha(p)!=h: die('先前輪次檔 sha256 不符或不存在，中止：'+p)
    excl|=set(json.load(open(p))['sample_days']); prior.append(p)
if DATE>'20261028' and not prior: die('找不到先前輪次，中止')

# 公開版：歷史題庫與歷史收盤檔不隨 repo 附上，路徑由參數給（預設 data/，相對於本檔）。
# FinMind 歷史收盤（TaiwanStockPrice，data_id=TAIEX，JSON 含 data[].date/open/close）須由使用者自行依 FinMind 條款取得，僅限非商業用途；
# 本程式只讀它當歷史題庫、交易日曆與舊日收盤核對，當天數字不用它。缺檔即明確訊息 exit 2。
PRED=a.pred; TAIEX=a.taiex_json
for _p,_n in ((PRED,'--pred 題庫 predictions.jsonl'),(TAIEX,'--taiex-json 歷史收盤（使用者自行取得）')):
    if not os.path.exists(_p): die(f'找不到 {_n}：{_p}，中止')
def mcnemar(b,c):  # 確切 McNemar 單尾 P(X>=b|n=b+c,p=.5)、雙尾
    n=b+c
    if n==0: return 1.0,1.0
    sf=sum(math.comb(n,i) for i in range(b,n+1))/2**n
    lo=sum(math.comb(n,i) for i in range(0,min(b,c)+1))/2**n
    return min(1,sf),min(1,2*lo)
px=json.load(open(TAIEX))['data']; days=[d['date'] for d in px]; ix={d:i for i,d in enumerate(days)}
cl={d['date']:d['close'] for d in px}; op={d['date']:d['open'] for d in px}
def act(d):
    i=ix[d]; x=cl[d]-cl[days[i-1]]; return '漲' if x>0 else ('跌' if x<0 else '平')
def oc(d):
    x=cl[d]-op[d]; return '漲' if x>0 else ('跌' if x<0 else '平')
rows={}
for l in open(PRED):
    d=json.loads(l)
    if d.get('kind')=='call' and d.get('rule') in ('P4','P5') and d['call'] in ('漲','跌'):
        rows.setdefault(d['rule'],{})[d['trading_day']]=d
common=sorted(set(rows['P4'])&set(rows['P5']))
pool=[d for d in common if d not in excl]
if len(pool)<N: die(f'題庫剩 {len(pool)} 天不足 {N}，中止')
# B8／B9：當天資料（抽樣與寫檔之前）。任一來源失敗 exit 2；休市或未公布 exit 3；都不抽題、不追加 rounds。
class Closed(Exception): pass
SRC=os.path.join(a.outdir,f'src-{DATE}')
def fetch(key,url,data=None):
    f=getattr(a,key+'_file',None)
    try:
        if f: raw=open(f,'rb').read(); at='離線檔 '+f; url='file:'+f
        else:
            raw=urllib.request.urlopen(urllib.request.Request(url,data=data,headers={'User-Agent':'Mozilla/5.0'}),timeout=30).read()
            at=datetime.datetime.now(TPE).isoformat(timespec='seconds')
    except Exception as e: die(f'{key} 抓取失敗：{type(e).__name__}: {e}')
    return raw,{'endpoint':url,'fetched_at_tpe':at,'sha256':hashlib.sha256(raw).hexdigest()}
NOTREADY=('查詢日期大於','很抱歉','沒有符合')
def twse(raw,key):
    try: d=json.loads(raw)
    except Exception as e: die(f'{key} 解析失敗：{e}')
    st=d.get('stat')
    if st is None or any(x in str(st) for x in NOTREADY): raise Closed(f'{key} stat={st}')
    if st!='OK': die(f'{key} stat 不是 OK：{st}')
    rd=str(d.get('date') or d.get('params',{}).get('date') or d.get('params',{}).get('dayDate') or '')
    if rd!=DATE: die(f'{key} 回應日期 {rd}≠{DATE}，中止')
    roc=f'{int(DATE[:4])-1911}年{DATE[4:6]}月{DATE[6:]}日'
    titles=[d.get('title','')]+[t.get('title','') for t in d.get('tables',[])]
    if not any(roc in (t or '') for t in titles): die(f'{key} 表題沒有 {roc}，中止')
    return d
live={}; raws={}
try:
    q=urllib.parse.urlencode
    raw,m=fetch('mi',f'{a.twse_base}/rwd/zh/afterTrading/MI_INDEX?'+q({'date':DATE,'type':'IND','response':'json'})); raws['MI_INDEX']=raw
    d=twse(raw,'MI_INDEX'); r=[x for t in d.get('tables',[]) for x in t.get('data',[]) if x and x[0]=='發行量加權股價指數']
    if not r: raise Closed('MI_INDEX 無加權指數列')
    sign=-1 if '-' in r[0][2] and '+' not in r[0][2] else 1
    m.update(close=float(r[0][1].replace(',','')),change=sign*float(r[0][3].replace(',',''))); live['MI_INDEX']=m
    raw,m=fetch('bfi',f'{a.twse_base}/rwd/zh/fund/BFI82U?'+q({'dayDate':DATE,'type':'day','response':'json'})); raws['BFI82U']=raw
    d=twse(raw,'BFI82U'); rr={x[0]:int(x[3].replace(',','')) for x in d.get('data',[])}
    if '外資及陸資(不含外資自營商)' not in rr: raise Closed('BFI82U 無外資列')
    m.update(fii_spot_net=rr['外資及陸資(不含外資自營商)']+rr.get('外資自營商',0)); live['BFI82U']=m
    ds=f'{DATE[:4]}/{DATE[4:6]}/{DATE[6:]}'
    raw,m=fetch('tf',f'{a.taifex_base}/cht/3/futContractsDateDown',q({'queryStartDate':ds,'queryEndDate':ds,'commodityId':'TXF'}).encode()); raws['TAIFEX-futContractsDate-TXF']=raw
    m['endpoint']+=f' POST queryStartDate=queryEndDate={ds}&commodityId=TXF'
    try: txt=raw.decode('cp950')
    except Exception as e: die(f'期交所解析失敗：{e}')
    if not txt.startswith('日期,'): 
        if not txt.strip() or '查無資料' in txt: raise Closed('期交所查無資料')
        die('期交所回應不是 CSV，中止')
    rs=[x for x in csv.reader(io.StringIO(txt)) if x and x[0]!='日期']
    if not rs: raise Closed('期交所只有表頭')
    if any(x[0]!=ds for x in rs): die(f'期交所回應日期 {sorted(set(x[0] for x in rs))}≠{ds}，中止')
    f_=[x for x in rs if x[1]=='臺股期貨' and x[2]=='外資及陸資']
    if not f_: die('期交所無臺股期貨外資列，中止')
    m.update(fii_fut_net_deal=int(f_[0][7]),fii_fut_net_oi=int(f_[0][13])); live['TAIFEX']=m
except Closed as e: die(f'{DATE} 非交易日或資料未公布（{e}），本輪不跑、不追加 rounds',3)
# 原始回應：原子寫入；已存在且 sha 不同就中止
os.makedirs(SRC,exist_ok=True)
for k,raw in raws.items():
    p=os.path.join(SRC,f'{k}-{DATE}.raw.txt')
    if os.path.exists(p):
        if open(p,'rb').read()!=raw: die('原始檔已存在且內容不同，中止：'+p)
        continue
    t=p+'.tmp'; f=open(t,'wb'); f.write(raw); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p)
fs,ff=live['BFI82U']['fii_spot_net'],live['TAIFEX']['fii_fut_net_deal']
live['P4_next_call']='漲' if (ff>0 and fs>0) else ('跌' if (ff<0 and fs<0) else '漲')
# B10：P5 用證交所 FMTQIK 月表組收盤序列（凍結 P5 定義需 270 個收盤，故抓到湊滿 270 筆為止，最多 16 個月）
import time, re as _re, glob as _glob
def sd_(x): m_=sum(x)/len(x); return math.sqrt(sum((v-m_)**2 for v in x)/(len(x)-1))
def p5_call(c):  # 與 backtest_v315_r2.py p5_call 相同定義
    if len(c)<270: return None,None
    c=c[-270:]; r=[math.log(c[i]/c[i-1]) for i in range(1,270)]
    sig=[sd_(r[j-19:j+1]) for j in range(19,269)]; last=sig[-1]
    pct=sum(1 for s in sig if s<=last)/250*100; ma60=sum(c[-60:])/60
    return ('跌' if (pct>=80 and c[-1]<ma60) else '漲'),{'sigma20':round(last,6),'pct':round(pct,6),'ma60':round(ma60,6),'close_last':c[-1]}
# 前一輪（rounds 中日期最大且 < DATE）
prevs=sorted((l.split() for l in RL[1:] if l.strip() and not l.startswith('#') and l.split()[0]<DATE),key=lambda x:x[0])
PREV=prevs[-1] if prevs else None
os.makedirs(a.fmtqik_dir,exist_ok=True)
def ym_back(ym,k):
    y,m_=int(ym[:4]),int(ym[4:]); m_-=k
    while m_<=0: m_+=12; y-=1
    return f'{y}{m_:02d}'
def fq_parse(raw,ym):
    try: return _fq_parse(raw,ym)
    except SystemExit: raise
    except Exception as e: die(f'FMTQIK {ym} 格式解析失敗：{type(e).__name__}: {e}，中止')
def _fq_parse(raw,ym):
    try: d=json.loads(raw)
    except Exception as e: die(f'FMTQIK {ym} 解析失敗：{e}')
    if d.get('stat')!='OK': die(f'FMTQIK {ym} stat 不是 OK：{d.get("stat")}')
    if len(d.get('fields',[]))!=6 or '加權' not in str(d['fields'][4]): die(f'FMTQIK {ym} fields 不是 6 欄或第 5 欄非加權指數：{d.get("fields")}，中止')
    rows_=[]
    for x in d.get('data',[]):
        if len(x)!=6: die(f'FMTQIK {ym} 有列長度 {len(x)}≠6：{x}，中止')
        y,mm,dd=x[0].split('/'); g=f'{int(y)+1911}{mm}{dd}'
        if g[:6]!=ym: die(f'FMTQIK {ym} 有不屬當月的日期 {g}，中止')
        rows_.append((g,float(x[4].replace(',',''))))
    if not rows_: die(f'FMTQIK {ym} 無資料，中止')
    return rows_
mi_c=live['MI_INDEX']['close']; imp=round(mi_c-live['MI_INDEX']['change'],2)
# 前幾輪（已驗 sha）的 MI_INDEX 收盤與漲跌：B15 月尾完整、B17 數值交叉核對用
RND={}
for l in RL[1:]:
    if not l.strip() or l.startswith('#'): continue
    d_,h_,p_=l.split()
    if d_>=DATE: continue
    if not os.path.exists(p_) or sha(p_)!=h_: die('前輪輸出 sha 不符或不存在，中止：'+p_)
    mi_=json.load(open(p_)).get('live',{}).get('MI_INDEX',{})
    if mi_.get('close') is None or mi_.get('change') is None: die(f'前輪 {d_} 無 MI_INDEX 收盤／漲跌，中止')
    RND[d_]=(float(mi_['close']),float(mi_['change']))
FMC={x['date'].replace('-',''):float(x['close']) for x in px}   # FinMind 只當日曆與舊日收盤核對，不進輸出
calmax=max(FMC)
def mend(ym):
    import calendar as _c; return ym+f'{_c.monthrange(int(ym[:4]),int(ym[4:]))[1]:02d}'
MANF=os.path.join(a.fmtqik_dir,'SHA256SUMS')
MAN={l.split()[1]:l.split()[0] for l in open(MANF) if l.strip()} if os.path.exists(MANF) else {}
fq_writes={}; nfetch=[0]
def get_month(ym,force):
    cache=sorted(_glob.glob(os.path.join(a.fmtqik_dir,f'FMTQIK-{ym}-asof-*.raw.txt')))
    if cache and not force:
        use=cache[-1]; raw=open(use,'rb').read()
        # B17：自己抓過的月表 sha 記在 fmtqik/SHA256SUMS，之後每次讀都驗；不符即竄改，exit 2（不重抓蓋過）
        exp_=MAN.get(os.path.basename(use))
        if exp_ and exp_!=hashlib.sha256(raw).hexdigest(): die(f'FMTQIK 快取 {os.path.basename(use)} sha 與 fmtqik/SHA256SUMS 不符（疑遭竄改），中止')
        meta={'month':ym,'endpoint':'file:'+use,'fetched_at_tpe':'快取／離線檔','sha256':hashlib.sha256(raw).hexdigest()}
    elif ym in fq_writes:  # 本次已抓過，不重複打網路
        use=None; raw=fq_writes[ym][0]; meta={'month':ym,'endpoint':'本次已抓','fetched_at_tpe':'同上','sha256':hashlib.sha256(raw).hexdigest(),'refetched':True}
    else:
        if nfetch[0]: time.sleep(3)  # 放慢頻率
        nfetch[0]+=1; use=None
        raw,meta=fetch('fq',f'{a.twse_base}/rwd/zh/afterTrading/FMTQIK?'+urllib.parse.urlencode({'date':ym+'01','response':'json'}))
        meta['month']=ym; meta['refetched']=bool(force)
    rr=fq_parse(raw,ym)
    if use and use.rsplit('asof-',1)[1][:8]!=rr[-1][0]: die(f'FMTQIK {ym} 檔名 asof 與最後一筆 {rr[-1][0]} 不符，中止')
    if not use: fq_writes[ym]=(raw,rr[-1][0])
    # B14/B15：FinMind 日曆只比它涵蓋到的日子；日曆涵蓋到月底的月份，最後一筆須是月底最後交易日
    cal={d_ for d_ in FMC if d_[:6]==ym}; got={d_ for d_,_ in rr if d_<=calmax}
    if got!=cal: die(f'FMTQIK {ym} 交易日與日曆不符（缺 {sorted(cal-got)[:5]}，多 {sorted(got-cal)[:5]}），中止')
    if ym<DATE[:6] and mend(ym)<=calmax and rr[-1][0]!=max(cal): die(f'FMTQIK {ym} 最後一筆 {rr[-1][0]} ≠ 該月最後交易日 {max(cal)}（月表被截斷），中止')
    # B17：與 FinMind 重疊日收盤逐筆比
    bad=[(d_,c_,FMC[d_]) for d_,c_ in rr if d_ in FMC and abs(c_-FMC[d_])>0.005]
    if bad: die(f'FMTQIK {ym} 收盤與歷史收盤不符 {bad[:3]}，中止')
    meta['n']=len(rr); meta['last']=rr[-1][0]
    return rr,meta
def build(force_months):
    fq={}; fq_meta=[]
    for k in range(16):
        ym=ym_back(DATE[:6],k)
        rr,meta=get_month(ym,ym in force_months); fq.update(dict(rr)); fq_meta.append(meta)
        if sum(1 for d_ in fq if d_<DATE)>=270: break
    fill={d_:RND[d_][0] for d_ in RND if d_ not in fq and d_>max(fq)}
    fq.update(fill)
    before=sorted(d_ for d_ in fq if d_<DATE)
    ok=bool(before) and abs(imp-fq[before[-1]])<=0.005
    return fq,fq_meta,fill,before,ok
# (b) 本月（DATE 前最近平日所在月）快取 asof 早於 DATE 前最近一個平日 → 一律重抓
_d=datetime.date(int(DATE[:4]),int(DATE[4:6]),int(DATE[6:]))-datetime.timedelta(days=1)
while _d.weekday()>=5: _d-=datetime.timedelta(days=1)
PREVWK=_d.strftime('%Y%m%d'); stale=set()
for ym_ in sorted({DATE[:6],PREVWK[:6]}):
    c_=sorted(_glob.glob(os.path.join(a.fmtqik_dir,f'FMTQIK-{ym_}-asof-*.raw.txt')))
    if c_ and c_[-1].rsplit('asof-',1)[1][:8]<PREVWK: stale.add(ym_)
if stale: print(f'(b) 快取 asof 早於最近平日 {PREVWK}，重抓 FMTQIK {sorted(stale)}')
fq,fq_meta,fill,before,ok=build(stale)
refetched=[]
if not ok:
    # B16：快取不含所需日期（上一交易日）→ 重抓本月（DATE 為月初時連同上月），新檔另存
    refetched=sorted(set([DATE[:6],ym_back(DATE[:6],1)])-stale)
    print(f'B16：序列前一筆與 MI 隱含昨收 {imp} 不符，重抓 FMTQIK {refetched}')
    fq,fq_meta,fill,before,ok=build(set(refetched)|stale) if refetched else (fq,fq_meta,fill,before,ok)
if not before: die('FMTQIK 序列為空，中止')
if not ok: die(f"FMTQIK 前一日收盤 {fq[before[-1]]}（{before[-1]}）與 MI_INDEX 隱含昨收 {imp} 不符，中止")
# 新抓的月表：原子寫入快取（檔名含 asof，不覆寫舊檔，新檔另存）
for ym,(raw,lastd) in fq_writes.items():
    p=os.path.join(a.fmtqik_dir,f'FMTQIK-{ym}-asof-{lastd}.raw.txt')
    if os.path.exists(p):
        if open(p,'rb').read()!=raw: die('FMTQIK 原始檔已存在且內容不同，中止：'+p)
    else:
        t_=p+'.tmp'; f=open(t_,'wb'); f.write(raw); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t_,p)
    MAN[os.path.basename(p)]=hashlib.sha256(raw).hexdigest()
if fq_writes:
    t_=MANF+'.tmp'; f=open(t_,'w'); f.write(''.join(f'{v}  {k}\n' for k,v in sorted(MAN.items()))); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t_,MANF)
if DATE in fq:
    if abs(fq[DATE]-mi_c)>0.005: die(f'FMTQIK {DATE} 收盤 {fq[DATE]} ≠ MI_INDEX {mi_c}，中止')
    fq_has=True
else: fq_has=False
if PREV and before[-1]<PREV[0]: die(f'FMTQIK 序列最後一天 {before[-1]} 早於前一輪 {PREV[0]}，有缺口，中止')
# B17：月表與前幾輪 MI_INDEX 交叉核對（收盤相等、前一筆＝該輪隱含昨收）
srt=sorted(fq)
for d_,(c_,ch_) in RND.items():
    if d_ not in fq: die(f'前輪 {d_} 不在收盤序列中（月表缺日），中止')
    if abs(fq[d_]-c_)>0.005: die(f'FMTQIK {d_} 收盤 {fq[d_]} ≠ 該輪 MI_INDEX {c_}，中止')
    i_=srt.index(d_)
    if i_>0 and abs(fq[srt[i_-1]]-(c_-ch_))>0.005: die(f'序列 {srt[i_-1]} 收盤 {fq[srt[i_-1]]} ≠ {d_} 輪 MI 隱含昨收 {round(c_-ch_,2)}，中止')
# B15：非當月且超出日曆的月份，用月表＋rounds 驗月尾：下一筆（下月首筆或 DATE）之前不得有 rounds 日期落在月表最後一筆之後
for m_ in fq_meta:
    ym=m_['month']
    if ym<DATE[:6] and mend(ym)>calmax:
        nxt=min([d_ for d_ in fq if d_[:6]>ym]+[DATE])
        gap=[d_ for d_ in RND if m_['last']<d_<nxt and d_[:6]==ym]
        if gap: die(f'FMTQIK {ym} 月尾不完整：最後一筆 {m_["last"]}，但前輪有 {gap}，中止')
series=[fq[d_] for d_ in before]+[mi_c]   # 最後一筆＝當天 MI_INDEX 收盤
c5,info5=p5_call(series)
if c5 is None: die(f'FMTQIK 序列只有 {len(series)} 筆，不足 270，中止')
live['FMTQIK']={'filled_from_rounds':fill,'refetched_months':sorted(set(refetched)|stale),'prev_weekday':PREVWK,'prev_close_implied_by_mi':imp,'continuity_ok':True,'months':fq_meta,'n_series':len(series),'first':before[0],'last_before_date':before[-1],'fmtqik_has_date':fq_has,'last_equals_mi':True}
live['P5_next_call']=c5; live['P5_info']=info5
fs,ff=live['BFI82U']['fii_spot_net'],live['TAIFEX']['fii_fut_net_deal']
live['P4_next_call']='漲' if (ff>0 and fs>0) else ('跌' if (ff<0 and fs<0) else '漲')
# B11：批改前一輪的 next_call
chg=live['MI_INDEX']['change']; actual='漲' if chg>0 else ('跌' if chg<0 else '平')
graded={}; grade_skipped=None
if PREV and PREV[0]!=before[-1]: grade_skipped=f'前一輪 {PREV[0]} ≠ 上一交易日 {before[-1]}'
elif not PREV: grade_skipped='無前一輪'
if PREV and PREV[0]==before[-1]:
    pd_,ph,pp=PREV
    if not os.path.exists(pp) or sha(pp)!=ph: die('前一輪輸出 sha 不符或不存在，中止：'+pp)
    plive=json.load(open(pp)).get('live',{})
    for r in ('P4','P5'):
        cc=plive.get(f'{r}_next_call')
        if cc in ('漲','跌'): graded[r]={'prev_date':pd_,'call':cc,'actual':actual,'hit':cc==actual}
live['scorable']=bool(graded); live['grade_skipped']=grade_skipped
# 相容欄位：證交所資料核對（資料面）；是否有東西可批改看 live.scorable
twse_ok=(live['MI_INDEX'].get('close') is not None) and live['FMTQIK']['last_equals_mi']
twse_check={'date':DATE,'twse_close':mi_c,'scorable':twse_ok,'status':'證交所 MI_INDEX stat=OK、日期相符；當天答案以證交所為準'}

sample=sorted(random.Random(SEED).sample(pool,N))
out={'seed':SEED,'n':N,'pool':len(common),'sample_days':sample,'rules_sha256':EXP,'date':DATE,'excluded_n':len(set(common)&excl),'prior_rounds':prior,'live':live,'twse_check':twse_check,'graded':graded,'grade_skipped':grade_skipped}
buf=io.StringIO(); _r=contextlib.redirect_stdout(buf); _r.__enter__()
def block(name,ds,truth):
    acts=[truth(d) for d in ds]; up=[a=='漲' for a in acts]; res={}
    print(f'### {name}（n={len(ds)}）；天天猜漲 {sum(up)}/{len(ds)}')
    for r in ('P4','P5'):
        hit=[rows[r][d]['call']==a for d,a in zip(ds,acts)]
        b=sum(h and not u for h,u in zip(hit,up)); c=sum(u and not h for h,u in zip(hit,up))
        p1,p2=mcnemar(b,c); res[r]=dict(hit=sum(hit),n=len(ds),up=sum(up),b=b,c=c,p1=p1,p2=p2)
        print(f'- {r}：命中 {sum(hit)}/{len(ds)}={sum(hit)/len(ds)*100:.2f}%；只規則對 {b}、只天天猜漲對 {c}；McNemar 確切單尾 p={p1:.4f}、雙尾 p={p2:.4f}')
    ps=sorted((res[r]['p1'],r) for r in res); run=0
    for i,(p,r) in enumerate(ps): run=max(run,min(1,p*(2-i))); res[r]['holm']=run
    print('- Holm（m=2，單尾）：'+'；'.join(f'{r} {res[r]["holm"]:.4f}' for r in ('P4','P5')))
    return res
print(f'## r6 練習（P4∩P5 {len(common)} 天，扣已抽 {len(set(common)&excl)} 天後抽，seed={SEED}）')
out['main']=block('主對照 call[d] 對 actual[d]',sample,act)
nxt=[d for d in sample if ix[d]+1<len(days)]
out['lag1']=block('輸入延後一天 call[d] 對 actual[d+1]',nxt,lambda d:act(days[ix[d]+1]))
# 為了 McNemar 配對，延後/探針的天天猜漲用同一個被比對日
out['probe']=block('洩漏探針 call[d] 對 actual[d−1]',sample,lambda d:act(days[ix[d]-1]))
out['oc']=block('開盤後到收盤 call[d] 對 sign(close−open)[d]',sample,oc)
out['pool_main']=block('全池主對照（2041 天）',common,act)
out['pool_oc']=block('全池開盤到收盤',common,oc)

print(f"### 當天證交所／期交所 {DATE}：加權收 {live['MI_INDEX']['close']}（{live['MI_INDEX']['change']:+}）；外資現貨淨買 {fs}；外資台指期淨交易口數 {ff}、淨未平倉 {live['TAIFEX']['fii_fut_net_oi']}；P4 對下一日 {live['P4_next_call']}、P5 對下一日 {live['P5_next_call']}（σ20 百分位 {live['P5_info']['pct']}、MA60 {live['P5_info']['ma60']}）；批改前一輪：{graded or '無前一輪，不計分'}")
_r.__exit__(None,None,None); md=buf.getvalue(); print(md,end='')
# B6：寫檔（冪等）＋原子追加 rounds.txt
jb=json.dumps(out,ensure_ascii=False,indent=1).encode()
def put(p,b):
    if os.path.exists(p):
        if open(p,'rb').read()!=b: die('輸出檔已存在且內容不同，中止：'+p)
        # 內容相同：原子重寫同一份位元組（sha 不變，只更新 mtime），不算覆寫
    t=p+'.tmp'; f=open(t,'wb'); f.write(b); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p); return 'new'
# 證交所抓取時間每次不同，json 冪等只比樣本與結果：去掉抓取時間後比
def core(b):
    d=json.loads(b)
    for v in d.get('live',{}).values():
        if isinstance(v,dict): v.pop('fetched_at_tpe',None)
    fq_=d.get('live',{}).get('FMTQIK',{}); fq_.pop('refetched_months',None)
    for m_ in fq_.get('months',[]):
        for k_ in ('endpoint','fetched_at_tpe','refetched'): m_.pop(k_,None)
    return d
if os.path.exists(OUTJ) and core(open(OUTJ,'rb').read())==core(jb): jb=open(OUTJ,'rb').read(); md=open(OUTM).read() if os.path.exists(OUTM) else md
put(OUTJ,jb); put(OUTM,md.encode()); h=sha(OUTJ)
if graded:
    GL=a.graded_log; old=open(GL).read().splitlines() if os.path.exists(GL) else []
    new_=[json.dumps(dict(date=DATE,rule=r,**v),ensure_ascii=False,sort_keys=True) for r,v in sorted(graded.items())]
    have=[l for l in old if json.loads(l).get('date')==DATE]
    if have:
        if sorted(have)!=sorted(new_): die(f'graded-log 已有 {DATE} 但內容不同，中止')
        print('graded-log 已有同內容，跳過')
    else:
        t_=GL+'.tmp'; f=open(t_,'w'); f.write(''.join(l+'\n' for l in old+new_)); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t_,GL)
        print('graded-log 已追加')
    print('graded-log sha256：'+sha(GL))
if same:
    if same[0]!=h: die(f'rounds.txt 已有 {DATE} 但 sha 不同，中止')
    print('rounds.txt 已有同 sha，跳過')
else:
    t=a.rounds+'.tmp'; f=open(t,'w'); f.write('\n'.join(RL)+f'\n{DATE} {h} {os.path.abspath(OUTJ)}\n'); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,a.rounds)
    print('rounds.txt 已追加')
