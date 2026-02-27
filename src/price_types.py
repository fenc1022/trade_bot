from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PriceSnapshot:
    timestamp: datetime
    chain: str
    dex: str
    pool_address: str
    pair_key: str
    price_token1_per_token0: float
    block_number: int
    latency_ms: float


@dataclass(frozen=True)
class SpreadSignal:
    timestamp: datetime
    pair_key: str
    chain_a: str
    chain_b: str
    price_a: float
    price_b: float
    ratio_a_over_b: float
    spread_pct: float
