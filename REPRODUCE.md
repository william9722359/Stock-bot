# REPRODUCE（v1.0.3b；FinMind・回測練習，不算正式成績）
方法見 `METHOD.md`。

## 0. 環境
python 3.13.5、numpy 2.5.4、scipy 1.18.1（只用這三個；邏輯迴歸用 numpy IRLS，McNemar 用 scipy）。

## 1. 資料
repo 不含 FinMind 原始檔與截斷檔（FinMind 條款不允許再散布）。請自行用 FinMind API 抓下列資料集，存到 repo 內 `data/finmind/`（檔名見 `data/MANIFEST.json`；資料檔不進 git，見 .gitignore），sha256 必須等於 MANIFEST 所記：
- TaiwanStockPrice（data_id=TAIEX）：ee85a934…
- TaiwanFuturesInstitutionalInvestors（TX）：ba5b116d…
- TaiwanStockTotalInstitutionalInvestors：847865ab…
- TaiwanStockPrice（data_id=0050，交易模擬用）：7419fbe1…

抓到的檔若 sha 不同（FinMind 事後修資料），程式會 rc=1 停下；此時只能比對彙總，無法逐位元重現。

## 2. 指令（全部不收參數；export PYTHONDONTWRITEBYTECODE=1）
```
python3 src/make_dev_slice.py      # 讀原檔一次，寫 data/dev/*.json（只到 2024-12-31）
python3 src/leak_test.py           # 防偷看 50 個目標日 → results/v1.0.3b/leak_test.txt
python3 src/backtest.py            # 主程式 → results/v1.0.3b/
bash src/run_twice.sh              # 兩次重跑逐位元比對，rc=0
```

## 3. 常數（src/common.py 頂端，唯一一處）
SCORE_START=2020-01-02、DEV_END=2024-12-31、HOLDOUT_START=2025-01-01；TRAIN=750、TEST=20、STEP=20、PURGE=1。

## 4. predictions.csv 欄位
date（目標日 D）, m0_pred（1=漲）, lr_pred（M2，1=漲 0=跌，p≥0.5 猜漲）, lr_prob_up（M2 上漲機率）, fold_id（對到 steps.csv）, m3_pred, m4_pred（只在各自副表期間有值；主表日、未湊滿 750 天、missing 留空）。不含實際漲跌、對錯、close、特徵；請用自己抓的 FinMind TAIEX close 對答案（y_D=1 若 close_D>close_{D−1}，平盤算錯）。
