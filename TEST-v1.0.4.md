# backtest-v1.0.4 驗收紀錄

基準為 `110ec47acc8bc92d00e6ed3190ad02f466e2df8a`。測試程式提交為 `d7daee92593e3d3624c0a729ba422aa609600f65`。Windows、Python 3.10.5、NumPy 2.2.6、SciPy 1.15.3。執行者是本次修正工作階段，沒有外部查核人的簽證。

~~~sh
git -c core.autocrlf=false clone --single-branch --branch codex/backtest-v1.0.4 https://github.com/1485865/Stock-bot.git check-v104
cd check-v104
python -B -X utf8 src/reproduce.py public
python -B -X utf8 -m unittest discover -s src -p "test_*.py" -v
git diff --exit-code 110ec47a -- results/v1.0.3b
~~~

|項目|結果|證據與邊界|
|---|---|---|
|程式靜態審查|PASS（完成）|逐檔根因與已知限制見 REVIEW-v1.0.4.md。|
|公開檔完整性|PASS|14 個已公布檔全部對上基準的 Git 物件，含 1,216 筆預測。|
|單元與整合|PASS|8 組測試。包含全漲／全跌、全對／全錯、無樣本、合法零分與分母為零。|
|750／20／20、purge、標準化與標籤對齊|PASS|攔截實際傳入 lr_fit 的矩陣與標籤，逐筆對照每輪應取的 750 日。核對均值、標準差、末輪、昨日特徵與當日標籤，錯位長度必須拒絕。|
|未來替換|PASS（合成）|執行基準版本沿用的 50 日抽樣替換邏輯，輸出 PASS 0。|
|資料切片隔離|PASS|新版切片器在開檔前拒絕執行。測試以攔截開檔及越界日期字串驗證，沒有使用 Holdout 行情。|
|固定規則、校準與逐筆重彙總|PASS（合成）|M3 有資料時立即出預測，淨 OI 不變與缺資料分開。M4 少任一腿記缺。由六模型逐筆及私有標籤重算 summary、yearly、prf、confusion，故意竄改樣本數會被偵測。固定 50% Brier 為 0.25。|
|0050 成本|PASS（合成）|逐腿驗算買進含費價金、賣出扣費及 ETF 稅價金、兩端滑價，並檢查空手日的現金報酬。|
|兩次獨立重跑|PASS（合成）|兩個隔離子行程目錄從不同排序／序列化的合成 JSON 重算未來替換、模型與各報表，逐檔 SHA 相同。|
|曾發現的失敗|FAIL → 修正後 PASS|單類訓練窗令原 IRLS 發生 Singular matrix。保留此失敗紀錄。修正版將該輪 M2 標為無效。|
|真實 Exact Reproduction|NOT RUN|缺少合法隔離的四檔、可信保管人的完整原檔雜湊證明與原記錄的軟體環境。|
|真實 Numerical Reproduction|NOT RUN|沒有完整、來源可核對的候選行情與參照切片。|
|歷史發布時間／真實修正版分類與交易回測|UNVERIFIED／NOT RUN|沒有歷史逐版本發布證據及所需行情。舊版公開結果的核對無法解除這些限制。|
|2025 年起 Holdout|NOT RUN|本次基準修正未讀該期行情、未解封亦未估分。|

機器可重現合成測試及公開檔核對。要完成真實的第三方驗證，仍須取得指引列出的合法輸入、核對來源時間及執行相應重現模式。[重現步驟](REPRODUCE.md)
