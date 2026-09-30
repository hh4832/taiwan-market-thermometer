"""Traceable per-run archive writer."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import pandas as pd

from .research_registry import CANONICAL_SIGNALS


def archive_run(
    root: str | Path,
    run_id: str,
    events: pd.DataFrame,
    calendar: pd.DataFrame,
    manifest: object,
    *,
    history_events: pd.DataFrame | None = None,
    target_calendar: pd.DataFrame | None = None,
) -> Path:
    folder = Path(root) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    events.to_csv(folder / "signal_events.csv", index=False)
    calendar.to_csv(folder / "forecast_calendar.csv", index=False)
    if history_events is not None:
        history_events.to_csv(folder / "signal_events_history.csv", index=False)
    if target_calendar is not None:
        target_calendar.to_csv(folder / "target_date_calendar.csv", index=False)
    pd.DataFrame([signal.as_dict() for signal in CANONICAL_SIGNALS]).to_csv(folder / "canonical_registry.csv", index=False)
    (folder / "run_manifest.json").write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    return folder
