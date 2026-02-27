from __future__ import annotations

import sys
import time
from pathlib import Path

# Allow direct execution: `python ./scripts/check_rpc_health.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_chain_configs
from src.rpc_clients import build_web3_client


def main() -> None:
    chains = get_chain_configs()
    if not chains:
        print("No RPC endpoints found in .env. Add at least ETH_RPC_URL.")
        return

    print("RPC health check")
    print("=" * 60)

    for chain in chains:
        start = time.perf_counter()
        try:
            w3 = build_web3_client(chain.rpc_url)
            chain_id = w3.eth.chain_id
            latest_block = w3.eth.block_number
            latency_ms = (time.perf_counter() - start) * 1000
            status = "OK"
            if chain_id != chain.expected_chain_id:
                status = "MISMATCH"
            print(
                f"[{chain.name:10}] {status:<9} chain_id={chain_id:<8} "
                f"expected={chain.expected_chain_id:<8} latest_block={latest_block:<12} "
                f"latency={latency_ms:.1f}ms"
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            print(f"[{chain.name:10}] FAIL error={exc} latency={latency_ms:.1f}ms")


if __name__ == "__main__":
    main()
