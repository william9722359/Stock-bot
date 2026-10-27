#!/usr/bin/env python3
# 公開版說明：本檔複製自內部 rigor v3.1.2b 程式（內部 sha256 3f6297d4c7ecfebddb24f9ec6b85570ac1b4f9ea662c4d8989730e175160103a），
# 只換人名、內部路徑、內部文件代號、個股帳本名、User-Agent 與寫帳資料夾防呆，判定邏輯未改。資料來源與授權見 repo 的 NOTICE.md。
"""recompute.py v3.1 — 獨立重算（rigor-rules v3.1 R6/B4；v3 唯讀備份 recompute_v3.py）。
不 import score_prf、shadow_run、fetch_anchor，自己從頭讀原始檔重算。v3.1 起不信紀錄行的自報欄位：
  - 涵蓋時間自己從快照重算（自寫的日期欄解析與 DG11319 夜盤參考價自核），取 max(行內值, 重算值)，重算失敗判缺（B2）。
  - 錨點只認核帳方寫、帶 Events API＋GH Archive＋commit 檔三份快照的 github-push 錨點；時間自己從快照解析，
    行內 external_time 不看（R1/A2/A5/A6）。方案未採用前（ANCHOR_SCHEME_ADOPTED=False）一律判缺錨點（B0）。
  - 休市表必須帶 --calendar-sha256 與官方來源 meta；臨時休市清單要 --closures-sha256，逐筆核官方網域、當日伺服器 Date、
    非寫帳方核准（B3）。--since、--until 必填（N6）。
  - compare 也比對 reasons（每種理由的次數）。
  - 小數秒保留到微秒（和 score_prf 一致，N7）。

用法:
  python3 recompute.py --root R --since YYYYMMDD --until YYYYMMDD --calendar P --calendar-sha256 SHA
                       --outcomes P --outcomes-sha256 SHA [--anchors P] [--closures P --closures-sha256 SHA] [--rules P1,P2]
                       [--dry-run-file] [--compare score.json] files...
  --compare：讀 `score_prf.py --json` 的輸出，逐欄比對；不一致就 exit 1。
  v3.1.2b-r5：--outcomes 必填、檔案必須存在，不再預設讀 root/shadow-calls.jsonl（依審查意見，審查檔未公開）。
  v3.1.2b-r6：--outcomes-sha256 必填、對不上拒算；outcomes 檔名以 shadow 開頭、有一行不是 kind=outcome、帶規則欄、
  或 outcome 行數 0 都拒算；有未配對日時同期基準與 p 為無法計算（None）；起始日前預設規則標籤「試跑」（依審查意見，審查檔未公開）。"""
import argparse, base64, csv, datetime, email.utils, gzip, hashlib, io, json, os, re, sys, urllib.parse
from math import comb

def _rb(p):
    """讀整個檔（bytes），讀完就關（v3.1.1 修 ResourceWarning）。"""
    with open(p, "rb") as f: return f.read()

def _rt(p, enc="utf-8"):
    """讀整個檔（文字），讀完就關。"""
    with open(p, encoding=enc) as f: return f.read()

TW = datetime.timezone(datetime.timedelta(hours=8))   # 台灣沒有日光節約，固定 +08:00
ANCHOR_SCHEME_ADOPTED = False   # v3.1 B0；只能改程式並重發 manifest 才能打開
REPO = "william9722359/Stock-bot"
NOT_VERIFIERS = ("股票機器人", "stock-bot", "stockbot", "stock_bot", "anchor-bot")
# v3.1.2b（B1，獨立實作）：臨時休市核證只認 watch-desk 以外、查證者寫的核證檔；路徑和預期 sha 只從核可者寫的這個檔讀，沒有命令列參數
APPROVALS_SHA_FILE = "approvals/closure-verifications.sha256"   # 公開版：相對於執行時的工作目錄；內部版是固定的絕對路徑
NOT_STARTED = "尚未開始"   # v3.1.2b-r1：即時預測起始日不寫死；first_live_day() 從錨點證據推
DATED_COLS = {"DG11672": "日期", "DG11555": "日期", "DG11319": "日期", "DG11596": "日期",
              "DG11592": "日期", "DG11322": "日期", "DG11339": "日期"}
NO_DATE_SETS = ("DG11655", "DG11761")

def h(path):
    try:
        with open(path, "rb") as fh: return hashlib.sha256(fh.read()).hexdigest()
    except OSError: return None

def when(s):
    """有明確時區的 ISO 時間或 HTTP Date；只寫日期、沒時區一律 None。小數秒保留到微秒（超過 6 位截斷）。"""
    if s is None or s == "": return None
    s = str(s).strip()
    if re.match(r"^\d{4}[-/]?\d{2}[-/]?\d{2}$", s): return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:?\d{2})$", s)
    if m:
        y, mo, d, hh, mi, ss, frac, tz = m.groups()
        if tz == "Z": off = 0
        else:
            sign = 1 if tz[0] == "+" else -1; t = tz[1:].replace(":", "")
            off = sign * (int(t[:2]) * 60 + int(t[2:]))
        us = int((frac or "0")[:6].ljust(6, "0"))
        try:
            return datetime.datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0), us,
                                     tzinfo=datetime.timezone(datetime.timedelta(minutes=off)))
        except ValueError: return None
    if re.search(r"^[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} (GMT|UTC|[+-]\d{4})$", s):
        try:
            t = email.utils.parsedate_to_datetime(s)
            return t if t.tzinfo else None
        except Exception: return None
    return None

