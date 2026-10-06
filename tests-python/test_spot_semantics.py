from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from dashboard.daily_email import _spot_plain_lines
from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.google_sheet_service import SPOT_HEADERS, sync_spot_signals
from dashboard.research_evidence import build_spot_evidence
from dashboard.research_registry import CANONICAL_SIGNALS
from dashboard.signal_engine import SignalEvent, _inside, is_production_vote
from dashboard.spot_flow_service import (
    LISTED_DEALER_HEDGE,
    LISTED_DEALER_SELF,
    LISTED_FOREIGN,
    LISTED_TRUST,
    OTC_DEALER_HEDGE,
    OTC_DEALER_SELF,
    OTC_FOREIGN,
    OTC_TRUST,
    build_spot_flow_report,
    rolling_pr_inclusive,
    rolling_z_inclusive,
)


SPOT_COMMIT = "353fa7037505c8022db5a5b01601d0cdec370e6f"
SPOT_RUN = "20260913_174226_353fa703"


def _tables(periods: int = 800):
    dates = pd.bdate_range("2023-01-02", periods=periods)
    turnover = pd.DataFrame({"TAIEX": 1_000.0, "OTC": 500.0}, index=dates)
    buy = pd.DataFrame({
        LISTED_FOREIGN: 10.0, LISTED_TRUST: 20.0,
        LISTED_DEALER_SELF: 30.0, LISTED_DEALER_HEDGE: 40.0,
        OTC_FOREIGN: 1.0, OTC_TRUST: 2.0,
        OTC_DEALER_SELF: 3.0, OTC_DEALER_HEDGE: 4.0,
        # Deliberately wrong convenience aggregate: production must ignore it.
        "上櫃三大法人合計*": 9_999.0,
    }, index=dates)
    sell = pd.DataFrame({
        LISTED_FOREIGN: 5.0, LISTED_TRUST: 6.0,
        LISTED_DEALER_SELF: 7.0, LISTED_DEALER_HEDGE: 8.0,
        OTC_FOREIGN: .5, OTC_TRUST: .6,
        OTC_DEALER_SELF: .7, OTC_DEALER_HEDGE: .8,
        "上櫃三大法人合計*": 8_888.0,
    }, index=dates)
    # Official net intentionally differs from buy-sell.
    net = pd.DataFrame({
        LISTED_FOREIGN: 101.0, LISTED_TRUST: 102.0,
        LISTED_DEALER_SELF: 103.0, LISTED_DEALER_HEDGE: 104.0,
        OTC_FOREIGN: 11.0, OTC_TRUST: 12.0,
        OTC_DEALER_SELF: 13.0, OTC_DEALER_HEDGE: 14.0,
        "上櫃三大法人合計*": 7_777.0,
    }, index=dates)
    return buy, sell, net, turnover


def _spot_registry():
    return tuple(signal for signal in CANONICAL_SIGNALS if signal.source == "spot_flow")


def _spec(signal_id: str):
    return next(signal for signal in _spot_registry() if signal.signal_id == signal_id)


def _event(spec, *, matched=True, status="MATCHED"):
    return SignalEvent(
        signal_date="2026-10-02", signal_id=spec.signal_id,
        economic_signal_id=spec.economic_signal_id, source=spec.source,
        subject=spec.subject, direction=spec.direction, horizon=spec.horizon,
        target_date="2026-10-05", matched=matched, evaluation_status=status,
        research_status=spec.research_status, evidence_grade=spec.evidence_grade,
        raw_value=.1, normalized_value=10.0, threshold="test",
        historical_mean_return=spec.mean_return, historical_median_return=spec.median_return,
        historical_win_rate=spec.win_rate, relative_mean_return=spec.relative_mean_return,
        sample_size=spec.sample_size, global_fdr=spec.global_fdr, family_fdr=spec.family_fdr,
        plain_definition=spec.plain_definition, market_mechanism=spec.market_mechanism,
        risks=spec.risks, research_commit=spec.research_commit, research_run=spec.research_run,
        calculation_timestamp="2026-10-02T20:00:00+08:00", source_data_date="2026-10-02",
        metric=spec.metric, normalization=spec.normalization, evidence_scope=spec.evidence_scope,
    )


