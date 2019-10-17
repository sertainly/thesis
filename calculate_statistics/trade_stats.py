#!/usr/bin/env python3
"""
"""
import numpy as np
import pandas as pd


def calculate_effective_statistics(
    transactions: pd.DataFrame, metainfo: pd.Series, tick_sizes: pd.DataFrame
) -> pd.DataFrame:

    transactions["effective_spread"] = 2 * np.abs(
        transactions["price"] - transactions["mid"]
    )
    transactions["relative_effective_spread_bps"] = (
        transactions["effective_spread"] / transactions["mid"]
    ) * 100

    # spread leeway using tick sizes and an unequal join
    price_decimals = 10 ** metainfo.price_decimals
    tick_sizes /= price_decimals
    # unequal join
    conditions = [
        (transactions.price.values >= step.price_start)
        & (transactions.price.values < step.price_end)
        for step in tick_sizes[["price_start", "price_end"]].itertuples()
    ]
    transactions["tick_size"] = np.piecewise(
        np.zeros(transactions.shape[0]), conditions, tick_sizes.tick_size.values
    )
    transactions["spread_leeway"] = round(
        transactions["effective_spread"] / transactions["tick_size"] - 1, 2
    )

    transactions["trade_value"] = transactions["price"] * transactions["size"]

    # group per microsecond to aggregate single trades
    grouped = transactions.groupby(["timestamp", "price"])
    aggregated = grouped[
        ["effective_spread", "relative_effective_spread_bps", "spread_leeway", "tick_size"]
    ].mean()
    aggregated["trade_value"] = grouped["trade_value"].sum()

    aggregated_statistics = aggregated.describe()
    transaction_statistics = transactions.describe()

    return (aggregated_statistics, transaction_statistics)
