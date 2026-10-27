#!/usr/bin/env python3
# 公開版說明：本檔複製自內部 rigor v3.1.2b 程式（內部 sha256 424cd5073fd52d1f4463435a44424d0ebb369b60c3871547d4b9d5261f68dc3c），
# 只換人名、內部路徑、內部文件代號、個股帳本名、User-Agent 與寫帳資料夾防呆，判定邏輯未改。資料來源與授權見 repo 的 NOTICE.md。
"""shadow_run.py — 每天存授權快照＋5 條凍結規則（frozen-rules-v1）對照預測。只練功，不計分。

用法：
  python3 shadow_run.py pre  [--date YYYYMMDD]   # 開盤前：下載快照、算 P1–P5、追加 call 行
  python3 shadow_run.py post [--date YYYYMMDD]   # 收盤後：下載快照、追加 outcome 行（與 late_input 行）
  python3 shadow_run.py notrun --date YYYYMMDD --reason "..."   # 沒跑成的日子留紀錄
  python3 shadow_run.py verify                     # 檢查 shadow-calls.jsonl 的 sha 鏈
共同參數：
  --root DIR        預設 .（目前資料夾；乾跑請指到暫存資料夾）
  --dry-run         不檢查 08:45 截止時間；行內加 "dry_run": true
防偷看依 rigor-rules v3：抓取一律走 fetch_anchor.py（收完回應後才記取得時間、存 HTTP Date 標頭、原始 sha256、快照檔），
每項輸入寫進 inputs_meta（fetched_at_tpe、server_date_http、snapshot_path、snapshot_sha256、coverage_end_tpe）。
截止時間 08:45（對齊台指期開盤）。v2 備份 shadow_run_v2.py。
v3.1（v3 唯讀備份 shadow_run_v3.py）：每行自動加 commit_nonce（A1）；拒寫 kind=anchor（A2/A6）；DG11319 的 inputs_meta 記 ref_snapshots（B2）。
規則定義只在 frozen-rules-v1-public.md（公開版）；本程式啟動時核對它的 sha256，不符就拒跑。
資料只用 data.gov.tw 資料集列出的下載網址（逐字白名單）。不碰正式帳資料夾 ledgers/。
"""
import argparse, csv, datetime as dt, hashlib, io, json, math, os, secrets, sys, time, urllib.request

def _rb(p):
    """讀整個檔（bytes），讀完就關（v3.1.1 修 ResourceWarning）。"""
    with open(p, "rb") as f: return f.read()

def _rt(p, enc="utf-8"):
    """讀整個檔（文字），讀完就關。"""
    with open(p, encoding=enc) as f: return f.read()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_anchor as FA
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")
RULES_VERSION = "v1"
FROZEN_DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frozen-rules-v1-public.md")
FROZEN_SHA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frozen-rules-v1-public.sha256")

# 逐字取自 data.gov.tw 資料集 API 的 resourceDownloadUrl（2026-10-23 查）
SOURCES = {
    "DG11672": "https://www.twse.com.tw/exchangeReport/FMTQIK?response=open_data",          # 加權當月每日收盤
    "DG11555": "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=open_data",        # 加權當日收盤（交叉核對）
    "DG11319": "https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name=DailyMarketReportFut",  # 期貨每日行情（含盤後）
    "DG11596": "https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name=MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate",  # 期貨三大法人
    "DG11655": "https://www.twse.com.tw/fund/MI_QFIIS_cat?response=open_data",              # 外資及陸資投資類股比率（P4 現貨代理）
    "DG11761": "https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=open_data", # 休市日曆
}
PRE_SOURCES = ["DG11672", "DG11319", "DG11596", "DG11655", "DG11761"]
POST_SOURCES = ["DG11672", "DG11555", "DG11319", "DG11596", "DG11655"]