def http_only(s):
    s = "" if s is None else str(s).strip()
    if not re.search(r"^[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} (GMT|UTC|[+-]\d{4})$", s): return None
    return when(s)

def iso_only(s):
    v = "" if s is None else str(s).strip()
    return None if http_only(v) else when(v)

def ymd(v):
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    if len(s) == 7: s = str(int(s[:3]) + 1911) + s[3:]
    return s if len(s) == 8 else None

def direction(v):
    v = "" if v is None else str(v).strip()
    return {"漲": "漲", "up": "漲", "UP": "漲", "rise": "漲", "跌": "跌", "down": "跌", "DOWN": "跌", "fall": "跌",
            "平": "平", "flat": "平", "平盤": "平"}.get(v, "missing")

def first(d, keys):
    for k in keys:
        if d.get(k) not in (None, ""): return d[k]
    return None

def table_bytes(b):
    return list(csv.DictReader(io.StringIO(b.decode("utf-8-sig", errors="replace"))))

def table(path):
    with open(path, "rb") as fh: return table_bytes(fh.read())

def path_in(root, p):
    return p if os.path.isabs(p) else os.path.join(root, p)

def official(url, hosts):
    u = urllib.parse.urlparse(str(url or ""))
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == x or host.endswith("." + x) for x in hosts)

def canon(v):
    """v3.1.2（N4，獨立實作）：NFKC→casefold→逐字掃：遇到開括號就略到對應收括號（沒收尾就略到最後），空白與連字號類略過。"""
    import unicodedata
    t = unicodedata.normalize("NFKC", "" if v is None else str(v)).casefold()
    opens, closes = "([【〔〈《「『", ")]】〕〉》」』"
    out, depth = [], 0
    for ch in t:
        if ch in opens: depth += 1; continue
        if ch in closes and depth: depth -= 1; continue
        if depth or ch.isspace() or ch in "-_‐‑‒–—―·・.。,，、:：/\\|": continue
        out.append(ch)
    return "".join(out)

def line_sha(x):
    """原列的 sha256：json.dumps(原列, ensure_ascii=False, sort_keys=True, separators=(',',':')) 的 UTF-8。不做名字正規化（canon() 是名字用的）。"""
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

def verify_records(root):
    if not os.path.isfile(APPROVALS_SHA_FILE): return {}, "核可者的核證 sha 檔不存在（全部待核）"
    first_line = (_rt(APPROVALS_SHA_FILE).splitlines() or [""])[0]
    m = re.match(r"^([0-9a-f]{64})[ \t]+\*?(/.+?)\s*$", first_line)
    if not m: return {}, "核可者的核證 sha 檔格式不對（全部待核）"
    want, path = m.group(1), m.group(2)
    full, base = os.path.realpath(path), os.path.realpath(root)
    if os.path.commonpath([full, base]) == base: return {}, "核證檔在 watch-desk 根目錄裡（寫帳方範圍，全部待核）"
    if not os.path.isfile(path): return {}, "核證檔不存在（全部待核）"
    if h(path) != want: return {}, "核證檔 sha256 與核可者的 sha 檔不符（全部待核）"
    got = {}
    for raw in _rt(path).splitlines():
        if raw.strip() == "": continue
        try: x = json.loads(raw)
        except ValueError: continue
        if not isinstance(x, dict) or ymd(x.get("date")) is None or not isinstance(x.get("row_sha256"), str): continue
        x["__dir"] = os.path.dirname(os.path.abspath(path))
        got[ymd(x.get("date")) + "/" + x["row_sha256"]] = x
    return got, None

def not_verifier(v):
    """空白或正規化後以寫帳方名字開頭／含寫帳方名字 → 不是核帳方。"""
    c = canon(v)
    if c == "": return True
    for w in NOT_VERIFIERS:
        cw = canon(w)
        if c[:len(cw)] == cw or c.find(cw) >= 0: return True
    return False

