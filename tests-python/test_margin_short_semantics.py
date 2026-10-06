from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.research_registry import CANONICAL_SIGNALS, validate_registry
from dashboard.signal_engine import evaluate_signals, production_events
from dashboard.signal_presentation import event_audit_record, research_evidence_record


MARGIN_COMMIT = "4a6fb82795342a55d174dbfc9beb1ee80a93bb4c"
MARGIN_RUN = "20260913_102806_4a6fb82_margin_buy_sell_prior_return_joint"
SHORT_COMMIT = "c3a89fb2f998ad69f6ea5e7ad362ea7fe2dfa696"
SHORT_RUN = "20260913_112534_c3a89fb_short_flow_prior_return_symmetry"


def _margin_short():
    return tuple(signal for signal in CANONICAL_SIGNALS if signal.source == "margin_short")


def _spec(signal_id: str):
    return next(signal for signal in _margin_short() if signal.signal_id == signal_id)


def test_provenance_is_exact():
    margin = tuple(signal for signal in _margin_short() if signal.subject.startswith("margin_"))
    short = tuple(signal for signal in _margin_short() if signal.subject.startswith("short_"))
    assert all(signal.research_commit == MARGIN_COMMIT and signal.research_run == MARGIN_RUN for signal in margin)
    assert all(signal.research_commit == SHORT_COMMIT and signal.research_run == SHORT_RUN for signal in short)


def test_all_interactions_are_conditional_and_activation_not_ready():
    assert len(_margin_short()) == 14
    assert all(signal.direction_mode == "conditional" for signal in _margin_short())
    assert all(signal.activation_ready is False for signal in _margin_short())
    assert all(signal.prior_condition.startswith("continuous Prior5D") for signal in _margin_short())


@pytest.mark.parametrize(("subject", "sign"), [
    ("margin_buy", "positive"),
    ("margin_sell", "negative"),
    ("short_cover", "negative"),
    ("short_repayment", "negative"),
    ("short_sell", "positive"),
])
def test_interaction_signs(subject: str, sign: str):
    rows = tuple(signal for signal in _margin_short() if signal.subject == subject)
    assert rows and all(signal.interaction_sign == sign for signal in rows)
    if subject == "short_sell":
        assert all(signal.research_status == "RETEST" for signal in rows)


def test_performance_and_interaction_scopes_are_distinct():
    for signal in _margin_short():
        assert signal.performance_scope == "PR95-100 flow-group descriptive statistics"
        assert signal.evidence_test_scope == "FlowHigh × continuous Prior5D interaction"
        assert signal.performance_scope != signal.evidence_test_scope


def test_exact_formula_and_normalization_metadata():
    for signal in _margin_short():
        assert signal.normalization == "rolling_pr_inclusive"
        assert signal.threshold_lower == 95
        assert signal.threshold_upper == 100
        assert signal.threshold_lower_inclusive and signal.threshold_upper_inclusive
        assert "sum(" in signal.formula and "TAIEX + OTC" in signal.formula
    assert all(signal.flow_ratio_type == "amount_ratio" for signal in _margin_short() if signal.subject.startswith("margin_"))
    assert all(signal.flow_ratio_type == "volume_ratio" for signal in _margin_short() if signal.subject.startswith("short_"))
    assert "margin_balance:融資券總買進" in _spec("margin_buy_prior5_c20").formula
    assert "market_transaction_info:成交金額[TAIEX + OTC]" in _spec("margin_buy_prior5_c20").formula
    assert "lots × 1000" in _spec("short_cover_prior5_k3_c20").formula
    assert "market_transaction_info:成交股數[TAIEX + OTC]" in _spec("short_cover_prior5_k3_c20").formula


def test_extreme_validity_does_not_inherit_interaction_grade():
    assert _spec("margin_sell_prior5_c3").extreme_validity == "PARTIAL"
    assert _spec("margin_sell_prior5_c3").extreme_evidence_grade == "C"
    assert all(
        signal.extreme_validity == "NO"
        for signal in _margin_short()
        if signal.signal_id != "margin_sell_prior5_c3"
    )
    assert all(
        signal.extreme_evidence_grade == "No Evidence"
        for signal in _margin_short()
        if signal.signal_id != "margin_sell_prior5_c3"
    )
    assert all(signal.monotonicity == "non-monotonic" for signal in _margin_short())


