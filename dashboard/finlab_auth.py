"""Non-interactive FinLab authentication for Colab, CI and server runtimes."""
from __future__ import annotations

import os
from typing import Mapping


NEW_CREDENTIAL_NAMES = (
    "FINLAB_REFRESH_TOKEN",
    "FINLAB_SESSION_ID",
    "FINLAB_API_KEY",
)
LEGACY_CREDENTIAL_NAME = "FINLAB_API_TOKEN"


class FinLabAuthError(RuntimeError):
    """Base application-level authentication error without credential content."""


class FinLabAuthUnavailable(FinLabAuthError):
    """No complete non-interactive credential set is available."""


class FinLabAuthFailed(FinLabAuthError):
    """A supplied non-interactive credential set could not authenticate."""


def credential_mode(environment: Mapping[str, str] | None = None) -> str:
    """Return NEW_HEADLESS, LEGACY_API_TOKEN, INCOMPLETE_NEW or UNAVAILABLE."""
    values = os.environ if environment is None else environment
    present = [bool(str(values.get(name, "")).strip()) for name in NEW_CREDENTIAL_NAMES]
    if all(present):
        return "NEW_HEADLESS"
    if any(present):
        return "INCOMPLETE_NEW"
    if str(values.get(LEGACY_CREDENTIAL_NAME, "")).strip():
        return "LEGACY_API_TOKEN"
    return "UNAVAILABLE"


def headless_credentials_available(environment: Mapping[str, str] | None = None) -> bool:
    return credential_mode(environment) in {"NEW_HEADLESS", "LEGACY_API_TOKEN"}


def authenticate_finlab_headless(*, legacy_token: str | None = None) -> str:
    """Authenticate without permitting FinLab's browser-login fallback.

    FinLab 2.2 reads the complete three-variable session directly through
    ``finlab.auth.get_id_token``.  Calling ``finlab.login()`` is deliberately
    avoided for this branch because a failed refresh would start browser auth.
    The explicit token argument remains as a verified, deprecated compatibility
    path and never gets mapped to ``FINLAB_API_KEY``.
    """
    mode = credential_mode()
    if mode == "UNAVAILABLE" and legacy_token:
        mode = "LEGACY_API_TOKEN"

    if mode == "INCOMPLETE_NEW":
        raise FinLabAuthUnavailable(
            "AUTH_UNAVAILABLE：FinLab 新版 headless credentials 不完整；需要同時設定 "
            "FINLAB_REFRESH_TOKEN、FINLAB_SESSION_ID、FINLAB_API_KEY。"
        )
    if mode == "UNAVAILABLE":
        raise FinLabAuthUnavailable(
            "AUTH_UNAVAILABLE：未設定 FinLab headless credentials；不會啟動瀏覽器登入。"
        )

    try:
        import finlab

        if mode == "NEW_HEADLESS":
            from finlab import auth

            if not auth.get_id_token():
                raise FinLabAuthFailed(
                    "AUTH_FAILED：FinLab headless session 無法換取有效 token；不會啟動瀏覽器登入。"
                )
            return mode

        token = legacy_token or os.environ.get(LEGACY_CREDENTIAL_NAME, "")
        if not token:
            raise FinLabAuthUnavailable(
                "AUTH_UNAVAILABLE：找不到舊版 FINLAB_API_TOKEN；不會啟動瀏覽器登入。"
            )
        finlab.login(token)
        return mode
    except FinLabAuthError:
        raise
    except Exception as exc:
        raise FinLabAuthFailed(
            f"AUTH_FAILED：FinLab headless authentication 失敗（{type(exc).__name__}）；"
            "不會啟動瀏覽器登入。"
        ) from exc