# ---- 凍結參數（與 frozen-rules-v1.md 一致；改了就是 v2）----
P2_DROP_PCT = -0.50       # 夜盤跌幅 ≤ -0.50% 才猜跌
P5_VOL_WIN, P5_PCT_WIN, P5_PCT_TH, P5_MA_WIN = 20, 250, 80.0, 60
PRE_DEADLINE = dt.time(8, 45)   # rigor-rules v3 R2：對齊台指期 08:45 開盤
RIGOR = "v3"          # 主版號（v3.1 是 v3 的修訂，判法見 rigor-rules.md「v3.1」節）
RIGOR_REV = "v3.1"

def now_tpe():
    return dt.datetime.now(TPE)

def iso(t):
    return t.isoformat(timespec="seconds")

def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()

def check_frozen():
    body = _rb(FROZEN_DOC)
    want = _rt(FROZEN_SHA_FILE).split()[0]
    got = sha256_bytes(body)
    if got != want:
        sys.exit(f"拒跑：{FROZEN_DOC} sha256={got} 與 {FROZEN_SHA_FILE}={want} 不符（規則被改過？要改請開 v2）")
    return got

# ---------------- 下載與快照 ----------------
def fetch(url, tries=3, timeout=90):
    """相容舊介面；實際抓取在 fetch_anchor.fetch（收完才記時間、帶 Date 標頭）。"""
    r = FA.fetch(url, tries, timeout)
    return r["status"], r["body"], r["error"], r["server_date_http"]

def prior_11319_paths(root, day):
    """D1 自核用（v3.1）：snapshots/ 下交易日早於 day 的所有 DG11319 快照路徑；路徑和 sha256 會記進 inputs_meta.ref_snapshots。"""
    return [p for p in _prior_11319(root, day)]

def _prior_11319(root, day):
    sd = os.path.join(root, "snapshots")
    if os.path.isdir(sd):
        for d in sorted(x for x in os.listdir(sd) if x < day):
            for fn in sorted(os.listdir(os.path.join(sd, d))):
                if "-DG11319-" in fn and fn.endswith(".csv"):
                    yield os.path.join(sd, d, fn)

def prior_11319_bodies(root, day):
    """D1 自核用：snapshots/ 下交易日早於 day 的所有 DG11319 快照。"""
    out = []
    sd = os.path.join(root, "snapshots")
    if os.path.isdir(sd):
        for d in sorted(x for x in os.listdir(sd) if x < day):
            for fn in sorted(os.listdir(os.path.join(sd, d))):
                if "-DG11319-" in fn and fn.endswith(".csv"):
                    out.append(_rb(os.path.join(sd, d, fn)))
    return out

def snapshot(root, day, phase, keys):
    d = os.path.join(root, "snapshots", day)
    os.makedirs(d, exist_ok=True)
    n, manifest_path = 1, os.path.join(d, f"manifest-{phase}.json")
    while os.path.exists(manifest_path):  # 快照不覆寫；重跑另存 manifest-{phase}-N.json
        n += 1
        manifest_path = os.path.join(d, f"manifest-{phase}-{n}.json")
    entries, files = [], {}
    refs = prior_11319_paths(root, day)
    for k in keys:
        meta, body = FA.snapshot_one(SOURCES[k], k, d, phase, k, None, (), root, refs if k == "DG11319" else ())
        entries.append(dict(meta))
        files[k] = dict(meta)
        files[k].update({"path": os.path.join(root, meta["snapshot_path"]), "sha256": meta["snapshot_sha256"],
                         "body": body, "downloaded_at_tpe": meta["fetched_at_tpe"],
                         "latest_data_date": latest_date_in(body)})
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"trading_day": day, "phase": phase, "rules_version": RULES_VERSION, "rigor_rules": RIGOR,
                   "written_at_tpe": iso(now_tpe()), "files": entries}, f, ensure_ascii=False, indent=1)
    return files

