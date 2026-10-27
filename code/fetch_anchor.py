#!/usr/bin/env python3
# 公開版說明：本檔複製自內部 rigor v3.1.2b 程式（內部 sha256 c07048c041ded9b8f44ecdd2afbca61e0bafecfff8a0e905a982d6ec8e83c085），
# 只換人名、內部路徑、內部文件代號、個股帳本名、User-Agent 與寫帳資料夾防呆，判定邏輯未改。資料來源與授權見 repo 的 NOTICE.md。
"""fetch_anchor.py — 共用抓取工具（rigor-rules v3.1，R1/R3/R4/D1；v3 唯讀備份 fetch_anchor_v3.py）。
v3.1：--dataset 在已知資料集時不准再給 --coverage-end（B2/N3）；inputs_meta 記 ref_snapshots（DG11319 自核用的先前快照路徑與 sha256，
讓計分端能從快照重算）；append 自動補 256 位元 commit_nonce（A1），紀錄自帶 commit_nonce 或 kind=anchor 一律拒寫（A2/A6）。
本工具不含任何推送、上傳或連 GitHub 的程式（B0）。每日簡報、shadow_run 都用它產生 inputs。

取得時間一律由程式在「收完回應之後」自動記（不准手打）；同時存伺服器回應的 HTTP Date 標頭、原始內容 sha256、快照檔。

用法：
  # 抓一個來源，存快照，印出一筆可直接放進 inputs_meta 的 JSON
  python3 fetch_anchor.py fetch --name 外資台指期 --dataset DG11596 --url URL --dir SNAPDIR [--phase pre]
  python3 fetch_anchor.py fetch --name 某新聞 --url URL --dir SNAPDIR --coverage-end 2026-10-27T05:00:00+08:00
  python3 fetch_anchor.py fetch --name 休市表 --dataset DG11761 --url URL --dir SNAPDIR      # 無結果日期的檔，用取得時間當上界
  # v3.1.1：每天早上抓 DG11319 之前，先確認前一個交易日的 DG11319 快照在手（缺就非 0 結束並明說缺哪一天）
  python3 fetch_anchor.py check-prev --day 20261027 --calendar CAL.csv --calendar-sha256 SHA --ref-dir SNAPROOT [--closures C --closures-sha256 S]
    （v3.1.2：不給 --closures 也套用 --root 底下的 closures/adhoc-closures.jsonl，sha 照 adhoc-closures.sha256）
  python3 fetch_anchor.py fetch --name 台指期行情 --dataset DG11319 --url URL --dir SNAPDIR --ref-dir SNAPROOT   # DG11319 不給 --ref-dir 拒抓
  # 往帳本追加一行（written_at_tpe、prev_sha256、sha256 由程式自動填；JSON 裡不准自帶 written_at_tpe）
  python3 fetch_anchor.py append --ledger practice-calls.jsonl --json-file rec.json
  python3 fetch_anchor.py verify --ledger PATH     # 核 sha 鏈
  python3 fetch_anchor.py tail --ledger PATH       # 印鏈尾 sha256（給外部錨點用）
"""
import argparse, csv, datetime as dt, email.utils, hashlib, io, json, os, secrets, sys, time, urllib.error, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

def _rb(p):
    """讀整個檔（bytes），讀完就關（v3.1.1 修 ResourceWarning）。"""
    with open(p, "rb") as f: return f.read()

def _rt(p, enc="utf-8"):
    """讀整個檔（文字），讀完就關。"""
    with open(p, encoding=enc) as f: return f.read()

TPE = ZoneInfo("Asia/Taipei")
MAX_SKEW_SEC = 120          # 本機收完時間與伺服器 Date 標頭最多相差 120 秒（審查 R1 改法 3）
TOOL_VERSION = "fetch_anchor v3.1.1"

