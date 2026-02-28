from __future__ import annotations

from itertools import combinations

from src.config import ArbitrageConfig
from src.price_types import ArbitrageOpportunity, PriceSnapshot, SpreadSignal


def compute_cross_chain_spreads(snapshots: list[PriceSnapshot]) -> list[SpreadSignal]:
    by_pair: dict[str, list[PriceSnapshot]] = {}
    for snapshot in snapshots:
        by_pair.setdefault(snapshot.pair_key, []).append(snapshot)

    spreads: list[SpreadSignal] = []
    for pair_key, pair_snapshots in by_pair.items():
        # Compare the same pair across different chains only.
        for a, b in combinations(pair_snapshots, 2):
            if a.chain == b.chain:
                continue
            if b.price_token1_per_token0 == 0:
                continue

            ratio = a.price_token1_per_token0 / b.price_token1_per_token0
            spread_pct = (ratio - 1.0) * 100
            spreads.append(
                SpreadSignal(
                    timestamp=max(a.timestamp, b.timestamp),
                    pair_key=pair_key,
                    chain_a=a.chain,
                    chain_b=b.chain,
                    price_a=a.price_token1_per_token0,
                    price_b=b.price_token1_per_token0,
                    ratio_a_over_b=ratio,
                    spread_pct=spread_pct,
                )
            )

    return sorted(spreads, key=lambda x: abs(x.spread_pct), reverse=True)


def compute_arbitrage_opportunities(
    snapshots: list[PriceSnapshot],
    cfg: ArbitrageConfig,
) -> list[ArbitrageOpportunity]:
    by_pair: dict[str, list[PriceSnapshot]] = {}
    for snapshot in snapshots:
        by_pair.setdefault(snapshot.pair_key, []).append(snapshot)

    opportunities: list[ArbitrageOpportunity] = []
    for pair_key, pair_snapshots in by_pair.items():
        for a, b in combinations(pair_snapshots, 2):
            if a.chain == b.chain:
                continue

            lower = a
            higher = b
            if a.price_token1_per_token0 > b.price_token1_per_token0:
                lower = b
                higher = a

            lower_price = lower.price_token1_per_token0
            higher_price = higher.price_token1_per_token0
            if lower_price <= 0:
                continue

            difference = higher_price - lower_price
            difference_pct = (difference / lower_price) * 100
            gross_profit = (difference / lower_price) * cfg.volume
            fees = cfg.fees_for_route(buy_chain=lower.chain, sell_chain=higher.chain)
            net_profit = gross_profit - fees

            if difference_pct < cfg.min_diff_pct:
                continue
            if net_profit <= 0:
                continue

            opportunities.append(
                ArbitrageOpportunity(
                    timestamp=max(a.timestamp, b.timestamp),
                    pair_key=pair_key,
                    buy_chain=lower.chain,
                    sell_chain=higher.chain,
                    buy_price=lower_price,
                    sell_price=higher_price,
                    difference=difference,
                    difference_pct=difference_pct,
                    volume=cfg.volume,
                    gross_profit=gross_profit,
                    fees=fees,
                    net_profit=net_profit,
                )
            )

    return sorted(opportunities, key=lambda x: x.net_profit, reverse=True)
