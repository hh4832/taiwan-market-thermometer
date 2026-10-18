from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dashboard.canonical_pipeline import canonical_metrics
from dashboard.daily_email import build_vnext_report
from dashboard.forecast_calendar import build_evidence_calendar, build_forecast_calendar
from dashboard.forecast_presentation import build_forecast_presentation
from dashboard.options_service import DATE_COLUMN, SCHEMA, _daily_options_frame, build_options_metrics
from dashboard.outcomes import outcome_for_signal
from dashboard.research_registry import CANONICAL_SIGNALS, validate_registry
from dashboard.signal_engine import (
    _inside,
    evaluate_signals,
    events_frame,
    events_from_frame,
    production_evaluation,
    production_events,
    rolling_pr,
    visible_evidence_counts,
    visible_events,
)
from dashboard.trading_calendar import target_session
from dashboard.signal_presentation import (
    event_audit_record,
    research_direction_label,
    research_evidence_record,
)


COMMIT = "96177df77da9b93936c3a414bda7e686719df546"
RUN = "20261002_170439_96177df77da9"


def _options():
    return tuple(signal for signal in CANONICAL_SIGNALS if signal.source == "options")


def _source(put: list[float], call: list[float]) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-02", periods=len(put))
    p = pd.Series(put, index=index, dtype=float)
    c = pd.Series(call, index=index, dtype=float)
    return pd.DataFrame({
        DATE_COLUMN: index,
        SCHEMA["volume_put"]: p,
        SCHEMA["volume_call"]: c,
        SCHEMA["volume_provider_pcr_percent"]: p / c.replace(0, np.nan) * 100,
        SCHEMA["open_interest_put"]: p * 2,
        SCHEMA["open_interest_call"]: c * 2,
        SCHEMA["open_interest_provider_pcr_percent"]: p / c.replace(0, np.nan) * 100,
    }).reset_index(drop=True)


def test_exact_finlab_schema_is_required():
    source = _source([1, 2], [2, 2]).drop(columns=[SCHEMA["volume_put"]])
    with pytest.raises(RuntimeError, match="schema missing"):
        build_options_metrics(source)


def test_daily_options_frame_is_idempotent():
    source = _source([1, 2, 3, 4], [2, 2, 2, 2])
    once = _daily_options_frame(source)
    twice = _daily_options_frame(once)
    pd.testing.assert_frame_equal(once, twice)


def test_normalized_options_frame_can_be_reused_by_canonical_pipeline():
    normalized = _daily_options_frame(_source([1, 2, 3, 4], [2, 2, 2, 2]))
    metrics = canonical_metrics(pd.DataFrame(), pd.DataFrame(), None, normalized)
    assert "options_volume_pcr_level_0d" in metrics
    assert "options_open_interest_pcr_change_3d" in metrics


def test_malformed_duplicate_and_unsorted_dates_are_rejected():
    malformed = _source([1, 2], [2, 2])
    malformed[DATE_COLUMN] = malformed[DATE_COLUMN].astype(object)
    malformed.loc[1, DATE_COLUMN] = "not-a-date"
    with pytest.raises(RuntimeError, match="malformed dates"):
        build_options_metrics(malformed)

    duplicate = _source([1, 2], [2, 2])
    duplicate.loc[1, DATE_COLUMN] = duplicate.loc[0, DATE_COLUMN]
    with pytest.raises(RuntimeError, match="duplicate dates"):
        build_options_metrics(duplicate)

    unsorted = _source([1, 2], [2, 2]).iloc[::-1]
    with pytest.raises(RuntimeError, match="not date-sorted"):
        build_options_metrics(unsorted)


def test_nonfinite_negative_and_all_missing_required_values_are_rejected():
    infinite = _source([1, 2], [2, 2])
    infinite.loc[1, SCHEMA["volume_put"]] = np.inf
    with pytest.raises(RuntimeError, match="infinite"):
        build_options_metrics(infinite)

    negative = _source([1, 2], [2, 2])
    negative.loc[1, SCHEMA["volume_put"]] = -1
    with pytest.raises(RuntimeError, match="negative"):
        build_options_metrics(negative)

    missing = _source([1, 2], [2, 2])
    missing[SCHEMA["volume_put"]] = np.nan
    with pytest.raises(RuntimeError, match="all-missing"):
        build_options_metrics(missing)


def test_provider_pcr_is_cross_validated():
    source = _source([1, 2], [2, 2])
    source.loc[1, SCHEMA["volume_provider_pcr_percent"]] = 999
    with pytest.raises(RuntimeError, match="does not match"):
        build_options_metrics(source)