# 有結果日期欄的資料集：預期的日期欄名（R4：欄名對不上就擋）
DATED = {"DG11672": "日期", "DG11555": "日期", "DG11319": "日期", "DG11596": "日期",
         "DG11592": "日期", "DG11322": "日期", "DG11339": "日期"}
# 沒有結果日期的檔：用取得時間當上界（審查「三、不擋」已接受 DG11655、DG11761）
UNDATED = {"DG11655", "DG11761"}

def now_tpe():
    return dt.datetime.now(TPE)

def iso(t):
    return t.isoformat(timespec="seconds") if t is not None else None

def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()

def parse_http_date(s):
    """HTTP Date 標頭（RFC 7231，一律 GMT）→ aware datetime；解析不了回 None。"""
    if not s:
        return None
    try:
        t = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        return None
    if t is None or t.tzinfo is None:
        return None
    return t

def strict_ts(v):
    """R3：只有日期、或沒有明確時區的時間一律回 None（擋）。"""
    import re
    if v in (None, ""):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}[-/]?\d{2}[-/]?\d{2}", s):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})", s):
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo is not None else None

# ---------------- 合規白名單 ----------------
def url_allowed(url):
    """資料合規（公開版 v3.1.2，C5）：只准下載 data.gov.tw 資料集頁登記的授權下載網址（DATA_GOV_URLS，逐字比對）。
    證交所、期交所的公告頁與其他網站一律拒抓；公告證據由查證者在內部取得，不在公開程式裡抓、也不發布。"""
    if url in DATA_GOV_URLS:
        return True, ""
    u = urllib.parse.urlparse(url)
    if u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"):
        return True, ""   # 只給測試用的本機伺服器（不出網）
    return False, "公開版只准 data.gov.tw 登記的授權下載網址（DATA_GOV_URLS）"

# 2026-10-23 從 data.gov.tw API v2（https://data.gov.tw/api/v2/rest/dataset/編號）逐一核對 resourceDownloadUrl 相符
DATA_GOV_URLS = {
    "https://www.twse.com.tw/exchangeReport/FMTQIK?response=open_data",                     # 11672
    "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=open_data",                   # 11555
    "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?response=open_data",               # 11755
    "https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name=DailyMarketReportFut",  # 11319
    "https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name=MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate",  # 11596
    "https://www.twse.com.tw/fund/MI_QFIIS_cat?response=open_data",                         # 11655
    "https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=open_data",           # 11761
}

# ---------------- 抓取 ----------------
def fetch(url, tries=3, timeout=90):
    """回傳 dict：status, body, error, server_date_http, request_started_tpe, received_at_tpe。
    received_at_tpe 在 r.read() 完成之後才取（審查 R1 改法 2）；失敗時是最後一次嘗試結束的時間。"""
    ok, why = url_allowed(url)
    if not ok:
        raise ValueError(f"拒抓：{why}：{url}")
    last = None
    for i in range(tries):
        t0 = now_tpe()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Stock-bot/1 (open data)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                t1 = now_tpe()
                return {"status": r.status, "body": body, "error": None,
                        "server_date_http": r.headers.get("Date"),
                        "request_started_tpe": t0, "received_at_tpe": t1, "attempt": i + 1}
        except urllib.error.HTTPError as e:
            last = {"status": e.code, "body": b"", "error": f"HTTPError {e.code}",
                    "server_date_http": e.headers.get("Date") if e.headers else None,
                    "request_started_tpe": t0, "received_at_tpe": now_tpe(), "attempt": i + 1}
        except Exception as e:  # noqa
            last = {"status": None, "body": b"", "error": f"{type(e).__name__}: {e}",
                    "server_date_http": None, "request_started_tpe": t0, "received_at_tpe": now_tpe(), "attempt": i + 1}
        if i + 1 < tries:
            time.sleep(5 * (i + 1))
    return last

# ---------------- 涵蓋結束時間 ----------------
def rows(body):
    txt = body.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(txt)))

def num(s):
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None

