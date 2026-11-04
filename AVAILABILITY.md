# 資料可用性審查表（v1.0.4，外部審查規格 C）

預測截止：**Asia/Taipei 08:45，預測 D 日加權指數收盤漲跌**（D 收盤 > D−1 收盤＝漲）。
判定原則：沒有附上可查證的歷史公布時間證據，一律 **UNVERIFIED**；不因欄位標示 D−1 就假設 08:45 前已公開。

| 模型 | 特徵 | Feature Date | Data Source | Data Coverage End | Publication Time | Prediction Cutoff | 狀態 |
|---|---|---|---|---|---|---|---|
| M0 | 無（恆猜漲） | — | — | — | — | D 08:45 | 不適用 |
| M1 | 昨收 ≥ 前收 | D−1、D−2 | FinMind TaiwanStockPrice TAIEX | 2024-12-31（dev 截斷） | 本 repo 未附證據（收盤行情一般於 D−1 收盤後公開） | D 08:45 | UNVERIFIED |
| M2 | LR 特徵（過去收盤） | ≤ D−1 | 同上 | 同上 | 同上 | D 08:45 | UNVERIFIED |
| M5 | 波動率＋均線（過去收盤） | ≤ D−1 | 同上 | 同上 | 同上 | D 08:45 | UNVERIFIED |
| M3 | 外資台指期淨未平倉變化 | D−1、D−2 | FinMind TaiwanFuturesInstitutionalInvestors TX | 2024-12-31 | 本 repo 未附證據（期交所盤後公布時間未查證） | D 08:45 | UNVERIFIED |
| M4 | 外資台指期多空交易口數 | D−1 | FinMind TaiwanFuturesInstitutionalInvestors TX | 2024-12-31 | 未附證據 | D 08:45 | UNVERIFIED |
| M4 | 外資現貨買賣超 | D−1 | FinMind TaiwanStockTotalInstitutionalInvestors | 2024-12-31 | 未附證據（證交所盤後公布時間未查證） | D 08:45 | UNVERIFIED |
| 交易表 | 0050 開盤／收盤 | D（同日成交，非特徵） | FinMind TaiwanStockPrice 0050 | 2024-12-31 | — | — | 不適用（結果端，非輸入） |

結論：所有使用資料的模型（M1–M5）目前都只能列為**暫定回測**，不能標示為「已完成嚴格無洩漏驗證」。
leak_test.py（未來資料替換測試）保留未刪；它只證明程式不讀未來列，不證明歷史公布時間。
已加 tests/test_alignment.py（窗口邊界、標籤對齊、未來擾動），只證明程式對齊，不證明公布時間。
沒有標為 NOT AVAILABLE 的項目：目前沒有證據顯示任何一項在 08:45 前不可得，只是也沒有證據顯示可得。
