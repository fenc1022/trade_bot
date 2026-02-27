from __future__ import annotations

import asyncio

from src.config import V2PoolConfig
from src.dex_uniswap_v2 import UniswapV2ReserveReader
from src.price_types import PriceSnapshot


async def collect_v2_snapshots(
    reader: UniswapV2ReserveReader,
    pools: list[V2PoolConfig],
) -> tuple[list[PriceSnapshot], list[str]]:
    tasks = [asyncio.to_thread(reader.fetch_snapshot, pool) for pool in pools]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    snapshots: list[PriceSnapshot] = []
    errors: list[str] = []
    for pool, result in zip(pools, results, strict=True):
        if isinstance(result, Exception):
            errors.append(f"{pool.chain}:{pool.dex}:{pool.pair_key} error={result}")
            continue
        snapshots.append(result)

    return snapshots, errors
