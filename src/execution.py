from __future__ import annotations

import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from web3 import Web3

from src.config import ExecutionConfig, V2PoolConfig
from src.price_types import ArbitrageOpportunity

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
]

UNISWAP_V2_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

UNISWAP_V2_PAIR_TOKEN_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
]

DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"


class ExecutionError(Exception):
    pass


@dataclass(frozen=True)
class SwapPlan:
    chain: str
    dex: str
    amount_in: int
    amount_out_estimate: int
    amount_out_min: int
    token_in_symbol: str
    token_out_symbol: str
    path: list[str]


@dataclass(frozen=True)
class BridgePlan:
    from_chain: str
    to_chain: str
    amount_in: int
    amount_out_estimate: int
    amount_out_min: int
    token_symbol: str
    approval_address: str
    transaction: dict[str, Any]
    tool: str


@dataclass(frozen=True)
class ExecutionPlan:
    opportunity: ArbitrageOpportunity
    buy_swap: SwapPlan
    bridge: BridgePlan
    sell_swap: SwapPlan


@dataclass(frozen=True)
class ExecutionResult:
    buy_tx_hash: str
    bridge_tx_hash: str
    sell_tx_hash: str
    bridged_amount: int
    quote_token_received: int
    buy_receipt: dict[str, Any]
    bridge_receipt: dict[str, Any]
    sell_receipt: dict[str, Any]


def _http_get_json(url: str, timeout_sec: int = 15) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "trade-bot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        import json

        return json.loads(resp.read().decode("utf-8"))


def _checksum(address: str) -> str:
    return Web3.to_checksum_address(address)


def _to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ExecutionError(f"Cannot convert value to int: {value!r}")