def test_change_is_session_difference_not_percent_change():
    metrics = build_options_metrics(_source([100, 110, 150, 170], [100] * 4))
    assert metrics["options_volume_put_change_1d"].iloc[-1] == 20
    assert metrics["options_volume_put_change_3d"].iloc[-1] == 70


def test_predictor_formulas_zero_denominator_and_absolute_changes():
    metrics = build_options_metrics(_source([2, 4, 8, 10, 12, 20], [1, 2, 0, 5, 4, 5]))
    pcr = metrics["options_volume_pcr_level_0d"]
    assert pcr.iloc[0] == 2
    assert pd.isna(pcr.iloc[2])
    assert metrics["options_volume_put_change_3d"].iloc[-1] == 12
    assert metrics["options_volume_pcr_change_1d"].iloc[-1] == pytest.approx(1.0)
    callshare = metrics["options_volume_callshare_level_0d"]
    assert callshare.iloc[-1] == pytest.approx(5 / 25)


def test_current_inclusive_midrank_percentile_and_full_window():
    values = pd.Series([1.0, 1.0, 1.0, 2.0])
    result = rolling_pr(values, 3, strict_prior=False, midrank_ties=True)
    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == 50.0
    assert result.iloc[3] == pytest.approx(100 * (2 + .5) / 3)


@pytest.mark.parametrize(
    ("label", "included", "excluded"),
    [
        ("5_20", (5.0, 19.999), (4.999, 20.0)),
        ("20_40", (20.0, 39.999), (19.999, 40.0)),
        ("80_95", (80.0, 95.0), (79.999, 95.001)),
        ("gt95", (95.001, 100.0), (95.0, 100.001)),
    ],
)
def test_exact_percentile_boundaries(label: str, included: tuple[float, ...], excluded: tuple[float, ...]):
    spec = next(signal for signal in _options() if f"_{label}_" in signal.signal_id)
    assert all(_inside(value, spec) for value in included)
    assert all(not _inside(value, spec) for value in excluded)


def test_registry_is_exact_retest_inventory_with_provenance():
    signals = _options()
    assert len(signals) == 19
    assert all(signal.research_status == "RETEST" for signal in signals)
    assert all(signal.activation_ready is False for signal in signals)
    assert all(signal.daily_evaluation_ready is True for signal in signals)
    assert all(signal.research_commit == COMMIT and signal.research_run == RUN for signal in signals)
    assert all(signal.normalization == "rolling_pr_inclusive_midrank" for signal in signals)
    assert all(signal.evidence_scope == "relative" for signal in signals)
    assert not any("callshare" in signal.signal_id for signal in signals)
    assert any(signal.horizon == 2 for signal in signals)
    assert validate_registry() == []


def test_options_economic_ids_use_pcr_ratio_semantics():
    ids = {signal.economic_signal_id for signal in _options()}
    assert {
        "options_volume_pcr_high",
        "options_volume_pcr_low",
        "options_oi_pcr_high",
    } <= ids
    assert not {
        "options_put_buying_high",
        "options_call_buying_dominant",
        "options_put_oi_high",
    } & ids
    assert not any("buying" in signal.plain_definition.lower() for signal in _options())


def test_relative_direction_wording_does_not_change_absolute_wording():
    assert research_direction_label("custom", "bullish", "relative") == "相對偏強"
    assert research_direction_label("custom", "bearish", "relative") == "相對偏弱"
    assert research_direction_label("custom", "bullish", "absolute") == "多"
    assert research_direction_label("custom", "bearish", "absolute") == "空"


def test_parameter_island_flags_are_transcribed_and_presented():
    expected = {
        "options_volume_pcr_w252_5_20_c2",
        "options_volume_pcr_w756_gt95_c10",
    }
    flagged = {signal.signal_id for signal in _options() if signal.research_flag == "PARAMETER_ISLAND"}
    assert flagged == expected
    for signal in _options():
        record = research_evidence_record(signal)
        if signal.signal_id in expected:
            assert record["Research Flag"] == "PARAMETER_ISLAND"
            assert "鄰近參數" in str(record["Research Warning"])
        else:
            assert record["Research Flag"] is None