def ymd_of(s):
    """'20261022'、'2026/10/22'、'2026-10-22'、民國 '1151022' -> date；其他（含帶時刻）回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    s2 = s.replace("/", "").replace("-", "")
    if not s2.isdigit():
        return None
    try:
        if len(s2) == 8:
            return dt.date(int(s2[:4]), int(s2[4:6]), int(s2[6:]))
        if len(s2) == 7:
            return dt.date(int(s2[:3]) + 1911, int(s2[3:5]), int(s2[5:]))
    except ValueError:
        return None
    return None

def night_ref_check(rs, ref_bodies=()):
    """D1：盤後列的參考價（最後成交價−漲跌價）必須等於前一一般時段結算價。
    比對對象：同檔同日同契約同到期月的一般列參考價（它也是前一一般結算價）；同檔一般列沒成交時，
    到 ref_bodies（先前快照）找日期早於該列、最近一天同契約同到期月一般列的結算價。
    任何一列對不上 → 回錯誤字串（整份擋）。沒成交或找不到比對對象的列略過，但每個有盤後列的日期
    至少要有 1 列核對通過，否則也擋。全部通過回 (None, 核對列數, 略過列數)。"""
    def key(r):
        return (r.get("日期", "").strip(), r.get("契約代號", "").strip(), r.get("到期月份(週別)", "").strip())
    gen = {}
    for r in rs:
        if r.get("交易時段", "").strip() == "一般":
            last, chg = num(r.get("最後成交價")), num(r.get("漲跌價"))
            if last is not None and chg is not None:
                gen[key(r)] = last - chg
    settle = {}  # (契約, 到期月) -> [(日期, 結算價)]
    for b in ref_bodies:
        try:
            for r in rows(b):
                if r.get("交易時段", "").strip() == "一般":
                    s = num(r.get("結算價"))
                    d = ymd_of(r.get("日期", ""))
                    if s is not None and d is not None:
                        settle.setdefault((r.get("契約代號", "").strip(), r.get("到期月份(週別)", "").strip()), []).append((d, s))
        except Exception:
            continue
    checked, skipped, dates_night, dates_ok = 0, 0, set(), set()
    for r in rs:
        if r.get("交易時段", "").strip() != "盤後":
            continue
        k = key(r)
        dates_night.add(k[0])
        last, chg = num(r.get("最後成交價")), num(r.get("漲跌價"))
        if last is None or chg is None:
            skipped += 1
            continue
        ref = last - chg
        if k in gen:
            want = gen[k]
        else:
            d = ymd_of(k[0])
            cands = [x for x in settle.get((k[1], k[2]), []) if d is not None and x[0] < d]
            if not cands:
                skipped += 1
                continue
            want = max(cands)[1]
        if abs(ref - want) > 1e-6:
            return f"盤後列 {k} 參考價 {ref:g} 對不上前一一般結算 {want:g}", checked, skipped
        checked += 1
        dates_ok.add(k[0])
    lack = sorted(dates_night - dates_ok)
    if lack:
        return f"盤後日期 {lack} 沒有任何一列能核對參考價", checked, skipped
    return None, checked, skipped

def night_start_day(rs, ref_bodies, x):
    """v3.1（rigor-rules §v3.1 交叉核對 1）：標示日期 x 的盤後列，實際是哪一個交易日 S 的 15:00 開始。
    只認「有價格證據」的 S：同契約、同到期月、日期 < x 的一般列，其結算價 == 該盤後列參考價（最後成交價 − 漲跌價）。
    找得到 → 回 S（多個候選取最晚，偏保守）；找不到 → None（呼叫端退回 x 05:00 上界，同樣偏保守）。"""
    sett = {}
    for b in [None] + list(ref_bodies):
        try:
            src = rs if b is None else rows(b)
        except Exception:
            continue
        for r in src:
            if r.get("交易時段", "").strip() != "一般":
                continue
            s, d = num(r.get("結算價")), ymd_of(r.get("日期", ""))
            if s is not None and d is not None and d < x:
                sett.setdefault((r.get("契約代號", "").strip(), r.get("到期月份(週別)", "").strip()), []).append((d, s))
    best = None
    for r in rs:
        if r.get("交易時段", "").strip() != "盤後" or ymd_of(r.get("日期", "")) != x:
            continue
        last, chg = num(r.get("最後成交價")), num(r.get("漲跌價"))
        if last is None or chg is None:
            continue
        ref = last - chg
        for d, s in sett.get((r.get("契約代號", "").strip(), r.get("到期月份(週別)", "").strip()), []):
            if abs(s - ref) <= 1e-6 and (best is None or d > best):
                best = d
    return best

def night_unproven(body, ref_bodies=()):
    """v3.1.1：回傳證不出開始日 S 的盤後列標示日（YYYYMMDD，排序）。非空＝前一交易日快照缺或對不上，要明說，不准默默判缺。"""
    try:
        rs = rows(body)
    except Exception:
        return []
    xs = sorted({ymd_of(r.get("日期", "")) for r in rs if (r.get("交易時段") or "").strip() == "盤後"} - {None})
    return [x.strftime("%Y%m%d") for x in xs if night_start_day(rs, ref_bodies, x) is None]

PREV_EXIT = 3   # 前一交易日快照缺：check-prev 與 fetch DG11319 都用這個結束碼

def prev_trading_day(cal_path, cal_sha, day, closed=()):
    """v3.1.1：照官方休市表（DG11761 快照，sha256 要相符）找 day 的前一個交易日。
    休市表沒有那一年 → ValueError（待官方表，不猜）。closed＝已核的全日臨時休市日（YYYYMMDD）。"""
    body = _rb(cal_path)
    if sha256_bytes(body) != cal_sha:
        raise ValueError("休市表 sha256 與 --calendar-sha256 不符，拒判")
    hol, years = set(), set()
    for r in rows(body):
        d = ymd_of(r.get("日期", ""))
        if d is None:
            continue
        years.add(d.year)
        name = r.get("名稱", "")
        if "開始交易" in name or "最後交易" in name:
            continue
        hol.add(d.strftime("%Y%m%d"))
    t = dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))
    if t.year in years and (t.weekday() >= 5 or day in hol or day in closed):
        raise ValueError(f"{day} 不是交易日（休市表或臨時休市清單），當天不寫預測、不用抓")
    for _ in range(31):
        t -= dt.timedelta(days=1)
        if t.year not in years:
            raise ValueError(f"休市表沒有 {t.year} 年（待官方表），無法判定 {day} 的前一個交易日")
        k = t.strftime("%Y%m%d")
        if t.weekday() < 5 and k not in hol and k not in closed:
            return k
    raise ValueError(f"{day} 往前 31 天找不到交易日")

CLOSURE_LEVELS = ("官方", "新聞")

def closure_days(path, want_sha):
    """v3.1.2（C4）：臨時休市清單 → 全日休市日集合（YYYYMMDD），給交易日判定用（check-prev）。
    整檔 sha256 要等於 want_sha；只收分級是官方或新聞、session=all、status 不是 rejected 的列（pending_verify 也算：
    這裡只決定「前一交易日是哪天」，不扣分母；扣分母要查證者核證，見 score_prf）。分級缺或不是官方／新聞 → 不收。"""
    body = _rb(path)
    if sha256_bytes(body) != want_sha:
        raise ValueError(f"臨時休市清單 {path} 的 sha256 與預期不符，拒判")
    out = set()
    for l in body.decode("utf-8").splitlines():
        if not l.strip():
            continue
        e = json.loads(l)
        if e.get("level") not in CLOSURE_LEVELS or str(e.get("session", "")).strip() != "all" or e.get("status") == "rejected":
            continue
        d = "".join(ch for ch in str(e.get("date") or "") if ch.isdigit())
        if len(d) == 8:
            out.add(d)
    return out

def default_closures(root):
    """v3.1.2（C4）：沒帶 --closures 也套用 root/closures/adhoc-closures.jsonl；sha 取旁邊 adhoc-closures.sha256 第一欄。缺檔 → ValueError。"""
    p = os.path.join(root, "closures", "adhoc-closures.jsonl"); q = os.path.join(root, "closures", "adhoc-closures.sha256")
    if not (os.path.isfile(p) and os.path.isfile(q)):
        raise ValueError(f"預設臨時休市清單 {p} 或 {q} 不存在（v3.1.2 起 --closures 預設開啟）")
    return p, (_rt(q).split() or [""])[0]

def find_prev_snapshot(ref_dirs, prev):
    """在 ref_dirs 底下找含 prev 日「一般」列且有結算價的 DG11319 快照，回傳路徑清單（排序）。"""
    hits = []
    for d in ref_dirs:
        for root_, _, fs in os.walk(d):
            for x in sorted(fs):
                if "DG11319" not in x or not x.endswith(".csv"):
                    continue
                p = os.path.join(root_, x)
                try:
                    rs = rows(_rb(p))
                except Exception:
                    continue
                if any((r.get("交易時段") or "").strip() == "一般" and ymd_of(r.get("日期", "")) is not None
                       and ymd_of(r.get("日期", "")).strftime("%Y%m%d") == prev and num(r.get("結算價")) is not None for r in rs):
                    hits.append(p)
    return sorted(hits)

def check_prev(day, cal_path, cal_sha, ref_dirs, closed=()):
    """回傳 (結束碼, 訊息)。0＝前一交易日快照在手；PREV_EXIT＝缺，訊息寫明缺哪一天、要先取哪個檔。"""
    try:
        prev = prev_trading_day(cal_path, cal_sha, day, closed)
    except ValueError as e:
        return PREV_EXIT, f"錯誤：{e}"
    hits = find_prev_snapshot(ref_dirs, prev)
    if not hits:
        return PREV_EXIT, (f"錯誤：缺前一交易日 {prev} 的 DG11319 快照（要有 {prev} 的一般列與結算價）。"
                           f"請先取 {prev} 的 DG11319 快照放進 --ref-dir，再抓 {day} 的盤後列；否則盤後列證不出開始日，會退回 {day} 05:00、可能判缺。")
    return 0, f"前一交易日 {prev} 的 DG11319 快照在手：" + "、".join(f"{h}（sha256 {sha256_bytes(_rb(h))}）" for h in hits)

def coverage_end(key, body, fetched, ref_bodies=()):
    """回傳 (涵蓋結束時間 aware datetime 或 None, 依據說明)。None 一律代表「缺資料，算錯」。
    - DG11655、DG11761：沒有結果日期 → 取得時間。
    - DATED：必須有預期日期欄；每列日期都要解析得了（整列全空才略過）；
      DG11319 交易時段=盤後、日期 X → 夜盤開始交易日 S 的次日 05:00（S 由參考價＝S 一般結算價證出；
      證不出來退回 X 05:00 上界）（期交所〈盤後交易介紹〉：盤後屬次一一般交易時段），且要過 D1 參考價自核；
      其他日期 X → X 23:59:59（只寫日期一律當天結束，R3）。
    - 檔內上界晚於取得時間 → None（R4）。"""
    if fetched is None:
        return None, "異常：沒有取得時間"
    if key in UNDATED:
        return fetched, "取得時間（檔案沒有結果日期欄）"
    col = DATED.get(key)
    if col is None:
        return None, f"異常：{key} 不在已知資料集清單，請用 --coverage-end 明確給（含時區）"
    try:
        rs = rows(body)
    except Exception as e:
        return None, f"異常：{type(e).__name__}"
    if not rs:
        return None, "異常：檔案沒有資料列"
    if col not in rs[0]:
        return None, f"異常：找不到日期欄「{col}」（欄名：{list(rs[0].keys())[:6]}）"
    bound = None
    night_start = {}
    for r in rs:
        raw = (r.get(col) or "").strip()
        if not raw:
            if all(not (v or "").strip() for v in r.values() if isinstance(v, str)):
                continue
            return None, "異常：有資料列的日期是空的"
        d = ymd_of(raw)
        if d is None:
            return None, f"異常：日期 {raw!r} 解析不了"
        if key == "DG11319" and (r.get("交易時段") or "").strip() == "盤後":
            if d not in night_start:
                night_start[d] = night_start_day(rs, ref_bodies, d)
            st = night_start[d]
            # v3.1：夜盤結束＝開始那個交易日 S 的次日 05:00；S 證不出來才用標示日 05:00（上界，偏保守）
            n1 = (st + dt.timedelta(days=1)) if st is not None else d
            e = dt.datetime(n1.year, n1.month, n1.day, 5, 0, tzinfo=TPE)
        else:
            e = dt.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=TPE)
        bound = e if bound is None or e > bound else bound
    if bound is None:
        return None, "異常：沒有任何日期列"
    basis = "檔內日期（盤後列=夜盤開始交易日次日05:00，證不出開始日則該列日期05:00；其餘=該日23:59:59上界）" if key == "DG11319" else "檔內日期（該日23:59:59上界）"
    if key == "DG11319":
        if "交易時段" not in rs[0]:
            return None, "異常：DG11319 找不到「交易時段」欄"
        err, nchk, nskip = night_ref_check(rs, ref_bodies)
        if err:
            return None, basis + "；異常：" + err
        basis += f"；盤後參考價自核通過 {nchk} 列（略過無成交或無比對對象 {nskip} 列）"
    if bound > fetched:
        return None, basis + "；異常：晚於取得時間"
    return bound, basis

# ---------------- 快照 ----------------
def snapshot_one(url, name, out_dir, phase="pre", dataset=None, coverage_end_override=None, ref_bodies=(), root=None, ref_paths=()):
    """抓一份、照原樣存檔、回傳 inputs_meta 一筆。快照檔名用收完時間；同名不覆寫。
    v3.1：資料集有代號（DATED、UNDATED）時拒絕 coverage_end_override（B2/N3，不准跳過檔內日期檢查）；
    ref_paths＝DG11319 自核用的先前快照路徑，連同 sha256 記進 ref_snapshots（計分端重算用）。"""
    if coverage_end_override is not None and dataset is not None:
        raise ValueError(f"拒抓：資料集 {dataset} 有固定判法，不准再給 --coverage-end（rigor v3.1 B2）")
    os.makedirs(out_dir, exist_ok=True)
    if ref_paths and not ref_bodies:
        ref_bodies = [_rb(x) for x in ref_paths]
    r = fetch(url)
    t1 = r["received_at_tpe"]
    tag = dataset or "".join(c if c.isalnum() else "_" for c in name)[:40]
    base = f"{phase}-{tag}-{t1.strftime('%Y%m%dT%H%M%S')}"
    path, n = os.path.join(out_dir, base + ".csv"), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(out_dir, f"{base}-{n}.csv")
    with open(path, "wb") as f:
        f.write(r["body"])
    h = sha256_bytes(r["body"])
    ok = r["status"] == 200 and len(r["body"]) > 0
    sd = parse_http_date(r["server_date_http"])
    if not ok:
        ce, basis = None, f"異常：抓取失敗 {r['status']} {r['error']}"
    elif coverage_end_override is not None:
        ce = strict_ts(coverage_end_override)
        basis = "明確給定（發布時間等，含時區）"
        if ce is None:
            basis = f"異常：--coverage-end {coverage_end_override!r} 沒有明確時區或解析不了"
        elif ce > t1:
            ce, basis = None, "異常：給定的涵蓋結束時間晚於取得時間"
    elif dataset:
        ce, basis = coverage_end(dataset, r["body"], t1, ref_bodies)
    else:
        ce, basis = None, "異常：沒有資料集代號也沒有 --coverage-end"
    unproven = night_unproven(r["body"], ref_bodies) if dataset == "DG11319" and ok else []
    if unproven:   # v3.1.1：不准默默判缺
        basis += f"；錯誤：前一交易日快照缺或對不上，盤後列 {'、'.join(unproven)} 證不出開始日，已退回該列日期 05:00"
    rel = os.path.relpath(path, root) if root else path
    extra = {"night_start_unproven": unproven} if dataset == "DG11319" else {}
    return {**extra, "name": name, "dataset": dataset, "url": url, "http_status": r["status"], "error": r["error"],
            "ok": ok, "bytes": len(r["body"]), "attempt": r["attempt"],
            "request_started_tpe": iso(r["request_started_tpe"]), "fetched_at_tpe": iso(t1),
            "server_date_http": r["server_date_http"],
            "server_date_tpe": iso(sd.astimezone(TPE)) if sd else None,
            "snapshot_path": rel, "snapshot_sha256": h,
            "coverage_end_tpe": iso(ce), "coverage_basis": basis, "tool": TOOL_VERSION,
            "ref_snapshots": [{"path": os.path.relpath(x, root) if root else x, "sha256": sha256_bytes(_rb(x))}
                              for x in ref_paths]}, r["body"]

# ---------------- 帳本（只能追加，sha 鏈）----------------
def canon(rec):
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def guard_path(p):
    ap = os.path.abspath(p)
    if "ledgers" in os.path.realpath(ap).casefold().split(os.sep):
        sys.exit("拒寫：不准寫進正式帳資料夾 ledgers/")
    return ap

def read_lines(p):
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in _rt(p).splitlines(True) if l.strip()]

def append_records(path, recs):
    """written_at_tpe、commit_nonce、prev_sha256、sha256 都由這裡自動填。紀錄自帶其中任一欄就拒寫。
    v3.1 A1：commit_nonce＝secrets.token_hex(32)，只留在私有帳本行內，outcome 寫入且錨點核完前不揭露。
    v3.1 A2/A6：kind=anchor 一律拒寫（錨點只由核帳方寫進獨立的 anchors.jsonl，寫帳方不寫）。"""
    path = guard_path(path)
    led = read_lines(path)
    prev = led[-1]["sha256"] if led else "0" * 64
    out = []
    with open(path, "a", encoding="utf-8") as f:
        for r in recs:
            r = dict(r)
            for k in ("written_at_tpe", "commit_nonce", "prev_sha256", "sha256"):
                if k in r:
                    sys.exit(f"拒寫：紀錄不准自帶 {k}（由程式自動產生）")
            if str(r.get("kind", "")) == "anchor" or str(r.get("type", "")) == "anchor":
                sys.exit("拒寫：錨點不准寫進預測帳本（rigor v3.1 A2/A6：由核帳方寫進 anchors.jsonl）")
            r["written_at_tpe"] = iso(now_tpe())
            r["commit_nonce"] = secrets.token_hex(32)
            r["prev_sha256"] = prev
            r["sha256"] = hashlib.sha256(canon(r).encode()).hexdigest()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            prev = r["sha256"]
            out.append(r)
    return out

def verify_chain(path):
    prev = "0" * 64
    for i, r in enumerate(read_lines(path), 1):
        r = dict(r)
        s = r.pop("sha256", None)
        if r.get("prev_sha256") != prev or hashlib.sha256(canon(r).encode()).hexdigest() != s:
            return False, i
        prev = s
    return True, None

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("fetch")
    f.add_argument("--name", required=True); f.add_argument("--url", required=True)
    f.add_argument("--dir", required=True, help="快照資料夾，例如 snapshots/20261027")
    f.add_argument("--phase", default="pre"); f.add_argument("--dataset")
    f.add_argument("--coverage-end", help="只限沒有 --dataset 的來源（新聞等）：原文發布時間，必須帶時區；和 --dataset 同給一律拒抓（v3.1 B2）")
    f.add_argument("--root", default=".", help="snapshot_path 相對於這個資料夾記錄")
    f.add_argument("--ref-dir", action="append", default=[], help="DG11319 自核用：先前 DG11319 快照所在資料夾（可多個）")
    a_ = sp.add_parser("append"); a_.add_argument("--ledger", required=True)
    g = a_.add_mutually_exclusive_group(required=True); g.add_argument("--json"); g.add_argument("--json-file")
    v = sp.add_parser("verify"); v.add_argument("--ledger", required=True)
    t = sp.add_parser("tail"); t.add_argument("--ledger", required=True)
    c = sp.add_parser("check-prev", help="v3.1.1：抓 DG11319 前先確認前一交易日快照在手")
    c.add_argument("--day", required=True); c.add_argument("--calendar", required=True); c.add_argument("--calendar-sha256", required=True)
    c.add_argument("--ref-dir", action="append", required=True)
    c.add_argument("--closures", help="v3.1.2：不給＝套用 --root 底下 closures/adhoc-closures.jsonl（sha 照 adhoc-closures.sha256）")
    c.add_argument("--closures-sha256"); c.add_argument("--root", default=".")
    a = ap.parse_args()
    if a.cmd == "check-prev":
        if a.closures and not a.closures_sha256:
            sys.exit("拒判：給了 --closures 就必須給相符的 --closures-sha256")
        if a.closures_sha256 and not a.closures:
            sys.exit("拒判：給了 --closures-sha256 卻沒有 --closures")
        try:
            cp, cs = (a.closures, a.closures_sha256) if a.closures else default_closures(a.root)
            closed = closure_days(cp, cs)
        except ValueError as e:
            sys.exit(f"拒判：{e}")
        code, msg = check_prev(a.day, a.calendar, a.calendar_sha256, a.ref_dir, closed)
        print(msg, file=sys.stderr if code else sys.stdout)
        sys.exit(code)
    if a.cmd == "fetch":
        guard_path(a.dir)
        if a.dataset == "DG11319" and not a.ref_dir:
            sys.exit("拒抓：DG11319 要先取前一交易日快照並用 --ref-dir 給（rigor-rules v3.1.1；先跑 check-prev）")
        refs = []
        for d in a.ref_dir:
            for root_, _, fs in os.walk(d):
                refs += [os.path.join(root_, x) for x in sorted(fs) if "DG11319" in x and x.endswith(".csv")]
        try:
            meta, _ = snapshot_one(a.url, a.name, a.dir, a.phase, a.dataset, a.coverage_end, (), a.root, refs)
        except ValueError as e:
            sys.exit(str(e))
        print(json.dumps(meta, ensure_ascii=False))
        if meta["coverage_end_tpe"] is None:
            print(f"警告：{meta['coverage_basis']}（這項輸入會被判缺資料）", file=sys.stderr)
        if meta.get("night_start_unproven"):
            print(f"錯誤：前一交易日快照缺或對不上，盤後列 {'、'.join(meta['night_start_unproven'])} 證不出開始日（rigor-rules v3.1.1）", file=sys.stderr)
            sys.exit(PREV_EXIT)
    elif a.cmd == "append":
        rec = json.loads(a.json if a.json else _rt(a.json_file))
        recs = rec if isinstance(rec, list) else [rec]
        for r in append_records(a.ledger, recs):
            print(r["written_at_tpe"], r["sha256"])
    elif a.cmd == "verify":
        ok, i = verify_chain(a.ledger)
        print("sha 鏈完整" if ok else f"鏈斷在第 {i} 行"); sys.exit(0 if ok else 1)
    elif a.cmd == "tail":
        led = read_lines(a.ledger)
        print(led[-1]["sha256"] if led else "0" * 64)

if __name__ == "__main__":
    main()
