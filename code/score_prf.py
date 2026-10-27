#!/usr/bin/env python3
# 公開版說明：本檔複製自內部 rigor v3.1.2b 程式（內部 sha256 e3ec04d7bf19ec3a80f777fca38ecbe2592a6e0a99b5b8e3c916c72ace7ed446），
# 只換人名、內部路徑、內部文件代號、個股帳本名、User-Agent 與寫帳資料夾防呆，判定邏輯未改。資料來源與授權見 repo 的 NOTICE.md。
"""score_prf.py v3.1 — Precision/Recall/F1 成績單（rigor-rules v3.1；v3 唯讀備份 score_prf_v3.py，v2 備份 score_prf_v2.py，v1 備份 score_prf_v1.py）。
主人 2026-10-23 要求每週列 P/R/F1，並要求極端嚴謹、大膽看資料。
（v3.1.2 更正：上一行是協調助理轉達（非本人核可），原話出處未核。）

用法（v3.1：--since、--until、--calendar、--calendar-sha256 四個都必填，缺一拒算；給 --closures 就必須同時給 --closures-sha256）:
  python3 score_prf.py --root . --since 20261027 --until 20261030 \\
      --calendar 快照/DG11761.csv --calendar-sha256 SHA --outcomes outcomes-taiex.jsonl --outcomes-sha256 SHA [--anchors anchors.jsonl] \\
      [--closures closures.jsonl --closures-sha256 SHA] [--rules P1,P2] [--json] [--dry-run-file] files...
  （v3.1.2b-r5：--outcomes 改必填、檔案必須存在，不再預設讀 root/shadow-calls.jsonl；依審查意見，審查檔未公開。）
  （v3.1.2b-r6：另加 --outcomes-sha256 必填、對不上拒算；outcomes 檔名以 shadow 開頭、任何一行 kind≠outcome 或帶規則欄、
   outcome 行數 0 一律拒算；有未配對日時同期天天猜漲基準與檢定 p 改「無法計算」；即時預測起始日（live_from）之前
   預設規則標籤是「試跑」、輸出「試跑基準」「試跑檢定」；依審查意見，審查檔未公開。）

v3.1 修正（對應審查者對 v3 與錨點方案的審查意見，審查檔未公開）：
  B0/R1/A2/A5/A6 外部錨點只認核帳方寫的 github-push 錨點：時間只取快照（Events API 回應＋GH Archive 小時檔）裡
     PushEvent 的 created_at，行內 external_time 一律不看；verified_by 必須是寫帳方以外的人。方案未採用前
     （ANCHOR_SCHEME_ADOPTED=False，只能改程式並重發 manifest 才能打開，沒有任何參數能打開）一律判缺錨點。
  A4 同一（日期, 帳本代號）只認第一個值；出現第二個值＝多押嫌疑，該帳本該日判錯。
  B3/N6 錨點指向的行不在帳本（截尾或改寫）＝整檔無效。
  B2/R4 涵蓋時間一律從快照重算，取 max(行內值, 重算值)；inputs_meta 必須有 dataset 欄。
  B3/R5 休市表必須帶 sha 和官方來源 meta；臨時休市清單必須帶 sha，每筆要官方網域快照、當日伺服器 Date、非寫帳方核准。

v3 判法（每條對應審查者對 v2 的審查意見，審查檔未公開）：
  R1 每行核 sha 鏈（prev_sha256 / sha256），鏈斷之後全判缺資料；每項輸入要有快照檔且 sha256 相符、
     有伺服器 HTTP Date 標頭（早於截止、和取得時間相差 ≤120 秒）；每行要有外部錨點（anchors.jsonl）早於截止。
  R2 截止時間 D 08:45：寫入、取得、伺服器時間都要 < D 08:45；涵蓋結束時間 < D 08:45。
  R3 只有日期、或沒有明確時區的時間一律解析失敗 → 缺資料。
  R4 涵蓋結束時間缺（fetch_anchor 在欄名對不上、日期解析不了、晚於取得時間時都記 null）→ 缺資料；涵蓋晚於取得也擋。
  R5 分母＝休市日曆（DG11761）列出的區間內每個交易日；沒有合格預測的日子算錯；同日只取第一行；壞行、草稿行、
     正式檔的 dry_run 行都照實記錯。
  R6 實際方向只從 --outcomes 檔取，並回頭重讀 post 快照重算；對不上或快照找不到 → 未配對日（預測照樣計入分母且算錯）。
平盤或 missing 的預測算錯；平盤實際值不屬於任何正類。"""
import argparse, datetime as dt, email.utils, glob, hashlib, json, os, re, sys
from collections import Counter
from math import comb
from zoneinfo import ZoneInfo

def _rb(p):
    """讀整個檔（bytes），讀完就關（v3.1.1 修 ResourceWarning）。"""
    with open(p, "rb") as f: return f.read()

def _rt(p, enc="utf-8"):
    """讀整個檔（文字），讀完就關。"""
    with open(p, encoding=enc) as f: return f.read()

TPE = ZoneInfo("Asia/Taipei")
MAX_SKEW = 120
PRED_TYPES = {"call", "prediction", "predict", ""}
UP = {"漲", "up", "UP", "rise"}; DOWN = {"跌", "down", "DOWN", "fall"}
SHADOW_RULES = ["P1", "P2", "P3", "P4", "P5"]
ANCHOR_SCHEME_ADOPTED = False   # rigor v3.1 B0：外部錨點方案未實作前一律判缺錨點；只能改這行並重發 code-v3.x.sha256 才能打開
ANCHOR_REPO = "william9722359/Stock-bot"
WRITERS = {"股票機器人", "stock-bot", "stockbot", "stock_bot", "anchor-bot"}   # 寫帳方：不准當核帳方（A6）
ANCHOR_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\t[0-9a-f]{64}\tL[1-3]$")
CAL_HOSTS = ("data.gov.tw", "twse.com.tw", "taifex.com.tw")
CLOSURE_HOSTS = ("taifex.com.tw", "twse.com.tw")
# v3.1.2b（B1，審查意見改法 1～3＋5；改法 4 不採）：臨時休市列的核證只認查證者在 watch-desk 以外寫的核證檔。
# 核證檔的路徑和預期 sha 只從核可者寫的這個檔讀（第一欄 sha256、第二欄絕對路徑）；沒有任何命令列參數能改。
APPROVALS_SHA_FILE = "approvals/closure-verifications.sha256"   # 公開版：相對於執行時的工作目錄；內部版是固定的絕對路徑
LIVE_NOT_STARTED = "尚未開始"   # v3.1.2b-r1：即時預測起始日不寫死；由 live_from() 從錨點證據推，沒有就是尚未開始
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_anchor as FA  # 只用它的 coverage_end（同一份判法）；recompute.py 另外獨立實作

