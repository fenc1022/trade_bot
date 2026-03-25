from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from web3 import Web3

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.collector import collect_v2_snapshots
from src.config import (
    get_arbitrage_config,
    get_chain_configs,
    get_execution_config,
    get_monitoring_config,
    get_v2_pool_configs,
)
from src.dex_uniswap_v2 import UniswapV2ReserveReader
from src.execution import ArbitrageExecutor, ExecutionError
from src.fees import FeeEstimationError, RealTimeFeeEstimator, route_pairs_from_snapshots
from src.monitoring import MonitoringError, append_event, maybe_alert_failure, maybe_alert_success
from src.price_types import ArbitrageOpportunity
from src.ratio import compute_arbitrage_opportunities, compute_cross_chain_spreads


class RuntimeContext:
    def __init__(self) -> None:
        self.pools = get_v2_pool_configs()
        self.arb_cfg = get_arbitrage_config()
        self.execution_cfg = get_execution_config()
        self.monitoring_cfg = get_monitoring_config()
        self.chain_web3 = _build_chain_web3()
        self.reader = UniswapV2ReserveReader(self.chain_web3)
        self.fee_estimator = RealTimeFeeEstimator(chain_web3=self.chain_web3, cfg=self.arb_cfg)


def _build_chain_web3() -> dict[str, Web3]:
    return {chain.name: Web3(Web3.HTTPProvider(chain.rpc_url)) for chain in get_chain_configs()}


def _find_pool(pools, chain: str, pair_key: str):
    for pool in pools:
        if pool.chain == chain and pool.pair_key == pair_key:
            return pool
    return None


def _format_token_amount(amount: int, decimals: int) -> str:
    return f"{amount / (10 ** decimals):.8f}"


def _print_plan(plan, buy_pool, sell_pool, live_mode: bool) -> None:
    mode = "LIVE" if live_mode else "DRY-RUN"
    print(f"Execution mode: {mode}")
    print(
        f"Selected opportunity: {plan.opportunity.pair_key} "
        f"buy={plan.opportunity.buy_chain} sell={plan.opportunity.sell_chain} "
        f"net={plan.opportunity.net_profit:.4f}"
    )
    print("Buy swap:")
    print(
        f"  {plan.buy_swap.chain} {plan.buy_swap.dex} "
        f"{_format_token_amount(plan.buy_swap.amount_in, buy_pool.token1_decimals)} {plan.buy_swap.token_in_symbol} "
        f"-> est {_format_token_amount(plan.buy_swap.amount_out_estimate, buy_pool.token0_decimals)} {plan.buy_swap.token_out_symbol} "
        f"(min {_format_token_amount(plan.buy_swap.amount_out_min, buy_pool.token0_decimals)})"
    )
    print("Bridge:")
    print(
        f"  {plan.bridge.from_chain} -> {plan.bridge.to_chain} via {plan.bridge.tool} "
        f"{_format_token_amount(plan.bridge.amount_in, buy_pool.token0_decimals)} {plan.bridge.token_symbol} "
        f"-> est {_format_token_amount(plan.bridge.amount_out_estimate, sell_pool.token0_decimals)} {plan.bridge.token_symbol} "
        f"(min {_format_token_amount(plan.bridge.amount_out_min, sell_pool.token0_decimals)})"
    )
    print("Sell swap:")
    print(
        f"  {plan.sell_swap.chain} {plan.sell_swap.dex} "
        f"{_format_token_amount(plan.sell_swap.amount_in, sell_pool.token0_decimals)} {plan.sell_swap.token_in_symbol} "
        f"-> est {_format_token_amount(plan.sell_swap.amount_out_estimate, sell_pool.token1_decimals)} {plan.sell_swap.token_out_symbol} "
        f"(min {_format_token_amount(plan.sell_swap.amount_out_min, sell_pool.token1_decimals)})"
    )


def _print_snapshots(snapshots) -> None:
    if not snapshots:
        return
    print("Latest snapshots:")
    for s in snapshots:
        print(
            f"  {s.chain:10} {s.dex:12} {s.pair_key:14} "
            f"price={s.price_token1_per_token0:.8f} block={s.block_number} "
            f"latency={s.latency_ms:.1f}ms"
        )


def _print_spreads(spreads) -> None:
    if not spreads:
        return
    print("Cross-chain spreads:")
    for spread in spreads[:5]:
        print(
            f"  {spread.pair_key:14} {spread.chain_a:10}/{spread.chain_b:10} "
            f"ratio={spread.ratio_a_over_b:.6f} spread={spread.spread_pct:+.3f}%"
        )


