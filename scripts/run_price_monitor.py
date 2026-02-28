from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from web3 import Web3

# Allow direct execution: `python ./scripts/run_price_monitor.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.collector import collect_v2_snapshots
from src.config import get_arbitrage_config, get_chain_configs, get_v2_pool_configs
from src.dex_uniswap_v2 import UniswapV2ReserveReader
from src.fees import FeeEstimationError, RealTimeFeeEstimator, route_pairs_from_snapshots
from src.ratio import compute_arbitrage_opportunities, compute_cross_chain_spreads


def _build_chain_web3() -> dict[str, Web3]:
    chain_map = {chain.name: Web3(Web3.HTTPProvider(chain.rpc_url)) for chain in get_chain_configs()}
    return chain_map


def _validate_pool_chains(pool_chains: set[str], configured_chains: set[str]) -> list[str]:
    missing = sorted(pool_chains - configured_chains)
    return [f"Missing RPC config for chain '{chain}'" for chain in missing]


async def main() -> None:
    pools = get_v2_pool_configs()
    arb_cfg = get_arbitrage_config()
    if not pools:
        print("No pools configured. Set V2_POOLS_JSON in .env.")
        return

    chain_web3 = _build_chain_web3()
    configured_chains = set(chain_web3.keys())
    pool_chains = {pool.chain for pool in pools}

    validation_errors = _validate_pool_chains(pool_chains, configured_chains)
    if validation_errors:
        for err in validation_errors:
            print(err)
        return

    reader = UniswapV2ReserveReader(chain_web3)
    fee_estimator = RealTimeFeeEstimator(chain_web3=chain_web3, cfg=arb_cfg)
    print("Starting price monitor (Ctrl+C to stop)")
    print("=" * 90)
    print(
        f"Arbitrage config: volume={arb_cfg.volume:.2f}, "
        f"min_diff_pct={arb_cfg.min_diff_pct:.3f}%, "
        f"min_net_profit={arb_cfg.min_net_profit:.3f}, "
        f"min_net_profit_pct={arb_cfg.min_net_profit_pct:.3f}%"
    )

    while True:
        snapshots, errors = await collect_v2_snapshots(reader, pools)
        spreads = compute_cross_chain_spreads(snapshots)
        route_fees = {}
        fee_errors: list[str] = []
        for buy_chain, sell_chain in sorted(route_pairs_from_snapshots(snapshots)):
            try:
                route_fees[(buy_chain, sell_chain)] = fee_estimator.estimate_route_fees(
                    buy_chain=buy_chain,
                    sell_chain=sell_chain,
                    volume=arb_cfg.volume,
                )
            except (FeeEstimationError, ValueError, KeyError, OSError) as exc:
                fee_errors.append(
                    f"fee_quote_failed route={buy_chain}->{sell_chain} reason={exc}"
                )

        opportunities = compute_arbitrage_opportunities(
            snapshots=snapshots,
            cfg=arb_cfg,
            route_fees=route_fees,
        )

        for error in errors:
            print(f"ERROR {error}")
        for fee_error in fee_errors:
            print(f"ERROR {fee_error}")

        if snapshots:
            print("Latest snapshots:")
            for s in snapshots:
                print(
                    f"  {s.chain:10} {s.dex:12} {s.pair_key:14} "
                    f"price={s.price_token1_per_token0:.8f} block={s.block_number} "
                    f"latency={s.latency_ms:.1f}ms"
                )

        if spreads:
            print("Cross-chain spreads:")
            for spread in spreads:
                print(
                    f"  {spread.pair_key:14} {spread.chain_a:10}/{spread.chain_b:10} "
                    f"ratio={spread.ratio_a_over_b:.6f} spread={spread.spread_pct:+.3f}%"
                )

        if opportunities:
            print("Arbitrage opportunities (after fees):")
            for opp in opportunities:
                fee_quote = route_fees[(opp.buy_chain, opp.sell_chain)]
                print(
                    f"  {opp.pair_key:14} buy={opp.buy_chain:10} sell={opp.sell_chain:10} "
                    f"diff={opp.difference_pct:+.3f}% gross={opp.gross_profit:.4f} "
                    f"fees={opp.fees:.4f} net={opp.net_profit:.4f} "
                    f"(gas={fee_quote.gas_buy_usd + fee_quote.gas_sell_usd:.4f}, "
                    f"bridge={fee_quote.bridge_fee_usd:.4f}, dex={fee_quote.dex_fee_usd:.4f})"
                )

        print("-" * 90)
        await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except json.JSONDecodeError:
        print("Invalid JSON format in .env. Check V2_POOLS_JSON and fee config JSON fields.")
    except KeyboardInterrupt:
        print("Stopped.")
