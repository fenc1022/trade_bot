from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from web3 import Web3

from src.config import ArbitrageConfig
from src.price_types import FeeBreakdown, PriceSnapshot

CHAIN_NATIVE_COINGECKO_ID = {
    "ethereum": "ethereum",
    "arbitrum": "ethereum",
    "base": "ethereum",
    "bsc": "binancecoin",
    "polygon": "matic-network",
    "avalanche": "avalanche-2",
}


class FeeEstimationError(Exception):
    pass


def _http_get_json(url: str, timeout_sec: int = 8) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "trade-bot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _json_path_get(payload: Any, path: str) -> float:
    current: Any = payload
    for key in path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            raise FeeEstimationError(f"JSON path '{path}' not found")
    try:
        return float(current)
    except (TypeError, ValueError) as exc:
        raise FeeEstimationError(f"Value at '{path}' is not numeric: {current!r}") from exc


class RealTimeFeeEstimator:
    def __init__(
        self,
        chain_web3: dict[str, Web3],
        cfg: ArbitrageConfig,
    ) -> None:
        self.chain_web3 = chain_web3
        self.cfg = cfg

    def _native_price_usd(self, chain: str) -> float:
        coingecko_id = CHAIN_NATIVE_COINGECKO_ID.get(chain)
        if not coingecko_id:
            raise FeeEstimationError(f"No native price mapping for chain '{chain}'")

        query = urllib.parse.urlencode({"ids": coingecko_id, "vs_currencies": "usd"})
        url = f"https://api.coingecko.com/api/v3/simple/price?{query}"
        payload = _http_get_json(url)
        try:
            return float(payload[coingecko_id]["usd"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FeeEstimationError(f"Unexpected CoinGecko payload for '{chain}': {payload!r}") from exc

    def _gas_cost_usd(self, chain: str) -> float:
        if chain not in self.chain_web3:
            raise FeeEstimationError(f"Missing Web3 client for chain '{chain}'")
        w3 = self.chain_web3[chain]
        gas_price_wei = int(w3.eth.gas_price)
        native_price = self._native_price_usd(chain)
        gas_native = (gas_price_wei * self.cfg.gas_units_per_swap) / 1e18
        return gas_native * native_price

    def _bridge_fee_usd(self, buy_chain: str, sell_chain: str, volume: float) -> float:
        if not self.cfg.bridge_fee_url_template or not self.cfg.bridge_fee_json_path:
            raise FeeEstimationError(
                "Missing ARB_BRIDGE_FEE_URL_TEMPLATE or ARB_BRIDGE_FEE_JSON_PATH"
            )

        url = self.cfg.bridge_fee_url_template.format(
            buy_chain=buy_chain,
            sell_chain=sell_chain,
            volume=volume,
        )
        payload = _http_get_json(url)
        return _json_path_get(payload, self.cfg.bridge_fee_json_path)

    def estimate_route_fees(
        self,
        buy_chain: str,
        sell_chain: str,
        volume: float,
    ) -> FeeBreakdown:
        gas_buy = self._gas_cost_usd(buy_chain)
        gas_sell = self._gas_cost_usd(sell_chain)
        bridge_fee = self._bridge_fee_usd(buy_chain=buy_chain, sell_chain=sell_chain, volume=volume)

        # Two swaps: buy on cheaper chain, sell on expensive chain.
        dex_fee = volume * (self.cfg.dex_fee_bps_per_swap / 10_000.0) * 2.0
        total = gas_buy + gas_sell + bridge_fee + dex_fee

        return FeeBreakdown(
            buy_chain=buy_chain,
            sell_chain=sell_chain,
            gas_buy_usd=gas_buy,
            gas_sell_usd=gas_sell,
            bridge_fee_usd=bridge_fee,
            dex_fee_usd=dex_fee,
            total_fees_usd=total,
        )


def route_pairs_from_snapshots(snapshots: list[PriceSnapshot]) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    by_pair: dict[str, list[PriceSnapshot]] = {}
    for s in snapshots:
        by_pair.setdefault(s.pair_key, []).append(s)

    for pair_snapshots in by_pair.values():
        for i in range(len(pair_snapshots)):
            for j in range(i + 1, len(pair_snapshots)):
                a = pair_snapshots[i]
                b = pair_snapshots[j]
                if a.chain == b.chain:
                    continue
                if a.price_token1_per_token0 <= b.price_token1_per_token0:
                    routes.add((a.chain, b.chain))
                else:
                    routes.add((b.chain, a.chain))
    return routes