def latest_date_in(body):
    """檔內「日期」欄的最大值（沒有日期欄回 None），用來證明預測當下資料最新到哪天。"""
    try:
        ds = [r.get("日期", "").strip() for r in rows(body)]
        ds = [d for d in ds if d]
        return max(ds) if ds else None
    except Exception:
        return None

ymd_of = FA.ymd_of
coverage_end = FA.coverage_end
rows = FA.rows

def num(s):
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None

def roc_to_ymd(s):  # "1151022" -> "20261022"
    s = s.strip()
    return f"{int(s[:-4]) + 1911}{s[-4:]}"

# ---------------- 帳（只能追加，sha 鏈）----------------
def ledger_path(root):
    return os.path.join(root, "shadow-calls.jsonl")

def read_ledger(root):
    p = ledger_path(root)
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in _rt(p).splitlines(True) if l.strip()]

def canon(rec):
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def append(root, recs):
    p = ledger_path(root)
    led = read_ledger(root)
    prev = led[-1]["sha256"] if led else "0" * 64
    with open(p, "a", encoding="utf-8") as f:
        for r in recs:
            r = dict(r)
            if str(r.get("kind", "")) == "anchor":
                sys.exit("拒寫：錨點不准寫進預測帳本（rigor v3.1 A2/A6）")
            r["commit_nonce"] = secrets.token_hex(32)   # rigor v3.1 A1：每行 256 位元隨機數，程式產生
            r["prev_sha256"] = prev
            r["sha256"] = hashlib.sha256(canon(r).encode()).hexdigest()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            prev = r["sha256"]

def verify(root):
    prev = "0" * 64
    for i, r in enumerate(read_ledger(root), 1):
        s = r.pop("sha256")
        if r.get("prev_sha256") != prev or hashlib.sha256(canon(r).encode()).hexdigest() != s:
            print(f"鏈斷在第 {i} 行"); return False
        prev = s
    print("sha 鏈完整"); return True

# ---------------- 資料整理 ----------------
def taiex_history(root, extra_bodies=()):
    """把 snapshots/ 下所有 DG11672 檔（加上本次下載）合成 {YYYYMMDD: (close, chg)}；同日不同值記衝突。"""
    hist, conflicts = {}, []
    bodies = []
    sd = os.path.join(root, "snapshots")
    if os.path.isdir(sd):
        for day in sorted(os.listdir(sd)):
            for fn in sorted(os.listdir(os.path.join(sd, day))):
                if "-DG11672-" in fn:
                    bodies.append(_rb(os.path.join(sd, day, fn)))
    bodies += list(extra_bodies)
    for b in bodies:
        for r in rows(b):
            try:
                d = roc_to_ymd(r["日期"])
            except Exception:
                continue
            c, ch = num(r.get("發行量加權股價指數")), num(r.get("漲跌點數"))
            if c is None:
                continue
            if d in hist and abs(hist[d][0] - c) > 1e-9:
                conflicts.append(d)
            hist[d] = (c, ch)
    return hist, conflicts

def holidays(body):
    out = set()
    for r in rows(body):
        name = r.get("名稱", "")
        if "開始交易" in name or "最後交易" in name:
            continue
        try:
            out.add(roc_to_ymd(r["日期"]))
        except Exception:
            pass
    return out

def tx_like_night(rows_11319, code, day):
    """回傳 (近月到期月份, 漲跌價, 漲跌%) 或 None：日期==day、契約==code、交易時段==盤後、到期月份 6 碼、漲跌價為數字，取到期月份最小者。"""
    cands = []
    for r in rows_11319:
        if r.get("日期", "").strip() != day or r.get("契約代號", "").strip() != code:
            continue
        if r.get("交易時段", "").strip() != "盤後":
            continue
        m = r.get("到期月份(週別)", "").strip()
        if not (len(m) == 6 and m.isdigit()):
            continue
        chg, pct = num(r.get("漲跌價")), num(r.get("漲跌%"))
        if chg is None or pct is None:
            continue
        cands.append((m, chg, pct))
    return min(cands) if cands else None

