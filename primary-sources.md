# 一手資料來源清單（primary-sources，公開版）

建立：2026-10-23 11:2x（台北時間）｜依據：主人 10/23 的指示：直接抓一手資料來源做判斷，不依賴別人消化過的資訊
實測證據：未公開的內部紀錄（原始回應檔、HTTP 狀態、大小、sha256 前 16 碼、抓取時間）
實測時間：2026-10-23（台北時間；確切時刻與快照檔時間見未公開的內部紀錄）。〔更正：原寫「盤中」有誤。10/23 因颱風全日休市（期交所 10/22 公告休市：因颱風，10/23 一般與盤後交易時段都休市一天；轉述，原文見期交所公告頁 https://www.taifex.com.tw/cht/11/announcement ），不是盤中。〕所以「最新日期」都是 10/22，**不代表各檔開盤前的實際更新時刻**。

## 授權等級
- **A＝已授權**：data.gov.tw 資料集所列下載網址，適用《政府資料開放授權條款－第1版》（https://data.gov.tw/license）。期交所網站使用條款說明：期交所已授權政府資料開放平臺（https://data.gov.tw）提供的網站資料，不受轉載限制（轉述，原文見期交所使用條款 https://www.taifex.com.tw/cht/edu/userTerms ；這個網址本版沒有逐字核對，待補）。顯名聲明見 `NOTICE.md`。
- **B＝授權未證實**：openapi.twse.com.tw、openapi.taifex.com.tw。實測可以抓，但期交所同一份使用條款寫明：引用或轉載網站上的軟體、程式或內容，要先取得期交所或其他權利人的書面同意（轉述，原文見上列使用條款網址），OpenAPI 本身沒有另外的授權條款，所以判「授權未證實」。只當交叉比對用，不進凍結規則。
- **C＝美國聯邦政府著作，推定公共領域**：依 17 U.S.C. §105 推定。各網站的條款頁沒有抓，所以標「推定」。
- **X＝禁用或未取得**。

## 台股現貨（證交所，經 data.gov.tw）
| DG | 網址 | 等級 | 欄位 | 實測（10/23 11:2x） |
|---|---|---|---|---|
| 11672 | twse.com.tw/exchangeReport/FMTQIK?response=open_data | A | 日期(民國)、成交股數、成交金額、筆數、加權指數、漲跌點數（當月逐日） | 200，1150 B，最新 1151022：46041.43、−846.34 |
| 11555 | …/exchangeReport/MI_INDEX?response=open_data | A | 各指數的收盤、漲跌、漲跌% | 200，20442 B，最新 1151022（加權 −1.81%） |
| 11755 | …/indicesReport/MI_5MINS_HIST?response=open_data | A | 加權指數開、高、低、收（當月逐日） | 200，885 B，最新 1151022 |
| 11655 | …/fund/MI_QFIIS_cat?response=open_data | A | 用途：只取類股合計，當外資現貨買賣超的代理（第 8 項）；**沒有日期欄** | 200，2104 B，無法判斷日期 |
| 11761 | …/holidaySchedule/holidaySchedule?response=open_data | A | 休市日名稱、日期、說明 | 200，2693 B，含 1151225 |
| — | 集中市場三大法人現貨買賣超（BFI82U/T86） | X | data.gov.tw 與 TWSE OpenAPI swagger 都沒有，只有網頁或付費 eshop | 未取得，不能用 |

## 期貨、選擇權（期交所，經 data.gov.tw；網址前綴 https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name=）
| DG | data_name | 等級 | 欄位 | 實測 |
|---|---|---|---|---|
| 11319 | DailyMarketReportFut | A | 各期貨契約開高低收、漲跌、漲跌%、量、結算、OI，交易時段分「一般」和「盤後」（TX、SXF、UNF 夜盤都在這裡） | 200，161572 B，UTF-8 BOM，只有單日 20261022 |
| 11596 | MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate | A | 各期貨商品三大法人的多空交易口數與淨額、未平倉 | 200，6205 B，20261022 |
| 11592 | MarketDataOfMajorInstitutionalTradersGeneralBytheDate | A | 三大法人期權合計的交易與未平倉淨額 | 200，695 B，20261022；外資未平倉淨額 −492861 口 |
| 11322 | PutCallRatio | A | 賣權、買權成交量與 OI，P/C 比% | 200，1108 B，**近 20 個交易日**（20260922–20261022）；10/22 量 P/C 86.21、OI P/C 66.18 |
| 11600 | MarketDataOfMajorInstitutionalTradersDetailsOfCallsAndPutsBytheDate | A | 選擇權買權、賣權依法人分 | 200，2789 B，20261022 |
| 11339 | DailyForeignExchangeRates | A | 期交所每日匯率參考（USD/TWD 等 10 組） | 200，3264 B，當月逐日，最新 20261022 USD/TWD 32.508 |

