from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dashboard.daily_email import build_vnext_report
from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.research_evidence import build_breadth_evidence
from dashboard.research_registry import CANONICAL_SIGNALS
from dashboard.signal_engine import _inside, evaluate_signals, production_events, rolling_pr


ROOT = Path(__file__).resolve().parents[1]


def _breadth(signal_id: str):
    return next(signal for signal in CANONICAL_SIGNALS if signal.signal_id == signal_id)


def test_strict_prior_pr_excludes_current_observation():
    values = pd.Series([100.0, 100.0, 100.0, 50.0])
    strict = rolling_pr(values, 3, strict_prior=True)
    inclusive = rolling_pr(values, 3, strict_prior=False)
    assert strict.iloc[-1] == 0.0
    assert inclusive.iloc[-1] == pytest.approx(100 / 3)


@pytest.mark.parametrize("window", [60, 126, 252])
def test_breadth_strict_prior_warmup_and_first_valid_value(window: int):
    values = pd.Series(np.arange(window + 1, dtype=float))
    result = rolling_pr(values, window, strict_prior=True)
    assert result.iloc[:window].isna().all()
    assert result.iloc[window] == 100.0


def test_breadth_strict_prior_missing_data_requires_complete_prior_window_and_current():
    prior_missing = pd.Series([1.0, np.nan, 3.0, 4.0])
    current_missing = pd.Series([1.0, 2.0, 3.0, np.nan])
    assert np.isnan(rolling_pr(prior_missing, 3, strict_prior=True).iloc[-1])
    assert np.isnan(rolling_pr(current_missing, 3, strict_prior=True).iloc[-1])


def test_breadth_threshold_boundaries_are_exact():
    exhaustion = _breadth("breadth_up_ratio_1d_pr60_ge95_c1")
    continuation = _breadth("breadth_big_up_ratio_5d_pr126_60_80_c3")
    assert not _inside(94.999, exhaustion)
    assert _inside(95.0, exhaustion)
    assert _inside(100.0, exhaustion)
    assert not _inside(59.999, continuation)
    assert _inside(60.0, continuation)
    assert _inside(79.999, continuation)
    assert not _inside(80.0, continuation)


def test_canonical_breadth_registry_matches_formal_retained_set():
    signals = [signal for signal in CANONICAL_SIGNALS if signal.source == "market_breadth"]
    by_id = {signal.signal_id: signal for signal in signals}
    retained_ids = {
        "breadth_up_ratio_1d_pr60_ge95_c1",
        "breadth_big_up_ratio_5d_pr126_60_80_c3",
        "breadth_big_up_ratio_5d_pr126_60_80_c5",
        "breadth_big_up_ratio_5d_pr252_60_80_c3",
    }
    assert {signal.signal_id for signal in signals if signal.research_status == "RETAINED"} == retained_ids
    assert not any("big_up_ratio_5d_pr60" in signal.signal_id for signal in signals)

    exhaustion = by_id["breadth_up_ratio_1d_pr60_ge95_c1"]
    assert (exhaustion.metric, exhaustion.rolling_window, exhaustion.horizon) == ("up_ratio", 60, 1)
    assert exhaustion.direction == "bearish"
    assert exhaustion.normalization == "rolling_pr_strict_prior"
    assert exhaustion.sample_size is None

    expected_big_up = {
        "breadth_big_up_ratio_5d_pr126_60_80_c3": (126, 3),
        "breadth_big_up_ratio_5d_pr126_60_80_c5": (126, 5),
        "breadth_big_up_ratio_5d_pr252_60_80_c3": (252, 3),
    }
    for signal_id, (window, horizon) in expected_big_up.items():
        signal = by_id[signal_id]
        assert signal.rolling_window == window
        assert signal.horizon == horizon
        assert signal.normalization == "rolling_pr_strict_prior"
        assert signal.direction == "bullish"
        assert signal.monotonicity == "non-monotonic"
        assert "return >= +5%" in signal.formula
        assert "trailing 5-session mean" in signal.formula
        assert signal.sample_size is None
        assert signal.mean_return is None
        assert signal.median_return is None
        assert signal.win_rate is None
        assert signal.relative_mean_return is None
        assert signal.global_fdr is None
        assert signal.family_fdr is None


def test_retest_breadth_signal_cannot_vote_even_when_matched():
    spec = _breadth("breadth_down_ratio_high_legacy")
    sessions = pd.bdate_range("2025-01-01", periods=300)
    values = pd.Series(np.arange(300, dtype=float), index=sessions)
    event = evaluate_signals(
        {"down_ratio": values}, sessions, signals=(spec,), as_of_date=sessions[-1]
    )[0]
    assert event.matched
    assert event.research_status == "RETEST"
    assert production_events((event,)) == ()
    assert build_forecast_calendar((event,)).empty


def test_vnext_email_uses_canonical_events_not_legacy_breadth(monkeypatch):
    import dashboard.research_evidence as legacy

    monkeypatch.setattr(
        legacy,
        "build_breadth_evidence",
        lambda _breadth: (_ for _ in ()).throw(AssertionError("legacy breadth called")),
    )
    subject, plain, _html = build_vnext_report(
        (), pd.DataFrame(), "2026-10-06", "run", "commit", "SUCCESS"
    )
    assert "正式訊號 0 個" in subject
    assert "VALID_NO_SIGNAL" in plain


def test_dashboard_and_vnext_email_do_not_import_legacy_breadth_builder():
    app_source = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    email_source = (ROOT / "dashboard" / "daily_email.py").read_text(encoding="utf-8")
    assert "build_breadth_evidence" not in app_source
    assert "build_daily_evidence_report" not in email_source
    assert "CANONICAL_SIGNALS" in app_source
    assert "build_canonical_events" in email_source
    assert "build_vnext_report" in email_source


def test_legacy_breadth_builder_is_hard_disabled():
    with pytest.raises(RuntimeError, match="Legacy market-breadth evidence is disabled"):
        build_breadth_evidence(pd.DataFrame())
