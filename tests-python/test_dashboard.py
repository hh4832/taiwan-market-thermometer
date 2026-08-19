from datetime import date
import math
import unittest
import pandas as pd

from dashboard.conclusion_engine import build_conclusion
from dashboard.data_service import build_breadth_from_close
from dashboard.scoring import expanding_percentile, safe_divide


class DashboardTests(unittest.TestCase):
    def test_expanding_percentile_has_no_lookahead(self):
        series = pd.Series(range(130), dtype=float)
        result = expanding_percentile(series, min_periods=126)
        self.assertTrue(math.isnan(result.iloc[125]))
        self.assertEqual(result.iloc[126], 100.0)

    def test_safe_divide_zero_is_nan(self):
        result = safe_divide(pd.Series([1.0]), pd.Series([0.0]))
        self.assertTrue(math.isnan(result.iloc[0]))

    def test_mismatched_dates_stop_conclusion(self):
        result = build_conclusion(95, 90, date(2026, 7, 15), date(2026, 7, 14), 0.98)
        self.assertEqual(result.overall_state, "資料不足／暫不判讀")

    def test_high_high_quadrant(self):
        result = build_conclusion(95, 90, date(2026, 7, 15), date(2026, 7, 15), 0.98)
        self.assertEqual(result.overall_state, "反彈條件相對完整")

    def test_extreme_foreign_bearish_signal_has_longer_horizon_warning(self):
        result = build_conclusion(60, 5, date(2026, 7, 15), date(2026, 7, 15), 0.98)
        self.assertEqual(result.overall_state, "短中期偏保守")
        self.assertIn("5至20日", result.foreign_summary)

    def test_nonextreme_foreign_bearish_signal_stays_short_term(self):
        result = build_conclusion(60, 20, date(2026, 7, 15), date(2026, 7, 15), 0.98)
        self.assertEqual(result.overall_state, "短線偏保守")
        self.assertIn("隔日", result.foreign_summary)

    def test_breadth_rejects_nonfinite_unclassified_prices(self):
        close = pd.DataFrame(
            {"A": [10.0, 9.0], "B": [20.0, float("inf")], "C": [30.0, 30.0]},
            index=pd.to_datetime(["2026-08-18", "2026-08-19"]),
        )
        row = build_breadth_from_close(close, ["A", "B", "C"]).iloc[-1]
        self.assertEqual(row["candidate_count"], 3)
        self.assertEqual(row["classified_count"], 2)
        self.assertEqual(row["unclassified_count"], 1)
        self.assertFalse(bool(row["breadth_quality_ok"]))
        self.assertTrue(math.isnan(row["breadth_rebound_score"]))


if __name__ == "__main__":
    unittest.main()
