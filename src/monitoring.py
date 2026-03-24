from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import MonitoringConfig


class MonitoringError(Exception):
    pass


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def append_event(log_path: str, event: dict[str, Any]) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(event)
    record.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=_json_default, sort_keys=True))
        fh.write("\n")


def emit_alert(cfg: MonitoringConfig, title: str, body: str) -> None:
    if not cfg.alert_webhook_url:
        return

    payload = json.dumps({"text": f"{title}\n{body}"}).encode("utf-8")
    req = urllib.request.Request(
        cfg.alert_webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "trade-bot/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8):
            return
    except OSError as exc:
        raise MonitoringError(f"Failed to send alert webhook: {exc}") from exc


def maybe_alert_success(cfg: MonitoringConfig, event: dict[str, Any]) -> None:
    if not cfg.alert_on_success:
        return
    body = (
        f"pair={event.get('pair_key')} "
        f"buy={event.get('buy_chain')} sell={event.get('sell_chain')} "
        f"net_profit_est={event.get('estimated_net_profit')} "
        f"buy_tx={event.get('buy_tx_hash')} bridge_tx={event.get('bridge_tx_hash')} "
        f"sell_tx={event.get('sell_tx_hash')}"
    )
    emit_alert(cfg, "Arbitrage execution succeeded", body)


def maybe_alert_failure(cfg: MonitoringConfig, event: dict[str, Any]) -> None:
    if not cfg.alert_on_failure:
        return
    body = (
        f"stage={event.get('stage')} "
        f"pair={event.get('pair_key')} "
        f"buy={event.get('buy_chain')} sell={event.get('sell_chain')} "
        f"reason={event.get('error')}"
    )
    emit_alert(cfg, "Arbitrage execution failed", body)
