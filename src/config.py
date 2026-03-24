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
    token0_address: str = ""
    token1_address: str = ""
    router_address: str = ""

    @property
    def pair_key(self) -> str:
        return f"{self.token0_symbol}/{self.token1_symbol}"

    @property
    def has_execution_config(self) -> bool:
        return bool(self.router_address)


@dataclass(frozen=True)
class ArbitrageConfig:
    volume: float
    min_diff_pct: float
    min_net_profit: float
    min_net_profit_pct: float
    dex_fee_bps_per_swap: float
    gas_units_per_swap: int
    bridge_fee_url_template: str
    bridge_fee_json_path: str


@dataclass(frozen=True)
class ExecutionConfig:
    wallet_private_key: str
    wallet_address: str
    live_mode: bool
    slippage_bps: int
    deadline_seconds: int
    receipt_timeout_sec: int
    bridge_timeout_sec: int
    bridge_poll_interval_sec: int
    min_native_balance: float
    lifi_base_url: str


@dataclass(frozen=True)
class MonitoringConfig:
    log_path: str
    dashboard_window_entries: int
    alert_webhook_url: str
    alert_on_failure: bool
    alert_on_success: bool
    execution_loop_interval_sec: int


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
                token0_address=str(item.get("token0_address", "")),
                token1_address=str(item.get("token1_address", "")),
                router_address=str(item.get("router_address", "")),
            )
        )
    return pools


def get_arbitrage_config() -> ArbitrageConfig:
    volume = float(os.getenv("ARB_TRADE_VOLUME", "1000"))
    min_diff_pct = float(os.getenv("ARB_MIN_DIFF_PCT", "0.1"))
    min_net_profit = float(os.getenv("ARB_MIN_NET_PROFIT", "0.0"))
    min_net_profit_pct = float(os.getenv("ARB_MIN_NET_PROFIT_PCT", "0.0"))
    dex_fee_bps_per_swap = float(os.getenv("ARB_DEX_FEE_BPS_PER_SWAP", "30"))
    gas_units_per_swap = int(os.getenv("ARB_GAS_UNITS_PER_SWAP", "220000"))
    bridge_fee_url_template = os.getenv("ARB_BRIDGE_FEE_URL_TEMPLATE", "").strip()
    bridge_fee_json_path = os.getenv("ARB_BRIDGE_FEE_JSON_PATH", "").strip()

    return ArbitrageConfig(
        volume=volume,
        min_diff_pct=min_diff_pct,
        min_net_profit=min_net_profit,
        min_net_profit_pct=min_net_profit_pct,
        dex_fee_bps_per_swap=dex_fee_bps_per_swap,
        gas_units_per_swap=gas_units_per_swap,
        bridge_fee_url_template=bridge_fee_url_template,
        bridge_fee_json_path=bridge_fee_json_path,
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_execution_config() -> ExecutionConfig:
    return ExecutionConfig(
        wallet_private_key=os.getenv("EXECUTION_WALLET_PRIVATE_KEY", "").strip(),
        wallet_address=os.getenv("EXECUTION_WALLET_ADDRESS", "").strip(),
        live_mode=_env_bool("EXECUTION_LIVE_MODE", False),
        slippage_bps=int(os.getenv("EXECUTION_SLIPPAGE_BPS", "100")),
        deadline_seconds=int(os.getenv("EXECUTION_DEADLINE_SECONDS", "180")),
        receipt_timeout_sec=int(os.getenv("EXECUTION_RECEIPT_TIMEOUT_SEC", "180")),
        bridge_timeout_sec=int(os.getenv("EXECUTION_BRIDGE_TIMEOUT_SEC", "900")),
        bridge_poll_interval_sec=int(os.getenv("EXECUTION_BRIDGE_POLL_INTERVAL_SEC", "5")),
        min_native_balance=float(os.getenv("EXECUTION_MIN_NATIVE_BALANCE", "0.005")),
        lifi_base_url=os.getenv("EXECUTION_LIFI_BASE_URL", "https://li.quest/v1").strip(),
    )


def get_monitoring_config() -> MonitoringConfig:
    return MonitoringConfig(
        log_path=os.getenv("MONITORING_LOG_PATH", "logs/arbitrage_events.jsonl").strip(),
        dashboard_window_entries=int(os.getenv("MONITORING_DASHBOARD_WINDOW_ENTRIES", "200")),
        alert_webhook_url=os.getenv("MONITORING_ALERT_WEBHOOK_URL", "").strip(),
        alert_on_failure=_env_bool("MONITORING_ALERT_ON_FAILURE", True),
        alert_on_success=_env_bool("MONITORING_ALERT_ON_SUCCESS", False),
        execution_loop_interval_sec=int(os.getenv("MONITORING_EXECUTION_LOOP_INTERVAL_SEC", "300")),
    )
