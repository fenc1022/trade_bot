from __future__ import annotations

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ChainConfig:
    name: str
    rpc_url: str
    expected_chain_id: int


@dataclass(frozen=True)
class V2PoolConfig:
    chain: str
    dex: str
    pool_address: str
    token0_symbol: str
    token1_symbol: str
    token0_decimals: int
    token1_decimals: int

    @property
    def pair_key(self) -> str:
        return f"{self.token0_symbol}/{self.token1_symbol}"


@dataclass(frozen=True)
class ArbitrageConfig:
    volume: float
    min_diff_pct: float
    default_fees: float
    route_fees: dict[tuple[str, str], float]

    def fees_for_route(self, buy_chain: str, sell_chain: str) -> float:
        return self.route_fees.get((buy_chain, sell_chain), self.default_fees)


def get_chain_configs() -> list[ChainConfig]:
    chains = [
        ("ethereum", os.getenv("ETH_RPC_URL", ""), 1),
        ("bsc", os.getenv("BSC_RPC_URL", ""), 56),
        ("polygon", os.getenv("POLYGON_RPC_URL", ""), 137),
        ("avalanche", os.getenv("AVALANCHE_RPC_URL", ""), 43114),
        ("arbitrum", os.getenv("ARBITRUM_RPC_URL", ""), 42161),
        ("base", os.getenv("BASE_RPC_URL", ""), 8453),
    ]

    return [
        ChainConfig(name=name, rpc_url=rpc_url, expected_chain_id=expected_chain_id)
        for name, rpc_url, expected_chain_id in chains
        if rpc_url
    ]


def get_v2_pool_configs() -> list[V2PoolConfig]:
    raw = os.getenv("V2_POOLS_JSON", "").strip()
    if not raw:
        return []

    payload = json.loads(raw)
    pools: list[V2PoolConfig] = []
    for item in payload:
        pools.append(
            V2PoolConfig(
                chain=str(item["chain"]).lower(),
                dex=str(item["dex"]).lower(),
                pool_address=str(item["pool_address"]),
                token0_symbol=str(item["token0_symbol"]).upper(),
                token1_symbol=str(item["token1_symbol"]).upper(),
                token0_decimals=int(item["token0_decimals"]),
                token1_decimals=int(item["token1_decimals"]),
            )
        )
    return pools


def get_arbitrage_config() -> ArbitrageConfig:
    volume = float(os.getenv("ARB_TRADE_VOLUME", "1000"))
    min_diff_pct = float(os.getenv("ARB_MIN_DIFF_PCT", "0.1"))
    default_fees = float(os.getenv("ARB_DEFAULT_FEES", "2.0"))

    route_fees_raw = os.getenv("ARB_ROUTE_FEES_JSON", "").strip()
    route_fees: dict[tuple[str, str], float] = {}
    if route_fees_raw:
        payload = json.loads(route_fees_raw)
        for item in payload:
            buy_chain = str(item["buy_chain"]).lower()
            sell_chain = str(item["sell_chain"]).lower()
            fees = float(item["fees"])
            route_fees[(buy_chain, sell_chain)] = fees

    return ArbitrageConfig(
        volume=volume,
        min_diff_pct=min_diff_pct,
        default_fees=default_fees,
        route_fees=route_fees,
    )
