from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

from .scoring import expanding_percentile, rolling_percentile, safe_divide

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "data" / "reference"


@dataclass
class DashboardData:
    breadth: pd.DataFrame
    futures: pd.DataFrame | None
    source: str
    error: str | None = None


def load_breadth_snapshot() -> pd.DataFrame:
    frame = pd.read_csv(REFERENCE / "market_breadth_dataset.csv", parse_dates=["date"]).set_index("date").sort_index()
    frame["candidate_count"] = frame["valid_count"]
    frame["classified_count"] = frame["up_count"] + frame["down_count"] + frame["flat_count"]
    frame["unclassified_count"] = (frame["candidate_count"] - frame["classified_count"]).clip(lower=0)
    frame["classification_ratio"] = safe_divide(frame["classified_count"], frame["candidate_count"])
    frame["breadth_quality_ok"] = frame["coverage_ratio"].ge(0.80) & frame["classification_ratio"].ge(0.99)
    frame["breadth_rebound_score"] = expanding_percentile(frame["down_ratio"])
    frame.loc[~frame["breadth_quality_ok"], "breadth_rebound_score"] = np.nan
    return frame


def build_breadth_from_close(close: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """由收盤價建立廣度，並拒絕NaN/inf與無法分類的觀察值。"""
    close = pd.DataFrame(close).copy()
    close.index = pd.to_datetime(close.index)
    selected = [symbol for symbol in symbols if symbol in close.columns]
    close = close.loc[:, selected].apply(pd.to_numeric, errors="coerce").sort_index()
    previous = close.shift(1)
    candidate = close.notna() & previous.notna()
    finite = pd.DataFrame(
        np.isfinite(close.to_numpy()) & np.isfinite(previous.to_numpy()),
        index=close.index,
        columns=close.columns,
    )
    change = close.sub(previous)
    up_mask = change.gt(0) & finite
    down_mask = change.lt(0) & finite
    flat_mask = change.eq(0) & finite
    classified = up_mask | down_mask | flat_mask
    up = up_mask.sum(axis=1)
    down = down_mask.sum(axis=1)
    flat = flat_mask.sum(axis=1)
    candidate_count = candidate.sum(axis=1)
    classified_count = classified.sum(axis=1)
    denominator = up + down
    frame = pd.DataFrame({
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "candidate_count": candidate_count,
        "classified_count": classified_count,
        "valid_count": classified_count,
        "unclassified_count": (candidate_count - classified_count).clip(lower=0),
    })
    frame["universe_total"] = len(selected)
    frame["coverage_ratio"] = safe_divide(frame["valid_count"], frame["universe_total"])
    frame["classification_ratio"] = safe_divide(frame["classified_count"], frame["candidate_count"])
    frame["up_ratio"] = safe_divide(frame["up_count"], denominator)
    frame["down_ratio"] = safe_divide(frame["down_count"], denominator)
    frame["breadth_net_ratio"] = safe_divide(frame["up_count"] - frame["down_count"], denominator)
    frame["breadth_quality_ok"] = frame["coverage_ratio"].ge(0.80) & frame["classification_ratio"].ge(0.99)
    frame["breadth_rebound_score"] = expanding_percentile(frame["down_ratio"])
    frame.loc[~frame["breadth_quality_ok"], "breadth_rebound_score"] = np.nan
    return frame


def load_live_breadth() -> pd.DataFrame:
    from finlab import data

    close = pd.DataFrame(data.get("price:收盤價")).copy()
    symbols = pd.read_csv(REFERENCE / "selected_stocks.csv", dtype={"symbol": str})["symbol"].tolist()
    return build_breadth_from_close(close, symbols)


FOREIGN_TX_COLUMN = "臺股期貨_外資及陸資"


def _native_daily_frame(source: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """將FinLab物件轉成原生Pandas，不改寫或補齊原始資料。"""
    frame = pd.DataFrame(source).copy()
    if frame.empty:
        raise RuntimeError(f"FinLab資料表為空：{table_name}")
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    if frame.index.isna().any():
        raise RuntimeError(f"FinLab資料表含無法辨識的日期：{table_name}")
    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    frame.index.name = "date"
    return frame


def _select_foreign_tx(source: pd.DataFrame, table_name: str) -> pd.Series:
    frame = _native_daily_frame(source, table_name)
    if FOREIGN_TX_COLUMN not in frame.columns:
        available = ", ".join(map(str, frame.columns[:12]))
        suffix = " ..." if len(frame.columns) > 12 else ""
        raise RuntimeError(
            f"{table_name}缺少精確欄位「{FOREIGN_TX_COLUMN}」；"
            f"目前欄位：{available}{suffix}"
        )
    return pd.to_numeric(frame[FOREIGN_TX_COLUMN], errors="coerce").rename(table_name)


def build_foreign_futures_from_tables(
    long_oi: pd.DataFrame,
    short_oi: pd.DataFrame,
    net_oi: pd.DataFrame,
) -> pd.DataFrame:
    """獨立整理外資臺股期貨資料，並嚴格驗證Long－Short＝Net。"""
    foreign = pd.concat(
        [
            _select_foreign_tx(long_oi, "foreign_long_oi"),
            _select_foreign_tx(short_oi, "foreign_short_oi"),
            _select_foreign_tx(net_oi, "foreign_net_oi"),
        ],
        axis=1,
        join="inner",
    ).sort_index()
    if foreign.empty:
        raise RuntimeError("外資臺股期貨多方、空方與淨部位沒有共同交易日。")

    valid = foreign.dropna(subset=["foreign_long_oi", "foreign_short_oi", "foreign_net_oi"])
    if valid.empty:
        raise RuntimeError("外資臺股期貨欄位沒有可驗證的完整觀察值。")
    formula_error = valid["foreign_long_oi"] - valid["foreign_short_oi"] - valid["foreign_net_oi"]
    mismatch = formula_error.abs().gt(1e-8)
    if mismatch.any():
        examples = ", ".join(str(value.date()) for value in mismatch.index[mismatch][:5])
        raise RuntimeError(f"外資臺股期貨資料未通過Long－Short＝Net驗證；異常日期：{examples}")

    denominator = foreign["foreign_long_oi"].shift(1) + foreign["foreign_short_oi"].shift(1)
    foreign["foreign_oi_ratio"] = safe_divide(foreign["foreign_net_oi"], foreign["foreign_long_oi"] + foreign["foreign_short_oi"])
    foreign["foreign_oi_change_ratio"] = safe_divide(foreign["foreign_net_oi"].diff(), denominator)
    foreign["foreign_long_change_ratio"] = safe_divide(foreign["foreign_long_oi"].diff(), denominator)
    foreign["foreign_short_change_ratio"] = safe_divide(foreign["foreign_short_oi"].diff(), denominator)
    foreign["foreign_direction_score"] = rolling_percentile(foreign["foreign_oi_change_ratio"])
    return foreign.sort_index()


def load_live_futures() -> pd.DataFrame:
    """直接從FinLab載入資料；不依賴其他研究repository或資料夾位置。"""
    from finlab import data

    prefix = "futures_institutional_investors_trading_summary"
    long_oi = data.get(f"{prefix}:多方未平倉口數")
    short_oi = data.get(f"{prefix}:空方未平倉口數")
    net_oi = data.get(f"{prefix}:多空未平倉口數淨額")
    return build_foreign_futures_from_tables(long_oi, short_oi, net_oi)


def load_dashboard_data(use_finlab: bool = False) -> DashboardData:
    if not use_finlab:
        return DashboardData(load_breadth_snapshot(), None, "研究快照")
    try:
        import finlab
        finlab.login()
        return DashboardData(load_live_breadth(), load_live_futures(), "FinLab即時資料")
    except Exception as exc:
        return DashboardData(load_breadth_snapshot(), None, "研究快照（FinLab更新失敗）", f"{type(exc).__name__}: {exc}")