def test_predictor_reconstruction_official_net_and_denominators():
    report = build_spot_flow_report(*_tables())
    metrics = report.canonical_metrics
    assert metrics is not None
    # OTC total = foreign + trust + (dealer self + hedge), not aggregate column.
    assert metrics["otc_total_institutional_buy_1d"].iloc[-1] == pytest.approx(10 / 500)
    assert metrics["otc_dealer_buy_1d"].iloc[-1] == pytest.approx(7 / 500)
    # Official net dealer = self + hedge; total = foreign + trust + dealer.
    assert metrics["listed_dealer_net_1d"].iloc[-1] == pytest.approx(207 / 1_000)
    assert metrics["listed_total_institutional_net_1d"].iloc[-1] == pytest.approx(410 / 1_000)
    assert metrics["otc_total_institutional_net_1d"].iloc[-1] == pytest.approx(50 / 500)
    # Combined denominator is listed + OTC.
    assert metrics["combined_foreign_buy_1d"].iloc[-1] == pytest.approx(11 / 1_500)


def test_rolling_predictor_is_sum_over_sum_not_mean_of_daily_ratios():
    buy, sell, net, turnover = _tables(20)
    turnover.loc[turnover.index[-2], "OTC"] = 100.0
    turnover.loc[turnover.index[-1], "OTC"] = 900.0
    buy.loc[buy.index[-2], OTC_FOREIGN] = 90.0
    buy.loc[buy.index[-1], OTC_FOREIGN] = 10.0
    report = build_spot_flow_report(buy, sell, net, turnover)
    metric = report.canonical_metrics["otc_foreign_buy_5d"]
    amount = buy[OTC_FOREIGN].iloc[-5:].sum()
    denominator = turnover["OTC"].iloc[-5:].sum()
    assert metric.iloc[-1] == pytest.approx(amount / denominator)


def test_spot_normalization_is_current_inclusive_and_z_uses_ddof_zero():
    values = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert rolling_pr_inclusive(values, 4).iloc[-1] == 100.0
    expected_z = (4.0 - values.mean()) / values.std(ddof=0)
    assert rolling_z_inclusive(values, 4).iloc[-1] == pytest.approx(expected_z)


@pytest.mark.parametrize("signal_id,value,expected", [
    ("spot_otc_total_sell5_pr5_20_c5", 5, False),
    ("spot_otc_total_sell5_pr5_20_c5", 20, True),
    ("spot_otc_total_sell5_pr60_80_c10", 60, False),
    ("spot_otc_total_sell5_pr60_80_c10", 80, True),
    ("spot_listed_dealer_net10_pr95_100_c10", 95, False),
    ("spot_listed_dealer_net10_pr95_100_c10", 100, True),
    ("spot_otc_dealer_sell10_z_m25_m15_c10", -2.5, True),
    ("spot_otc_dealer_sell10_z_m25_m15_c10", -1.5, False),
    ("spot_otc_foreign_buy5_z05_15_c10", .5, True),
    ("spot_otc_foreign_buy5_z05_15_c10", 1.5, False),
    ("spot_combined_foreign_sell10_z_ge25_c10", 2.5, True),
])
def test_canonical_bin_boundaries(signal_id, value, expected):
    assert _inside(value, _spec(signal_id)) is expected


