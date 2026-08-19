from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math


@dataclass(frozen=True)
class Conclusion:
    overall_state: str
    headline: str
    breadth_summary: str
    foreign_summary: str
    reference_action: str
    risk_warning: str
    data_confidence: str


def _missing(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def build_conclusion(
    breadth_score: float | None,
    foreign_score: float | None,
    breadth_date: date | None,
    foreign_date: date | None,
    coverage_ratio: float | None = None,
) -> Conclusion:
    warning = "本頁為統計研究結果整理，不構成投資建議。"
    invalid = (
        _missing(breadth_score)
        or _missing(foreign_score)
        or breadth_date is None
        or foreign_date is None
        or breadth_date != foreign_date
        or (coverage_ratio is not None and coverage_ratio < 0.80)
    )
    if invalid:
        return Conclusion("資料不足／暫不判讀", "資料日期尚未對齊", "市場廣度保留獨立顯示。", "外資期貨等待同日有效資料。", "維持觀察，不產生方向建議。", warning, "低")

    breadth_high = float(breadth_score) >= 80
    foreign_high = float(foreign_score) > 60
    foreign_low = float(foreign_score) < 40
    foreign_extreme_low = float(foreign_score) <= 5
    if 40 <= float(foreign_score) <= 60:
        return Conclusion("訊號中性", "外資方向尚未表態", "市場廣度依自身分數解讀。", "外資方向分數位於中性區。", "等待價格與部位變化確認。", warning, "中")
    if breadth_high and foreign_high:
        return Conclusion("反彈條件相對完整", "超跌，且外資往多方移動", "市場廣度提供5至10日均值回歸條件。", "外資期貨提供隔日偏多確認。", "等待價格止跌或既定進場條件。", warning, "中")
    if breadth_high and foreign_low:
        if foreign_extreme_low:
            return Conclusion("超跌但外資極端偏空", "市場超跌，外資大幅往空方移動", "後續存在均值回歸條件。", "外資極端空方調整的負向差異可能延續至5至20日。", "避免把超跌直接視為底部，等待外資壓力緩解。", warning, "中")
        return Conclusion("超跌但尚未獲得外資確認", "市場超跌，外資仍偏防守", "後續存在反彈傾向。", "外資期貨仍往空方移動。", "避免把超跌直接視為底部。", warning, "中")
    if not breadth_high and foreign_high:
        return Conclusion("隔日方向偏多", "外資往多方移動", "目前沒有明顯超跌條件。", "隔日氣氛相對正向。", "不可延伸為5至10日反彈訊號。", warning, "中")
    if foreign_extreme_low:
        return Conclusion("短中期偏保守", "外資極端往空方移動", "市場尚未進入極端反彈區。", "歷史負向差異可能延續至5至20日。", "降低追價，等待部位壓力緩解。", warning, "中")
    return Conclusion("短線偏保守", "外資偏空，且沒有超跌條件", "市場尚未進入極端反彈區。", "外資期貨往空方移動，主要作為隔日方向參考。", "降低追價，維持風險控制。", warning, "中")