def foreign_hold_total(body):
    tot, n = 0, 0
    for r in rows(body):
        if r.get("產業別", "").strip() == "ETF":
            continue
        v = num(r.get("僑外資及陸資持有總股數"))
        if v is None:
            return None
        tot += v; n += 1
    return int(tot) if n else None

def prev_11655(root, day):
    """找 snapshots/ 下、交易日早於 day 的最近一份 pre 或 post DG11655 檔。"""
    sd = os.path.join(root, "snapshots")
    if not os.path.isdir(sd):
        return None
    for d in sorted([x for x in os.listdir(sd) if x < day], reverse=True):
        fs = sorted([f for f in os.listdir(os.path.join(sd, d)) if "-DG11655-" in f], reverse=True)
        for fn in fs:
            p = os.path.join(sd, d, fn)
            b = _rb(p)
            if foreign_hold_total(b) is not None:
                return p, sha256_bytes(b), foreign_hold_total(b)
    return None

# ---------------- 五條規則 ----------------
def rule_calls(root, day, files, hist, prev_day):
    out = []
    r11319 = rows(files["DG11319"]["body"]) if files["DG11319"]["ok"] else []
    sha = {k: v["sha256"] for k, v in files.items()}
    latest_11319 = max((r.get("日期", "") for r in r11319), default="")

    # P1 夜盤方向
    tx = tx_like_night(r11319, "TX", day)
    if tx:
        call = "漲" if tx[1] >= 0 else "跌"
        out.append(("P1", call, {"TX_month": tx[0], "TX_night_chg_pts": tx[1], "TX_night_chg_pct": tx[2]}, ["DG11319"], None))
    else:
        out.append(("P1", "missing", {"DG11319_latest_date": latest_11319}, ["DG11319"],
                    f"DG11319 沒有日期={day} 的 TX 盤後列（最新日期 {latest_11319 or '無'}）"))
    # P2 夜盤跌幅 ≤ -0.50% 才猜跌
    if tx:
        call = "跌" if tx[2] <= P2_DROP_PCT else "漲"
        out.append(("P2", call, {"TX_month": tx[0], "TX_night_chg_pct": tx[2], "threshold_pct": P2_DROP_PCT}, ["DG11319"], None))
    else:
        out.append(("P2", "missing", {"DG11319_latest_date": latest_11319}, ["DG11319"], "同 P1"))
    # P3 費半期夜盤，沒有就那斯達克 100 期
    us = None
    for code in ("SXF", "UNF"):
        x = tx_like_night(r11319, code, day)
        if x:
            us = (code,) + x; break
    if us:
        call = "漲" if us[3] > 0 else "跌"
        out.append(("P3", call, {"contract": us[0], "month": us[1], "night_chg_pct": us[3]}, ["DG11319"], None))
    else:
        out.append(("P3", "missing", {"DG11319_latest_date": latest_11319}, ["DG11319"],
                    f"DG11319 沒有日期={day} 的 SXF/UNF 盤後列"))
    # P4 外資期貨淨交易口數（D-1）＋ 外資類股總股數變化（代理）
    reason, inp = None, {"prev_trading_day": prev_day}
    fut = None
    if files["DG11596"]["ok"] and prev_day:
        for r in rows(files["DG11596"]["body"]):
            if r.get("日期", "").strip() == prev_day and r.get("商品名稱", "").strip() == "臺股期貨" \
               and r.get("身份別", "").strip() == "外資及陸資":
                fut = num(r.get("多空交易口數淨額"))
    inp["TX_foreign_net_trade_contracts"] = fut
    cur_hold = foreign_hold_total(files["DG11655"]["body"]) if files["DG11655"]["ok"] else None
    pv = prev_11655(root, day)
    dh = None
    if cur_hold is not None and pv is not None:
        dh = cur_hold - pv[2]
        inp.update({"foreign_hold_shares_now": cur_hold, "foreign_hold_shares_prev": pv[2],
                    "prev_DG11655_file": os.path.relpath(pv[0], root), "prev_DG11655_sha256": pv[1]})
    inp["foreign_hold_shares_delta"] = dh
    if fut is None:
        reason = f"DG11596 沒有日期={prev_day} 的臺股期貨外資列"
    elif cur_hold is None or pv is None:
        reason = "DG11655 本次或前一份快照缺"
    elif dh == 0:
        reason = "DG11655 類股總股數與前一份完全相同，判定未更新（stale）"
    if reason:
        out.append(("P4", "missing", inp, ["DG11596", "DG11655"], reason))
    else:
        if fut > 0 and dh > 0:
            call = "漲"
        elif fut < 0 and dh < 0:
            call = "跌"
        else:
            call = "漲"
        out.append(("P4", call, inp, ["DG11596", "DG11655"], None))
    # P5 高波動＋跌破 60 日均才猜跌
    ds = sorted(d for d in hist if d < day)
    closes = [hist[d][0] for d in ds]
    need = P5_VOL_WIN + P5_PCT_WIN  # 270 個收盤
    inp = {"prev_trading_day": prev_day, "closes_available": len(closes), "closes_needed": need,
           "history_first": ds[0] if ds else None}
    if not ds or ds[-1] != prev_day or len(closes) < need:
        out.append(("P5", "missing", inp, ["DG11672"], f"加權收盤歷史只有 {len(closes)} 筆，要 {need} 筆（20 日波動＋250 日百分位）"))
    else:
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        vols = []
        for i in range(P5_VOL_WIN, len(rets) + 1):
            w = rets[i - P5_VOL_WIN:i]
            m = sum(w) / len(w)
            vols.append(math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1)))
        win = vols[-P5_PCT_WIN:]
        pct = 100.0 * sum(1 for v in win if v <= win[-1]) / len(win)
        ma = sum(closes[-P5_MA_WIN:]) / P5_MA_WIN
        call = "跌" if (pct >= P5_PCT_TH and closes[-1] < ma) else "漲"
        inp.update({"vol20": vols[-1], "vol20_pctile_250": pct, "ma60": ma, "close_prev": closes[-1]})
        out.append(("P5", call, inp, ["DG11672"], None))
    return out, sha