def test_registry_has_exact_master_status_rows_and_provenance():
    expected = {
        "RETAINED": {
            "spot_otc_total_sell5_pr5_20_c5", "spot_otc_total_sell5_pr5_20_c10",
            "spot_otc_total_sell5_pr5_20_c20", "spot_otc_dealer_sell10_z_m25_m15_c5",
            "spot_otc_dealer_sell10_z_m25_m15_c10", "spot_otc_dealer_sell10_z_m25_m15_c20",
            "spot_listed_dealer_net10_pr95_100_c10",
        },
        "RETEST": {
            "spot_otc_total_sell5_pr5_20_c3", "spot_otc_dealer_sell10_z_m25_m15_c3",
            "spot_combined_foreign_sell10_z_ge25_c5", "spot_combined_foreign_sell10_z_ge25_c10",
            "spot_combined_foreign_sell10_z_ge25_c20", "spot_listed_dealer_net10_pr95_100_c20",
            "spot_otc_total_sell5_pr60_80_c10", "spot_otc_foreign_buy5_z05_15_c10",
            "spot_otc_foreign_buy5_z05_15_c20",
        },
        "REJECTED": {
            "spot_listed_dealer_net10_pr95_100_c5", "spot_listed_foreign_net5_pr95_100_c5",
            "spot_listed_foreign_net5_pr95_100_c10", "spot_listed_foreign_net5_pr95_100_c20",
            "spot_otc_foreign_buy5_z05_15_c5",
        },
    }
    assert len(_spot_registry()) == 21
    for status, ids in expected.items():
        assert {signal.signal_id for signal in _spot_registry() if signal.research_status == status} == ids
    assert all(signal.research_commit == SPOT_COMMIT and signal.research_run == SPOT_RUN for signal in _spot_registry())


def test_master_statistics_relative_scope_and_conservative_metadata():
    retained = _spec("spot_otc_total_sell5_pr5_20_c5")
    assert (retained.sample_size, retained.mean_return, retained.median_return, retained.win_rate) == pytest.approx((203, .013111, .010668, .714286))
    listed_foreign = _spec("spot_listed_foreign_net5_pr95_100_c10")
    assert listed_foreign.research_status == "REJECTED" and listed_foreign.evidence_grade == "C"
    relative = _spec("spot_otc_foreign_buy5_z05_15_c10")
    assert relative.evidence_scope == "relative"
    assert relative.mean_return == pytest.approx(.0001)
    assert relative.relative_mean_return == pytest.approx(-.0073)
    assert all(signal.monotonicity == "UNKNOWN" for signal in _spot_registry())
    assert {signal.extreme_validity for signal in _spot_registry()} <= {"UNKNOWN", "PARTIAL", "NO"}
    assert all(signal.market in {"listed", "otc", "combined"} for signal in _spot_registry())
    assert all("市場機制尚未證實" in signal.market_mechanism for signal in _spot_registry())


def test_all_master_statistics_are_locked():
    # N, mean, median, win, relative, global FDR, family FDR
    expected = {
        "spot_otc_total_sell5_pr5_20_c3": (203, .0060, .0057, .6305, .0043, .0124204, .0207007),
        "spot_otc_total_sell5_pr5_20_c5": (203, .013111, .010668, .714286, .010030, .000491177, .000245588),
        "spot_otc_total_sell5_pr5_20_c10": (203, .02427, .0213, .7931, .0169, .000529845, .000176615),
        "spot_otc_total_sell5_pr5_20_c20": (203, .0408, .0401, .8374, .0245, .00650161, .0024381),
        "spot_otc_dealer_sell10_z_m25_m15_c3": (94, .0079, .0091, .6596, .0061, .03135, .036575),
        "spot_otc_dealer_sell10_z_m25_m15_c5": (94, .015215, .011435, .744681, .011684, .0128201, .00925895),
        "spot_otc_dealer_sell10_z_m25_m15_c10": (94, .0331, .0387, .8085, .0254, .00108734, .000402087),
        "spot_otc_dealer_sell10_z_m25_m15_c20": (94, .0717, .0665, .9894, .0557, .00000308612, .000000514353),
        "spot_combined_foreign_sell10_z_ge25_c5": (54, .0172, .0132, .7222, .0153, .0128201, .00925895),
        "spot_combined_foreign_sell10_z_ge25_c10": (54, .0448, .0412, .8889, .0409, .0000131204, .00000437347),
        "spot_combined_foreign_sell10_z_ge25_c20": (54, .0764, .0629, .9074, .0683, .00108734, .000422854),
        "spot_listed_dealer_net10_pr95_100_c5": (109, .0111, .0079, .6697, .0073, .202107, .183237),
        "spot_listed_dealer_net10_pr95_100_c10": (109, .0309, .0279, .8349, .0230, .0128201, .00523784),
        "spot_listed_dealer_net10_pr95_100_c20": (109, .0460, .0372, .8257, .0284, .0457146, .0228573),
        "spot_otc_total_sell5_pr60_80_c10": (285, -.00996, -.00956, .4175, -.0191, .0013628, .000755033),
        "spot_otc_foreign_buy5_z05_15_c5": (977, .0010, .0014, .5261, -.0019, .243486, .186672),
        "spot_otc_foreign_buy5_z05_15_c10": (977, .0001, .0009, .5107, -.0073, .00650161, .00233197),
        "spot_otc_foreign_buy5_z05_15_c20": (972, .0010, .0038, .5319, -.0131, .000927084, .000386285),
        "spot_listed_foreign_net5_pr95_100_c5": (165, .0054, .0042, .5879, .0022, .364011, .273545),
        "spot_listed_foreign_net5_pr95_100_c10": (165, .0108, .0061, .6727, .0038, .328633, .237346),
        "spot_listed_foreign_net5_pr95_100_c20": (165, .0185, .0167, .7212, .0037, .568627, .458061),
    }
    assert set(expected) == {signal.signal_id for signal in _spot_registry()}
    for signal_id, values in expected.items():
        signal = _spec(signal_id)
        actual = (
            signal.sample_size, signal.mean_return, signal.median_return,
            signal.win_rate, signal.relative_mean_return, signal.global_fdr, signal.family_fdr,
        )
        assert actual == pytest.approx(values)