def test_annual_robustness_metadata():
    assert _spec("short_cover_prior5_c10").annual_robustness == "PARTIAL"
    assert _spec("short_cover_prior5_k3_c20").annual_robustness == "PARTIAL"
    assert _spec("short_repayment_prior5_k10_w126_c20").annual_robustness == "FAILED_DIRECTIONAL_CONSISTENCY"
    assert _spec("margin_buy_prior5_c20").annual_robustness == "UNKNOWN"


EXPECTED_ADDITIONS = {
    "margin_buy_prior5_c20": (301, .01192, .00821, .56146, .00010, .000475706, .00071356),
    "margin_sell_prior5_k5_c10": (153, .00909, .01026, .64706, .00308, .00670188, .00570337),
    "margin_sell_prior5_k5_c20": (153, .01644, .01398, .64706, .00380, .00489066, .0042947),
    "short_repayment_prior5_c5": (260, .00446, .00274, .54615, .00158, .0380226, .0268437),
    "short_cover_prior5_k3_c20": (273, .01016, .01896, .64103, -.00362, .00393724, .00231071),
    "short_repayment_prior5_k10_w126_c20": (375, .01170, .01418, .61333, -.00205, .00393724, .00262483),
}


@pytest.mark.parametrize(("signal_id", "expected"), EXPECTED_ADDITIONS.items())
def test_new_representative_rows_match_master(signal_id: str, expected: tuple[float, ...]):
    signal = _spec(signal_id)
    actual = (
        signal.sample_size, signal.mean_return, signal.median_return, signal.win_rate,
        signal.relative_mean_return, signal.global_fdr, signal.family_fdr,
    )
    assert actual == pytest.approx(expected)
    assert signal.research_status == "RETAINED"
    assert "representative" in signal.representative_scope


def test_interactions_never_enter_production_even_if_malformed_as_matched():
    sessions = pd.bdate_range("2025-01-01", periods=800)
    events = []
    for signal in _margin_short():
        event = evaluate_signals(
            {signal.metric: pd.Series(range(800), index=sessions, dtype=float)},
            sessions,
            signals=(signal,),
            as_of_date=sessions[-1],
        )[0]
        assert event.evaluation_status == "UNVALIDATED_ACTIVATION_RULE"
        events.append(replace(
            event,
            matched=True,
            evaluation_status="MATCHED",
            raw_value=1.0,
            normalized_value=100.0,
            source_data_date=event.signal_date,
            availability_status="KNOWN",
        ))
    malformed = tuple(events)
    assert production_events(malformed) == ()
    assert build_forecast_calendar(malformed).empty


def test_research_presentation_does_not_show_fixed_direction():
    signal = _spec("margin_sell_prior5_c3")
    record = research_evidence_record(signal)
    assert record["Direction"] == "條件式"
    assert record["Direction Mode"] == "conditional"
    assert record["Interaction Sign"] == "negative"
    assert record["Performance Scope"] != record["Interaction Evidence Scope"]

    sessions = pd.bdate_range("2025-01-01", periods=300)
    event = evaluate_signals(
        {signal.metric: pd.Series(range(300), index=sessions, dtype=float)},
        sessions,
        signals=(signal,),
        as_of_date=sessions[-1],
    )[0]
    assert event_audit_record(event)["direction"] == "條件式"


def test_other_research_categories_are_unchanged_in_size_and_key_contracts():
    counts = {
        source: sum(signal.source == source for signal in CANONICAL_SIGNALS)
        for source in {signal.source for signal in CANONICAL_SIGNALS}
    }
    assert counts == {"market_breadth": 5, "futures": 8, "spot_flow": 21, "margin_short": 14}
    assert next(s for s in CANONICAL_SIGNALS if s.signal_id == "futures_foreign_change_pr0_20_c1").rolling_window == 120
    assert next(s for s in CANONICAL_SIGNALS if s.signal_id == "spot_otc_total_sell5_pr5_20_c5").threshold_upper_inclusive
    assert next(s for s in CANONICAL_SIGNALS if s.signal_id == "breadth_up_ratio_1d_pr60_ge95_c1").threshold_lower == 95


def test_registry_validation_still_passes():
    assert validate_registry() == []