def _print_fee_quotes(route_fees) -> None:
    if not route_fees:
        return
    print("Route fees:")
    for (buy_chain, sell_chain), fee in sorted(route_fees.items()):
        print(
            f"  {buy_chain:10}->{sell_chain:10} total={fee.total_fees_usd:.4f} "
            f"gas={fee.gas_buy_usd + fee.gas_sell_usd:.4f} "
            f"bridge={fee.bridge_fee_usd:.4f} dex={fee.dex_fee_usd:.4f}"
        )


def _event_base() -> dict[str, str]:
    return {"event_time": datetime.now(timezone.utc).isoformat()}


def _opportunity_fields(opp) -> dict[str, object]:
    if opp is None:
        return {}
    return {
        "pair_key": opp.pair_key,
        "buy_chain": opp.buy_chain,
        "sell_chain": opp.sell_chain,
        "buy_price": opp.buy_price,
        "sell_price": opp.sell_price,
        "difference": opp.difference,
        "difference_pct": opp.difference_pct,
        "volume": opp.volume,
        "gross_profit": opp.gross_profit,
        "fees": opp.fees,
        "estimated_net_profit": opp.net_profit,
    }


def _plan_fields(plan) -> dict[str, object]:
    if plan is None:
        return {}
    return {
        "buy_swap_plan": {
            "chain": plan.buy_swap.chain,
            "dex": plan.buy_swap.dex,
            "amount_in": plan.buy_swap.amount_in,
            "amount_out_estimate": plan.buy_swap.amount_out_estimate,
            "amount_out_min": plan.buy_swap.amount_out_min,
            "token_in_symbol": plan.buy_swap.token_in_symbol,
            "token_out_symbol": plan.buy_swap.token_out_symbol,
        },
        "bridge_plan": {
            "from_chain": plan.bridge.from_chain,
            "to_chain": plan.bridge.to_chain,
            "amount_in": plan.bridge.amount_in,
            "amount_out_estimate": plan.bridge.amount_out_estimate,
            "amount_out_min": plan.bridge.amount_out_min,
            "token_symbol": plan.bridge.token_symbol,
            "tool": plan.bridge.tool,
        },
        "sell_swap_plan": {
            "chain": plan.sell_swap.chain,
            "dex": plan.sell_swap.dex,
            "amount_in": plan.sell_swap.amount_in,
            "amount_out_estimate": plan.sell_swap.amount_out_estimate,
            "amount_out_min": plan.sell_swap.amount_out_min,
            "token_in_symbol": plan.sell_swap.token_in_symbol,
            "token_out_symbol": plan.sell_swap.token_out_symbol,
        },
    }


def _safe_log(log_path: str, event: dict[str, object]) -> None:
    try:
        append_event(log_path, event)
    except OSError as exc:
        print(f"WARNING failed to write monitoring log: {exc}")


async def _collect_best_opportunity(ctx: RuntimeContext):
    pools = ctx.pools
    arb_cfg = ctx.arb_cfg
    execution_cfg = ctx.execution_cfg
    monitoring_cfg = ctx.monitoring_cfg
    if not pools:
        print("No pools configured. Set V2_POOLS_JSON in .env.")
        return None

    snapshots, errors = await collect_v2_snapshots(ctx.reader, pools)
    for error in errors:
        print(f"ERROR {error}")
    _print_snapshots(snapshots)
    spreads = compute_cross_chain_spreads(snapshots)
    _print_spreads(spreads)

    route_fees = {}
    for buy_chain, sell_chain in sorted(route_pairs_from_snapshots(snapshots)):
        try:
            route_fees[(buy_chain, sell_chain)] = ctx.fee_estimator.estimate_route_fees(
                buy_chain=buy_chain,
                sell_chain=sell_chain,
                volume=arb_cfg.volume,
            )
        except (FeeEstimationError, ValueError, KeyError, OSError) as exc:
            print(f"ERROR fee_quote_failed route={buy_chain}->{sell_chain} reason={exc}")
    _print_fee_quotes(route_fees)

    opportunities = compute_arbitrage_opportunities(
        snapshots=snapshots,
        cfg=arb_cfg,
        route_fees=route_fees,
    )
    if not opportunities:
        _safe_log(
            monitoring_cfg.log_path,
            {
                **_event_base(),
                "event_type": "scan_no_opportunity",
                "snapshot_count": len(snapshots),
                "route_fee_count": len(route_fees),
                "top_spread_pct": spreads[0].spread_pct if spreads else None,
                "reason": "Current spreads are either below thresholds or fully consumed by fees.",
            },
        )
        print(
            "No executable arbitrage opportunities found. "
            "Current spreads are either below thresholds or fully consumed by fees."
        )
        return None

    best = opportunities[0]
    buy_pool = _find_pool(pools, best.buy_chain, best.pair_key)
    sell_pool = _find_pool(pools, best.sell_chain, best.pair_key)
    if buy_pool is None or sell_pool is None:
        print(f"Missing pool config for opportunity {best.pair_key} {best.buy_chain}->{best.sell_chain}")
        return None

    executor = ArbitrageExecutor(chain_web3=ctx.chain_web3, execution_cfg=execution_cfg)
    return executor, execution_cfg, monitoring_cfg, best, buy_pool, sell_pool


