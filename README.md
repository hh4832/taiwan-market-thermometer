# 臺股市場溫度計

整合市場廣度、外資臺股期貨部位與法人現貨Phase 2證據的研究型儀表板。市場廣度與外資期貨分開顯示，再由日期與資料品質保護的規則引擎產生既有綜合參考；法人現貨目前只作A級證據監測，不影響綜合判讀，也不提供直接買賣指令。

## 本機版本（建議日常使用）

需求：Windows、Conda環境 `py311`、可登入的FinLab帳號。

```bash
conda activate py311
pip install -r local-requirements.txt
python -m streamlit run dashboard/app.py
```

瀏覽器通常會自動開啟 `http://localhost:8501`。也可以雙擊 `start_dashboard.bat`。關閉執行Streamlit的命令視窗即可停止網站。

目前啟動檔固定使用 `C:\Users\hh483\anaconda3\condabin\conda.bat` 啟用 `py311`，並在三秒後自動開啟本機網站。網站只綁定 `localhost`，不對區域網路或外部網路開放。

如果你曾看到 `ModuleNotFoundError: No module named 'dashboard'`，請確認使用本修正版；程式現在會自動加入專案根目錄，不需要自行設定 `PYTHONPATH`。

第一次開啟先顯示上傳的市場廣度研究快照。按「更新 FinLab 資料」後才登入FinLab並取得最新資料。若下載失敗，頁面保留快照並明確標示錯誤，不把舊資料冒充今日資料。

市場廣度另有資料品質閘門：有效股票覆蓋率需至少80%，且上漲、下跌、平盤的分類完整率需至少99%。未通過時保留Raw資訊供除錯，但不計算溫度，也不產生綜合方向結論。

## FinLab登入與安全

程式使用 `finlab.login()`；不得把Token寫進Python、README或Git。`.env`、`.streamlit/secrets.toml`、快取及輸出資料已列入忽略清單。FinLab付費原始資料只在本機處理。

外資期貨資料由本Dashboard直接從FinLab下載，不依賴其他研究repository、資料夾名稱或相對位置。固定使用以下精確欄位：

```text
futures_institutional_investors_trading_summary:多方未平倉口數
futures_institutional_investors_trading_summary:空方未平倉口數
futures_institutional_investors_trading_summary:多空未平倉口數淨額
欄位：臺股期貨_外資及陸資
```

程式會移除FinLab回傳的`NaT`或空日期列，再驗證 `Long OI − Short OI = Net OI`；不會替無日期列猜測日期，也不會forward fill。精確欄位不存在、整張表沒有有效日期、三張表沒有共同日期或公式不一致時，會停止更新並顯示原因。本專案可獨立放在任何資料夾，不必和「三大法人期貨未平倉預測大盤報酬」研究專案放在一起。

## 指標定義

### 超跌反彈溫度

`down_ratio = 下跌家數 / (上漲家數 + 下跌家數)`。分數為當日 `down_ratio` 相對當日以前歷史資料的百分位。高分代表普跌嚴重與5至10日反彈環境增加，不代表市場健康或已經止跌。

完整樣本探索性門檻：最差20%為 `down_ratio ≥ 68.5541%`；最差5%為 `down_ratio ≥ 84.5405%`。固定門檻僅用於重現原研究。

### 外資期貨方向溫度

`OI Change Ratio = ΔNet OI / (前日Long OI + 前日Short OI)`。分數為過去252個交易日、至少126筆、排除當日的歷史百分位。一般分數主要作為隔日方向參考；只有落入最低5%的極端往空方調整，才有延伸至5、10及20日的負向統計證據。不可把高分或一般低分對稱延伸解讀為相同的中短期效果。

頁面上的橫向刻度代表0至100的歷史百分位位置，不是載入進度，也不是預測勝率。外資方向高分只代表當日淨部位往多方調整，可能是增加多單或回補空單，不代表累積部位已經淨多。

