"""Regression gate for bounded, provenance-labelled research-statistics backfill.

Only presentation statistics are backfilled. The frozen research rule, evidence
grades/FDR, vote eligibility and timing definitions must not be changed.
"""
from __future__ import annotations

import pytest

from dashboard.research_registry import CANONICAL_SIGNALS, validate_registry


BY_ID = {item.signal_id: item for item in CANONICAL_SIGNALS}
FIELDS = ("sample_size", "mean_return", "median_return", "win_rate", "relative_mean_return")


@pytest.mark.parametrize(
    "signal_id,n,mean,median,positive_rate,relative",
    [
        ("breadth_big_up_ratio_5d_pr126_60_80_c3", 526, 0.004423407730482864, 0.004205677928413531, 0.6140684410646388, 0.002582258031652602),
        ("breadth_big_up_ratio_5d_pr126_60_80_c5", 526, 0.008314325006280402, 0.006621604189132158, 0.6349809885931559, 0.004728752493811213),
        ("breadth_big_up_ratio_5d_pr252_60_80_c3", 497, 0.004684192970089907, 0.004379562043795637, 0.5955734406438632, 0.002843043271259645),
    ],
)
def test_bullish_breadth_stats_are_exactly_v11_post2015(signal_id, n, mean, median, positive_rate, relative):
    signal = BY_ID[signal_id]
    assert signal.research_status == "RETAINED"
    assert signal.sample_size == n
    assert signal.mean_return == pytest.approx(mean)
    assert signal.median_return == pytest.approx(median)
    assert signal.win_rate == pytest.approx(positive_rate)
    assert signal.relative_mean_return == pytest.approx(relative)
    assert "POST_2015_10PCT" in signal.performance_scope
    assert "in-sample" in signal.performance_scope
    assert "Global FDR" in signal.risks
    assert "untouched OOS" in signal.risks
    assert signal.global_fdr is None and signal.family_fdr is None


def test_bearish_breadth_does_not_mislabel_positive_return_rate_as_short_win_rate():
    signal = BY_ID["breadth_up_ratio_1d_pr60_ge95_c1"]
    assert signal.research_status == "RETAINED"
    assert signal.direction == "bearish"
    assert signal.sample_size == 193
    assert signal.mean_return == pytest.approx(-0.0009175867429253348)
    assert signal.median_return == pytest.approx(-0.001596169193934571)
    assert signal.relative_mean_return == pytest.approx(-0.001055548566262569)
    assert signal.win_rate is None  # v11 36.8% is P(return > 0), not P(bearish HIT)
    assert "36.8%" in signal.risks
    assert "POST_2015_10PCT" in signal.performance_scope
    assert signal.global_fdr is None and signal.family_fdr is None


@pytest.mark.parametrize(
    "signal_id,relative,mean,median,n",
    [
        ("futures_divergence_le_m60_c1", -0.0013568295375945138, -0.00106, -0.00073, 593),
        ("futures_divergence_ge60_c1", 0.0009431479092912358, 0.00095, 0.00059, 602),
    ],
)
def test_futures_divergence_retains_original_statistics_with_exact_relative_benchmark(
    signal_id, relative, mean, median, n,
):
    signal = BY_ID[signal_id]
    assert signal.research_status == "RETAINED"
    assert signal.sample_size == n
    assert signal.mean_return == pytest.approx(mean)
    assert signal.median_return == pytest.approx(median)
    assert signal.relative_mean_return == pytest.approx(relative)
    assert "mean_diff_vs_nongroup" in signal.risks


def test_all_remaining_missing_performance_fields_are_explicit_and_intentional():
    missing = {
        signal.signal_id: {field for field in FIELDS if getattr(signal, field) is None}
        for signal in CANONICAL_SIGNALS
    }
    missing = {signal: values for signal, values in missing.items() if values}
    assert missing == {
        "breadth_up_ratio_1d_pr60_ge95_c1": {"win_rate"},
        "breadth_down_ratio_high_legacy": set(FIELDS),
        "futures_foreign_change_pr80_100_c3": set(FIELDS),
    }


def test_registry_contract_remains_valid():
    assert validate_registry() == []
