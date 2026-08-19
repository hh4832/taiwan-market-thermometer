# 臺股市場溫度計

整合市場廣度與外資臺股期貨部位的研究型儀表板。兩個指標分開顯示，再由日期與資料品質保護的規則引擎產生綜合參考；不提供直接買賣指令。

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