def lines_with_chain(path):
    out, prev, ok, brk = [], "0" * 64, True, None
    with open(path, encoding="utf-8") as fh:
        n = 0
        for raw in fh:
            if raw.strip() == "": continue
            n += 1
            try:
                rec = json.loads(raw)
                if not isinstance(rec, dict): rec = None
            except ValueError: rec = None
            if ok:
                if rec is None: ok = False
                else:
                    tmp = {k: v for k, v in rec.items() if k != "sha256"}
                    dig = hashlib.sha256(json.dumps(tmp, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    if tmp.get("prev_sha256") != prev or dig != rec.get("sha256"): ok = False
                    else: prev = rec["sha256"]
                if not ok: brk = n
            out.append((n, rec, ok))
    return out, brk

# ---------- 涵蓋時間自己重算（B2）----------
def num(s):
    try: return float(str(s).strip().replace(",", "").replace("%", ""))
    except (TypeError, ValueError): return None

def day_of(s):
    s2 = (s or "").strip().replace("/", "").replace("-", "")
    if not s2.isdigit(): return None
    try:
        if len(s2) == 8: return datetime.date(int(s2[:4]), int(s2[4:6]), int(s2[6:]))
        if len(s2) == 7: return datetime.date(int(s2[:3]) + 1911, int(s2[3:5]), int(s2[5:]))
    except ValueError: return None
    return None

def night_ok(rows, refs):
    k = lambda r: ((r.get("日期") or "").strip(), (r.get("契約代號") or "").strip(), (r.get("到期月份(週別)") or "").strip())
    same = {}
    for r in rows:
        if (r.get("交易時段") or "").strip() == "一般" and num(r.get("最後成交價")) is not None and num(r.get("漲跌價")) is not None:
            same[k(r)] = num(r.get("最後成交價")) - num(r.get("漲跌價"))
    prior = {}
    for b in refs:
        try: rr = table_bytes(b)
        except Exception: continue
        for r in rr:
            if (r.get("交易時段") or "").strip() == "一般" and num(r.get("結算價")) is not None and day_of(r.get("日期")) is not None:
                prior.setdefault(((r.get("契約代號") or "").strip(), (r.get("到期月份(週別)") or "").strip()), []).append((day_of(r.get("日期")), num(r.get("結算價"))))
    need, good = set(), set()
    for r in rows:
        if (r.get("交易時段") or "").strip() != "盤後": continue
        key = k(r); need.add(key[0])
        a, c = num(r.get("最後成交價")), num(r.get("漲跌價"))
        if a is None or c is None: continue
        if key in same: target = same[key]
        else:
            dd = day_of(key[0])
            older = [x for x in prior.get((key[1], key[2]), []) if dd is not None and x[0] < dd]
            if not older: continue
            target = max(older)[1]
        if abs((a - c) - target) > 1e-6: return False
        good.add(key[0])
    return need <= good

def night_began(rows, refs, label):
    """獨立實作（不 import fetch_anchor）：標示 label 的盤後列是哪個交易日開盤的。
    證據＝同契約同到期月、日期早於 label 的一般列結算價恰好等於盤後列的「最後成交價−漲跌價」；多個取最晚；沒有回 None。"""
    pool = {}
    tables = [rows]
    for b in refs:
        try: tables.append(table_bytes(b))
        except Exception: pass
    for t in tables:
        for r in t:
            if (r.get("交易時段") or "").strip() != "一般": continue
            dd, st = day_of(r.get("日期")), num(r.get("結算價"))
            if dd is None or st is None or not dd < label: continue
            pool.setdefault(((r.get("契約代號") or "").strip(), (r.get("到期月份(週別)") or "").strip()), set()).add((dd, st))
    found = []
    for r in rows:
        if (r.get("交易時段") or "").strip() != "盤後" or day_of(r.get("日期")) != label: continue
        a, c = num(r.get("最後成交價")), num(r.get("漲跌價"))
        if a is None or c is None: continue
        found += [dd for dd, st in pool.get(((r.get("契約代號") or "").strip(), (r.get("到期月份(週別)") or "").strip()), ()) if abs(st - (a - c)) <= 1e-6]
    return max(found) if found else None

def own_coverage(ds, body, got, refs):
    """回傳 (重算的涵蓋上界或 None, 是否失敗)。"""
    if ds in NO_DATE_SETS: return got, False
    col = DATED_COLS.get(ds, "日期" if ds is None else None)
    if col is None: return None, True
    try: rows = table_bytes(body)
    except Exception: return (None, ds is not None)
    if not rows or col not in rows[0]: return (None, ds is not None)
    if ds == "DG11319" and "交易時段" not in rows[0]: return None, True
    top, began = None, {}
    for r in rows:
        v = (r.get(col) or "").strip()
        if v == "":
            if all(not (x or "").strip() for x in r.values() if isinstance(x, str)): continue
            return None, True
        dd = day_of(v)
        if dd is None: return None, True
        if ds == "DG11319" and (r.get("交易時段") or "").strip() == "盤後":
            if dd not in began: began[dd] = night_began(rows, refs, dd)
            z = began[dd] + datetime.timedelta(days=1) if began[dd] is not None else dd   # v3.1：開始日次日 05:00
            e = datetime.datetime(z.year, z.month, z.day, 5, 0, tzinfo=TW)
        else:
            e = datetime.datetime(dd.year, dd.month, dd.day, 23, 59, 59, tzinfo=TW)
        if top is None or e > top: top = e
    if top is None: return None, True
    if ds == "DG11319" and not night_ok(rows, refs): return None, True
    if ds is not None and top > got: return None, True
    return top, False

def check_line(rec, day, root, anchored):
    """回傳理由字串（不合格）或 None（合格）。理由文字和 score_prf 對齊，方便逐欄比對。"""
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
    deadline = datetime.datetime(y, m, d, 8, 45, tzinfo=TW)
    wt = iso_only(rec.get("written_at_tpe"))
    if wt is None: return "缺寫入時間或時間沒有時區"
    if not wt < deadline: return "寫入時間不早於 08:45"
    items = []
    if isinstance(rec.get("inputs_meta"), dict):
        items = [v if isinstance(v, dict) else {} for v in rec["inputs_meta"].values()]
    elif isinstance(rec.get("inputs"), list):
        items = [x for x in rec["inputs"] if isinstance(x, dict)]
    if len(items) == 0: return "缺 inputs_meta（v3 錨點欄位）"
    for it in items:
        got = iso_only(it.get("fetched_at_tpe"))
        if got is None: return "輸入缺取得時間或沒有時區"
        if not (got < wt and got < deadline): return "輸入取得時間不早於寫入時間或 08:45"
        if it.get("request_started_tpe") is not None:
            st = iso_only(it.get("request_started_tpe"))
            if st is None or st > got: return "輸入請求開始時間異常"
        srv = http_only(it.get("server_date_http"))
        if srv is None: return "輸入缺伺服器 Date 標頭"
        if not (srv < deadline and srv < wt): return "伺服器 Date 不早於寫入時間或 08:45"
        if abs((srv - got).total_seconds()) > 120: return "伺服器 Date 與取得時間相差超過 120 秒"
        if not it.get("snapshot_path") or not it.get("snapshot_sha256"): return "輸入缺快照路徑或 sha256"
        sp = path_in(root, it["snapshot_path"])
        if h(sp) != it["snapshot_sha256"]: return "快照檔不存在或 sha256 對不上"
        cov = iso_only(it.get("coverage_end_tpe"))
        if cov is None: return "輸入缺涵蓋結束時間（或來源異常）"
        if "dataset" not in it: return "inputs_meta 缺 dataset 欄"
        ds = it.get("dataset")
        if ds is not None and ds not in DATED_COLS and ds not in NO_DATE_SETS: return f"資料集 {ds} 不在已知清單"
        refs = []
        if ds == "DG11319":
            for r in it.get("ref_snapshots") or []:
                if not isinstance(r, dict) or not r.get("path") or h(path_in(root, r["path"])) != r.get("sha256"):
                    return "DG11319 先前快照不存在或 sha256 對不上"
                refs.append(_rb(path_in(root, r["path"])))
        mine, bad = own_coverage(ds, _rb(sp), got, refs)
        if bad: return "從快照重算涵蓋結束時間失敗（或晚於取得時間）"
        if mine is not None and mine > cov: cov = mine
        if cov > got: return "涵蓋結束時間晚於取得時間（異常）"
        if not cov < deadline: return "輸入涵蓋到預測日 08:45 以後"
    if not anchored(deadline): return "沒有早於 08:45 的外部錨點"
    return None

# ---------- 外部錨點（自己從快照核）----------
def blob(root, ev):
    if not isinstance(ev, dict) or not ev.get("path") or not ev.get("sha256"): return None
    p = path_in(root, ev["path"])
    if h(p) != ev["sha256"]: return None
    with open(p, "rb") as fh: return fh.read()

def is_push(e, eid, head):
    return (isinstance(e, dict) and e.get("type") == "PushEvent" and str(e.get("id")) == eid
            and isinstance(e.get("repo"), dict) and e["repo"].get("name") == REPO
            and isinstance(e.get("payload"), dict) and e["payload"].get("ref") == "refs/heads/main"
            and e["payload"].get("head") == head)

def anchor_time(x, root):
    if x.get("type") != "github-push": return None, "錨點 type 不是 github-push"
    if not_verifier(x.get("verified_by")): return None, "錨點沒有 verified_by 或由寫帳方自核"
    m = re.match(r"^(\d+)/([0-9a-f]{40})$", str(x.get("ref") or ""))
    if not m: return None, "錨點 ref 格式不對"
    eid, head = m.groups()
    day, led, cov = ymd(x.get("trading_day")), str(x.get("ledger") or ""), str(x.get("covers_sha256") or "")
    if day is None or not re.match(r"^L[1-3]$", led) or not re.match(r"^[0-9a-f]{64}$", cov): return None, "錨點欄位不全"
    ev = {}
    for e in x.get("evidence") or []:
        if isinstance(e, dict): ev[e.get("kind")] = e
    api, arc, cf = blob(root, ev.get("events-api")), blob(root, ev.get("gharchive")), blob(root, ev.get("commit-file"))
    if api is None or arc is None or cf is None: return None, "錨點證據快照缺或 sha256 對不上"
    try: lst = json.loads(api.decode("utf-8"))
    except Exception: return None, "Events API 快照解析不了"
    found = [e for e in lst if is_push(e, eid, head)] if isinstance(lst, list) else []
    if len(found) != 1: return None, "Events API 快照找不到對應 main 的 PushEvent"
    t_api = iso_only(found[0].get("created_at"))
    if arc[:2] == b"\x1f\x8b":
        try: arc = gzip.decompress(arc)
        except Exception: return None, "GH Archive 快照解壓失敗"
    t_arc = None
    for ln in arc.decode("utf-8", errors="replace").splitlines():
        try: e = json.loads(ln)
        except ValueError: continue
        if is_push(e, eid, head):
            t_arc = iso_only(e.get("created_at")); break
    if t_api is None or t_arc is None or t_api != t_arc: return None, "Events API 與 GH Archive 的 created_at 缺或不一致"
    try:
        doc = json.loads(cf.decode("utf-8"))
        txt = base64.b64decode(doc.get("content") or "").decode("utf-8")
    except Exception: return None, "commit 檔快照解析不了"
    if ("ref=" + head) not in str(doc.get("url") or "") or doc.get("path") != "anchors/" + day[:4] + ".txt":
        return None, "commit 檔快照不是該 head 的 anchors 檔"
    rows = [r for r in txt.split("\n") if r != ""]
    rows = [r[:-1] if r.endswith("\r") else r for r in rows]
    if not rows or any(not re.match(r"^\d{4}-\d{2}-\d{2}\t[0-9a-f]{64}\tL[1-3]$", r) or r.endswith("\n") for r in rows):
        return None, "anchors 檔有不合白名單格式的行"
    if "-".join((day[:4], day[4:6], day[6:])) + "\t" + cov + "\t" + led not in rows: return None, "anchors 檔裡沒有這一行"
    return t_api, None

def first_live_day(anchors):
    """v3.1.2b-r1（獨立實作）：驗過的錨點裡，push 時間（Events API＝GH Archive 的 created_at）早於該日 08:45（+08:00）的最早非週末日期；沒有就 None。"""
    best = None
    for a in anchors:
        d, t = a.get("day"), a.get("t")
        if not d or t is None: continue
        y, mo, da = int(d[:4]), int(d[4:6]), int(d[6:])
        if datetime.date(y, mo, da).isoweekday() > 5: continue
        if t < datetime.datetime(y, mo, da, 8, 45, tzinfo=TW) and (best is None or d < best): best = d
    return best

def read_anchors(path, root, notes):
    if not ANCHOR_SCHEME_ADOPTED:
        notes["外部錨點方案尚未採用（v3.1 B0）：一律判缺錨點"] = notes.get("外部錨點方案尚未採用（v3.1 B0）：一律判缺錨點", 0) + 1
        return [], set()
    if not os.path.exists(path): return [], set()
    ok = []
    for raw in _rt(path).splitlines(True):
        if raw.strip() == "": continue
        try: x = json.loads(raw)
        except ValueError: x = None
        if not isinstance(x, dict):
            notes["錨點清單壞行"] = notes.get("錨點清單壞行", 0) + 1; continue
        t, why = anchor_time(x, root)
        if why:
            k = "錨點不採用：" + why; notes[k] = notes.get(k, 0) + 1; continue
        ok.append({"cov": x["covers_sha256"], "t": t, "led": x["ledger"], "day": ymd(x["trading_day"])})
    seen, dup = {}, set()
    for a in sorted(ok, key=lambda a: a["t"]):
        key = (a["day"], a["led"])
        if key not in seen: seen[key] = a["cov"]
        elif seen[key] != a["cov"]: dup.add(key)
    if dup:
        k = "同一（日期, 帳本）錨點出現第二個值＝多押嫌疑 " + json.dumps(sorted(dup), ensure_ascii=False)
        notes[k] = notes.get(k, 0) + 1
    return [a for a in ok if (a["day"], a["led"]) not in dup], dup

def code_of(root, f):
    try:
        with open(os.path.join(root, "ledger-codes.json"), encoding="utf-8") as fh: m = json.load(fh)
    except Exception: return None
    for k, v in m.items():
        if re.match(r"^L[1-3]$", str(k)) and v == os.path.basename(f): return k
    return None

# ---------- 實際方向 ----------
def actual_from_snapshots(root, day, rec):
    src = rec.get("sources") or {}
    for ds in ("DG11672", "DG11555"):
        s = src.get(ds) or {}
        if not s.get("sha256"): continue
        tries = []
        if s.get("snapshot_path"): tries.append(path_in(root, s["snapshot_path"]))
        folder = os.path.join(root, "snapshots", day)
        if os.path.isdir(folder):
            tries += [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.startswith(f"post-{ds}-") and f.endswith(".csv")]
        hit = next((p for p in tries if h(p) == s["sha256"]), None)
        if hit is None: continue
        for r in table(hit):
            if ymd(r.get("日期")) != day: continue
            if ds == "DG11555" and (r.get("指數/報酬指數") or "").strip() != "發行量加權股價指數": continue
            try: x = float(str(r.get("漲跌點數")).replace(",", ""))
            except ValueError: return None
            if ds == "DG11555" and (r.get("漲跌(+/-)") or "").strip() == "-": x = -x
            return "漲" if x > 0 else ("跌" if x < 0 else "平")
    return None

def outcomes(path, root, allow_dry, notes):
    res, bad = {}, {}
    if not path or not os.path.exists(path): return res, bad
    lines, _ = lines_with_chain(path)
    for n, rec, ok in lines:
        if rec is None or str(first(rec, ["kind", "type"]) or "") != "outcome": continue
        day = ymd(first(rec, ["trading_day", "trade_date", "date"]))
        if day is None: continue
        if day in res or day in bad:
            notes["outcome 同日重複行（取第一行）"] = notes.get("outcome 同日重複行（取第一行）", 0) + 1; continue
        if not ok or (rec.get("dry_run") and not allow_dry): bad[day] = 1; continue
        a = actual_from_snapshots(root, day, rec)
        if a is None or direction(rec.get("actual")) != a: bad[day] = 1; continue
        res[day] = a
    return res, bad

# ---------- 交易日 ----------
def business_days(cal, since, until, closed):
    holidays, years = set(), set()
    for r in table(cal):
        dd = ymd(r.get("日期"))
        if dd is None: continue
        years.add(dd[:4])
        if "開始交易" in (r.get("名稱") or "") or "最後交易" in (r.get("名稱") or ""): continue
        holidays.add(dd)
    a = datetime.date(int(since[:4]), int(since[4:6]), int(since[6:]))
    b = datetime.date(int(until[:4]), int(until[4:6]), int(until[6:]))
    out = []
    while a <= b:
        s = a.strftime("%Y%m%d")
        if s[:4] not in years: sys.exit(f"休市日曆沒有 {s[:4]} 年")
        if a.isoweekday() <= 5 and s not in holidays and s not in closed: out.append(s)
        a += datetime.timedelta(days=1)
    return out

def calendar_ok(cal, want):
    if not cal or not want: sys.exit("拒算：v3.1 要求 --calendar 和 --calendar-sha256")
    if h(cal) != want: sys.exit("拒算：休市表不存在或 sha256 不符")
    stem = cal[:-4] if cal.endswith(".csv") else cal
    for mp in (stem + ".meta.json", cal + ".meta.json"):
        if not os.path.exists(mp): continue
        try:
            with open(mp, encoding="utf-8") as fh: meta = json.load(fh)
        except Exception: meta = None
        if isinstance(meta, dict) and official(meta.get("url"), ("data.gov.tw", "twse.com.tw", "taifex.com.tw")) \
                and want in (meta.get("sha256"), meta.get("snapshot_sha256")): return
    sys.exit("拒算：休市表沒有官方來源 meta")

def closures(path, root, notes, verify=({}, None), live=None):
    """v3.1.2b：status＝verified 只由查證者核證檔算（日期＋列 sha 對得上、verified_by＝查證者、甲級），清單列的 status／verified_by 不看；再照裁定 2 分級查證據。
    只休夜盤：date＝目標交易日、announced_date＝宣布日；14:00 前宣布只管目標日 ≥ 即時預測起始日 live（None＝尚未開始）。"""
    closed = set()
    if not path: return closed
    got, note = verify
    bad = "臨時休市清單不合格行（不扣）"
    seen = 0
    for raw in _rt(path).splitlines(True):
        if raw.strip() == "": continue
        seen += 1
        try: x = json.loads(raw)
        except ValueError: x = None
        dd = ymd(x.get("date")) if isinstance(x, dict) else None
        if dd is None: bump(notes, bad); continue
        if "level" in x and x["level"] not in ("官方", "新聞"):
            bump(notes, bad); bump(notes, "臨時休市列分級不是官方或新聞（不扣）"); continue
        rec = got.get(dd + "/" + line_sha(x))
        if rec is None: why = "臨時休市列沒有對得上的核證列（日期＋列 sha，待核）"
        elif rec.get("verified_by") != "查證者": why = "核證列 verified_by 不是查證者（待核）"
        elif rec.get("tier") != "甲": why = "核證列不是甲級（待核）"
        else: why = None
        if why:
            bump(notes, "臨時休市列待查證者核證（pending_verify，不扣）"); bump(notes, why); continue
        bump(notes, "臨時休市列依查證者核證檔算成 verified")
        lack = []
        is_official = x.get("approval_basis") == "official_notice" or x.get("level") == "官方"
        if is_official:
            if not official(x.get("url"), ("taifex.com.tw", "twse.com.tw")): lack.append("官方網址")
            sp = x.get("snapshot_path")
            if not sp or not x.get("snapshot_sha256") or not os.path.exists(path_in(root, sp)) or h(path_in(root, sp)) != x.get("snapshot_sha256"): lack.append("快照 sha")
            sd = http_only(x.get("server_date_http"))
            if sd is None or sd.astimezone(TW).strftime("%Y%m%d") != dd:
                bc = rec.get("browser_check") if isinstance(rec.get("browser_check"), dict) else {}
                shot = str(bc.get("screenshot_path") or "")
                if shot and not os.path.isabs(shot): shot = os.path.join(rec["__dir"], shot)
                nd = ymd(bc.get("notice_date"))
                sub = (official(bc.get("url"), ("taifex.com.tw", "twse.com.tw")) and nd is not None and nd <= dd
                       and bool(bc.get("screenshot_sha256")) and os.path.isfile(shot) and h(shot) == bc.get("screenshot_sha256"))
                if not sub: lack.append("當日伺服器 Date")
        else:
            links = rec.get("url") if isinstance(rec.get("url"), list) else [rec.get("url")]
            if not any(isinstance(u, str) and u[:8] == "https://" for u in links): lack.append("網址")
            if str(rec.get("source_date") or "").strip() == "": lack.append("核過的日期")
        if lack:
            bump(notes, bad)
            if x.get("approval_basis") == "official_notice": bump(notes, "approval_basis=official_notice 缺" + "、".join(lack) + "（拒絕）")
            elif is_official: bump(notes, "官方級缺" + "、".join(lack) + "（不扣）")
            else: bump(notes, "新聞級缺" + "、".join(lack) + "（不扣）")
            continue
        kind = x.get("session", "all")
        if kind == "night":
            ann = ymd(x.get("announced_date"))
            if ann is None or ann > dd: bump(notes, "只休夜盤行沒有合格的 announced_date（不採計，夜盤照常）"); continue
            bump(notes, "即時預測起始日：" + (live if live else NOT_STARTED) + "（只認 GitHub 伺服器端 push 時間早於當日 08:45 的 anchors 紀錄）")
            if live and dd >= live:
                t = iso_only(x.get("announced_at_tpe"))   # T111 一(三)：14:00 前宣布才只休夜盤
                if t is None or t.astimezone(TW).strftime("%Y%m%d") != ann or (t.astimezone(TW).hour, t.astimezone(TW).minute) >= (14, 0):
                    bump(notes, "只休夜盤行沒有 14:00 前的宣布時間（不採計，夜盤照常）"); continue
            closed.add("N:" + dd + ":" + ann)
        elif kind == "all": closed.add(dd)
        else: bump(notes, bad)
    if seen and note: bump(notes, note)
    return closed

def default_closures_path(root):
    """v3.1.2（C4）：沒帶 --closures 也套用 root/closures/adhoc-closures.jsonl，sha 要等於旁邊 adhoc-closures.sha256 第一欄。"""
    p = os.path.join(root, "closures", "adhoc-closures.jsonl"); q = os.path.join(root, "closures", "adhoc-closures.sha256")
    if not (os.path.isfile(p) and os.path.isfile(q)): sys.exit(f"拒算：預設臨時休市清單 {p} 或 {q} 不存在")
    if h(p) != (_rt(q).split() or [""])[0]: sys.exit(f"拒算：預設臨時休市清單 {p} sha256 與 {q} 不符")
    return p

def tail_p(k, n, p):   # v3.1.1（P-B3）：p＝同期天天猜漲率，呼叫端一定要給；沒有預設值
    if p is None or isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
        raise ValueError("拒算：tail_p 要明確給同期天天猜漲率")
    return None if n == 0 else sum(comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k, n + 1))

def holm_adjust(pvals):
    live = sorted([i for i, p in enumerate(pvals) if p is not None], key=lambda i: pvals[i])
    out, best, m = [None] * len(pvals), 0.0, len(live)
    for rank, i in enumerate(live):
        best = max(best, min(1.0, (m - rank) * pvals[i])); out[i] = best
    return out

def bump(d, k): d[k] = d.get(k, 0) + 1

def run(a):
    root = a.root
    since, until = ymd(a.since), ymd(a.until)
    if since is None or until is None: sys.exit("拒算：v3.1 要求 --since 和 --until")
    calendar_ok(a.calendar, a.calendar_sha256)
    shared = {}
    anchors, dup = read_anchors(a.anchors or os.path.join(root, "anchors.jsonl"), root, shared)
    cp = None
    if a.closures:
        if not a.closures_sha256 or h(a.closures) != a.closures_sha256: sys.exit("拒算：--closures 要配相符的 --closures-sha256")
        cp = a.closures
    else:
        if a.closures_sha256: sys.exit("拒算：給了 --closures-sha256 卻沒有 --closures")
        cp = default_closures_path(root)
    live = first_live_day(anchors)
    closed = closures(cp, root, shared, verify_records(root), live)
    stage = "正式" if live is not None and since >= live else "試跑"   # v3.1.2b-r6：起始日之前一律試跑
    report = []
    for f in a.files:
        if os.path.basename(f) == "practice-calls-L2.jsonl":   # 2026-10-23 主人裁定：只計大盤指數，此帳不計分
            # v3.1.2 更正：這裡和下一行的「主人裁定」是協調助理轉達（非本人核可），待本人確認；原話出處未核。
            report.append({"file": f, "exists": os.path.exists(f), "excluded": "practice-calls-L2.jsonl 自 2026-10-23 起不計分（主人裁定：只計大盤指數；舊行保留）"}); continue
        if not os.path.exists(f): report.append({"file": f, "exists": False}); continue
        notes = dict(shared)
        lines, brk = lines_with_chain(f)
        if brk: bump(notes, f"sha 鏈斷在第 {brk} 行（之後全判缺資料）")
        pos = {rec["sha256"]: n for n, rec, ok in lines if rec and ok and "sha256" in rec}
        code = code_of(root, f)
        if code is None and ANCHOR_SCHEME_ADOPTED: bump(notes, "帳本沒有中性代號對照（ledger-codes.json），對不到錨點")
        mine = [x for x in anchors if x["led"] == code]
        broken_file = any(x["cov"] not in pos for x in mine)
        if broken_file: bump(notes, "錨點指向的行不存在＝帳本被截或改（整檔無效）")
        def anchored_from(n):
            return lambda dl: any(pos.get(x["cov"], -1) >= n and x["t"] < dl for x in mine)
        got, days_seen, rules_seen = {}, set(), set()
        for n, rec, ok in lines:
            if rec is None: bump(notes, "JSON 壞行"); continue
            kind = str(first(rec, ["kind", "type", "record_type"]) or "")
            if kind == "outcome": continue
            if kind == "anchor": bump(notes, "帳本內的錨點行（不採用、不計）"); continue
            if kind == "late_input": bump(notes, "late_input 行（不計分）"); continue
            day = ymd(first(rec, ["trading_day", "trade_date", "交易日", "預測交易日", "date", "target_date"]))
            if day is None: bump(notes, "預測日格式不明的行"); continue
            days_seen.add(day)
            if kind == "not_run" or rec.get("status") == "not_run":
                bump(notes, "not_run 行（該日算錯）")
                if ("*", day) not in got: got[("*", day)] = "missing"
                continue
            rule = str(first(rec, ["rule_id", "rule", "規則編號"]) or stage); rules_seen.add(rule)
            if (rule, day) in got or ("*", day) in got: bump(notes, "同日重複預測行（取第一行）"); continue
            if kind not in ("call", "prediction", "predict", ""): why = f"非預測型別 {kind!r} 的行"
            elif not ok: why = "sha 鏈斷點之後的行"
            elif rec.get("dry_run") and not a.dry_run_file: why = "正式檔有 dry_run 行"
            elif broken_file: why = "整檔無效（錨點指向的行不存在）"
            elif (day, code) in dup: why = "同一（日期, 帳本）錨點多值＝多押嫌疑"
            else: why = check_line(rec, day, root, anchored_from(n))
            call = direction(first(rec, ["call", "prediction", "direction", "漲或跌", "預測"]))
            if why is None and call == "missing": why = "預測方向缺或認不得"
            if why: bump(notes, why); got[(rule, day)] = "missing"
            else: got[(rule, day)] = call
        if a.rules: rules = a.rules.split(",")
        elif "shadow" in os.path.basename(f) or rules_seen & {"P1", "P2", "P3", "P4", "P5"}: rules = ["P1", "P2", "P3", "P4", "P5"]
        else: rules = sorted(rules_seen) or [stage]
        item = {"file": f, "exists": True, "file_sha256": h(f), "range": [since, until], "rules": {}}
        days = business_days(a.calendar, since, until, closed)
        item["trading_days"] = days
        item["night_closed_next_day"] = {c.split(":")[1]: c.split(":")[2] for c in closed
                                         if c.startswith("N:") and c.split(":")[1] in days}   # 裁定 4：鍵＝目標交易日
        stray = sorted(x for x in days_seen if x not in days and since <= x <= until)
        if stray: bump(notes, f"非交易日的預測行日期 {stray}")
        outs, bad = outcomes(a.outcomes, root, a.dry_run_file, notes)
        item["unpaired_days"] = sorted(d for d in days if d not in outs)
        hits, sizes = [], []
        for rule in rules:
            seq = []
            for d in days:
                if (rule, d) not in got and ("*", d) not in got: bump(notes, "交易日沒有預測行（算錯）")
                seq.append((got.get((rule, d), got.get(("*", d), "missing")), outs.get(d)))
            k = sum(1 for p, x in seq if x in ("漲", "跌") and p == x)
            c = lambda pos: {"tp": sum(p == pos and x == pos for p, x in seq), "fp": sum(p == pos and x != pos for p, x in seq),
                             "fn": sum(p != pos and x == pos for p, x in seq)}
            item["rules"][rule] = {"n": len(seq), "hits": k, "up": c("漲"), "down": c("跌"), "days": {d: list(v) for d, v in zip(days, seq)}}
            hits.append(k); sizes.append(len(seq))
        if item["unpaired_days"]: base = [None] * len(rules)   # v3.1.2b-r6（N1）：缺實際方向的日子不能當沒漲
        else: base = [sum(1 for _, x in item["rules"][r]["days"].values() if x == "漲") / n if n else None for r, n in zip(rules, sizes)]
        ps2 = [tail_p(k, n, b) if n and b is not None else None for k, n, b in zip(hits, sizes, base)]
        for rule, b, p, q in zip(rules, base, ps2, holm_adjust(ps2)):
            item["rules"][rule].update(always_up_rate=b, p_same_period=p, holm_p_same_period=q)
        item["reasons"] = notes
        report.append(item)
    return report

def compare(mine, theirs):
    diffs = []
    for m, t in zip(mine, theirs):
        if m.get("exists") != t.get("exists"): diffs.append((m["file"], "exists")); continue
        if m.get("excluded") != t.get("excluded"): diffs.append((m["file"], "excluded", m.get("excluded"), t.get("excluded"))); continue
        if m.get("excluded"): continue
        if not m.get("exists"): continue
        for key in ("file_sha256", "range", "trading_days", "reasons", "night_closed_next_day"):
            if m.get(key) != t.get(key): diffs.append((m["file"], key, m.get(key), t.get(key)))
        if sorted(m.get("unpaired_days", [])) != sorted(t.get("unpaired_days", {})):
            diffs.append((m["file"], "unpaired_days", m.get("unpaired_days"), sorted(t.get("unpaired_days", {}))))
        if set(m["rules"]) != set(t["rules"]): diffs.append((m["file"], "rules", list(m["rules"]), list(t["rules"])))
        for r in m["rules"]:
            if r not in t["rules"]: continue
            for key in ("n", "hits", "up", "down", "days"):
                if m["rules"][r][key] != t["rules"][r][key]: diffs.append((m["file"], r, key, m["rules"][r][key], t["rules"][r][key]))
            for key in ("always_up_rate", "p_same_period", "holm_p_same_period"):
                x, y = m["rules"][r].get(key), t["rules"][r].get(key)
                if (x is None) != (y is None) or (x is not None and abs(x - y) > 1e-12): diffs.append((m["file"], r, key, x, y))
    if len(mine) != len(theirs): diffs.append(("檔案數不同", len(mine), len(theirs)))
    return diffs

def outcomes_gate(path, sha):
    """v3.1.2b-r6（N1）：獨立重寫的 outcomes 檔把關，條件與 score_prf 相同。"""
    if not sha: sys.exit("拒算：v3.1.2b-r6 起 --outcomes-sha256 必填")
    if h(path) != sha.strip().lower(): sys.exit(f"拒算：--outcomes 檔 sha256 與 --outcomes-sha256 不符：{path}")
    if os.path.basename(path).lower().startswith("shadow"): sys.exit(f"拒算：--outcomes 檔名以 shadow 開頭：{path}")
    rows = [x for x in _rt(path).splitlines() if x.strip()]
    if not rows: sys.exit(f"拒算：--outcomes 檔沒有任何 outcome 行：{path}")
    for n, x in enumerate(rows, 1):
        try: rec = json.loads(x)
        except ValueError: sys.exit(f"拒算：--outcomes 檔第 {n} 筆不是 JSON")
        if not isinstance(rec, dict) or str(first(rec, ["kind", "type", "record_type"]) or "") != "outcome":
            sys.exit(f"拒算：--outcomes 檔第 {n} 筆不是 kind=outcome")
        if {"rule_id", "rule", "規則編號"} & set(rec): sys.exit(f"拒算：--outcomes 檔第 {n} 筆帶規則欄")

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=".")
    for x in ("--since", "--until", "--calendar", "--calendar-sha256", "--outcomes", "--anchors", "--closures",
              "--closures-sha256", "--rules", "--compare", "--outcomes-sha256"): p.add_argument(x)
    p.add_argument("--dry-run-file", action="store_true")
    p.add_argument("files", nargs="+")
    a = p.parse_args(argv)
    if not a.outcomes: sys.exit("拒算：v3.1.2b-r5 起 --outcomes 必填（不再預設讀影子規則檔）")
    if not os.path.isfile(a.outcomes): sys.exit(f"拒算：--outcomes 檔不存在：{a.outcomes}")
    outcomes_gate(a.outcomes, a.outcomes_sha256)
    rep = run(a)
    if a.compare:
        theirs = json.loads(_rt(a.compare))
        d = compare(rep, theirs)
        if d:
            print("不一致：", json.dumps(d, ensure_ascii=False, default=str)); sys.exit(1)
        print(f"一致：{len(rep)} 個檔，逐欄比對 n、命中、TP/FP/FN、逐日預測與實際、p、Holm p、交易日、未配對日、理由")
    else:
        print(json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=True))

if __name__ == "__main__":
    main()
