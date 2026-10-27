#!/usr/bin/env python3
"""README 成績單更新（只改 <!-- SCORECARD:BEGIN --> 與 <!-- SCORECARD:END --> 之間）。

輸入：score_prf.py --json 的輸出兩份（當週一份、累計一份），只取正式帳本 practice-calls.jsonl 那一筆。
只列大盤指數一條線；影子規則、回測、練習數一律不收（遇到就拒跑）。
同一份輸入重跑，README 與 HTML 結果逐字相同（不讀時鐘、不讀網路）。

用法：
  python3 code/update_scorecard.py --week 當週.json --cum 累計.json --readme README.md [--html scorecard.html]
  python3 code/update_scorecard.py --check --week … --cum … --readme README.md   # 只比對，不寫；不同就回非 0
"""
import argparse, html, json, os, sys

def _rb(p):
    """讀整個檔（bytes），讀完就關（v3.1.1 修 ResourceWarning）。"""
    with open(p, "rb") as f: return f.read()

def _rt(p, enc="utf-8"):
    """讀整個檔（文字），讀完就關。"""
    with open(p, encoding=enc) as f: return f.read()

def _w(p, data, mode="w", enc=None):
    """寫整個檔，寫完就關。"""
    with open(p, mode, **({} if "b" in mode else {"encoding": enc})) as f: return f.write(data)

BEGIN, END = "<!-- SCORECARD:BEGIN -->", "<!-- SCORECARD:END -->"
FORMAL = "practice-calls.jsonl"
COLS = ["區間", "期間", "n", "命中率", "同期實際天天猜漲率",
        "以漲為正類 Precision", "以漲為正類 Recall", "以漲為正類 F1",
        "以跌為正類 Precision", "以跌為正類 Recall", "以跌為正類 F1", "備註"]
NA = "無法計算"
SMALL = "樣本太少，只供參考"

def die(msg): sys.exit("拒跑：" + msg)

def pick(path):
    """從 score_prf --json 輸出挑出正式帳本那一筆；其他種類一律拒。"""
    with open(path, encoding="utf-8") as f: data = json.load(f)
    if isinstance(data, dict): data = [data]
    hit = []
    for r in data:
        name = os.path.basename(str(r.get("file", "")))
        if name == FORMAL: hit.append(r)
        elif "shadow" in name or "backtest" in name or "dry" in name:
            continue   # 不是正式帳本，不進表
    if len(hit) != 1: die(f"{path} 裡正式帳本 {FORMAL} 要剛好一筆，實得 {len(hit)} 筆")
    r = hit[0]
    if "dry-run" in str(r.get("file", "")) or "dry_run" in str(r.get("file", "")): die(f"{path}：乾跑目錄的帳本不是正式成績")
    if r.get("excluded"): die(f"{path}：正式帳本被標為不計分")
    rules = r.get("rules") or {}
    if len(rules) > 1: die(f"{path}：只准一條線（大盤指數），實得規則 {sorted(rules)}")
    return r

def pct(x): return NA if x is None else f"{x * 100:.1f}%"
def ratio(a, b): return a / b if b else None

def row(label, r):
    """回傳一列儲存格；尚無正式預測時除區間、備註外全留空。"""
    rules = r.get("rules") or {}
    rr = next(iter(rules.values()), None)
    n = rr["n"] if rr else 0
    if not r.get("exists") or not r.get("lines") or rr is None or n == 0:
        return [label] + [""] * 10 + ["尚無正式預測、待更新"], 0
    rng = r.get("range") or ["", ""]
    cells = [label, f"{rng[0]}–{rng[1]}", str(n), pct(ratio(rr["hits"], n)), pct(ratio(rr["always_up_hits"], n))]
    for key in ("up", "down"):
        c = rr[key]
        P = ratio(c["tp"], c["tp"] + c["fp"]); R = ratio(c["tp"], c["tp"] + c["fn"])
        F = 2 * P * R / (P + R) if P is not None and R is not None and P + R > 0 else None
        cells += [pct(P), pct(R), pct(F)]
    cells.append(SMALL if n < 30 else "")
    return cells, n

def build(week, cum):
    wr, cr = pick(week), pick(cum)
    if wr.get("range") and cr.get("range") and wr.get("exists") and cr.get("exists"):
        if not (cr["range"][0] <= wr["range"][0] and wr["range"][1] == cr["range"][1]):
            die(f"當週區間 {wr['range']} 不在累計區間 {cr['range']} 的尾端")
    rows, ns = [], []
    for label, r in (("當週", wr), ("累計", cr)):
        cells, n = row(label, r); rows.append(cells); ns.append(n)
    return rows, ns

def notes(ns):
    out = []
    if max(ns) == 0: out.append("尚無正式預測、待更新。表格留空，不以回測或練習數充數。")
    elif min(ns) < 30: out.append("n<30 的列：樣本太少，只供參考。")
    out.append("只計正式預測（大盤指數一條線）；影子規則、回測、練習數都不列在這張表。"
               "缺預測或資料不合格的交易日照樣算進 n、算錯。分母是 0 的格寫「無法計算」。"
               "同期實際天天猜漲率＝同一區間實際收漲的交易日 ÷ n。")
    return out

def markdown(rows, ns):
    L = [BEGIN, "## 成績單（大盤指數，正式預測）", ""]
    for t in notes(ns): L += [t, ""]
    L.append("| " + " | ".join(COLS) + " |")
    L.append("|" + "---|" * len(COLS))
    for cells in rows: L.append("| " + " | ".join(cells) + " |")
    L.append("")
    L.append("本段由 `code/update_scorecard.py` 依 `code/score_prf.py --json` 的輸出產生，請勿手改。")
    L.append(END)
    return "\n".join(L)

def html_table(rows, ns):
    e = html.escape
    h = ["<!DOCTYPE html>", '<html lang="zh-Hant"><head><meta charset="utf-8"><title>成績單（大盤指數，正式預測）</title></head><body>',
         "<h2>成績單（大盤指數，正式預測）</h2>"]
    h += [f"<p>{e(t)}</p>" for t in notes(ns)]
    h.append("<table border=\"1\">")
    h.append("<tr>" + "".join(f"<th>{e(c)}</th>" for c in COLS) + "</tr>")
    for cells in rows: h.append("<tr>" + "".join(f"<td>{e(c)}</td>" for c in cells) + "</tr>")
    h += ["</table>", "</body></html>", ""]
    return "\n".join(h)

def splice(text, block):
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
        die("README 的 SCORECARD 標記要各剛好一個、且 BEGIN 在 END 前")
    a = text.index(BEGIN); b = text.index(END) + len(END)
    return text[:a] + block + text[b:]

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", required=True); ap.add_argument("--cum", required=True)
    ap.add_argument("--readme", required=True); ap.add_argument("--html")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    rows, ns = build(a.week, a.cum)
    with open(a.readme, encoding="utf-8") as f: old = f.read()
    new = splice(old, markdown(rows, ns))
    page = html_table(rows, ns)
    if a.check:
        same = new == old and (not a.html or (os.path.exists(a.html) and _rt(a.html) == page))
        print("一致" if same else "不一致"); return 0 if same else 1
    if new != old:
        with open(a.readme, "w", encoding="utf-8") as f: f.write(new)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f: f.write(page)
    print(f"已更新：當週 n={ns[0]}，累計 n={ns[1]}" + ("（README 未變）" if new == old else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())