# ---------------- 指令 ----------------
def prev_trading_day(hist, day):
    ds = [d for d in hist if d < day]
    return max(ds) if ds else None

META_KEYS = ("name","dataset","url","http_status","request_started_tpe","fetched_at_tpe","server_date_http","server_date_tpe","snapshot_path","snapshot_sha256","coverage_end_tpe","coverage_basis","tool","ref_snapshots")

def cmd_pre(a):
    frozen_sha = check_frozen()
    t = now_tpe()
    day = a.date or t.strftime("%Y%m%d")
    led = read_ledger(a.root)
    if any(r.get("kind") == "call" and r.get("trading_day") == day for r in led):
        sys.exit(f"拒跑：{day} 已有 call 行（只能寫一次）")
    if not a.dry_run and (t.strftime("%Y%m%d") != day or t.time() >= PRE_DEADLINE):
        append(a.root, [{"kind": "not_run", "written_at_tpe": iso(t), "trading_day": day, "status": "not_run",
                         "reason": f"pre 執行時間 {iso(t)} 已過 {day} 08:45 截止，不補寫預測", "rules_version": RULES_VERSION}])
        sys.exit("已過截止，只記 not_run")
    files = snapshot(a.root, day, "pre", PRE_SOURCES)
    wd = dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))
    hol = holidays(files["DG11761"]["body"]) if files["DG11761"]["ok"] else set()
    if wd.weekday() >= 5 or day in hol:
        append(a.root, [{"kind": "not_run", "written_at_tpe": iso(now_tpe()), "trading_day": day, "status": "not_run",
                         "reason": "DG11761 休市日曆或週末：非交易日", "rules_version": RULES_VERSION,
                         "dry_run": a.dry_run}])
        print(f"{day} 非交易日，只記 not_run"); return
    hist, conflicts = taiex_history(a.root, [files["DG11672"]["body"]] if files["DG11672"]["ok"] else [])
    pday = prev_trading_day(hist, day)
    calls, sha = rule_calls(a.root, day, files, hist, pday)
    # rigor-rules v3 連假後首日：前一個平日不是前一交易日 → 標記；判法不放寬，取得與涵蓋時間證明不了一律 missing
    prev_wd = wd - dt.timedelta(days=1)
    while prev_wd.weekday() >= 5:
        prev_wd -= dt.timedelta(days=1)
    post_holiday = bool(pday) and pday != prev_wd.strftime("%Y%m%d")
    recs = []
    for rid, call, inputs, used, why in calls:
        rec = {"kind": "call", "written_at_tpe": iso(now_tpe()), "trading_day": day, "rules_version": RULES_VERSION,
               "frozen_rules_sha256": frozen_sha, "rule": rid, "call": call, "inputs": inputs,
               "snapshot_sha256": {k: sha[k] for k in used}, "prev_trading_day": pday,
               "inputs_downloaded_at_tpe": {k: files[k].get("downloaded_at_tpe") for k in used},
               "inputs_latest_data_date": {k: files[k].get("latest_data_date") for k in used},
               "inputs_coverage_end_tpe": {k: files[k].get("coverage_end_tpe") for k in used},
               "inputs_coverage_basis": {k: files[k].get("coverage_basis") for k in used},
               "inputs_server_date": {k: files[k].get("server_date_http") for k in used},
               "inputs_meta": {k: {x: files[k].get(x) for x in META_KEYS} for k in used},
               "rigor_rules": RIGOR, "post_holiday_first_day": post_holiday}
        if why:
            rec["missing_reason"] = why
        if conflicts:
            rec["history_conflicts"] = conflicts
        if a.dry_run:
            rec["dry_run"] = True
        recs.append(rec)
    append(a.root, recs)
    for rid, call, _, _, why in calls:
        print(f"{day} {rid}: {call}" + (f"（{why}）" if why else ""))

