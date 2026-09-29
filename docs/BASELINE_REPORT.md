# Baseline Report

- Repository: `hh4832/taiwan-market-thermometer`
- Branch: `main`
- Starting commit: `7669882 Fix FinLab OTC par value reference field`
- Starting working tree: clean
- Local Python: 3.12.14; GitHub Actions target: 3.11
- Dependency file: `local-requirements.txt`; frontend: `package.json`
- Baseline Python tests: 22 passed
- Baseline Node tests: 1 passed; build and artifact validation passed

## Previous production architecture

`cloud_daily` independently loaded breadth/futures/spot, wrote the wide `daily_signals` sheet, calculated close-to-close outcomes, rendered a separately hard-coded email, and sent Gmail. `research_evidence.py`, `spot_flow_service.py`, Streamlit and email contained overlapping research definitions. The Actions daily guard could exit successfully without producing a report.

## Previous schemas and entry points

- Actions: `.github/workflows/daily-cloud-report.yml`
- Cloud: `python -m dashboard.cloud_daily`
- Streamlit: `python -m streamlit run dashboard/app.py`
- Email: `dashboard/daily_email.py`
- Sheets: `daily_signals`, `run_log`, `spot_signal_daily`
- Outcomes: `d1/d3/d5/d10/d20_return`, previously C0 close based
- Colab: `notebooks/taiwan_market_thermometer_colab.ipynb`
