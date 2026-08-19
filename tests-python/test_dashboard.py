from datetime import date
import math
import unittest
import pandas as pd

from dashboard.conclusion_engine import build_conclusion
from dashboard.data_service import build_breadth_from_close, build_foreign_futures_from_tables
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

    def test_standalone_futures_loader_selects_exact_foreign_tx_column(self):
        dates = pd.bdate_range("2025-01-02", periods=140)
        column = "臺股期貨_外資及陸資"
        long_values = pd.Series(range(20_000, 20_140), index=dates, dtype=float)
        short_values = pd.Series(range(25_000, 25_140), index=dates, dtype=float)
        long_oi = pd.DataFrame({column: long_values, "小型臺指期貨_外資及陸資": 1.0})
        short_oi = pd.DataFrame({column: short_values, "小型臺指期貨_外資及陸資": 1.0})
        net_oi = pd.DataFrame({column: long_values - short_values, "小型臺指期貨_外資及陸資": 0.0})

        result = build_foreign_futures_from_tables(long_oi, short_oi, net_oi)

        self.assertEqual(result.iloc[-1]["foreign_net_oi"], -5_000.0)
        self.assertIn("foreign_direction_score", result.columns)
        self.assertFalse(math.isnan(result.iloc[-1]["foreign_direction_score"]))

    def test_standalone_futures_loader_rejects_missing_exact_column(self):
        dates = pd.bdate_range("2025-01-02", periods=2)
        wrong = pd.DataFrame({"小型臺指期貨_外資及陸資": [1.0, 2.0]}, index=dates)
        with self.assertRaisesRegex(RuntimeError, "臺股期貨_外資及陸資"):
            build_foreign_futures_from_tables(wrong, wrong, wrong)

    def test_standalone_futures_loader_rejects_formula_mismatch(self):
        dates = pd.bdate_range("2025-01-02", periods=2)
        column = "臺股期貨_外資及陸資"
        long_oi = pd.DataFrame({column: [10.0, 11.0]}, index=dates)
        short_oi = pd.DataFrame({column: [4.0, 5.0]}, index=dates)
        wrong_net = pd.DataFrame({column: [999.0, 999.0]}, index=dates)
        with self.assertRaisesRegex(RuntimeError, "Long－Short＝Net"):
            build_foreign_futures_from_tables(long_oi, short_oi, wrong_net)

    def test_standalone_futures_loader_drops_undated_finlab_rows(self):
        column = "臺股期貨_外資及陸資"
        index = [pd.Timestamp("2025-01-02"), None, pd.Timestamp("2025-01-03")]
        long_oi = pd.DataFrame({column: [10.0, 999.0, 12.0]}, index=index)
        short_oi = pd.DataFrame({column: [4.0, 999.0, 5.0]}, index=index)
        net_oi = pd.DataFrame({column: [6.0, 0.0, 7.0]}, index=index)

        result = build_foreign_futures_from_tables(long_oi, short_oi, net_oi)

        self.assertEqual(len(result), 2)
        self.assertFalse(result.index.isna().any())
        self.assertEqual(result.iloc[-1]["foreign_net_oi"], 7.0)

    def test_standalone_futures_loader_rejects_table_with_only_undated_rows(self):
        column = "臺股期貨_外資及陸資"
        undated = pd.DataFrame({column: [1.0]}, index=[None])
        with self.assertRaisesRegex(RuntimeError, "沒有可辨識的有效日期"):
            build_foreign_futures_from_tables(undated, undated, undated)


if __name__ == "__main__":
    unittest.main()
