from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np
import pandas as pd

from .scoring import expanding_percentile, rolling_percentile, safe_divide
from .research_evidence import rolling_pr_inclusive

if TYPE_CHECKING:
    from .spot_flow_service import SpotFlowReport

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "data" / "reference"


@dataclass
class DashboardData:
    breadth: pd.DataFrame
    futures: pd.DataFrame | None
    spot: SpotFlowReport | None
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


def _price_tick(price: float) -> float:
    if price < 10: return 0.01
    if price < 50: return 0.05
    if price < 100: return 0.1
    if price < 500: return 0.5
    if price < 1000: return 1.0
    return 5.0


def _limit_price(reference: float, rate: float, upward: bool) -> float:
    raw = reference * (1 + rate if upward else 1 - rate)
    tick = _price_tick(raw)
    units = raw / tick
    rounded = np.floor(units + 1e-10) if upward else np.ceil(units - 1e-10)
    return float(rounded * tick)


def build_breadth_from_close(
    close: pd.DataFrame,
    symbols: list[str],
    reference_price: pd.DataFrame | None = None,
    close_0050: pd.Series | None = None,
) -> pd.DataFrame:
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
    daily_return = safe_divide(close, previous) - 1.0
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
    big_up_count = (daily_return.ge(0.05) & finite).sum(axis=1)
    frame["big_up_ratio"] = safe_divide(big_up_count, frame["valid_count"])
    frame["big_up_ratio_5d"] = frame["big_up_ratio"].rolling(5, min_periods=5).mean()
    frame["breadth_quality_ok"] = frame["coverage_ratio"].ge(0.80) & frame["classification_ratio"].ge(0.99)
    frame["breadth_rebound_score"] = expanding_percentile(frame["down_ratio"])
    frame["delta_down_ratio_1d"] = frame["down_ratio"].diff()
    if reference_price is not None:
        ref = pd.DataFrame(reference_price).reindex(index=close.index, columns=selected).apply(pd.to_numeric, errors="coerce")
        limit_up_count, limit_down_count = [], []
        for when in close.index:
            rate = 0.07 if when < pd.Timestamp("2015-06-01") else 0.10
            ups = downs = 0
            for symbol in selected:
                price, base = close.at[when, symbol], ref.at[when, symbol]
                if not (np.isfinite(price) and np.isfinite(base) and base > 0):
                    continue
                ups += price >= _limit_price(base, rate, True) - 1e-9
                downs += price <= _limit_price(base, rate, False) + 1e-9
            limit_up_count.append(ups); limit_down_count.append(downs)
        frame["limit_up_count"] = limit_up_count
        frame["limit_down_count"] = limit_down_count
        frame["limit_up_ratio"] = safe_divide(frame["limit_up_count"], frame["valid_count"])
        frame["limit_down_ratio"] = safe_divide(frame["limit_down_count"], frame["valid_count"])
    else:
        frame["limit_up_count"] = np.nan; frame["limit_down_count"] = np.nan
        frame["limit_up_ratio"] = np.nan; frame["limit_down_ratio"] = np.nan
    for metric in ("up_ratio", "big_up_ratio_5d", "delta_down_ratio_1d", "limit_up_ratio", "limit_down_ratio"):
        frame[f"{metric}_pr252"] = rolling_pr_inclusive(frame[metric], 252)
    if close_0050 is not None:
        etf = pd.to_numeric(pd.Series(close_0050), errors="coerce").reindex(frame.index)
        frame["market_regime"] = np.where(etf > etf.rolling(60, min_periods=60).mean(), "BULL", "BEAR")
    else:
        frame["market_regime"] = "資料不足"
    frame.loc[~frame["breadth_quality_ok"], "breadth_rebound_score"] = np.nan
    return frame


def load_live_breadth() -> pd.DataFrame:
    from finlab import data

    close = pd.DataFrame(data.get("price:收盤價")).copy()
    symbols = pd.read_csv(REFERENCE / "selected_stocks.csv", dtype={"symbol": str})["symbol"].tolist()
    reference = close.shift(1)
    reference_tables = (
        "dividend_tse:除權息參考價", "dividend_otc:除權息參考價",
        "capital_reduction_tse:恢復買賣參考價", "capital_reduction_otc:減資恢復買賣開始日參考價格",
        "par_value_change_tse:恢復買賣參考價", "par_value_change_otc:恢復買賣開始日參考價",
    )
    for table_name in reference_tables:
        event = pd.DataFrame(data.get(table_name)).reindex(index=reference.index, columns=reference.columns)
        reference = event.combine_first(reference)
    etf = pd.to_numeric(close["0050"], errors="coerce") if "0050" in close.columns else None
    return build_breadth_from_close(close, symbols, reference, etf)


def load_live_0050_close() -> pd.Series:
    """取得0050調整後收盤；若FinLab帳號無此表才退回原始收盤並明確命名。"""
    from finlab import data

    errors: list[str] = []
    for table_name in ("etl:adj_close", "price:收盤價"):
        try:
            frame = _native_daily_frame(data.get(table_name), table_name)
            if "0050" not in frame.columns:
                raise RuntimeError(f"{table_name}缺少0050欄位")
            series = pd.to_numeric(frame["0050"], errors="coerce").dropna().sort_index()
            if series.empty:
                raise RuntimeError(f"{table_name}的0050資料為空")
            series.name = "0050_adj_close" if table_name == "etl:adj_close" else "0050_close"
            return series
        except Exception as exc:
            errors.append(f"{table_name}: {exc}")
    raise RuntimeError("無法取得0050收盤資料；" + "；".join(errors))