def test_options_registry_statistics_remain_exactly_equal_to_candidate_csv():
    path = Path(__file__).resolve().parents[1] / "data" / "reference" / "options_canonical_candidates.csv"
    candidates = pd.read_csv(path, keep_default_na=False)
    signals = {signal.signal_id: signal for signal in _options()}
    assert len(signals) == len(candidates) == 19
    for row in candidates.to_dict("records"):
        label = str(row["percentile_bin"])
        signal_id = (
            f"options_{str(row['source']).lower()}_pcr_w{int(row['rolling_window'])}_"
            f"{label.replace('>', 'gt').replace('<', 'lt').replace('-', '_')}_c{int(row['horizon'])}"
        )
        signal = signals[signal_id]
        assert signal.sample_size == int(row["n"])
        assert signal.mean_return == pytest.approx(float(row["mean_return"]))
        assert signal.median_return == pytest.approx(float(row["median_return"]))
        assert signal.win_rate == pytest.approx(float(row["win_rate"]))
        assert signal.relative_mean_return == pytest.approx(float(row["mean_excess_vs_unconditional"]))
        assert signal.global_fdr == pytest.approx(float(row["unconditional_p_value_fdr_global"]))
        assert signal.family_fdr == pytest.approx(float(row["unconditional_p_value_fdr_family"]))
        assert signal.evidence_grade == row["evidence_level"]
        assert signal.rolling_window == int(row["rolling_window"])
        assert signal.horizon == int(row["horizon"])


def test_exact_formal_statistics_are_transcribed():
    signal = next(s for s in _options() if s.signal_id == "options_open_interest_pcr_w252_gt95_c10")
    assert signal.sample_size == 387
    assert signal.mean_return == pytest.approx(.0182083824343956)
    assert signal.median_return == pytest.approx(.0145695364238409)
    assert signal.win_rate == pytest.approx(.7131782945736435)
    assert signal.relative_mean_return == pytest.approx(.0123625389292719)
    assert signal.global_fdr == pytest.approx(.0115382810554975)
    assert signal.family_fdr == pytest.approx(.0007211425659685)
    assert signal.evidence_grade == "A"


def test_options_are_evaluated_and_visible_but_never_vote():
    spec = next(s for s in _options() if s.signal_id == "options_volume_pcr_w60_gt95_c5")
    source = _source(list(range(1, 61)), [10] * 60)
    metrics = build_options_metrics(source)
    sessions = pd.bdate_range(source[DATE_COLUMN].iloc[0], periods=80)
    event = evaluate_signals(metrics, sessions, signals=(spec,), as_of_date=source[DATE_COLUMN].iloc[-1])[0]
    assert event.evaluation_status == "MATCHED"
    assert event.matched
    assert event.activation_ready is False
    assert event.daily_evaluation_ready is True
    assert visible_events((event,)) == (event,)
    assert production_events((event,)) == ()
    assert build_forecast_calendar((event,)).empty
    row = build_evidence_calendar((event,)).iloc[0]
    assert row.visible_bullish_count == 1
    assert row.production_bullish_count == 0
    assert row.experimental_bullish_count == 1
    assert row.net_vote == 0
    counts = visible_evidence_counts((event,))
    assert counts["visible_count"] == 1 and counts["production_count"] == 0
    assert counts["experimental_relative_stronger_count"] == 1
    assert counts["experimental_relative_weaker_count"] == 0
    assert production_evaluation((event,)).result == "VALID_NO_SIGNAL"


def test_missing_options_does_not_invalidate_production_availability():
    sessions = pd.bdate_range("2025-01-02", periods=800)
    events = evaluate_signals({}, sessions, signals=_options(), as_of_date=sessions[-1])
    assert all(event.evaluation_status == "DATA_UNAVAILABLE" for event in events)
    evaluation = production_evaluation(events)
    assert evaluation.result == "VALID_NO_SIGNAL"
    assert evaluation.eligible_signals == 0
    assert evaluation.nonproduction_unavailable_signals == 19


def test_canonical_pipeline_adds_options_without_second_engine():
    source = _source([1, 2, 3], [2, 2, 2])
    metrics = canonical_metrics(pd.DataFrame(), pd.DataFrame(), None, source)
    assert "options_volume_pcr_level_0d" in metrics
    assert "options_open_interest_pcr_change_3d" in metrics


def test_legacy_event_artifact_defaults_new_boolean_fields():
    spec = next(s for s in _options() if s.signal_id == "options_volume_pcr_w60_gt95_c5")
    source = _source(list(range(1, 61)), [10] * 60)
    sessions = pd.bdate_range(source[DATE_COLUMN].iloc[0], periods=80)
    event = evaluate_signals(build_options_metrics(source), sessions, signals=(spec,), as_of_date=source[DATE_COLUMN].iloc[-1])[0]
    legacy = events_frame((event,)).drop(columns=["activation_ready", "daily_evaluation_ready"])
    restored = events_from_frame(legacy)[0]
    assert restored.activation_ready is True
    assert restored.daily_evaluation_ready is True
    blank = events_frame((event,))
    blank[["activation_ready", "daily_evaluation_ready"]] = blank[["activation_ready", "daily_evaluation_ready"]].astype(object)
    blank.loc[0, ["activation_ready", "daily_evaluation_ready"]] = ""
    restored_blank = events_from_frame(blank)[0]
    assert restored_blank.activation_ready is True
    assert restored_blank.daily_evaluation_ready is True