## 央行
| DG | 網址 | 等級 | 欄位 | 實測 |
|---|---|---|---|---|
| 10818 | https://www.cbc.gov.tw/public/data/OpenData/經研處/EG51D01.csv | A | 美元即期匯率（銀行間收盤等）**日資料** | 200，430781 B，**最新 20260831，落後將近 2 個月**，不能當盤前輸入 |
| 7232、10797 | FTDOpenData015/016、EG52M01 | A | 年或月資料（資料集頁更新頻率：7232 每年、10797 每月） | 沒測下載檔，頻率太低，預測用不上。2026-10-23（台北時間）照資料集頁核對：提供機關中央銀行，授權政府資料開放授權條款-第1版 |

## OpenAPI（B，只交叉比對）
| 網址 | 實測 | 備註 |
|---|---|---|
| https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX | 200 JSON，46506 B，1151022 | 跟 11555 內容相同 |
| https://openapi.taifex.com.tw/v1/DailyMarketReportFut | 200 JSON，807219 B，只有 20261022 | 跟 11319 相同，**沒有比較早** |
| https://openapi.taifex.com.tw/v1/PutCallRatio | 200 JSON，3072 B，多日 | 跟 11322 相同 |

## 美國總經、利率
| 來源 | 網址 | 等級 | 實測 |
|---|---|---|---|
| 美國財政部每日公債殖利率曲線 | https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv | C | 200 CSV，16553 B，最新 10/22/2026：10Y 4.96、2Y 4.65（10/21 是 5.13、4.81）。**可取代 FRED 殖利率** |
| Fed FOMC 日程 | https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm | C（推定，網頁） | 200 HTML。2026 年場次：1/27-28、3/17-18、4/28-29、6/16-17、7/28-29、9/15-16、**10/27-28**、12/8-9。10/28 美東決議約等於台北 10/29 清晨 |
| BLS 發布日程 | https://www.bls.gov/schedule/news_release/bls.ics | C | **403 Access Denied**（BLS 擋程式存取）。程式抓不到，只能人工看 |
| FRED（SP500、NASDAQCOM、DJIA、殖利率） | api.stlouisfed.org | X | 沒呼叫。2026-10-10 主人裁定停用：SP500、DJIA 的條款禁止這種用法，NASDAQCOM 是第三方版權（內部授權查核結論，原件未公開）；API 也要 key（不註冊） |
| VIX | CBOE | X | 未取得，CBOE 版權，沒有授權的免費一手來源 |
| 美股 ADR | — | X | **未取得、只能用新聞二手** |
| 費城半導體 SOX 現貨 | — | X | **未取得、只能用新聞二手**。授權替代：11319 的 SXF（美國費城半導體期貨）盤後漲跌% |

## 哪些預測輸入可以改用一手來源
1. 台指夜盤（取代新聞的「夜盤漲跌」）：11319 TX 盤後列。**限制**：盤前通常還沒更新，實測 10/23 11:2x 時檔案仍只有 10/22。
2. 費半：11319 SXF 盤後（替代 SOX，同樣有時效問題）。
3. 外資期貨部位：11596 臺股期貨外資淨額、11592 外資未平倉淨額。前一日收盤後就有，**盤前可用**。
4. 選擇權情緒：11322 P/C 比（有近 20 日序列），**盤前可用**。
5. 匯率：11339 USD/TWD（前一日），**盤前可用**。央行 EG51D01 落後太久，不用。
6. 美國利率：財政部殖利率 CSV（美國前一日收盤，台北早上可用）。
7. 事件日：FOMC 網頁、11761 休市表。BLS 只能人工看。
8. 外資現貨買賣超：**沒有授權一手來源**，只能用資料集 11655 的類股合計變化當代理（沒有日期欄、更新時刻未證實）。
9. 美股 ADR、SOX 現貨、VIX、美股指數：**未取得、只能用新聞二手**。

## 未解
- 各檔的實際更新時刻都沒實測。要分別在收盤後、傍晚、晚間、隔日清晨、開盤前各抓一次才能確定。尤其 11319 盤後列什麼時候出現，決定 P1–P3 能不能在盤前用。
- 11655 沒有日期欄，只能靠抓取時間和前後差異判斷。

勘誤（2026-10-23，v3.1.2）：第 3 行、第 52 行的「主人…指示／裁定」都是協調助理轉達（非本人核可），沒有主人本人原話的出處。舊行不改。
