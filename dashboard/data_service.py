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


def load_live_futures() -> pd.DataFrame:
    try:
        from institutional_futures_oi_research.config import ResearchConfig
        from institutional_futures_oi_research.finlab_loader import load_finlab_data
        from institutional_futures_oi_research.core import build_raw_dataset, add_institutional_aggregates, add_predictors

        config = ResearchConfig()
        loaded = load_finlab_data(config=config, login=False, include_equivalent_market_oi=False)
        raw = build_raw_dataset(loaded.adjusted_close, loaded.long_oi, loaded.short_oi, loaded.net_oi, config)
        predictors = add_predictors(add_institutional_aggregates(raw), config)
        foreign = predictors.filter(regex=r"^foreign_").copy()
    except (ImportError, TypeError):
        from finlab import data
        prefix = "futures_institutional_investors_trading_summary"
        long_oi = pd.DataFrame(data.get(f"{prefix}:多方未平倉口數"))
        short_oi = pd.DataFrame(data.get(f"{prefix}:空方未平倉口數"))
        net_oi = pd.DataFrame(data.get(f"{prefix}:多空未平倉口數淨額"))
        raise RuntimeError("找得到FinLab資料，但無法安全辨識外資與臺股期貨欄位；請使用現有研究模組。")

    required = ["foreign_long_oi", "foreign_short_oi", "foreign_net_oi"]
    missing = [column for column in required if column not in foreign.columns]
    if missing:
        raise RuntimeError(f"外資期貨欄位不足：{missing}")
    denominator = foreign["foreign_long_oi"].shift(1) + foreign["foreign_short_oi"].shift(1)
    foreign["foreign_oi_ratio"] = safe_divide(foreign["foreign_net_oi"], foreign["foreign_long_oi"] + foreign["foreign_short_oi"])
    foreign["foreign_oi_change_ratio"] = safe_divide(foreign["foreign_net_oi"].diff(), denominator)
    foreign["foreign_long_change_ratio"] = safe_divide(foreign["foreign_long_oi"].diff(), denominator)
    foreign["foreign_short_change_ratio"] = safe_divide(foreign["foreign_short_oi"].diff(), denominator)
    foreign["foreign_direction_score"] = rolling_percentile(foreign["foreign_oi_change_ratio"])
    return foreign.sort_index()


def load_dashboard_data(use_finlab: bool = False) -> DashboardData:
    if not use_finlab:
        return DashboardData(load_breadth_snapshot(), None, "研究快照")
    try:
        import finlab
        finlab.login()
        return DashboardData(load_live_breadth(), load_live_futures(), "FinLab即時資料")
    except Exception as exc:
        return DashboardData(load_breadth_snapshot(), None, "研究快照（FinLab更新失敗）", f"{type(exc).__name__}: {exc}")
