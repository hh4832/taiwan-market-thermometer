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

## GitHub Actions 與完整性

`.github/workflows/daily-cloud-report.yml` 在臺北時間 20:13 執行，也支援 `workflow_dispatch`。舊版「guard 跳過但 job 顯示 success」已移除。每次真實執行都必須建立 `outputs/run_manifest.json`；mandatory stage、freshness 或輸出不完整時 validation step 會 exit 1。

Daily business record 採 upsert；`run_audit` 每次執行保留獨立 `run_id`。Google Sheet 使用既有 `daily_signals`、`run_log`、`spot_signal_daily`，並新增 normalized `signal_events`、`forecast_calendar`、`run_audit`。

必要 GitHub Secrets：

`FINLAB_API_TOKEN`、`GMAIL_SENDER`、`GMAIL_APP_PASSWORD`、`EMAIL_RECIPIENTS`、`GOOGLE_SHEET_ID`、`GOOGLE_SERVICE_ACCOUNT_JSON`。

程式碼與 notebook 不保存 secrets。Email 使用 canonical signal events，不自行重算另一套訊號。

## Colab Quick Start

開啟上方 badge，在 Colab Secrets 建立 `FINLAB_API_TOKEN`，執行 `Run all`。預設 `preview` 會建立 events、calendar、manifest 並備份至：

```text
MyDrive/Quant_Research/taiwan-market-thermometer/<run_id>/
```

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