### 市場廣度研究錨點

研究錨點只適用於 `down_ratio ≥ 84.5405%` 的完整樣本極端5%條件，與外資期貨分數無關。若今日未觸發，頁面會明示歷史報酬僅為研究背景，不套用為今日預期報酬。

### 外資期貨研究錨點

研究錨點只適用於外資 `OI Change Ratio` 的252日無前視百分位最低5%。歷史樣本約239次；未來1、5、10、20日平均報酬約為−0.33%、−0.41%、−0.66%、−0.30%，相對non-group差異約為−0.41%、−0.77%、−1.36%、−1.67%，HAC與FDR校正後仍有統計證據。

上述報酬為d0收盤至未來收盤的統計結果。法人資料在d0盤後公布，因此不得直接解釋為可實現策略報酬。

### 外資多空部位變化拆解

把 `OI Change Ratio` 進一步拆成 `Long Change Ratio` 與 `Short Change Ratio` 後，FDR校正支持的結果集中在多單變化：多單增加的PR 80～95組，未來1日平均約+0.23%，相對non-group約+0.19%（FDR p=0.030）；多單減少的PR 5～20組，未來10日相對non-group約−0.63%（FDR p=0.005）。

未來5日與20日的多單個別變化未通過FDR；空單變化單獨看，在1、5、10、20日也都未通過FDR。因此，整體 `OI Change Ratio` 最低5%的負向證據不能直接改寫為「新增空單本身具有穩健預測力」。拆解結果只作判讀輔助，不另行改寫方向溫度分數。

### 法人現貨Phase 2 A級證據監測

法人現貨區塊不是第三個溫度分數。每日只顯示原始買進／賣出金額、市場成交金額、依正式定義計算的累積比例、504／756日無前視百分位，以及A級條件是否命中。

- 上市自營商Net：5日504日PR 80～95，或10日756日PR 95～100時，列示歷史偏多證據。
- 上櫃三大法人Sell：1／5日504日PR 5～20時列示歷史偏多證據；5／10日756日PR 60～80時列示歷史偏空證據。
- 上市外資Net：5日504／756日PR 95～100時列示歷史偏多證據。

同一family多條件同時命中只計為一個family。偏多與偏空family同時出現時標記為「證據方向不一致」，不互相抵銷、不合成淨方向。所有結果均標記為`research_only`，不產生權重、預期報酬、部位或操作建議，也不修改現有`overall_state`。

上市外資固定使用`上市外資及陸資(不含外資自營商)`；不得fallback至資料期間不一致的`上市外資`短欄位。法人現貨資料另外寫入Google Sheet的`spot_signal_daily`長表，不擴張原有`daily_signals`寬表。

## 研究限制

- 市場廣度極端5%組約189次；隔日開盤至第5日平均約+0.57%、勝率56.1%；至第10日平均約+0.88%、勝率63.1%。Group vs non-group的HAC結果未達傳統5%門檻，FDR後仍屬臨界。
- 法人資料在收盤後公布，不能假設在訊號日收盤成交。
- 不同資料日期、覆蓋率不足、缺值或樣本不足時，綜合結論停止判讀。
- 本頁為統計研究結果整理，不構成投資建議。

## 測試

```bash
python -m unittest discover -s tests-python -v
npm run build
npm run validate:artifact
```

前端頁面是可檢視的研究快照介面；日常即時更新請使用本機Streamlit版本。

## v1.5.0：雲端每日紀錄與Gmail

主要流程由私人GitHub Repository的GitHub Actions執行，不需要開著電腦：

```text
週一至週五20:00（Asia/Taipei）
→ 登入FinLab並計算當日指標
→ 寫入Google Sheet的daily_signals
→ 補登既有訊號的d1/d3/d5/d10/d20報酬
→ 寫入run_log
→ 逐一寄送Gmail
```