def test_c2_uses_second_trading_session_and_canonical_outcome():
    sessions = pd.bdate_range("2026-10-01", periods=4)
    assert target_session(sessions[0], 2, sessions) == sessions[2]
    opens = pd.Series([100, 100, 100, 100], index=sessions, dtype=float)
    closes = pd.Series([100, 101, 102, 103], index=sessions, dtype=float)
    assert outcome_for_signal(sessions[0], opens, closes, 2) == pytest.approx(.02)


def test_pcr_callshare_alias_is_not_double_counted():
    spec = next(s for s in _options() if s.signal_id == "options_volume_pcr_w60_gt95_c5")
    source = _source(list(range(1, 61)), [10] * 60)
    sessions = pd.bdate_range(source[DATE_COLUMN].iloc[0], periods=80)
    event = evaluate_signals(build_options_metrics(source), sessions, signals=(spec,), as_of_date=source[DATE_COLUMN].iloc[-1])[0]
    duplicate = replace(event, signal_id="alias", normalized_value=99.5)
    visible = visible_events((event, duplicate))
    assert len(visible) == 1


def test_dedup_headline_keeps_all_matched_constituent_variants():
    specs = tuple(
        s for s in _options()
        if s.economic_signal_id == "options_volume_pcr_high"
        and s.horizon == 5
        and s.rolling_window in {60, 120}
    )
    source = _source(list(range(1, 121)), [10] * 120)
    sessions = pd.bdate_range(source[DATE_COLUMN].iloc[0], periods=140)
    events = evaluate_signals(
        build_options_metrics(source), sessions, signals=specs,
        as_of_date=source[DATE_COLUMN].iloc[-1],
    )
    assert len(events) == 2 and all(event.matched for event in events)
    assert len(visible_events(events)) == 1
    view = build_forecast_presentation(
        events, events, pd.DataFrame(), events[0].signal_date,
    )["post_close_forward_signals"]
    group = view["signal_groups"][0]
    assert len(group["horizons"]) == 1
    assert {row["rolling_window"] for row in group["constituent_variants"]} == {60, 120}
    assert all(row["signal_identity"] == "選擇權成交量 PCR" for row in group["constituent_variants"])


def test_options_audit_shows_relative_returns_scope_and_human_identity():
    spec = next(s for s in _options() if s.signal_id == "options_volume_pcr_w252_5_20_c10")
    source = _source([0.1] * 25 + [1] * 226 + [0.5], [1] * 252)
    sessions = pd.bdate_range(source[DATE_COLUMN].iloc[0], periods=280)
    event = evaluate_signals(
        build_options_metrics(source), sessions, signals=(spec,),
        as_of_date=source[DATE_COLUMN].iloc[-1],
    )[0]
    assert event.matched
    row = event_audit_record(event)
    assert row["direction_label"] == "相對偏弱"
    assert row["signal_identity"] == "選擇權成交量 PCR"
    assert row["evidence_scope"] == "relative"
    assert row["historical_mean_return"] == "+0.113%"
    assert row["relative_mean_return"] == "-0.472 pp"


def test_email_labels_options_as_experimental_with_evidence_details():
    spec = next(s for s in _options() if s.signal_id == "options_volume_pcr_w60_gt95_c5")
    source = _source(list(range(1, 61)), [10] * 60)
    sessions = pd.bdate_range(source[DATE_COLUMN].iloc[0], periods=80)
    event = evaluate_signals(
        build_options_metrics(source), sessions, signals=(spec,),
        as_of_date=source[DATE_COLUMN].iloc[-1],
    )[0]
    subject, plain, _html = build_vnext_report(
        (event,), build_forecast_calendar((event,)), event.signal_date,
        "run", "commit", "SUCCESS",
    )
    assert "正式 0多 0空" in subject
    assert "Research / Experimental Details（不計正式票數）" in plain
    assert event.plain_definition in plain
    assert "相對偏強" in plain
    assert "Evidence Scope relative" in plain
    assert "Raw" in plain and "Family FDR" in plain
