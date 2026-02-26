from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ChainConfig:
    name: str
    rpc_url: str


def get_chain_configs() -> list[ChainConfig]:
    chains = [
        ("ethereum", os.getenv("ETH_RPC_URL", "")),
        ("bsc", os.getenv("BSC_RPC_URL", "")),
        ("polygon", os.getenv("POLYGON_RPC_URL", "")),
        ("avalanche", os.getenv("AVALANCHE_RPC_URL", "")),
    ]

    return [ChainConfig(name=name, rpc_url=rpc_url) for name, rpc_url in chains if rpc_url]
