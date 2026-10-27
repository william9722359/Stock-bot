# 資料說明

本目錄的回測使用 FinMind（https://finmindtrade.com/）提供的資料，原始出處是臺灣證券交易所和臺灣期貨交易所。

- FinMind Free 的授權只允許非商業使用，不得再散布原始資料，使用時要標示資料來源「FinMind」。
- 原始資料（`data/`）和含原始數值的輸出（`runs/`）已經列在 `.gitignore`，不會進公開 repo。要重跑，請自己向 FinMind 下載同樣的資料集，再用 `INPUTS.sha256` 核對。
- 本目錄的結果只是練習用的回測，不是投資建議，也不是正式的 610 筆門檻。