def _print_result(result, sell_pool) -> None:
    print("Execution completed:")
    print(f"  buy_tx={result.buy_tx_hash}")
    print(f"  bridge_tx={result.bridge_tx_hash}")
    print(f"  sell_tx={result.sell_tx_hash}")
    print(
        f"  received={_format_token_amount(result.quote_token_received, sell_pool.token1_decimals)} "
        f"{sell_pool.token1_symbol} on {sell_pool.chain}"
    )


async def run_once(ctx: RuntimeContext, *, force_dry_run: bool = False) -> None:
    collected = await _collect_best_opportunity(ctx)
    if collected is None:
        return

    executor, execution_cfg, monitoring_cfg, best, buy_pool, sell_pool = collected
    effective_live_mode = execution_cfg.live_mode and not force_dry_run
    plan = None
    if effective_live_mode:
        plan = executor.plan_execution(best, buy_pool, sell_pool)
        _print_plan(plan, buy_pool, sell_pool, effective_live_mode)
    else:
        print(
            f"Selected opportunity: {best.pair_key} "
            f"buy={best.buy_chain} sell={best.sell_chain} net={best.net_profit:.4f}"
        )
    _safe_log(
        monitoring_cfg.log_path,
        {
            **_event_base(),
            "event_type": "execution_candidate",
            "live_mode": effective_live_mode,
            "forced_dry_run": force_dry_run,
            **_opportunity_fields(best),
            **_plan_fields(plan),
        },
    )

    if not effective_live_mode:
        _safe_log(
            monitoring_cfg.log_path,
            {
                **_event_base(),
                "event_type": "execution_skipped",
                "stage": "pre_live_guard",
                "reason": "Live execution disabled." if not force_dry_run else "Forced dry-run mode.",
                "forced_dry_run": force_dry_run,
                **_opportunity_fields(best),
                **_plan_fields(plan),
            },
        )
        if force_dry_run:
            print("Forced dry-run mode is active. No transactions were submitted.")
        else:
            print("Live execution is disabled. Set EXECUTION_LIVE_MODE=true after funding wallet and checking config.")
        return

    try:
        result = executor.execute(best, buy_pool, sell_pool)
    except ExecutionError as exc:
        event = {
            **_event_base(),
            "event_type": "execution_failed",
            "stage": "execute",
            "error": str(exc),
            **_opportunity_fields(best),
            **_plan_fields(plan),
        }
        _safe_log(monitoring_cfg.log_path, event)
        try:
            maybe_alert_failure(monitoring_cfg, event)
        except MonitoringError as alert_exc:
            print(f"WARNING alert delivery failed: {alert_exc}")
        raise

    _print_result(result, sell_pool)
    success_event = {
        **_event_base(),
        "event_type": "execution_succeeded",
        **_opportunity_fields(best),
        **_plan_fields(plan),
        "buy_tx_hash": result.buy_tx_hash,
        "bridge_tx_hash": result.bridge_tx_hash,
        "sell_tx_hash": result.sell_tx_hash,
        "bridged_amount": result.bridged_amount,
        "quote_token_received": result.quote_token_received,
        "buy_receipt": result.buy_receipt,
        "bridge_receipt": result.bridge_receipt,
        "sell_receipt": result.sell_receipt,
    }
    _safe_log(monitoring_cfg.log_path, success_event)
    try:
        maybe_alert_success(monitoring_cfg, success_event)
    except MonitoringError as alert_exc:
        print(f"WARNING alert delivery failed: {alert_exc}")


async def main() -> None:
    ctx = RuntimeContext()
    monitoring_cfg = ctx.monitoring_cfg
    interval = max(1, monitoring_cfg.execution_loop_interval_sec)
    cycle = 0

    print("Starting arbitrage execution loop (Ctrl+C to stop)")
    print(f"loop_interval_sec={interval}")

    while True:
        cycle += 1
        print(f"[cycle {cycle}] scanning opportunities")
        try:
            await run_once(ctx)
        except (ExecutionError, ValueError) as exc:
            print(f"[cycle {cycle}] Execution failed: {exc}")

        print(f"[cycle {cycle}] sleeping {interval}s")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except json.JSONDecodeError:
        print("Invalid JSON format in .env. Check V2_POOLS_JSON and fee config JSON fields.")
    except KeyboardInterrupt:
        print("Stopped.")
