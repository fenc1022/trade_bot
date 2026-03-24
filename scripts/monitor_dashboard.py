from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_monitoring_config


def _load_events(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def _fmt_num(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main() -> None:
    cfg = get_monitoring_config()
    path = Path(cfg.log_path)
    events = _load_events(path, cfg.dashboard_window_entries)
    if not events:
        print(f"No monitoring events found at {path}")
        return

    counts = Counter(event.get("event_type", "unknown") for event in events)
    success_events = [e for e in events if e.get("event_type") == "execution_succeeded"]
    failure_events = [e for e in events if e.get("event_type") == "execution_failed"]
    skip_events = [e for e in events if e.get("event_type") == "execution_skipped"]
    no_op_events = [e for e in events if e.get("event_type") == "scan_no_opportunity"]

    print("Arbitrage Monitoring Dashboard")
    print("=" * 72)
    print(f"log_path={path}")
    print(f"events_loaded={len(events)}")
    print(
        "event_counts="
        f"candidate:{counts.get('execution_candidate', 0)} "
        f"success:{counts.get('execution_succeeded', 0)} "
        f"failed:{counts.get('execution_failed', 0)} "
        f"skipped:{counts.get('execution_skipped', 0)} "
        f"no_op:{counts.get('scan_no_opportunity', 0)}"
    )

    if success_events:
        total_received = sum(float(e.get("quote_token_received", 0)) for e in success_events)
        total_est_net = sum(float(e.get("estimated_net_profit", 0)) for e in success_events)
        print(
            f"successful_executions={len(success_events)} "
            f"quote_token_received_raw={_fmt_num(total_received)} "
            f"estimated_net_profit_sum={_fmt_num(total_est_net)}"
        )

    if failure_events:
        stage_counts = Counter(str(e.get("stage", "unknown")) for e in failure_events)
        print("failure_stages=" + " ".join(f"{stage}:{count}" for stage, count in sorted(stage_counts.items())))

    print("-" * 72)
    print("Recent events:")
    for event in events[-10:]:
        event_type = event.get("event_type")
        event_time = event.get("event_time", "n/a")
        pair_key = event.get("pair_key", "n/a")
        route = f"{event.get('buy_chain', 'n/a')}->{event.get('sell_chain', 'n/a')}"
        if event_type == "execution_succeeded":
            summary = (
                f"net={_fmt_num(event.get('estimated_net_profit'))} "
                f"buy_tx={event.get('buy_tx_hash')} bridge_tx={event.get('bridge_tx_hash')} "
                f"sell_tx={event.get('sell_tx_hash')}"
            )
        elif event_type == "execution_failed":
            summary = f"stage={event.get('stage')} error={event.get('error')}"
        elif event_type == "execution_skipped":
            summary = f"reason={event.get('reason')}"
        elif event_type == "scan_no_opportunity":
            summary = f"top_spread_pct={_fmt_num(event.get('top_spread_pct'))} reason={event.get('reason')}"
        else:
            summary = ""
        print(f"{event_time} {event_type:22} {pair_key:14} {route:24} {summary}")

    if no_op_events:
        latest = no_op_events[-1]
        print("-" * 72)
        print(
            "Latest no-op summary: "
            f"top_spread_pct={_fmt_num(latest.get('top_spread_pct'))} "
            f"snapshot_count={latest.get('snapshot_count')} "
            f"route_fee_count={latest.get('route_fee_count')}"
        )


if __name__ == "__main__":
    main()
