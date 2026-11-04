# Backtest v1.0.4 重現指引

先以 Git clone 此修正分支。需要完整 Git 歷史以讀取基準 110ec47a。Windows clone 時加上 `git -c core.autocrlf=false clone ...`，避免改變已公布檔的位元組。以下命令在專案根目錄執行。修正版測試使用 Python 3.10.5、NumPy 2.2.6、SciPy 1.15.3。

## 只有公開倉庫

~~~sh
python -m pip install -r requirements-v104.txt
python -B -X utf8 src/reproduce.py public
python -B -X utf8 -m unittest discover -s src -p "test_*.py" -v
~~~

public 將 14 個發布檔逐一與基準 Git blob 比對。合成測試不讀取行情。舊版的方法及原始指引可用 git show 110ec47a:METHOD.md 和 git show 110ec47a:REPRODUCE.md 查閱。

## 私有開發期輸入

四個 JSON 都由有授權的資料保管人先行隔離，每個檔最晚 2024-12-31，使用 MANIFEST 裡三個 data/dev/ 的檔名，及 0050 的原檔名。它們放在同一個專用目錄。請勿指定含完整跨期 FinMind 檔的目錄。新版切片入口在開檔前停止。下載相同資料集不代表能取得相同快照。

## A — Exact Reproduction，核對 v1.0.3b

使用原記錄的 Python 3.13.5、NumPy 2.5.4、SciPy 1.18.1 及相符的數值環境。資料保管人另提供原檔雜湊 JSON，內容是四個原始檔名對應完整小寫 SHA-256 的物件，並附 GPG detached signature（檔名為 JSON 路徑加 .sig）。金鑰須由獨立可信管道確認。需要 gpgv。保管人核對完整原檔的工作屬另一授權環境，本審查只讀簽章與已隔離開發檔。

~~~sh
python src/reproduce.py exact --inputs /private/pinned-dev --custody /private/originals.json --keyring /trusted/custodian.gpg
~~~

程式比對簽章內四個原始 SHA、三個截斷檔與 0050 SHA、原發布程式 SHA、METHOD SHA、套件版本。自 Git 基準提取實際的舊版程式到兩個乾淨目錄，分別執行 50 次替換測試及回測。A/B 所有產物必須逐位元一致，再逐檔比較原倉庫公布的 14 個輸出。原本沒有公開的內部標籤、報告、防漏紀錄只能做 A/B 比對。缺少任何前置條件時，不能認定 Exact 成功。

## B — Numerical Reproduction，核對修正版

另需一套已知 SHA 的原版開發切片作為市場數值的參照。候選資料包須由保管人聲明只涵蓋開發期，並隨附 development-inputs.json，以各檔名為鍵，值為含 source、last、sha256 的物件。last 使用 YYYY-MM-DD。先核對該清單再讀候選檔。不能把混合期間的原檔放進這個目錄。

~~~sh
python src/reproduce.py numerical --inputs /private/candidate-dev --reference /private/pinned-dev
python src/reproduce.py corrected --inputs /private/pinned-dev
~~~

數值模式整理日期、欄位、數字和排序，逐一比對模型使用的價格及法人值、識別碼、交易日與缺漏。它在兩個隔離目錄重算模型，再與以參照切片跑的修正版比較所有日期、實際標籤、六模型方向和 M2 機率。數值容差為 abs_tol=1e-9、rel_tol=1e-12，日期與方向精確比對。僅有舊版公開的七欄預測不足以完成這項驗證。模式 A 與 B 使用不同的判定標準。

每次執行建立新的暫存根目錄，主控台印出其位置。兩次執行使用獨立子行程、來源副本、輸入與輸出，套件則使用同一套指定直譯器。bash src/run_twice.sh 可接受上述同一組 mode 及參數。修正版 A/output/ 包含報表，internal_actuals.csv 及私有逐日輸出不得提交 GitHub。可另執行 python src/audit_outputs.py /private/A/output 檢查逐筆重彙總。