def load_live_0050_prices() -> tuple[pd.Series, pd.Series]:
    """Return adjusted 0050 open and close required by O1→Cn outcomes."""
    from finlab import data

    open_frame = _native_daily_frame(data.get("etl:adj_open"), "etl:adj_open")
    close_frame = _native_daily_frame(data.get("etl:adj_close"), "etl:adj_close")
    if "0050" not in open_frame or "0050" not in close_frame:
        raise RuntimeError("FinLab adjusted open/close 缺少0050欄位")
    adjusted_open = pd.to_numeric(open_frame["0050"], errors="coerce").dropna().sort_index()
    adjusted_close = pd.to_numeric(close_frame["0050"], errors="coerce").dropna().sort_index()
    if adjusted_open.empty or adjusted_close.empty:
        raise RuntimeError("0050 adjusted open/close 資料為空")
    adjusted_open.name = "0050_adj_open"
    adjusted_close.name = "0050_adj_close"
    return adjusted_open, adjusted_close


FOREIGN_TX_COLUMN = "臺股期貨_外資及陸資"


def _native_daily_frame(source: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """將FinLab物件轉成原生Pandas；移除無日期列，但不補值或猜日期。"""
    frame = pd.DataFrame(source).copy()
    if frame.empty:
        raise RuntimeError(f"FinLab資料表為空：{table_name}")
    parsed_index = pd.to_datetime(frame.index, errors="coerce")
    valid_date = ~pd.isna(parsed_index)
    frame = frame.loc[valid_date].copy()
    if frame.empty:
        raise RuntimeError(f"FinLab資料表沒有可辨識的有效日期：{table_name}")
    frame.index = parsed_index[valid_date]
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
    dealer_long_oi: pd.DataFrame | None = None,
    dealer_short_oi: pd.DataFrame | None = None,
    dealer_net_oi: pd.DataFrame | None = None,
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
    denominator_3d = foreign["foreign_long_oi"].shift(3) + foreign["foreign_short_oi"].shift(3)
    foreign["foreign_net_oi_change_ratio_3d"] = safe_divide(
        foreign["foreign_net_oi"] - foreign["foreign_net_oi"].shift(3), denominator_3d
    )
    if dealer_long_oi is not None and dealer_short_oi is not None and dealer_net_oi is not None:
        dealer = pd.concat([
            _select_tx_column(dealer_long_oi, "dealer_long_oi", "臺股期貨_自營商"),
            _select_tx_column(dealer_short_oi, "dealer_short_oi", "臺股期貨_自營商"),
            _select_tx_column(dealer_net_oi, "dealer_net_oi", "臺股期貨_自營商"),
        ], axis=1, join="inner")
        foreign = foreign.join(dealer, how="left")
        dealer_denominator = foreign["dealer_long_oi"].shift(3) + foreign["dealer_short_oi"].shift(3)
        foreign["dealer_net_oi_change_ratio_3d"] = safe_divide(
            foreign["dealer_net_oi"] - foreign["dealer_net_oi"].shift(3), dealer_denominator
        )
        foreign_pr = _strict_prior_pr(foreign["foreign_net_oi_change_ratio_3d"], 120)
        dealer_pr = _strict_prior_pr(foreign["dealer_net_oi_change_ratio_3d"], 120)
        foreign["foreign_dealer_pr_divergence"] = (foreign_pr - dealer_pr) / 100.0
    return foreign.sort_index()


def _select_tx_column(source: pd.DataFrame, table_name: str, column: str) -> pd.Series:
    frame = _native_daily_frame(source, table_name)
    if column not in frame.columns:
        raise RuntimeError(f"{table_name}缺少精確欄位「{column}」")
    return pd.to_numeric(frame[column], errors="coerce").rename(table_name)


def _strict_prior_pr(series: pd.Series, window: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    result = pd.Series(np.nan, index=values.index)
    for position in range(window, len(values)):
        previous = values.iloc[position-window:position].dropna()
        current = values.iloc[position]
        if len(previous) == window and np.isfinite(current):
            result.iloc[position] = float((previous <= current).mean() * 100.0)
    return result


def load_live_futures() -> pd.DataFrame:
    """直接從FinLab載入資料；不依賴其他研究repository或資料夾位置。"""
    from finlab import data

    prefix = "futures_institutional_investors_trading_summary"
    long_oi = data.get(f"{prefix}:多方未平倉口數")
    short_oi = data.get(f"{prefix}:空方未平倉口數")
    net_oi = data.get(f"{prefix}:多空未平倉口數淨額")
    return build_foreign_futures_from_tables(long_oi, short_oi, net_oi, long_oi, short_oi, net_oi)


def load_dashboard_data(use_finlab: bool = False) -> DashboardData:
    if not use_finlab:
        return DashboardData(load_breadth_snapshot(), None, None, "研究快照")
    try:
        import finlab
        finlab.login()
        from .spot_flow_service import load_live_spot_flow

        return DashboardData(load_live_breadth(), load_live_futures(), load_live_spot_flow(), "FinLab即時資料")
    except Exception as exc:
        return DashboardData(load_breadth_snapshot(), None, None, "研究快照（FinLab更新失敗）", f"{type(exc).__name__}: {exc}")