def norm(v):
    if v is None: return "missing"
    v = str(v).strip()
    if v in UP: return "漲"
    if v in DOWN: return "跌"
    if v in {"平", "flat", "平盤"}: return "平"
    return "missing"

def g(d, *ks):
    for k in ks:
        if k in d and d[k] not in (None, ""): return d[k]
    return None

ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})")
def ts(v):
    """R3：只有日期、或沒有明確時區的一律回 None。"""
    if v in (None, ""): return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}[-/]?\d{2}[-/]?\d{2}", s): return None
    if not ISO_RE.fullmatch(s): return None
    if s.endswith("Z"): s = s[:-1] + "+00:00"
    try: t = dt.datetime.fromisoformat(s)
    except ValueError: return None
    return t if t.tzinfo is not None else None

HTTP_DATE_RE = re.compile(r"^[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} (GMT|UTC|[+-]\d{4})$")
def http_date(v):
    """伺服器 Date 標頭：只收 HTTP 格式（RFC 7231），例 'Thu, 22 Oct 2026 01:00:00 GMT'（＝台北 10/22 09:00 集中市場開盤；10/23 颱風全日休市，r4 更正）。"""
    if not v or not HTTP_DATE_RE.match(str(v).strip()): return None
    try: t = email.utils.parsedate_to_datetime(str(v))
    except (TypeError, ValueError, IndexError): return None
    return t if t is not None and t.tzinfo is not None else None

def ext_time(v):
    return ts(v) or http_date(v)

def day8(v):
    s = re.sub(r"\D", "", str(v or ""))
    return s if len(s) == 8 else None

def sha_file(p):
    try: return hashlib.sha256(_rb(p)).hexdigest()
    except OSError: return None

def canon(rec):
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def resolve(root, p):
    return p if os.path.isabs(p) else os.path.join(root, p)

# ---------------- 讀檔與 sha 鏈 ----------------
def read_chain(path):
    """回傳 [(行號, 紀錄或 None, 鏈是否完好到這行)]、檔案 sha256、斷點行號。"""
    out, prev, broken_at = [], "0" * 64, None
    raw = _rt(path).splitlines()
    i = 0
    for ln in raw:
        if not ln.strip(): continue
        i += 1
        try: d = json.loads(ln)
        except Exception: d = None
        if broken_at is None:
            if not isinstance(d, dict): broken_at = i
            else:
                body = dict(d); s = body.pop("sha256", None)
                if body.get("prev_sha256") != prev or hashlib.sha256(canon(body).encode()).hexdigest() != s:
                    broken_at = i
                else: prev = s
        out.append((i, d if isinstance(d, dict) else None, broken_at is None))
    return out, sha_file(path), broken_at

# ---------------- 外部錨點（v3.1）----------------
def host_ok(url, hosts):
    import urllib.parse
    u = urllib.parse.urlparse(str(url or ""))
    h = (u.hostname or "").lower()
    return u.scheme == "https" and any(h == x or h.endswith("." + x) for x in hosts)

_NAME_DROP = re.compile(r"[\s\-_‐‑‒–—―·・.。,，、:：/\\|]+")
_PAREN = re.compile(r"[(\[【〔〈《「『][^)\]】〕〉》」』]*[)\]】〕〉》」』]")
_PAREN_OPEN = re.compile(r"[(\[【〔〈《「『].*$")
def norm_name(v):
    """v3.1.2（N4）：NFKC＋casefold，去掉括號註記（含沒收尾的括號）、空白和連字號類符號。"""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(v if v is not None else "")).casefold()
    t = _PAREN_OPEN.sub("", _PAREN.sub("", t))
    return _NAME_DROP.sub("", t)

def is_writer(v):
    """v3.1.2（N4）：空白＝寫帳方；正規化後以寫帳方名字開頭或含寫帳方名字，一律算寫帳方（「股票機器人（依官方公告）」「Stock-Bot 2」都擋）。"""
    n = norm_name(v)
    return not n or any(n.startswith(w) or w in n for w in {norm_name(x) for x in WRITERS})

