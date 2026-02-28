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


@dataclass(frozen=True)
class ArbitrageOpportunity:
    timestamp: datetime
    pair_key: str
    buy_chain: str
    sell_chain: str
    buy_price: float
    sell_price: float
    difference: float
    difference_pct: float
    volume: float
    gross_profit: float
    fees: float
    net_profit: float
