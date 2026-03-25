from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit

from web3 import Web3

from src.config import ArbitrageConfig
from src.price_types import FeeBreakdown, PriceSnapshot

CHAIN_NATIVE_COINGECKO_ID = {
    "ethereum": "ethereum",
    "arbitrum": "ethereum",
    "base": "ethereum",
    "bsc": "binancecoin",
    "polygon": "polygon-ecosystem-token",
    "avalanche": "avalanche-2",
}


class FeeEstimationError(Exception):
    pass


EXTERNAL_PRICE_CACHE_TTL_SEC = 300
BRIDGE_FEE_CACHE_TTL_SEC = 300


def _http_get_json(url: str, timeout_sec: int = 8) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "trade-bot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _json_path_get(payload: Any, path: str) -> float:
    current: Any = payload
    for key in path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
            continue
        if isinstance(current, list):
            try:
                current = current[int(key)]
                continue
            except (ValueError, IndexError) as exc:
                raise FeeEstimationError(f"JSON path '{path}' not found") from exc
        raise FeeEstimationError(f"JSON path '{path}' not found")
    try:
        return float(current)
    except (TypeError, ValueError) as exc:
        raise FeeEstimationError(f"Value at '{path}' is not numeric: {current!r}") from exc


def _lifi_same_token_fee_usd(payload: Any) -> float:
    try:
        from_amount = int(payload["estimate"]["fromAmount"])
        to_amount = int(payload["estimate"]["toAmount"])
        decimals = int(payload["action"]["fromToken"]["decimals"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FeeEstimationError("Unexpected LI.FI quote payload") from exc

    fee_units = max(0, from_amount - to_amount)
    return fee_units / (10 ** decimals)


def _lifi_base_url(url_template: str) -> str:
    if not url_template:
        return "https://li.quest/v1"
    parsed = urlsplit(url_template)
    if not parsed.scheme or not parsed.netloc:
        return "https://li.quest/v1"
    path = parsed.path or ""
    if "/v1" in path:
        prefix = path.split("/v1", 1)[0]
        return f"{parsed.scheme}://{parsed.netloc}{prefix}/v1"
    return f"{parsed.scheme}://{parsed.netloc}/v1"


class RealTimeFeeEstimator:
    def __init__(
        self,
        chain_web3: dict[str, Web3],
        cfg: ArbitrageConfig,
    ) -> None:
        self.chain_web3 = chain_web3
        self.cfg = cfg
        self._lifi_token_cache: dict[tuple[int, str], dict[str, Any]] = {}
        self._native_price_cache: dict[str, tuple[float, float]] = {}
        self._bridge_fee_cache: dict[tuple[str, str, float], tuple[float, float]] = {}

    def _cached_value(self, cache: dict[Any, tuple[float, float]], key: Any, ttl_sec: int) -> float | None:
        cached = cache.get(key)
        if cached is None:
            return None
        value, timestamp = cached
        if time.time() - timestamp <= ttl_sec:
            return value
        return None

    def _stale_value(self, cache: dict[Any, tuple[float, float]], key: Any) -> float | None:
        cached = cache.get(key)
        if cached is None:
            return None
        value, _timestamp = cached
        return value

    def _remember(self, cache: dict[Any, tuple[float, float]], key: Any, value: float) -> float:
        cache[key] = (value, time.time())
        return value

    def _is_rate_limited(self, exc: Exception) -> bool:
        return isinstance(exc, HTTPError) and exc.code == 429

    def _native_price_usd(self, chain: str) -> float:
        coingecko_id = CHAIN_NATIVE_COINGECKO_ID.get(chain)
        if not coingecko_id:
            raise FeeEstimationError(f"No native price mapping for chain '{chain}'")

        cached = self._cached_value(self._native_price_cache, coingecko_id, EXTERNAL_PRICE_CACHE_TTL_SEC)
        if cached is not None:
            return cached

        query = urllib.parse.urlencode({"ids": coingecko_id, "vs_currencies": "usd"})
        url = f"https://api.coingecko.com/api/v3/simple/price?{query}"
        try:
            payload = _http_get_json(url)
        except OSError as exc:
            stale = self._stale_value(self._native_price_cache, coingecko_id)
            if stale is not None and self._is_rate_limited(exc):
                return stale
            raise
        try:
            return self._remember(self._native_price_cache, coingecko_id, float(payload[coingecko_id]["usd"]))
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

        if self.cfg.bridge_fee_json_path == "__LIFI_SAME_TOKEN_FEE_USD__":
            return self._lifi_same_token_quote_fee_usd(
                buy_chain=buy_chain,
                sell_chain=sell_chain,
                volume=volume,
            )

        url = self.cfg.bridge_fee_url_template.format(
            buy_chain=buy_chain,
            sell_chain=sell_chain,
            volume=volume,
        )
        payload = _http_get_json(url)
        return _json_path_get(payload, self.cfg.bridge_fee_json_path)

    def _lifi_token(self, chain_id: int, symbol: str) -> dict[str, Any]:
        cache_key = (chain_id, symbol.upper())
        cached = self._lifi_token_cache.get(cache_key)
        if cached is not None:
            return cached

        base_url = _lifi_base_url(self.cfg.bridge_fee_url_template)
        query = urllib.parse.urlencode({"chain": str(chain_id), "token": symbol})
        payload = _http_get_json(f"{base_url}/token?{query}")
        if not isinstance(payload, dict) or "address" not in payload or "decimals" not in payload:
            raise FeeEstimationError(f"Unexpected LI.FI token payload for {symbol} on chain {chain_id}: {payload!r}")

        self._lifi_token_cache[cache_key] = payload
        return payload

    def _lifi_same_token_quote_fee_usd(self, buy_chain: str, sell_chain: str, volume: float) -> float:
        cache_key = (buy_chain, sell_chain, volume)
        cached = self._cached_value(self._bridge_fee_cache, cache_key, BRIDGE_FEE_CACHE_TTL_SEC)
        if cached is not None:
            return cached

        buy_w3 = self.chain_web3.get(buy_chain)
        sell_w3 = self.chain_web3.get(sell_chain)
        if buy_w3 is None or sell_w3 is None:
            raise FeeEstimationError(f"Missing Web3 client for route {buy_chain}->{sell_chain}")

        buy_chain_id = int(buy_w3.eth.chain_id)
        sell_chain_id = int(sell_w3.eth.chain_id)
        from_token = self._lifi_token(buy_chain_id, "USDC")
        to_token = self._lifi_token(sell_chain_id, "USDC")
        from_decimals = int(from_token["decimals"])
        from_amount = int(volume * (10 ** from_decimals))

        base_url = _lifi_base_url(self.cfg.bridge_fee_url_template)
        query = urllib.parse.urlencode(
            {
                "fromChain": str(buy_chain_id),
                "toChain": str(sell_chain_id),
                "fromToken": str(from_token["address"]),
                "toToken": str(to_token["address"]),
                "fromAddress": "0x000000000000000000000000000000000000dEaD",
                "toAddress": "0x000000000000000000000000000000000000dEaD",
                "fromAmount": str(from_amount),
            }
        )
        try:
            payload = _http_get_json(f"{base_url}/quote?{query}")
        except OSError as exc:
            stale = self._stale_value(self._bridge_fee_cache, cache_key)
            if stale is not None and self._is_rate_limited(exc):
                return stale
            raise
        return self._remember(self._bridge_fee_cache, cache_key, _lifi_same_token_fee_usd(payload))

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
