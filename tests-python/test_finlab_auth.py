from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from dashboard.dashboard_source import (
    calculate_live_source,
    load_current_run_artifacts,
    select_dashboard_source,
    write_current_run_artifacts,
)
from dashboard.finlab_auth import (
    FinLabAuthFailed,
    FinLabAuthUnavailable,
    authenticate_finlab_headless,
    credential_mode,
)


NOW = pd.Timestamp("2026-09-30 20:30", tz="Asia/Taipei")
FINLAB_NAMES = (
    "FINLAB_REFRESH_TOKEN", "FINLAB_SESSION_ID", "FINLAB_API_KEY", "FINLAB_API_TOKEN",
)


def _clear_credentials(monkeypatch):
    for name in FINLAB_NAMES:
        monkeypatch.delenv(name, raising=False)


def _write_empty_current(folder: Path) -> None:
    calendar = pd.DataFrame(columns=["target_date", "bullish_count", "bearish_count", "net_vote", "active_signals"])
    write_current_run_artifacts(
        folder, (), calendar, run_id="no-signal", git_commit="abc",
        calculated_at=NOW.isoformat(), actual_data_date="2026-09-30",
    )


def test_valid_current_run_never_authenticates_or_downloads(tmp_path: Path):
    _write_empty_current(tmp_path)
    with patch("dashboard.dashboard_source.authenticate_finlab_headless") as authenticate, \
         patch("finlab.login") as login, patch("finlab.data.get") as data_get:
        source = select_dashboard_source(tmp_path, now=NOW, live_available=True)
    assert source.source_type == "CURRENT_RUN"
    authenticate.assert_not_called()
    login.assert_not_called()
    data_get.assert_not_called()


def test_complete_new_headless_credentials_use_token_exchange_not_browser(monkeypatch):
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("FINLAB_REFRESH_TOKEN", "refresh-secret")
    monkeypatch.setenv("FINLAB_SESSION_ID", "session-secret")
    monkeypatch.setenv("FINLAB_API_KEY", "api-key-secret")
    with patch("finlab.auth.get_id_token", return_value="id-token") as get_id_token, \
         patch("finlab.login") as browser_capable_login:
        assert authenticate_finlab_headless() == "NEW_HEADLESS"
    get_id_token.assert_called_once_with()
    browser_capable_login.assert_not_called()


def test_no_credentials_is_auth_unavailable_without_browser_login(monkeypatch):
    _clear_credentials(monkeypatch)
    with patch("finlab.login") as browser_capable_login, \
         pytest.raises(FinLabAuthUnavailable, match="AUTH_UNAVAILABLE"):
        authenticate_finlab_headless()
    browser_capable_login.assert_not_called()


def test_invalid_new_credentials_are_auth_failed_without_browser_login(monkeypatch):
    _clear_credentials(monkeypatch)
    for name in FINLAB_NAMES[:3]:
        monkeypatch.setenv(name, f"invalid-{name}")
    with patch("finlab.auth.get_id_token", return_value=None), \
         patch("finlab.login") as browser_capable_login, \
         pytest.raises(FinLabAuthFailed, match="AUTH_FAILED"):
        authenticate_finlab_headless()
    browser_capable_login.assert_not_called()


def test_valid_no_signal_current_run_does_not_reauthenticate(tmp_path: Path):
    _write_empty_current(tmp_path)
    with patch("dashboard.dashboard_source.authenticate_finlab_headless") as authenticate:
        source = select_dashboard_source(tmp_path, now=NOW, live_available=True)
    assert source.evaluation_result == "VALID_NO_SIGNAL"
    assert source.calendar.empty
    authenticate.assert_not_called()


def test_manual_refresh_without_credentials_is_auth_unavailable(monkeypatch, tmp_path: Path):
    _clear_credentials(monkeypatch)
    with patch("finlab.login") as browser_capable_login, \
         pytest.raises(FinLabAuthUnavailable, match="AUTH_UNAVAILABLE"):
        calculate_live_source(tmp_path, NOW)
    browser_capable_login.assert_not_called()


def test_snapshot_fallback_reports_not_current_market_data(monkeypatch, tmp_path: Path):
    _clear_credentials(monkeypatch)
    source = select_dashboard_source(tmp_path, now=NOW)
    assert source.source_type == "RESEARCH_SNAPSHOT"
    assert source.status == "AUTH_UNAVAILABLE"
    assert "不是最新市場資料" in source.warning
    assert "AUTH_UNAVAILABLE" in source.warning


def test_credentials_never_enter_errors_or_current_run_manifest(monkeypatch, tmp_path: Path):
    secrets = {
        "FINLAB_REFRESH_TOKEN": "refresh-do-not-leak",
        "FINLAB_SESSION_ID": "session-do-not-leak",
        "FINLAB_API_KEY": "api-key-do-not-leak",
    }
    _clear_credentials(monkeypatch)
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    with patch("finlab.auth.get_id_token", return_value=None), pytest.raises(FinLabAuthFailed) as captured:
        authenticate_finlab_headless()
    error_text = str(captured.value)
    assert all(value not in error_text for value in secrets.values())

    _write_empty_current(tmp_path)
    manifest_text = (tmp_path / "run_manifest.json").read_text(encoding="utf-8")
    assert all(value not in manifest_text for value in secrets.values())
    assert load_current_run_artifacts(tmp_path, NOW).status == "SUCCESS"


def test_legacy_token_is_supported_without_mapping_to_new_api_key(monkeypatch):
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("FINLAB_API_TOKEN", "legacy-secret")
    with patch("finlab.login") as login:
        assert authenticate_finlab_headless() == "LEGACY_API_TOKEN"
    login.assert_called_once_with("legacy-secret")
    assert "FINLAB_API_KEY" not in os.environ
    assert credential_mode() == "LEGACY_API_TOKEN"