def outcome_for(files, day):
    c11672 = None
    if files["DG11672"]["ok"]:
        for r in rows(files["DG11672"]["body"]):
            if roc_to_ymd(r["日期"]) == day:
                c11672 = (num(r["發行量加權股價指數"]), num(r["漲跌點數"]))
    c11555 = None
    if files.get("DG11555", {}).get("ok"):
        for r in rows(files["DG11555"]["body"]):
            if r.get("指數/報酬指數", "").strip() == "發行量加權股價指數" and roc_to_ymd(r["日期"]) == day:
                pts = num(r["漲跌點數"]); sign = r.get("漲跌(+/-)", "").strip()
                if pts is not None and sign == "-":
                    pts = -pts
                c11555 = (num(r["收盤指數"]), pts)
    return c11672, c11555

def cmd_post(a):
    frozen_sha = check_frozen()
    t = now_tpe()
    day = a.date or t.strftime("%Y%m%d")
    led = read_ledger(a.root)
    if any(r.get("kind") == "outcome" and r.get("trading_day") == day for r in led):
        sys.exit(f"拒跑：{day} 已有 outcome 行")
    files = snapshot(a.root, day, "post", POST_SOURCES)
    c1, c2 = outcome_for(files, day)
    base = {"written_at_tpe": iso(now_tpe()), "trading_day": day, "rules_version": RULES_VERSION, "rigor_rules": RIGOR}
    if a.dry_run:
        base["dry_run"] = True
    if c1 is None and c2 is None:
        append(a.root, [dict(base, kind="not_run", status="not_run",
                             reason="DG11672 與 DG11555 都沒有這天的加權收盤（休市或尚未更新）；可稍後重跑 post，快照另存 manifest-post-N.json")])
        print("沒有收盤資料，只記 not_run"); return
    close, chg = c1 if c1 else c2
    flags = []
    if c1 and c2 and (abs(c1[0] - c2[0]) > 1e-6 or abs(c1[1] - c2[1]) > 1e-6):
        flags.append(f"DG11672 {c1} 與 DG11555 {c2} 不一致，採 DG11672")
    actual = "漲" if chg > 0 else ("跌" if chg < 0 else "平")
    calls = {r["rule"]: r["call"] for r in led if r.get("kind") == "call" and r.get("trading_day") == day}
    per = {}
    for rid in ("P1", "P2", "P3", "P4", "P5"):
        c = calls.get(rid)
        if c is None:
            per[rid] = "no_call_row"
        elif c == "missing":
            per[rid] = "missing"
        else:
            per[rid] = (c == actual)
    rec = dict(base, kind="outcome", taiex_close=close, change_pts=chg, actual=actual, per_rule_correct=per,
               always_up_correct=(actual == "漲"),
               sources={k: {"url": SOURCES[k], "sha256": files[k]["sha256"], "snapshot_path": files[k]["snapshot_path"],
                            "server_date_http": files[k]["server_date_http"], "fetched_at_tpe": files[k]["fetched_at_tpe"],
                            "value": v} for k, v in (("DG11672", c1), ("DG11555", c2))},
               rigor_rules=RIGOR,
               frozen_rules_sha256=frozen_sha)
    if flags:
        rec["flags"] = flags
    recs = [rec]
    # late_input：P1–P3 若事前拿不到，用收盤後才上架的同一批（開盤前就已形成的）夜盤數字補算，只當研究參考
    if files["DG11319"]["ok"]:
        hist = {}
        fake = {"DG11319": files["DG11319"], "DG11596": {"ok": False, "body": b"", "sha256": None},
                "DG11655": {"ok": False, "body": b"", "sha256": None}}
        lc, _ = rule_calls(a.root, day, fake, hist, None)
        for rid, call, inputs, used, why in lc[:3]:
            if per.get(rid) == "missing" and call != "missing":
                recs.append(dict(base, kind="late_input", rule=rid, call=call, inputs=inputs,
                                 snapshot_sha256={"DG11319": files["DG11319"]["sha256"]},
                                 actual=actual, correct=(call == actual),
                                 note="輸入是開盤前就形成的夜盤數字，但授權檔收盤後才拿到；不是事前預測，不算命中率"))
    append(a.root, recs)
    print(f"{day} 加權 {close}（{chg:+}）實際 {actual}；各規則：{per}；天天猜漲 {'對' if actual=='漲' else '錯'}")
    for r in recs[1:]:
        print(f"  late_input {r['rule']}: {r['call']} → {'對' if r['correct'] else '錯'}")

def cmd_notrun(a):
    if not a.date or not a.reason:
        sys.exit("notrun 要 --date 和 --reason")
    append(a.root, [{"kind": "not_run", "written_at_tpe": iso(now_tpe()), "trading_day": a.date,
                     "status": "not_run", "reason": a.reason, "rules_version": RULES_VERSION}])
    print("已記 not_run")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["pre", "post", "notrun", "verify"])
    ap.add_argument("--date")
    ap.add_argument("--reason")
    ap.add_argument("--root", default=".")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if "ledgers" in os.path.realpath(a.root).casefold().split(os.sep):
        sys.exit("拒跑：不准寫進 ledgers/")
    if a.dry_run and os.path.realpath(a.root).casefold() in {os.path.realpath(".").casefold(), os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")).casefold()}:
        sys.exit("乾跑請用 --root 指到另一個暫存資料夾，不要寫正式 shadow-calls.jsonl")
    os.makedirs(a.root, exist_ok=True)
    {"pre": cmd_pre, "post": cmd_post, "notrun": cmd_notrun, "verify": lambda a: verify(a.root)}[a.cmd](a)

if __name__ == "__main__":
    main()
