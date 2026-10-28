# 每日練習｜凍結規則檔 v9（公開版，對應 drill_r10.py）
- 範圍：只看大盤指數（加權指數）；紙上練習，不是投資建議。
- 凍結日期：2026-10-28（台北）。適用於第一筆計分日 2026-10-29（含）以後的每日練習；本檔 sha256 記在同名 .sha256 檔。
- 注意：本檔寫於 10/28 練習已跑過之後，對 10/28 那一輪（含 r2 重跑）不構成事前凍結，只約束之後的輪次。
- 10/28（含）以前所有數字不計分。
## 考古題
- 題庫：--pred 指定的題庫 predictions.jsonl 中 P4、P5 都表態（漲/跌）的交易日；歷史收盤來自 FinMind（使用者自行依條款取得，非商業；本 repo 不附原始資料），只供練習。
- 抽樣：每輪 n=60，seed=int(當天台北日期 YYYYMMDD)，或由 --date 傳入；實際 seed、date 寫進輸出。抽樣前讀 rounds.txt（首行必須是「# genesis 20261028」）列的、日期早於本輪的各輪 drill*-out.json（逐檔驗 sha256），扣掉其 sample_days 再抽，輸出記 excluded_n。
- 實際方向：FinMind 收盤對前一交易日收盤；平盤算錯。基準＝同一批樣本的天天猜漲。
- 附加對照：輸入延後一天（call[d] 對 actual[d+1]）、洩漏探針（call[d] 對 actual[d−1]）、開盤到收盤方向（close−open，平算錯）。
## 檢定
- 確切 McNemar（只看規則與天天猜漲結果不同的日子，二項 p=0.5），單尾（H1：規則勝），α=0.05，Holm m=2（P4、P5）。
- n<30 或未顯著時，不下「有效／無效」結論。
## 知新候選
- 候選與網格必須在跑之前寫進本檔新版本並重新凍結；訓練段 1999-01-06～2016-12-31，保留段 2017-01-01～最新。
- 停止規則：訓練段最佳候選命中數 ≤ 訓練段天天猜漲 → 不開保留段、不進候選。
- 保留段只開一次；結果不論好壞都記錄。
## 執行
- drill_r10.py sha256：10134cca213bd0cb44d048001a1348019eef74860545e830b1b23b6a63ce19a4
- 輸出：drill-out-{DATE}.json 與 drill-out-{DATE}.md 由腳本自己寫；檔已存在時內容相同（json 不計各來源抓取時間）就原子重寫同一份位元組（sha 不變），不同就 exit 2。寫完後以 tmp＋fsync＋os.replace 原子追加「DATE sha256 path」到 rounds.txt；同 DATE 已有一行時 sha 相同跳過、不同 exit 2。
- 異常一律印明確訊息 exit 2：rounds.txt 不存在、空檔或首行不是 genesis、DATE>20261028 卻無先前輪次、先前輪次檔 sha 不符、題庫扣除後不足 60 天。
- 當天資料（B9 甲）：FinMind 只當歷史題庫，不再用來算當天任何數字。當天收盤與漲跌只用證交所 MI_INDEX（www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=DATE&type=IND&response=json），也是 P5 的當天輸入；P4 現貨＝證交所 BFI82U（www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate=DATE&type=day&response=json）外資及陸資（不含外資自營商）＋外資自營商買賣差額；P4 期貨＝期交所 www.taifex.com.tw/cht/3/futContractsDateDown（POST queryStartDate=queryEndDate=DATE、commodityId=TXF）臺股期貨「外資及陸資」多空交易口數淨額（與回測 long_deal_volume−short_deal_volume 同義）。每個來源記端點、抓取時間（台北）、sha256。
- 當天資料核對（B8），在本地檢查（rounds、題庫數）之後、抽樣與寫檔之前做：證交所回應須 stat=OK、回應 date（或 params.date／dayDate）＝DATE、表題含民國日期；期交所 CSV 每列日期＝DATE。stat 缺漏、含「查詢日期大於／很抱歉／沒有符合」、無資料列＝休市或未公布 → exit 3（不抽題、不寫輸出、不追加 rounds；例行視為今天休，不算失敗、不耗題庫）。日期不符、stat 其他值、網路或解析失敗 → exit 2。原始回應存 src-DATE/來源-DATE.raw.txt，tmp＋fsync＋os.replace 原子寫入；已存在且內容不同 → exit 2。
- P5 當天計算（B10）：凍結 P5 定義＝backtest_v315_r2.py p5_call：取最近 270 個收盤，算 20 日對數報酬標準差 σ20 序列（250 個），當日 σ20 的百分位 pct≥80 且最後收盤＜MA60 → 跌，否則漲。所需 270 筆收盤（不是 60 筆；MA60 只是其中一項）以證交所 FMTQIK 月表（www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=YYYYMM01&response=json）由當月往前逐月補到 DATE 之前滿 270 筆（最多 16 個月），每次網路抓取間隔 3 秒。每月須 stat=OK、所有資料日期都在該月，否則 exit 2。序列＝FMTQIK 中早於 DATE 的收盤＋當天 MI_INDEX 收盤；若 FMTQIK 已含 DATE，其收盤須等於 MI_INDEX 收盤（差 >0.005 → exit 2）；FMTQIK 最後一天早於前一輪日期（缺口）→ exit 2。月表原始回應存 --fmtqik-dir（預設 fmtqik/）FMTQIK-YYYYMM-asof-最後資料日.raw.txt，原子寫入、不覆寫；先用已有快取；若序列前一筆≠MI 隱含昨收（快取不含上一交易日），重抓本月與上月一次（B16），新檔另存、原子寫入、不覆寫舊檔，重抓後仍不符 exit 2；快取最後一天之後、DATE 之前的日子，用 rounds.txt 中已驗 sha 的前幾輪輸出 live.MI_INDEX.close 補（記在 live.FMTQIK.filled_from_rounds），補完後序列最後一天仍早於前一輪日期 → exit 2。另記 continuity_ok＝（當天 MI 收盤−漲跌點數）是否等於序列前一筆，不符即 exit 2（B12）。每月來源、sha256、筆數記在輸出 live.FMTQIK。
- 批改（B11）：每輪 D 讀 rounds.txt 中日期早於 D 的最後一輪輸出（驗 sha），取其 live.P4_next_call、live.P5_next_call（漲/跌才批），對 D 日 MI_INDEX 漲跌點數正負（>0 漲、<0 跌、=0 平；平算錯），寫 out.graded[規則]={prev_date,call,actual,hit}，並在寫完輸出後、追加 rounds 前以 tmp＋fsync＋os.replace 原子追加到 graded-log.jsonl（每規則一行，含 date、rule；同 date 已有相同內容跳過、不同 exit 2），印出 graded-log sha256。live.scorable＝本輪是否真的批改到至少一條（無前一輪即 False）；twse_check.scorable 只表示當天證交所資料核對通過，兩者不同。
- 月表完整性（B14）：每月月表的交易日，凡在 FinMind 題庫交易日曆涵蓋範圍內者，日期集合須與日曆完全相同（FinMind 只當日曆，不取其數字）；早於當月的完整月份，最後一筆須等於該月最後交易日，且快取檔名 asof＝最後一筆；日曆未涵蓋到該月月底 → exit 2。當月超出日曆的日子由 B12 連續性核對把關。
- 本月快取時效：DATE 前最近一個平日（週一至週五，不看假日）所在月份與 DATE 當月，其最新快取 asof 早於該平日 → 一律重抓該月月表（同 B16：新檔另存、原子寫入、不覆寫、更新 fmtqik/SHA256SUMS）；重抓失敗 exit 2。
- 月表解析：fq_parse 任何例外（缺欄、型別錯、格式變）一律明確訊息 exit 2，不留 Traceback。
- 月表欄位（B18）：fields 必須 6 欄且第 5 欄（fields[4]）含「加權」；每列長度必須 6；否則 exit 2。
- 交易日判斷（B15）：當天是否交易以證交所 MI_INDEX 為準（休市 exit 3）。FinMind 日曆只比對它涵蓋到的日子，不再因日曆未涵蓋月底而中止；超出日曆的非當月月份，以 TWSE 月表本身＋rounds 驗月尾：前幾輪日期必須都在序列中，且月表最後一筆之後、下一筆之前不得有前輪日期，否則 exit 2；漏跑的月底交易日由連續性核對（隱含昨收）擋下。
- 收盤數值核對（B17）：月表收盤與 FinMind 重疊日逐筆比（差 >0.005 exit 2）；自己抓過的月表 sha 記在 fmtqik/SHA256SUMS（原子更新），讀快取時 sha 不符即 exit 2、不重抓蓋過；前幾輪（已驗 sha）的 MI_INDEX 收盤須等於序列同日收盤，且序列前一筆須等於該輪「收盤−漲跌」，否則 exit 2。
- 批改條件（B13）：只有前一輪日期＝收盤序列中 DATE 的上一個交易日時才批改；否則 graded 留空、live.scorable=False，並在 out.grade_skipped 記原因（「前一輪 X ≠ 上一交易日 Y」或「無前一輪」）。
- 建例行前：須先在暫存資料夾實跑一個交易日拿到 scorable=true。
- drill_r10.py 開頭驗：本檔 sha256＝同夾 drill-rules-v9-public.md.sha256 鎖檔、腳本自身 sha256 出現在本檔；任一不符即中止（exit 2）。腳本不能寫死本檔 sha（會循環），所以改用鎖檔；鎖檔可被同時竄改，屬已知限制，需外部錨點。
