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
from src.config import get_chain_configs, get_v2_pool_configs
from src.dex_uniswap_v2 import UniswapV2ReserveReader
from src.ratio import compute_cross_chain_spreads


def _build_chain_web3() -> dict[str, Web3]:
    chain_map = {chain.name: Web3(Web3.HTTPProvider(chain.rpc_url)) for chain in get_chain_configs()}
    return chain_map


def _validate_pool_chains(pool_chains: set[str], configured_chains: set[str]) -> list[str]:
    missing = sorted(pool_chains - configured_chains)
    return [f"Missing RPC config for chain '{chain}'" for chain in missing]


async def main() -> None:
    pools = get_v2_pool_configs()
    if not pools:
        print("No pools configured. Set V2_POOLS_JSON in .env (see .env.example).")
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
    print("Starting Step 2 monitor (Ctrl+C to stop)")
    print("=" * 90)

    while True:
        snapshots, errors = await collect_v2_snapshots(reader, pools)
        spreads = compute_cross_chain_spreads(snapshots)

        for error in errors:
            print(f"ERROR {error}")

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

        print("-" * 90)
        await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except json.JSONDecodeError:
        print("Invalid V2_POOLS_JSON format in .env. Use valid JSON array (see .env.example).")
    except KeyboardInterrupt:
        print("Stopped.")
