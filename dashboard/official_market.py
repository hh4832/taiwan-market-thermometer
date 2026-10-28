"""Official-market evidence used only after a FinLab freshness failure."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import json
from typing import Any, Callable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
TWSE_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date={date}"
TAIFEX_URL = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"


class EvidenceStatus(str, Enum):
    TRADED = "TRADED"
    NO_RECORD = "NO_RECORD"
    API_FAILED = "API_FAILED"


@dataclass(frozen=True)
class OfficialEvidence:
    market: str
    session: str
    requested_date: str
    status: EvidenceStatus
    observed_dates: tuple[str, ...]
    source_url: str
    fetched_at: str
    detail: str = ""

    def as_json(self) -> str:
        payload = asdict(self)
        payload["status"] = self.status.value
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _default_get(url: str, timeout: float) -> Any:
    request = Request(url, headers={"User-Agent": "taiwan-market-thermometer/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def _roc_date(value: str) -> str:
    year, month, day = value.strip().split("/")
    return f"{int(year) + 1911:04d}-{int(month):02d}-{int(day):02d}"


def fetch_twse_evidence(
    target_date: str,
    *,
    get_json: Callable[[str, float], Any] = _default_get,
    timeout: float = 15,
    now: datetime | None = None,
) -> OfficialEvidence:
    url = TWSE_URL.format(date=target_date.replace("-", ""))
    fetched = (now or datetime.now(TAIPEI)).astimezone(TAIPEI).isoformat()
    try:
        payload = get_json(url, timeout)
        dates = tuple(sorted({_roc_date(str(row[0])) for row in payload.get("data", []) if row}))
        status = EvidenceStatus.TRADED if target_date in dates else EvidenceStatus.NO_RECORD
        detail = f"stat={payload.get('stat', '')}; rows={len(payload.get('data', []))}"
    except Exception as exc:
        dates, status = (), EvidenceStatus.API_FAILED
        detail = f"{type(exc).__name__}: {exc}"[:500]
    return OfficialEvidence("TWSE", "DAY", target_date, status, dates, url, fetched, detail)


def fetch_taifex_evidence(
    target_date: str,
    *,
    contract: str = "TX",
    session: str = "盤後",
    get_json: Callable[[str, float], Any] = _default_get,
    timeout: float = 15,
    now: datetime | None = None,
) -> OfficialEvidence:
    fetched = (now or datetime.now(TAIPEI)).astimezone(TAIPEI).isoformat()
    try:
        payload = get_json(TAIFEX_URL, timeout)
        rows = [
            row for row in payload
            if str(row.get("Contract", "")).upper() == contract.upper()
            and str(row.get("TradingSession", "")) == session
        ]
        dates = tuple(sorted({
            f"{value[:4]}-{value[4:6]}-{value[6:8]}"
            for value in (str(row.get("Date", "")) for row in rows)
            if len(value) == 8 and value.isdigit()
        }))
        status = EvidenceStatus.TRADED if target_date in dates else EvidenceStatus.NO_RECORD
        detail = f"contract={contract}; session={session}; rows={len(rows)}"
    except Exception as exc:
        dates, status = (), EvidenceStatus.API_FAILED
        detail = f"{type(exc).__name__}: {exc}"[:500]
    return OfficialEvidence("TAIFEX", session, target_date, status, dates, TAIFEX_URL, fetched, detail)


def fetch_cross_market_evidence(target_date: str) -> OfficialEvidence:
    """Verify both markets independently when the failing source is ambiguous."""
    twse = fetch_twse_evidence(target_date)
    taifex_day = fetch_taifex_evidence(target_date, session="一般")
    taifex_night = fetch_taifex_evidence(target_date, session="盤後")
    items = (twse, taifex_day, taifex_night)
    if any(item.status == EvidenceStatus.TRADED for item in items):
        status = EvidenceStatus.TRADED
    elif all(item.status == EvidenceStatus.NO_RECORD for item in items):
        status = EvidenceStatus.NO_RECORD
    else:
        status = EvidenceStatus.API_FAILED
    return OfficialEvidence(
        "TWSE+TAIFEX", "DAY+NIGHT", target_date, status,
        tuple(sorted({day for item in items for day in item.observed_dates})),
        f"{TWSE_URL.format(date=target_date.replace('-', ''))} | {TAIFEX_URL}",
        max(item.fetched_at for item in items),
        "; ".join(f"{item.market}/{item.session}={item.status.value}" for item in items),
    )
