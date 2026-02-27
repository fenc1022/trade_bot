from __future__ import annotations

from itertools import combinations

from src.price_types import PriceSnapshot, SpreadSignal


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