def _extract_nested(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = payload
        ok = True
        for key in path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                ok = False
                break
        if ok:
            return current
    raise ExecutionError(f"Missing field in payload. Tried paths: {paths!r}")


class ArbitrageExecutor:
    def __init__(
        self,
        chain_web3: dict[str, Web3],
        execution_cfg: ExecutionConfig,
    ) -> None:
        self.chain_web3 = chain_web3
        self.execution_cfg = execution_cfg
        self.account = None
        if execution_cfg.wallet_private_key:
            self.account = Web3().eth.account.from_key(execution_cfg.wallet_private_key)

    @property
    def quote_address(self) -> str:
        if self.execution_cfg.wallet_address:
            return _checksum(self.execution_cfg.wallet_address)
        if self.account:
            return self.account.address
        return _checksum(DEAD_ADDRESS)

    @property
    def wallet_address(self) -> str:
        if not self.execution_cfg.wallet_private_key and not self.execution_cfg.wallet_address:
            raise ExecutionError("Missing EXECUTION_WALLET_PRIVATE_KEY or EXECUTION_WALLET_ADDRESS")
        return self.quote_address

    def validate_pool(self, pool: V2PoolConfig) -> None:
        if not pool.router_address:
            raise ExecutionError(f"Pool {pool.chain}:{pool.dex}:{pool.pair_key} is missing router_address")

    def validate_wallet(self) -> None:
        if not self.execution_cfg.wallet_private_key:
            raise ExecutionError("Missing EXECUTION_WALLET_PRIVATE_KEY")
        _ = self.wallet_address

    def _w3(self, chain: str) -> Web3:
        try:
            return self.chain_web3[chain]
        except KeyError as exc:
            raise ExecutionError(f"Missing Web3 client for chain '{chain}'") from exc

    def _erc20(self, chain: str, token_address: str):
        return self._w3(chain).eth.contract(address=_checksum(token_address), abi=ERC20_ABI)

    def _router(self, chain: str, router_address: str):
        return self._w3(chain).eth.contract(address=_checksum(router_address), abi=UNISWAP_V2_ROUTER_ABI)

    def _pair(self, chain: str, pool_address: str):
        return self._w3(chain).eth.contract(address=_checksum(pool_address), abi=UNISWAP_V2_PAIR_TOKEN_ABI)

    def _token_addresses(self, pool: V2PoolConfig) -> tuple[str, str]:
        if pool.token0_address and pool.token1_address:
            return _checksum(pool.token0_address), _checksum(pool.token1_address)
        pair = self._pair(pool.chain, pool.pool_address)
        return (
            _checksum(str(pair.functions.token0().call())),
            _checksum(str(pair.functions.token1().call())),
        )

    def _quote_amount_to_units(self, pool: V2PoolConfig, amount_quote: float) -> int:
        return int(amount_quote * (10 ** pool.token1_decimals))

    def _apply_slippage(self, amount: int) -> int:
        return max(1, amount * (10_000 - self.execution_cfg.slippage_bps) // 10_000)

    def _native_balance(self, chain: str) -> float:
        w3 = self._w3(chain)
        balance_wei = w3.eth.get_balance(self.wallet_address)
        return balance_wei / 1e18

    def validate_native_balances(self, *chains: str) -> None:
        for chain in chains:
            balance = self._native_balance(chain)
            if balance < self.execution_cfg.min_native_balance:
                raise ExecutionError(
                    f"Insufficient native balance on {chain}: {balance:.6f} < {self.execution_cfg.min_native_balance:.6f}"
                )

    def erc20_balance(self, chain: str, token_address: str) -> int:
        token = self._erc20(chain, token_address)
        return int(token.functions.balanceOf(self.wallet_address).call())

    def _next_tx_params(self, chain: str) -> dict[str, Any]:
        w3 = self._w3(chain)
        return {
            "from": self.wallet_address,
            "nonce": w3.eth.get_transaction_count(self.wallet_address),
            "chainId": w3.eth.chain_id,
            "gasPrice": int(w3.eth.gas_price),
        }

    def _estimate_and_fill_gas(self, chain: str, tx: dict[str, Any]) -> dict[str, Any]:
        w3 = self._w3(chain)
        tx = dict(tx)
        if "gas" not in tx:
            gas_estimate = int(w3.eth.estimate_gas(tx))
            tx["gas"] = max(21_000, gas_estimate * 12 // 10)
        return tx

    def _sign_and_send(self, chain: str, tx: dict[str, Any]) -> str:
        self.validate_wallet()
        tx = self._estimate_and_fill_gas(chain, tx)
        signed = self._w3(chain).eth.account.sign_transaction(tx, self.execution_cfg.wallet_private_key)
        tx_hash = self._w3(chain).eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    def wait_for_receipt(self, chain: str, tx_hash: str) -> dict[str, Any]:
        receipt = self._w3(chain).eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=self.execution_cfg.receipt_timeout_sec,
        )
        if int(receipt["status"]) != 1:
            raise ExecutionError(f"Transaction failed on {chain}: {tx_hash}")
        return {
            "transactionHash": receipt["transactionHash"].hex(),
            "blockHash": receipt["blockHash"].hex(),
            "blockNumber": int(receipt["blockNumber"]),
            "status": int(receipt["status"]),
            "gasUsed": int(receipt["gasUsed"]),
            "cumulativeGasUsed": int(receipt["cumulativeGasUsed"]),
            "effectiveGasPrice": int(receipt.get("effectiveGasPrice", 0)),
            "from": receipt["from"],
            "to": receipt["to"],
        }

    def ensure_approval(self, chain: str, token_address: str, spender: str, min_amount: int) -> str | None:
        token = self._erc20(chain, token_address)
        allowance = int(token.functions.allowance(self.wallet_address, _checksum(spender)).call())
        if allowance >= min_amount:
            return None

        tx = token.functions.approve(
            _checksum(spender),
            2**256 - 1,
        ).build_transaction(self._next_tx_params(chain))
        tx_hash = self._sign_and_send(chain, tx)
        self.wait_for_receipt(chain, tx_hash)
        return tx_hash

    def build_buy_swap_plan(self, pool: V2PoolConfig, opportunity: ArbitrageOpportunity) -> SwapPlan:
        self.validate_pool(pool)
        router = self._router(pool.chain, pool.router_address)
        amount_in = self._quote_amount_to_units(pool, opportunity.volume)
        token0_address, token1_address = self._token_addresses(pool)
        path = [token1_address, token0_address]
        amounts = router.functions.getAmountsOut(amount_in, path).call()
        amount_out_estimate = int(amounts[-1])
        return SwapPlan(
            chain=pool.chain,
            dex=pool.dex,
            amount_in=amount_in,
            amount_out_estimate=amount_out_estimate,
            amount_out_min=self._apply_slippage(amount_out_estimate),
            token_in_symbol=pool.token1_symbol,
            token_out_symbol=pool.token0_symbol,
            path=path,
        )

    def build_sell_swap_plan(self, pool: V2PoolConfig, token0_amount: int) -> SwapPlan:
        self.validate_pool(pool)
        router = self._router(pool.chain, pool.router_address)
        token0_address, token1_address = self._token_addresses(pool)
        path = [token0_address, token1_address]
        amounts = router.functions.getAmountsOut(token0_amount, path).call()
        amount_out_estimate = int(amounts[-1])
        return SwapPlan(
            chain=pool.chain,
            dex=pool.dex,
            amount_in=token0_amount,
            amount_out_estimate=amount_out_estimate,
            amount_out_min=self._apply_slippage(amount_out_estimate),
            token_in_symbol=pool.token0_symbol,
            token_out_symbol=pool.token1_symbol,
            path=path,
        )

    def _build_swap_tx(self, plan: SwapPlan, router_address: str) -> dict[str, Any]:
        router = self._router(plan.chain, router_address)
        deadline = int(time.time()) + self.execution_cfg.deadline_seconds
        return router.functions.swapExactTokensForTokens(
            plan.amount_in,
            plan.amount_out_min,
            plan.path,
            self.wallet_address,
            deadline,
        ).build_transaction(self._next_tx_params(plan.chain))

    def _lifi_quote(self, from_chain: str, to_chain: str, from_token: str, to_token: str, amount_in: int) -> dict[str, Any]:
        from_chain_id = self._w3(from_chain).eth.chain_id
        to_chain_id = self._w3(to_chain).eth.chain_id
        params = {
            "fromChain": from_chain_id,
            "toChain": to_chain_id,
            "fromToken": _checksum(from_token),
            "toToken": _checksum(to_token),
            "fromAmount": str(amount_in),
            "fromAddress": self.quote_address,
            "toAddress": self.quote_address,
            "slippage": f"{self.execution_cfg.slippage_bps / 10_000:.4f}",
        }
        query = urllib.parse.urlencode(params)
        url = f"{self.execution_cfg.lifi_base_url.rstrip('/')}/quote?{query}"
        payload = _http_get_json(url)
        if isinstance(payload, dict) and payload.get("message"):
            raise ExecutionError(f"LI.FI quote failed: {payload['message']}")
        return payload

    def build_bridge_plan(
        self,
        from_pool: V2PoolConfig,
        to_pool: V2PoolConfig,
        amount_in: int,
    ) -> BridgePlan:
        payload = self._lifi_quote(
            from_chain=from_pool.chain,
            to_chain=to_pool.chain,
            from_token=self._token_addresses(from_pool)[0],
            to_token=self._token_addresses(to_pool)[0],
            amount_in=amount_in,
        )
        transaction_request = _extract_nested(payload, ("transactionRequest",), ("step", "transactionRequest"))
        approval_address = _extract_nested(
            payload,
            ("estimate", "approvalAddress"),
            ("approvalAddress",),
            ("step", "estimate", "approvalAddress"),
        )
        tool = str(_extract_nested(payload, ("tool",), ("step", "tool")))
        amount_out_estimate = int(_extract_nested(payload, ("estimate", "toAmount"), ("step", "estimate", "toAmount")))
        amount_out_min = int(
            _extract_nested(payload, ("estimate", "toAmountMin"), ("step", "estimate", "toAmountMin"))
        )

        tx = {
            "from": self.quote_address,
            "to": _checksum(str(transaction_request["to"])),
            "data": str(transaction_request["data"]),
            "value": _to_int(transaction_request.get("value", "0")),
            "nonce": self._w3(from_pool.chain).eth.get_transaction_count(self.quote_address),
            "chainId": self._w3(from_pool.chain).eth.chain_id,
            "gasPrice": int(self._w3(from_pool.chain).eth.gas_price),
        }
        if "gasLimit" in transaction_request:
            tx["gas"] = _to_int(transaction_request["gasLimit"])

        return BridgePlan(
            from_chain=from_pool.chain,
            to_chain=to_pool.chain,
            amount_in=amount_in,
            amount_out_estimate=amount_out_estimate,
            amount_out_min=amount_out_min,
            token_symbol=from_pool.token0_symbol,
            approval_address=_checksum(str(approval_address)),
            transaction=tx,
            tool=tool,
        )

    def plan_execution(
        self,
        opportunity: ArbitrageOpportunity,
        buy_pool: V2PoolConfig,
        sell_pool: V2PoolConfig,
    ) -> ExecutionPlan:
        buy_swap = self.build_buy_swap_plan(buy_pool, opportunity)
        bridge = self.build_bridge_plan(buy_pool, sell_pool, buy_swap.amount_out_estimate)
        sell_swap = self.build_sell_swap_plan(sell_pool, bridge.amount_out_estimate)
        return ExecutionPlan(
            opportunity=opportunity,
            buy_swap=buy_swap,
            bridge=bridge,
            sell_swap=sell_swap,
        )

    def _wait_for_balance_increase(self, chain: str, token_address: str, starting_balance: int) -> int:
        deadline = time.time() + self.execution_cfg.bridge_timeout_sec
        while time.time() < deadline:
            current_balance = self.erc20_balance(chain, token_address)
            if current_balance > starting_balance:
                return current_balance - starting_balance
            time.sleep(self.execution_cfg.bridge_poll_interval_sec)
        raise ExecutionError(f"Timed out waiting for bridged funds on {chain}")

    def execute(
        self,
        opportunity: ArbitrageOpportunity,
        buy_pool: V2PoolConfig,
        sell_pool: V2PoolConfig,
    ) -> ExecutionResult:
        self.validate_wallet()
        self.validate_pool(buy_pool)
        self.validate_pool(sell_pool)
        self.validate_native_balances(buy_pool.chain, sell_pool.chain)

        buy_token0_address, buy_token1_address = self._token_addresses(buy_pool)
        sell_token0_address, sell_token1_address = self._token_addresses(sell_pool)
        initial_buy_token0 = self.erc20_balance(buy_pool.chain, buy_token0_address)
        initial_sell_token0 = self.erc20_balance(sell_pool.chain, sell_token0_address)

        buy_plan = self.build_buy_swap_plan(buy_pool, opportunity)
        self.ensure_approval(buy_pool.chain, buy_token1_address, buy_pool.router_address, buy_plan.amount_in)
        buy_tx_hash = self._sign_and_send(
            buy_pool.chain,
            self._build_swap_tx(buy_plan, buy_pool.router_address),
        )
        buy_receipt = self.wait_for_receipt(buy_pool.chain, buy_tx_hash)

        bought_amount = self.erc20_balance(buy_pool.chain, buy_token0_address) - initial_buy_token0
        if bought_amount <= 0:
            raise ExecutionError(f"Buy swap produced no {buy_pool.token0_symbol} on {buy_pool.chain}")

        bridge_plan = self.build_bridge_plan(buy_pool, sell_pool, bought_amount)
        self.ensure_approval(
            buy_pool.chain,
            buy_token0_address,
            bridge_plan.approval_address,
            bridge_plan.amount_in,
        )
        bridge_tx_hash = self._sign_and_send(buy_pool.chain, bridge_plan.transaction)
        bridge_receipt = self.wait_for_receipt(buy_pool.chain, bridge_tx_hash)

        bridged_amount = self._wait_for_balance_increase(
            sell_pool.chain,
            sell_token0_address,
            initial_sell_token0,
        )

        sell_plan = self.build_sell_swap_plan(sell_pool, bridged_amount)
        self.ensure_approval(sell_pool.chain, sell_token0_address, sell_pool.router_address, sell_plan.amount_in)
        initial_quote_balance = self.erc20_balance(sell_pool.chain, sell_token1_address)
        sell_tx_hash = self._sign_and_send(
            sell_pool.chain,
            self._build_swap_tx(sell_plan, sell_pool.router_address),
        )
        sell_receipt = self.wait_for_receipt(sell_pool.chain, sell_tx_hash)
        final_quote_balance = self.erc20_balance(sell_pool.chain, sell_token1_address)

        return ExecutionResult(
            buy_tx_hash=buy_tx_hash,
            bridge_tx_hash=bridge_tx_hash,
            sell_tx_hash=sell_tx_hash,
            bridged_amount=bridged_amount,
            quote_token_received=max(0, final_quote_balance - initial_quote_balance),
            buy_receipt=buy_receipt,
            bridge_receipt=bridge_receipt,
            sell_receipt=sell_receipt,
        )