休市日或FinLab尚未更新時，不會冒充新的交易日訊號；同一資料日期重跑只會更新原列，不會重複新增。`run_log`仍會記錄每次實際執行。Google Sheet只保存衍生指標與0050價格，不保存FinLab完整原始資料表。

### 一次性Google設定

1. 建立一份空白Google Sheet，從網址複製`/d/`與`/edit`之間的Spreadsheet ID。
2. 在Google Cloud建立Project並啟用Google Sheets API。
3. 建立Service Account及JSON金鑰。
4. 複製Service Account的Email地址，將空白Google Sheet分享給它並設為「編輯者」。
5. Google帳號開啟兩步驟驗證，建立Gmail 16碼應用程式密碼。

程式第一次執行會自動建立`daily_signals`與`run_log`兩個分頁，請勿手動修改第一列欄名。

### 一次性GitHub設定

將本專案放入「Private」Repository，進入`Settings → Secrets and variables → Actions`，建立以下Repository secrets：

| Secret | 內容 |
|---|---|
| `FINLAB_API_TOKEN` | FinLab Token |
| `GMAIL_SENDER` | Gmail寄件地址 |
| `GMAIL_APP_PASSWORD` | Gmail 16碼應用程式密碼 |
| `EMAIL_RECIPIENTS` | 收件者，以逗號分隔 |
| `GOOGLE_SHEET_ID` | Google Sheet網址中的Spreadsheet ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service Account下載的完整JSON內容 |

多人收件時，例如：

```text
haw@example.com,user2@example.com,user3@example.com
```

程式會各寄一封，不使用CC。完成後進入GitHub的`Actions → Daily cloud market report → Run workflow`手動測試一次。測試成功後，後續會在交易日台北時間20:00自動執行。

雲端排程檔位於`.github/workflows/daily-cloud-report.yml`。若日後要改為21:00，將cron由`0 12 * * 1-5`改為`0 13 * * 1-5`；GitHub cron使用UTC，台北時間需減8小時。

### 安全原則

- Repository必須保持Private。
- JSON金鑰、Token與應用程式密碼不得放進Python、README、Google Sheet或Git。
- 若憑證曾出現在Git歷史或公開畫面，應立即撤銷並重建。
- 對外提供或收費前，另行確認FinLab衍生資料及商業使用授權。

## v1.4.1：Windows本機備援寄送

Windows版仍可在週一至週五20:00自動更新FinLab並寄出摘要，作為雲端失敗時的本機備援。只需完成一次設定：

1. Google帳號先開啟「兩步驟驗證」。
2. 到Google帳號的「應用程式密碼」建立一組16碼密碼；不可使用一般Google密碼。
3. 雙擊`setup_daily_email.bat`。
4. 依畫面輸入Gmail寄件地址、收件地址、16碼應用程式密碼及FinLab Token。
5. 收到測試信後即完成。Windows會建立「Taiwan Market Thermometer Daily Email」工作排程。

如果已收到測試信，但最後建立排程失敗，可直接雙擊`install_daily_email_task.bat`補建排程，不必重新輸入Gmail密碼或FinLab Token。

Gmail應用程式密碼與FinLab Token使用Windows認證管理員保存，不會寫入Git；Email地址保存在被Git忽略的`config/email_notification.json`。每日執行紀錄位於`logs/daily_email.log`。

排程只在週一至週五執行。若當天休市或FinLab資料日期尚未更新，Email會標示「資料日期未齊」，不會把舊資料當成今日訊號。更新失敗時會寄出錯誤通知。

電腦處於睡眠時，工作排程可嘗試喚醒；若電腦完全關機則無法執行。漏跑後會在下一次開機登入時補執行。若要手動測試，可在Windows工作排程器中找到該任務並按「執行」。

若要修改寄送時間，雙擊`change_daily_email_time.bat`，輸入24小時制的`HH:MM`，例如`18:30`或`21:00`。程式會同步修改本機設定與Windows工作排程，不需要重輸密碼或Token。
