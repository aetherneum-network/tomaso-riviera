"""Paper-trading research harness on synthetic worlds (standard library only, no network, no account).

Nothing in this package can place an order: there is no connector, no client and no key. Every ledger
row is labelled ``"mode": "PAPER"``. Amounts are integers ("units" of a simulated bankroll),
probabilities and prices are integers in parts per million (ppm), ratios are exact fractions.
"""

VERSION = "2.0.0"