def row_sha(c):
    """核證用的列 sha：sha256(json.dumps(原列, ensure_ascii=False, sort_keys=True, separators=(',',':')))，和 fetch_anchor_v3／shadow_run 的 canon 同法。"""
    return hashlib.sha256(json.dumps(c, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

def load_verify(root):
    """v3.1.2b（B1）：回傳 ({(日期, row_sha256): 核證列}, 註記或 None)。核可者的 sha 檔缺、核證檔缺、核證檔在 root 裡、sha 不符 → 全部待核。"""
    if not os.path.isfile(APPROVALS_SHA_FILE): return {}, "核可者的核證 sha 檔不存在（全部待核）"
    parts = (_rt(APPROVALS_SHA_FILE).split("\n")[0].split(None, 1) + ["", ""])[:2]
    want, path = parts[0].strip(), parts[1].strip().lstrip("*")
    if not re.fullmatch(r"[0-9a-f]{64}", want) or not os.path.isabs(path): return {}, "核可者的核證 sha 檔格式不對（全部待核）"
    rp, rr = os.path.realpath(path), os.path.realpath(root)
    if rp == rr or rp.startswith(rr + os.sep): return {}, "核證檔在 watch-desk 根目錄裡（寫帳方範圍，全部待核）"
    if not os.path.isfile(path): return {}, "核證檔不存在（全部待核）"
    if sha_file(path) != want: return {}, "核證檔 sha256 與核可者的 sha 檔不符（全部待核）"
    out = {}
    for ln in _rt(path).splitlines():
        if not ln.strip(): continue
        try: r = json.loads(ln)
        except Exception: continue
        d = day8(r.get("date")) if isinstance(r, dict) else None
        if d and isinstance(r.get("row_sha256"), str):
            r["__verify_dir"] = os.path.dirname(os.path.abspath(path))
            out[(d, r["row_sha256"])] = r
    return out, None

def closure_verified(c, recs, dd):
    """回傳 (核證列, None) 或 (None, 待核理由)。status＝verified 只由這裡算；清單列的 status／verified_by 只供顯示，手改會讓列 sha 變掉。"""
    r = recs.get((dd, row_sha(c)))
    if r is None: return None, "臨時休市列沒有對得上的核證列（日期＋列 sha，待核）"
    if r.get("verified_by") != "查證者": return None, "核證列 verified_by 不是查證者（待核）"
    if r.get("tier") != "甲": return None, "核證列不是甲級（待核）"
    return r, None

def closure_grade_miss(c, r, root, dd):
    """裁定 2 分級：官方（level＝官方或 approval_basis＝official_notice）＝官方網址＋快照 sha＋當日來源
    （當日伺服器 Date，或核證列 browser_check 的公告頁日期＋截圖 sha）；新聞＝核證列有 https 網址＋核過的日期。回傳缺的項目。"""
    if c.get("approval_basis") == "official_notice" or c.get("level") == "官方":
        miss = [x for x in closure_evidence_ok(c, root, dd) if x != "當日伺服器 Date"]
        sd = http_date(c.get("server_date_http"))
        if sd is None or sd.astimezone(TPE).strftime("%Y%m%d") != dd:
            bc = r.get("browser_check") if isinstance(r.get("browser_check"), dict) else {}
            nd = day8(bc.get("notice_date")); sp = str(bc.get("screenshot_path") or "")
            if sp and not os.path.isabs(sp): sp = os.path.join(r.get("__verify_dir") or "", sp)
            if not (host_ok(bc.get("url"), CLOSURE_HOSTS) and nd and nd <= dd and bc.get("screenshot_sha256")
                    and os.path.isfile(sp) and sha_file(sp) == bc.get("screenshot_sha256")): miss.append("當日伺服器 Date")
        return miss
    miss = []
    urls = r.get("url") if isinstance(r.get("url"), list) else [r.get("url")]
    if not [u for u in urls if isinstance(u, str) and u.startswith("https://")]: miss.append("網址")
    if not str(r.get("source_date") or "").strip(): miss.append("核過的日期")
    return miss

def ev_bytes(root, ev):
    if not isinstance(ev, dict) or not ev.get("path") or not ev.get("sha256"): return None
    p = resolve(root, ev["path"])
    if sha_file(p) != ev["sha256"]: return None
    return _rb(p)

def push_match(e, eid, head):
    if not isinstance(e, dict): return False
    pl = e.get("payload") or {}
    return (str(e.get("id")) == eid and e.get("type") == "PushEvent" and (e.get("repo") or {}).get("name") == ANCHOR_REPO
            and pl.get("ref") == "refs/heads/main" and pl.get("head") == head)

def verify_anchor(a, root):
    """回傳 (created_at aware datetime, None) 或 (None, 理由)。行內 external_time 不看（R1）。"""
    if a.get("type") != "github-push": return None, "錨點 type 不是 github-push"
    if is_writer(a.get("verified_by")): return None, "錨點沒有 verified_by 或由寫帳方自核"
    ref = str(a.get("ref") or "")
    if not re.fullmatch(r"\d+/[0-9a-f]{40}", ref): return None, "錨點 ref 格式不對"
    eid, head = ref.split("/")
    day = day8(a.get("trading_day")); led = str(a.get("ledger") or ""); cov = str(a.get("covers_sha256") or "")
    if not day or not re.fullmatch(r"L[1-3]", led) or not re.fullmatch(r"[0-9a-f]{64}", cov): return None, "錨點欄位不全"
    evs = {e.get("kind"): e for e in (a.get("evidence") or []) if isinstance(e, dict)}
    bodies = {k: ev_bytes(root, evs.get(k)) for k in ("events-api", "gharchive", "commit-file")}
    if any(v is None for v in bodies.values()): return None, "錨點證據快照缺或 sha256 對不上"
    try: events = json.loads(bodies["events-api"].decode("utf-8"))
    except Exception: return None, "Events API 快照解析不了"
    hit = [e for e in events if push_match(e, eid, head)] if isinstance(events, list) else []
    if len(hit) != 1: return None, "Events API 快照找不到對應 main 的 PushEvent"
    t1 = ts(hit[0].get("created_at"))
    raw = bodies["gharchive"]
    if raw[:2] == b"\x1f\x8b":
        import gzip
        try: raw = gzip.decompress(raw)
        except Exception: return None, "GH Archive 快照解壓失敗"
    t2 = None
    for ln in raw.decode("utf-8", errors="replace").splitlines():
        try: e = json.loads(ln)
        except Exception: continue
        if push_match(e, eid, head): t2 = ts(e.get("created_at")); break
    if t1 is None or t2 is None or t1 != t2: return None, "Events API 與 GH Archive 的 created_at 缺或不一致"
    try:
        import base64
        cf = json.loads(bodies["commit-file"].decode("utf-8"))
        text = base64.b64decode(cf.get("content") or "").decode("utf-8")
    except Exception: return None, "commit 檔快照解析不了"
    if f"ref={head}" not in str(cf.get("url") or "") or cf.get("path") != f"anchors/{day[:4]}.txt":
        return None, "commit 檔快照不是該 head 的 anchors 檔"
    lines = [x for x in text.splitlines() if x != ""]
    if not lines or not all(ANCHOR_LINE_RE.fullmatch(x) for x in lines): return None, "anchors 檔有不合白名單格式的行"
    if f"{day[:4]}-{day[4:6]}-{day[6:]}\t{cov}\t{led}" not in lines: return None, "anchors 檔裡沒有這一行"
    return t1, None

def load_anchors(path, root):
    """回傳 (錨點清單 [(covers, 時間, 帳本代號, 日期)], 多押鍵集合 {(日期, 代號)}, 理由 Counter)。"""
    rs = Counter()
    if not ANCHOR_SCHEME_ADOPTED:
        rs["外部錨點方案尚未採用（v3.1 B0）：一律判缺錨點"] += 1
        return [], set(), rs
    if not path or not os.path.exists(path): return [], set(), rs
    good = []
    for ln in _rt(path).splitlines(True):
        if not ln.strip(): continue
        try: a = json.loads(ln)
        except Exception: rs["錨點清單壞行"] += 1; continue
        if not isinstance(a, dict): rs["錨點清單壞行"] += 1; continue
        t, why = verify_anchor(a, root)
        if why: rs["錨點不採用：" + why] += 1; continue
        good.append((a["covers_sha256"], t, a["ledger"], day8(a["trading_day"])))
    first, multi = {}, set()
    for c, t, led, day in sorted(good, key=lambda x: x[1]):
        k = (day, led)
        if k in first and first[k] != c: multi.add(k)
        first.setdefault(k, c)
    if multi: rs["同一（日期, 帳本）錨點出現第二個值＝多押嫌疑 " + json.dumps(sorted(multi), ensure_ascii=False)] += 1
    return [x for x in good if (x[3], x[2]) not in multi], multi, rs

def live_from(anchors):
    """v3.1.2b-r1 裁定 4：即時預測起始日＝第一個「有 anchors/ 紀錄、GitHub 伺服器端 push 時間早於該日 08:45 台北」的交易日。
    anchors 只收 load_anchors 驗過的（Events API 與 GH Archive 的 created_at 一致＝GitHub 伺服器端時間；行內 external_time 不看）。
    沒有這種錨點、或時間缺 → None（尚未開始）。週六日不算交易日。不連網、不寫死日期。"""
    days = []
    for _c, t, _led, day in anchors:
        if t is None or not day: continue
        try: d0 = dt.datetime.strptime(day, "%Y%m%d")
        except ValueError: continue
        if d0.weekday() >= 5: continue
        if t < d0.replace(hour=8, minute=45, tzinfo=TPE): days.append(day)
    return min(days) if days else None

def ledger_code(root, path):
    """A7：帳本檔名 → 中性代號 L1–L3（對照表只放私有 root/ledger-codes.json，不進 repo）。"""
    try: m = json.loads(_rt(os.path.join(root, "ledger-codes.json")))
    except Exception: return None
    rev = {v: k for k, v in m.items() if re.fullmatch(r"L[1-3]", str(k))}
    return rev.get(os.path.basename(path))

# ---------------- 防偷看 ----------------
def inputs_of(d):
    m = d.get("inputs_meta")
    if isinstance(m, dict):
        return [(k, v if isinstance(v, dict) else {}) for k, v in m.items()]
    if isinstance(d.get("inputs"), list):
        return [(str(it.get("name", i)), it) for i, it in enumerate(d["inputs"]) if isinstance(it, dict)]
    return []

def csv_rows_bytes(b):
    import csv, io
    return list(csv.DictReader(io.StringIO(b.decode("utf-8-sig", errors="replace"))))

def rederive_coverage(it, root, ft):
    """B2/R4：從快照重算涵蓋結束時間。回傳 (重算值；None＝檔內沒有日期欄可重算, 錯誤字串或 None)。"""
    if "dataset" not in it: return None, "inputs_meta 缺 dataset 欄"
    ds = it.get("dataset")
    body = _rb(resolve(root, it["snapshot_path"]))
    fail = "從快照重算涵蓋結束時間失敗（或晚於取得時間）"
    if ds in FA.UNDATED: return ft, None
    if ds in FA.DATED:
        refs = []
        if ds == "DG11319":
            for r in it.get("ref_snapshots") or []:
                b = ev_bytes(root, r)
                if b is None: return None, "DG11319 先前快照不存在或 sha256 對不上"
                refs.append(b)
        rc, _ = FA.coverage_end(ds, body, ft, refs)
        return (rc, None) if rc is not None else (None, fail)
    if ds is not None: return None, f"資料集 {ds} 不在已知清單"
    try: rs = csv_rows_bytes(body)
    except Exception: return None, None
    if not rs or "日期" not in rs[0]: return None, None
    bound = None
    for r in rs:
        raw = (r.get("日期") or "").strip()
        if not raw:
            if all(not (v or "").strip() for v in r.values() if isinstance(v, str)): continue
            return None, fail
        dd = FA.ymd_of(raw)
        if dd is None: return None, fail
        e = dt.datetime(dd.year, dd.month, dd.day, 23, 59, 59, tzinfo=TPE)
        bound = e if bound is None or e > bound else bound
    return bound, None

def leak_check(d, day, root, anchored_before):
    d0 = dt.datetime(int(day[:4]), int(day[4:6]), int(day[6:]), tzinfo=TPE)
    cut = d0.replace(hour=8, minute=45)
    w = ts(g(d, "written_at_tpe"))
    if w is None: return "缺寫入時間或時間沒有時區"
    if w >= cut: return "寫入時間不早於 08:45"
    ins = inputs_of(d)
    if not ins: return "缺 inputs_meta（v3 錨點欄位）"
    for name, it in ins:
        ft = ts(it.get("fetched_at_tpe"))
        if ft is None: return "輸入缺取得時間或沒有時區"
        if ft >= w or ft >= cut: return "輸入取得時間不早於寫入時間或 08:45"
        rs = ts(it.get("request_started_tpe"))
        if it.get("request_started_tpe") is not None and (rs is None or rs > ft): return "輸入請求開始時間異常"
        sd = http_date(it.get("server_date_http"))
        if sd is None: return "輸入缺伺服器 Date 標頭"
        if sd >= cut or sd >= w: return "伺服器 Date 不早於寫入時間或 08:45"
        if abs((sd - ft).total_seconds()) > MAX_SKEW: return "伺服器 Date 與取得時間相差超過 120 秒"
        sp, sh = it.get("snapshot_path"), it.get("snapshot_sha256")
        if not sp or not sh: return "輸入缺快照路徑或 sha256"
        if sha_file(resolve(root, sp)) != sh: return "快照檔不存在或 sha256 對不上"
        ct = ts(it.get("coverage_end_tpe"))
        if ct is None: return "輸入缺涵蓋結束時間（或來源異常）"
        rc, err = rederive_coverage(it, root, ft)
        if err: return err
        if rc is not None and rc > ct: ct = rc
        if ct > ft: return "涵蓋結束時間晚於取得時間（異常）"
        if ct >= cut: return "輸入涵蓋到預測日 08:45 以後"
    if not anchored_before(cut): return "沒有早於 08:45 的外部錨點"
    return None

# ---------------- 實際方向（R6）----------------
def roc8(s):
    s = str(s or "").strip()
    if re.fullmatch(r"\d{7}", s): return f"{int(s[:3]) + 1911}{s[3:]}"
    return day8(s)

def csv_rows(p):
    import csv, io
    return list(csv.DictReader(io.StringIO(_rb(p).decode("utf-8-sig", errors="replace"))))

def find_snapshot(root, day, ds, src):
    sh = (src or {}).get("sha256")
    if not sh: return None
    cands = []
    if src.get("snapshot_path"): cands.append(resolve(root, src["snapshot_path"]))
    cands += sorted(glob.glob(os.path.join(root, "snapshots", day, f"post-{ds}-*.csv")))
    for p in cands:
        if sha_file(p) == sh: return p
    return None

def rederive(root, day, rec):
    srcs = rec.get("sources") or {}
    p = find_snapshot(root, day, "DG11672", srcs.get("DG11672"))
    if p:
        for r in csv_rows(p):
            if roc8(r.get("日期")) == day:
                try: ch = float(str(r.get("漲跌點數", "")).replace(",", ""))
                except ValueError: return None, "DG11672 快照漲跌點數解析不了"
                return ("漲" if ch > 0 else "跌" if ch < 0 else "平"), None
    p = find_snapshot(root, day, "DG11555", srcs.get("DG11555"))
    if p:
        for r in csv_rows(p):
            if (r.get("指數/報酬指數") or "").strip() == "發行量加權股價指數" and roc8(r.get("日期")) == day:
                try: ch = float(str(r.get("漲跌點數", "")).replace(",", ""))
                except ValueError: return None, "DG11555 快照漲跌點數解析不了"
                if (r.get("漲跌(+/-)") or "").strip() == "-": ch = -ch
                return ("漲" if ch > 0 else "跌" if ch < 0 else "平"), None
    return None, "post 快照找不到、sha256 對不上或沒有該日收盤"

def load_outcomes(path, root, reasons):
    outs, why = {}, {}
    if not path or not os.path.exists(path): return outs, why
    recs, _, _ = read_chain(path)
    for i, d, ok in recs:
        if d is None or str(g(d, "kind", "type") or "") != "outcome": continue
        day = day8(g(d, "trading_day", "trade_date", "date"))
        if not day or day in outs or day in why:
            if day: reasons["outcome 同日重複行（取第一行）"] += 1
            continue
        if not ok: why[day] = "outcome 行在 sha 鏈斷點之後"; continue
        if d.get("dry_run") and not ARGS.dry_run_file: why[day] = "outcome 行是 dry_run"; continue
        a, err = rederive(root, day, d)
        if a is None: why[day] = err; continue
        if norm(d.get("actual")) != a and not (a == "平" and norm(d.get("actual")) == "平"):
            why[day] = f"outcome 行 actual={d.get('actual')} 與快照重算 {a} 不一致"; continue
        outs[day] = a
    return outs, why

# ---------------- 交易日（R5）----------------
def trading_days(cal_path, since, until, closures):
    rows = csv_rows(cal_path)
    hol, years = set(), set()
    for r in rows:
        dd = roc8(r.get("日期"))
        if not dd: continue
        years.add(dd[:4])
        name = r.get("名稱", "")
        if "開始交易" in name or "最後交易" in name: continue
        hol.add(dd)
    out, t = [], dt.date(int(since[:4]), int(since[4:6]), int(since[6:]))
    end = dt.date(int(until[:4]), int(until[4:6]), int(until[6:]))
    while t <= end:
        k = t.strftime("%Y%m%d")
        if k[:4] not in years: raise SystemExit(f"休市日曆 {cal_path} 沒有 {k[:4]} 年，無法列出分母")
        if t.weekday() < 5 and k not in hol and k not in closures: out.append(k)
        t += dt.timedelta(days=1)
    return out

CLOSURE_LEVELS = ("官方", "新聞")
DEFAULT_CLOSURES = os.path.join("closures", "adhoc-closures.jsonl")   # v3.1.2（C4）：相對 --root；旁邊 adhoc-closures.sha256 是凍結 sha

def default_closures(root):
    """v3.1.2（C4）：沒帶 --closures 也套用 root/closures/adhoc-closures.jsonl；檔案或 sha 檔缺、sha 不符一律拒算。"""
    p = os.path.join(root, DEFAULT_CLOSURES); sp = p[:-len(".jsonl")] + ".sha256"
    if not os.path.exists(p) or not os.path.exists(sp): sys.exit(f"拒算：預設臨時休市清單 {p} 或 {sp} 不存在（v3.1.2 起 --closures 預設開啟）")
    want = (_rt(sp).split() or [""])[0]
    if sha_file(p) != want: sys.exit(f"拒算：預設臨時休市清單 {p} 的 sha256 與 {sp} 不符")
    return p

def closure_evidence_ok(c, root, dd):
    """官方網址（https、taifex／twse）、快照 sha 相符、伺服器 Date 在當天台北日期：回傳缺的項目清單。"""
    miss = []
    if not host_ok(c.get("url"), CLOSURE_HOSTS): miss.append("官方網址")
    if not (c.get("snapshot_path") and c.get("snapshot_sha256") and sha_file(resolve(root, c["snapshot_path"])) == c.get("snapshot_sha256")): miss.append("快照 sha")
    sd = http_date(c.get("server_date_http"))
    if sd is None or sd.astimezone(TPE).strftime("%Y%m%d") != dd: miss.append("當日伺服器 Date")
    return miss

def load_closures(path, root, rs, verify=({}, None), live=None):
    """v3.1.2b：扣分母只收「查證者核證檔裡（日期, 列 sha）對得上、verified_by＝查證者、甲級」的列（status＝verified 由程式算），
    分級（有填時）是官方或新聞，再照裁定 2 分級查證據。清單列自己的 status／verified_by 不看。
    只休夜盤列：date＝目標交易日（那一場夜盤歸屬的交易日），announced_date＝宣布日；14:00 前宣布只管即時預測（目標日 ≥ 即時預測起始日 live；live＝None＝尚未開始，全部當歷史回測列）。"""
    ok = {}
    if not path: return ok
    recs, note = verify
    rows = 0
    for ln in _rt(path).splitlines(True):
        if not ln.strip(): continue
        rows += 1
        try: c = json.loads(ln)
        except Exception: c = None
        dd = day8(c.get("date")) if isinstance(c, dict) else None
        if not dd:
            rs["臨時休市清單不合格行（不扣）"] += 1; continue
        if "level" in c and c.get("level") not in CLOSURE_LEVELS:
            rs["臨時休市清單不合格行（不扣）"] += 1; rs["臨時休市列分級不是官方或新聞（不扣）"] += 1; continue
        r, why = closure_verified(c, recs, dd)
        if r is None:
            rs["臨時休市列待查證者核證（pending_verify，不扣）"] += 1; rs[why] += 1; continue
        rs["臨時休市列依查證者核證檔算成 verified"] += 1
        miss = closure_grade_miss(c, r, root, dd)
        if miss:
            rs["臨時休市清單不合格行（不扣）"] += 1
            if c.get("approval_basis") == "official_notice": rs["approval_basis=official_notice 缺" + "、".join(miss) + "（拒絕）"] += 1
            elif c.get("level") == "官方": rs["官方級缺" + "、".join(miss) + "（不扣）"] += 1
            else: rs["新聞級缺" + "、".join(miss) + "（不扣）"] += 1
            continue
        if c.get("session", "all") == "night":
            ad = day8(c.get("announced_date"))
            if not ad or ad > dd:
                rs["只休夜盤行沒有合格的 announced_date（不採計，夜盤照常）"] += 1; continue
            rs["即時預測起始日：" + (live or LIVE_NOT_STARTED) + "（只認 GitHub 伺服器端 push 時間早於當日 08:45 的 anchors 紀錄）"] += 1
            if live is not None and dd >= live:
                # T111 一(三)：即時預測要 14:00 前宣布＝只休夜盤、日盤照開
                at = ts(c.get("announced_at_tpe"))
                if not (at is not None and at.astimezone(TPE).strftime("%Y%m%d") == ad and at.astimezone(TPE).time() < dt.time(14, 0)):
                    rs["只休夜盤行沒有 14:00 前的宣布時間（不採計，夜盤照常）"] += 1; continue
            ok["N:" + dd + ":" + ad] = c.get("url")
        elif c.get("session", "all") == "all": ok[dd] = c.get("url")
        else: rs["臨時休市清單不合格行（不扣）"] += 1
    if rows and note: rs[note] += 1
    return ok

def check_calendar(cal, want):
    """B3/N5：休市表必須明確給路徑和 sha256；旁邊要有 meta（https 官方網域網址、sha256 相符）。不合就拒算。"""
    if not cal or not want: sys.exit("拒算：v3.1 要求 --calendar 和 --calendar-sha256")
    if not os.path.exists(cal) or sha_file(cal) != want: sys.exit("拒算：休市表不存在或 sha256 與 --calendar-sha256 不符")
    base = cal[:-4] if cal.endswith(".csv") else cal
    for mp in (base + ".meta.json", cal + ".meta.json"):
        if os.path.exists(mp):
            try: m = json.loads(_rt(mp))
            except Exception: m = {}
            if isinstance(m, dict) and host_ok(m.get("url"), CAL_HOSTS) and want in (m.get("sha256"), m.get("snapshot_sha256")): return
    sys.exit("拒算：休市表沒有官方來源 meta（https 官方網域網址＋相同 sha256）")

# ---------------- 統計 ----------------
def binom_p(k, n, p0):
    """單尾二項 P(X ≥ k | n, p0)。v3.1.1（審查意見 P-B3，審查檔未公開）：p0 必須由呼叫端明確給同期天天猜漲率，沒有預設值（53.6% 舊數已拿掉）。"""
    if p0 is None or isinstance(p0, bool) or not isinstance(p0, (int, float)) or not 0 <= p0 <= 1:
        raise ValueError("拒算：binom_p 要明確給同期天天猜漲率 p0（0–1），不准用預設值")
    if n == 0: return None
    return sum(comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(k, n + 1))

def holm(ps):
    idx = [i for i, p in enumerate(ps) if p is not None]; m = len(idx)
    order = sorted(idx, key=lambda i: ps[i]); adj = [None] * len(ps); run = 0
    for r, i in enumerate(order):
        run = max(run, min(1, (m - r) * ps[i])); adj[i] = run
    return adj

def counts(pairs, pos):
    return {"tp": sum(1 for p, a in pairs if p == pos and a == pos),
            "fp": sum(1 for p, a in pairs if p == pos and a != pos),
            "fn": sum(1 for p, a in pairs if p != pos and a == pos)}

# ---------------- 主流程 ----------------
NOT_SCORED = {"practice-calls-L2.jsonl": "practice-calls-L2.jsonl 自 2026-10-23 起不計分（主人裁定：只計大盤指數；舊行保留）"}   # 2026-10-23 裁定：只算大盤指數
# v3.1.2 更正：上一行「主人裁定」是協調助理轉達（非本人核可），待本人確認；原話出處未核。

def score_file(path, root, outcomes_path, anchor_pack, cal_path, closures, base_reasons=()):
    reasons = Counter(dict(base_reasons))
    anchors_all, multi = anchor_pack
    if os.path.basename(path) in NOT_SCORED:
        return {"file": path, "exists": os.path.exists(path), "excluded": NOT_SCORED[os.path.basename(path)]}
    if not os.path.exists(path):
        return {"file": path, "exists": False}
    recs, fsha, broken = read_chain(path)
    if broken: reasons[f"sha 鏈斷在第 {broken} 行（之後全判缺資料）"] += 1
    idx_of = {d["sha256"]: i for i, d, ok in recs if d and ok and "sha256" in d}
    code = ledger_code(root, path)
    if code is None and ANCHOR_SCHEME_ADOPTED: reasons["帳本沒有中性代號對照（ledger-codes.json），對不到錨點"] += 1
    anchors = [(s, t) for s, t, led, _ in anchors_all if led == code]
    invalid = any(s not in idx_of for s, _ in anchors)
    if invalid: reasons["錨點指向的行不存在＝帳本被截或改（整檔無效）"] += 1
    def anchored(i):
        def f(cut):
            return any(idx_of.get(s, -1) >= i and t < cut for s, t in anchors)
        return f
    preds, seen_days, rules_seen = {}, set(), set()
    for i, d, ok in recs:
        if d is None: reasons["JSON 壞行"] += 1; continue
        typ = str(g(d, "kind", "type", "record_type") or "")
        if typ == "outcome": continue
        if typ == "anchor": reasons["帳本內的錨點行（不採用、不計）"] += 1; continue
        if typ == "late_input": reasons["late_input 行（不計分）"] += 1; continue
        day = day8(g(d, "trading_day", "trade_date", "交易日", "預測交易日", "date", "target_date"))
        if not day: reasons["預測日格式不明的行"] += 1; continue
        seen_days.add(day)
        if typ == "not_run" or g(d, "status") == "not_run":
            reasons["not_run 行（該日算錯）"] += 1
            preds.setdefault(("*", day), "missing"); continue
        rule = str(g(d, "rule_id", "rule", "規則編號") or ARGS.stage); rules_seen.add(rule)
        if (rule, day) in preds or ("*", day) in preds:
            reasons["同日重複預測行（取第一行）"] += 1; continue
        why = None
        if typ not in PRED_TYPES: why = f"非預測型別 {typ!r} 的行"
        elif not ok: why = "sha 鏈斷點之後的行"
        elif d.get("dry_run") and not ARGS.dry_run_file: why = "正式檔有 dry_run 行"
        elif invalid: why = "整檔無效（錨點指向的行不存在）"
        elif (day, code) in multi: why = "同一（日期, 帳本）錨點多值＝多押嫌疑"
        else: why = leak_check(d, day, root, anchored(i))
        if why is None and norm(g(d, "call", "prediction", "direction", "漲或跌", "預測")) == "missing":
            why = "預測方向缺或認不得"
        if why: reasons[why] += 1; preds[(rule, day)] = "missing"
        else: preds[(rule, day)] = norm(g(d, "call", "prediction", "direction", "漲或跌", "預測"))
    since, until = day8(ARGS.since), day8(ARGS.until)   # v3.1 B3/N6：必填（main 已檢查），不從帳本推，帳尾缺的日子照樣在分母
    if ARGS.rules: rules = ARGS.rules.split(",")
    elif "shadow" in os.path.basename(path) or rules_seen & set(SHADOW_RULES): rules = SHADOW_RULES
    else: rules = sorted(rules_seen) or [ARGS.stage]
    res = {"file": path, "exists": True, "file_sha256": fsha, "lines": len(recs), "chain_break_line": broken,
           "range": [since, until], "calendar": cal_path, "calendar_sha256": sha_file(cal_path) if cal_path else None,
           "closures": sorted(closures), "rules": {}, "unpaired_days": {}, "reasons": {}, "stage": ARGS.stage}
    if not since:
        res["trading_days"] = []; res["reasons"] = dict(reasons); return res
    days = trading_days(cal_path, since, until, closures)
    res["trading_days"] = days
    res["night_closed_next_day"] = {}
    for k in sorted(closures):
        if k.startswith("N:"):
            tgt, ad = k[2:].split(":")
            if tgt in days: res["night_closed_next_day"][tgt] = ad   # v3.1.2b 裁定 4：鍵＝目標交易日，值＝宣布日；該日用到夜盤的規則記 missing，週報另列
    off = sorted(x for x in seen_days if x not in days and since <= x <= until)
    if off: reasons[f"非交易日的預測行日期 {off}"] += 1
    outs, why_un = load_outcomes(outcomes_path, root, reasons)
    for dd in days:
        if dd not in outs: res["unpaired_days"][dd] = why_un.get(dd, "沒有 outcome 行")
    ks, ns = [], []
    for rule in rules:
        pairs = []
        for dd in days:
            p = preds.get((rule, dd)) or preds.get(("*", dd)) or "missing"
            if (rule, dd) not in preds and ("*", dd) not in preds: reasons["交易日沒有預測行（算錯）"] += 1
            pairs.append((p, outs.get(dd)))
        n = len(pairs); k = sum(1 for p, a in pairs if p == a and a in ("漲", "跌"))
        res["rules"][rule] = {"n": n, "hits": k, "always_up_hits": sum(1 for _, a in pairs if a == "漲"),
                              "up": counts(pairs, "漲"), "down": counts(pairs, "跌"),
                              "days": {dd: [p, a] for dd, (p, a) in zip(days, pairs)}}
        ks.append(k); ns.append(n)
    # predict-algo §11：正式比較＝H0 命中率 ≤ 同期間實際天天猜漲率（平盤算錯）
    # v3.1.2b-r6（N1）：有任何未配對日，同期基準不知道，不當 0 算
    p0s = [res["rules"][r]["always_up_hits"] / n if n and not res["unpaired_days"] else None for r, n in zip(rules, ns)]
    ps2 = [binom_p(k, n, p0) if n and p0 is not None else None for k, n, p0 in zip(ks, ns, p0s)]
    for rule, p0, p, q in zip(rules, p0s, ps2, holm(ps2)):
        res["rules"][rule].update(always_up_rate=p0, p_same_period=p, holm_p_same_period=q)
    res["reasons"] = dict(reasons)
    return res

pct = lambda x: "無法計算" if x is None else f"{x * 100:.1f}%"
def show(res):
    print(f"== {res['file']}")
    if res.get("excluded"): print(f"  不計分：{res['excluded']}"); return
    if not res.get("exists"): print("  檔案不存在：n=0，全部無法計算（樣本太少，參考用）"); return
    print(f"  檔案 sha256 {res['file_sha256']}，{res['lines']} 行；區間 {res['range'][0]}–{res['range'][1]}；"
          f"休市日曆 {res['calendar']}（sha256 {res['calendar_sha256']}）")
    if res["reasons"]: print("  照實列出（算錯或略過）：" + "；".join(f"{k} {v}" for k, v in res["reasons"].items()))
    if res["unpaired_days"]: print("  未配對日（預測照樣計入分母、算錯）：" + "；".join(f"{k} {v}" for k, v in res["unpaired_days"].items()))
    for rule, r in res["rules"].items():
        n = r["n"]
        print(f"{rule}｜n={n}" + ("（樣本太少，參考用）" if n < 30 else ""))
        print(f"  命中率 {pct(r['hits'] / n if n else None)}｜同期天天猜漲 {pct(r['always_up_rate'])}（{res.get('stage', '試跑')}基準）"
              f"｜回測參考：近 5 年 55.65%（n=1213）、全期 53.00%（n=6887），FinMind，只供回測參考"
              f"｜53.6% 舊數、來源不合規，不當基準")
        for pos, key in (("漲", "up"), ("跌", "down")):
            c = r[key]; P = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else None
            R = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else None
            F = 2 * P * R / (P + R) if P is not None and R is not None and P + R > 0 else None
            print(f"  以{pos}為正類：Precision {pct(P)}｜Recall {pct(R)}｜F1 {pct(F)}")
    if res["rules"]:
        print(f"  {res.get('stage', '試跑')}檢定（單尾二項，H0 命中率 ≤ 同期間實際天天猜漲率；多條規則用 Holm 校正；predict-algo §11）：")
        for rule, r in res["rules"].items():
            note = "（n<30，不下有效或無效結論）" if r["n"] < 30 else ""
            p, q = r["p_same_period"], r["holm_p_same_period"]
            print(f"   {rule}: {r['hits']}/{r['n']}，基準 {pct(r['always_up_rate'])}，p={'無法計算' if p is None else f'{p:.4f}'}，"
                  f"Holm 校正 p={'無法計算' if q is None else f'{q:.4f}'}{note}")

RULE_KEYS = ("rule_id", "rule", "規則編號")
def check_outcomes_file(path, want_sha):
    """v3.1.2b-r6（N1）：實際方向檔只准是 outcome 帳本；影子帳本或混入其他行一律拒算。"""
    if not want_sha: sys.exit("拒算：v3.1.2b-r6 起 --outcomes-sha256 必填")
    if sha_file(path) != want_sha.strip().lower(): sys.exit(f"拒算：--outcomes 檔 sha256 與 --outcomes-sha256 不符：{path}")
    if os.path.basename(path).lower().startswith("shadow"): sys.exit(f"拒算：--outcomes 檔名以 shadow 開頭（影子規則帳本不能當實際方向）：{path}")
    n = 0
    for i, ln in enumerate(_rt(path).splitlines(), 1):
        if not ln.strip(): continue
        try: d = json.loads(ln)
        except Exception: sys.exit(f"拒算：--outcomes 檔第 {i} 行不是 JSON")
        if not isinstance(d, dict): sys.exit(f"拒算：--outcomes 檔第 {i} 行不是 JSON 物件")
        if str(g(d, "kind", "type", "record_type") or "") != "outcome": sys.exit(f"拒算：--outcomes 檔第 {i} 行 kind 不是 outcome")
        if any(k in d for k in RULE_KEYS): sys.exit(f"拒算：--outcomes 檔第 {i} 行帶規則欄（outcome 帳本不該有規則）")
        n += 1
    if n == 0: sys.exit(f"拒算：--outcomes 檔沒有任何 outcome 行：{path}")

def main(argv=None):
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--since"); ap.add_argument("--until")
    ap.add_argument("--calendar", help="必填：DG11761 快照（旁邊要有官方來源 .meta.json）")
    ap.add_argument("--calendar-sha256", help="必填：休市表凍結 sha256，對不上就拒算")
    ap.add_argument("--closures", help="臨時休市清單；v3.1.2 起不給＝套用 root/closures/adhoc-closures.jsonl（sha 照旁邊的 .sha256）。扣分母要 status=verified、verified_by 非寫帳方、官方快照、當日伺服器 Date")
    ap.add_argument("--closures-sha256", help="給 --closures 時必填：清單檔凍結 sha256")
    ap.add_argument("--outcomes", help="必填（v3.1.2b-r5）：實際方向唯一來源，檔案必須存在；沒有預設檔")
    ap.add_argument("--outcomes-sha256", help="必填（v3.1.2b-r6）：--outcomes 檔的 sha256，對不上就拒算")
    ap.add_argument("--anchors", help="外部錨點清單；預設 root/anchors.jsonl")
    ap.add_argument("--rules", help="逗號分隔的規則編號（決定分母裡的規則）")
    ap.add_argument("--dry-run-file", action="store_true", help="測試／乾跑檔：dry_run 行照常判（結果不是正式成績）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("files", nargs="+")
    ARGS = ap.parse_args(argv)
    root = ARGS.root
    if not day8(ARGS.since) or not day8(ARGS.until):
        sys.exit("拒算：v3.1 要求 --since 和 --until（起日照規則寫死，迄日＝計分當天前的最後交易日）")
    cal = ARGS.calendar
    check_calendar(cal, ARGS.calendar_sha256)
    base = Counter()
    clo_path = None
    if ARGS.closures:
        if not ARGS.closures_sha256 or sha_file(ARGS.closures) != ARGS.closures_sha256:
            sys.exit("拒算：給了 --closures 就必須給相符的 --closures-sha256")
        clo_path = ARGS.closures
    else:
        if ARGS.closures_sha256: sys.exit("拒算：給了 --closures-sha256 卻沒有 --closures")
        clo_path = default_closures(root)
    if not ARGS.outcomes: sys.exit("拒算：v3.1.2b-r5 起 --outcomes 必填（不再預設讀影子規則檔）")
    if not os.path.isfile(ARGS.outcomes): sys.exit(f"拒算：--outcomes 檔不存在：{ARGS.outcomes}")
    check_outcomes_file(ARGS.outcomes, ARGS.outcomes_sha256)
    outcomes = ARGS.outcomes
    anchors, multi, ars = load_anchors(ARGS.anchors or os.path.join(root, "anchors.jsonl"), root)
    base.update(ars)
    closures = load_closures(clo_path, root, base, load_verify(root), live_from(anchors))
    lf = live_from(anchors)   # v3.1.2b-r6：起始日之前（含尚未開始）一律是試跑
    ARGS.stage = "正式" if lf is not None and day8(ARGS.since) >= lf else "試跑"
    allres = [score_file(p, root, outcomes, (anchors, multi), cal, closures, base) for p in ARGS.files]
    if ARGS.json:
        print(json.dumps(allres, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        if ARGS.dry_run_file: print("【乾跑／測試檔：不是正式成績】")
        for r in allres: show(r)
    return allres

ARGS = None
if __name__ == "__main__":
    main()
