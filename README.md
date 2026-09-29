# 臺股市場溫度計 vNext

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hh4832/taiwan-market-thermometer/blob/main/notebooks/taiwan_market_thermometer_colab.ipynb)

這是一個研究證據監測系統。它把市場廣度、臺指期法人未平倉、法人現貨及融資融券研究，轉成每日可稽核的訊號事件與未來交易日 Forecast Calendar。它不把歷史平均報酬換算成溫度、勝率或操作建議。

## 架構

```text
Canonical Research Snapshot
        ↓
dashboard/research_registry.py
        ↓
Daily Raw Data → Signal Evaluation
        ↓
Canonical Signal Events
        ↓
Taiwan Trading-Date Target Mapping
        ↓
Forecast Calendar
        ↓
Streamlit / Google Sheet / Daily Email / Historical Validation
        ↓
Run Manifest → Run Archive → GitHub Actions Validation
```

所有 downstream consumer 共用 registry、signal events 與 calendar；Static Next.js 頁面只作文件預覽，不包含第二套 prediction logic。

## Canonical research sources

研究 snapshot 由指定 Google Drive 的四份 master summary 管理：

- Market Breadth：commit `62d7aba6a2173a52e4b53afbadd1e3327ac8169a`
- Futures：commit `c55c9bf91208f5b09912460ac65553d1a439f7e6`
- Spot Institutional Flow：commit `353fa7037505c8022db5a5b01601d0cdec370e6f`
- Margin / Short：commits `4a6fb82795342a55d174dbfc9beb1ee80a93bb4c`、`c3a89fb2f998ad69f6ea5e7ad362ea7fe2dfa696`

狀態定義：

- `RETAINED`：可進入正式 Forecast Calendar。
- `RETEST`：只在 Research / Experimental view 顯示，不投票。
- `REJECTED`：不進入 production prediction engine。

同一經濟假設以 `economic_signal_id` 去重，不會因 robustness window 不同重複投票。Margin / Short 的 continuous `Prior5D` interaction 已登錄，但 production hard cutoff 尚未由 canonical output 驗證，因此標示 `UNVALIDATED_ACTIVATION_RULE`，目前不投票。

## Forecast Calendar

Calendar 把 d0 收盤後可知的 retained signal 映射到實際臺灣交易日：

```text
signal_date d0 → next session adjusted open O1 → C1/C3/C5/C10/C20
```

`target_date` 使用實際 session index，不使用 calendar-day offset。每日只呈現「幾多／幾空」；`net_vote` 是票數差，不是上漲機率、預測報酬或信心百分比。

正式 outcome：

```text
O1→Cn = adjusted_close[d0+n] / adjusted_open[d0+1] - 1
```

尚未成熟的 outcome 是 `PENDING`，不會寫成零。

## NO_SIGNAL 與 DATA_UNAVAILABLE

- `VALID_NO_SIGNAL`：資料有效且計算完成，但今天沒有 retained condition 命中；允許成功並留下 run record。
- `DATA_UNAVAILABLE`：必要資料無法取得，不能顯示成 `0 多 / 0 空`。
- `STALE_DATA`：actual data date 不等於 expected trading date，workflow 必須失敗。

## Streamlit

```bash
python -m pip install -r local-requirements.txt
python -m streamlit run dashboard/app.py
```

介面包含：Forecast Calendar、Today / Latest Signals、Historical Validation、Research Evidence、System / Run Health。

Dashboard 會依固定優先順序選擇資料：`CURRENT_RUN` → `LIVE_FINLAB` → `RESEARCH_SNAPSHOT`。成功且新鮮的 `outputs/current/` 會直接被使用，避免 Colab 已計算一次後 Streamlit 又重新下載。只有 current run 不存在／過期時才嘗試 Live FinLab；兩者都不可用才載入研究快照。介面會顯示資料來源、資料日期、計算時間、run ID、commit、pipeline 狀態與 freshness。Research Snapshot 僅供預覽，會顯示「不是最新市場資料」警告。

## GitHub Actions 與完整性

`.github/workflows/daily-cloud-report.yml` 在臺北時間 20:13 執行，也支援 `workflow_dispatch`。舊版「guard 跳過但 job 顯示 success」已移除。每次真實執行都必須建立 `outputs/run_manifest.json`；mandatory stage、freshness 或輸出不完整時 validation step 會 exit 1。

Daily business record 採 upsert；`run_audit` 每次執行保留獨立 `run_id`。Google Sheet 使用既有 `daily_signals`、`run_log`、`spot_signal_daily`，並新增 normalized `signal_events`、`forecast_calendar`、`run_audit`。

必要 GitHub Secrets：

`FINLAB_REFRESH_TOKEN`、`FINLAB_SESSION_ID`、`FINLAB_API_KEY`、`GMAIL_SENDER`、`GMAIL_APP_PASSWORD`、`EMAIL_RECIPIENTS`、`GOOGLE_SHEET_ID`、`GOOGLE_SERVICE_ACCOUNT_JSON`。

FinLab 2.2 的 headless authentication 需要前三個 FinLab Secrets 同時存在；可先在已登入的本機執行 `python -m finlab token --env` 取得。舊 `FINLAB_API_TOKEN` 目前僅保留 SDK 已驗證的 deprecated 相容路徑，不會被轉填成 `FINLAB_API_KEY`。沒有完整 credential 時，伺服器不會啟動 browser login，而會標示 `AUTH_UNAVAILABLE`。

程式碼與 notebook 不保存 secrets。Email 使用 canonical signal events，不自行重算另一套訊號。

## Colab Quick Start

開啟上方 badge，在 Colab Secrets 建立 `FINLAB_REFRESH_TOKEN`、`FINLAB_SESSION_ID`、`FINLAB_API_KEY`，執行 `Run all`。若尚未完成 migration，SDK 目前仍允許暫用舊 `FINLAB_API_TOKEN`。預設 `preview` 會用最新 FinLab 資料完成一次 canonical calculation，將同一批 `signal_events.csv`、`forecast_calendar.csv`、`latest_signal_summary.csv`、`run_manifest.json` 發布到 repo runtime 的 `outputs/current/`，再備份至：

```text
MyDrive/Quant_Research/taiwan-market-thermometer/<run_id>/
```

接著啟動的 Streamlit 會直接讀取 `outputs/current/`，因此畫面與剛才的 Colab calculation 是同一個 run，不會默默切回舊 research snapshot，也不會重複消耗 FinLab quota。若 current run 無效且無法使用 FinLab，才會 fallback Research Snapshot，並在畫面明確標示非最新正式 Forecast。

`cloud_daily` 還需要 Gmail 與 Google Sheet secrets，會執行正式寫入與寄信。Colab Streamlit 透過 Colab port proxy 開啟，不依賴 Streamlit Community Cloud。

## 測試

```bash
python -m pytest tests-python -q
python -m compileall dashboard
npm test
npm run build
npm run validate:artifact
git diff --check
```

完整 baseline、migration contract、舊新對照與 validation report 位於 `docs/`。
