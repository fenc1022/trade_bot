from __future__ import annotations

import time
from datetime import datetime, timezone

from web3 import Web3

from src.config import V2PoolConfig
from src.price_types import PriceSnapshot

V2_PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]


class UniswapV2ReserveReader:
    def __init__(self, chain_web3: dict[str, Web3]) -> None:
        self.chain_web3 = chain_web3

    def fetch_snapshot(self, pool: V2PoolConfig) -> PriceSnapshot:
        start = time.perf_counter()
        w3 = self.chain_web3[pool.chain]
        pair = w3.eth.contract(address=Web3.to_checksum_address(pool.pool_address), abi=V2_PAIR_ABI)

        reserve0, reserve1, _ = pair.functions.getReserves().call()
        block_number = w3.eth.block_number

        reserve0_norm = reserve0 / (10 ** pool.token0_decimals)
        reserve1_norm = reserve1 / (10 ** pool.token1_decimals)
        if reserve0_norm == 0:
            raise ValueError(f"Zero reserve0 for pool {pool.pool_address}")

        price = reserve1_norm / reserve0_norm
        latency_ms = (time.perf_counter() - start) * 1000

        return PriceSnapshot(
            timestamp=datetime.now(timezone.utc),
            chain=pool.chain,
            dex=pool.dex,
            pool_address=pool.pool_address,
            pair_key=pool.pair_key,
            price_token1_per_token0=price,
            block_number=block_number,
            latency_ms=latency_ms,
        )