def test_only_retained_absolute_matched_events_vote():
    retained = _event(_spec("spot_otc_total_sell5_pr5_20_c5"))
    retest = _event(_spec("spot_otc_total_sell5_pr5_20_c3"))
    rejected = _event(_spec("spot_listed_foreign_net5_pr95_100_c5"))
    relative = _event(_spec("spot_otc_foreign_buy5_z05_15_c10"))
    assert is_production_vote(retained)
    assert not is_production_vote(retest)
    assert not is_production_vote(rejected)
    assert not is_production_vote(relative)
    calendar = build_forecast_calendar((retained, retest, rejected, relative))
    assert calendar.iloc[0]["bullish_count"] == 1
    assert calendar.iloc[0]["bearish_count"] == 0


def test_economic_signal_id_dedup_remains_one_vote():
    event = _event(_spec("spot_otc_total_sell5_pr5_20_c5"))
    duplicate = replace(event, signal_id="custom_robustness_variant")
    calendar = build_forecast_calendar((event, duplicate))
    assert calendar.iloc[0]["bullish_count"] == 1
    assert calendar.iloc[0]["active_signals"] == event.signal_id


def test_legacy_email_and_sheet_project_canonical_stats_only():
    report = build_spot_flow_report(*_tables())
    plain = "\n".join(_spot_plain_lines(report))
    assert "DEPRECATED legacy view" in plain
    assert "1.3111%" in plain
    assert "2.77%" not in plain
    daily = build_spot_evidence(report)
    assert not any(item.matched for item in daily if "RETEST" in item.status or "REJECTED" in item.status)

    class FakeSheet:
        def __init__(self):
            self.values = [SPOT_HEADERS]
        def get_all_values(self):
            return self.values
        def clear(self):
            self.values = []
        def update(self, matrix, *_args, **_kwargs):
            self.values = matrix
        def freeze(self, **_kwargs):
            return None

    sheet = FakeSheet()
    sync_spot_signals(sheet, report, pd.Timestamp("2026-10-02", tz="Asia/Taipei").to_pydatetime(), "test")
    records = [dict(zip(SPOT_HEADERS, row)) for row in sheet.values[1:]]
    row = next(item for item in records if item["trigger_id"] == "spot_otc_total_sell5_pr5_20_c5")
    assert row["evidence_statement"].startswith("N 203；平均 1.3111%")
    assert row["research_status"] == "RETAINED"
    assert row["threshold_label"].endswith("(5, 20]")
