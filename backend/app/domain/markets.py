from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketIdentity:
    network: str
    dex: str
    coin: str

    def __post_init__(self) -> None:
        if not self.network.strip():
            raise ValueError("Market network must not be blank")
        if not self.coin.strip():
            raise ValueError("Market coin must not be blank")

    @property
    def key(self) -> str:
        dex = self.dex or "native"
        return f"{self.network}:{dex}:{self.coin}"

    @property
    def exchange_coin(self) -> str:
        return f"{self.dex}:{self.coin}" if self.dex else self.coin


# Display aliases are configuration, not a second form of market identity.
MARKETS = {
    "BTC": "BTC",
    "ETH": "ETH",
    "SP500": "xyz:SP500",
    "XYZ100": "xyz:XYZ100",
    "BRENTOIL": "xyz:BRENTOIL",
    "HYPE": "HYPE",
}
UI_MARKETS = tuple(symbol for symbol in MARKETS if symbol != "HYPE")


def resolve_market(value: str | MarketIdentity, network: str = "mainnet") -> MarketIdentity:
    if isinstance(value, MarketIdentity):
        if value.network != network:
            raise KeyError(value.key)
        value = value.exchange_coin
    coin = MARKETS.get(value, value)
    if coin not in MARKETS.values():
        raise KeyError(value)
    dex, _, name = coin.rpartition(":")
    return MarketIdentity(network, dex, name)


def market_symbol(market: MarketIdentity) -> str:
    return next(symbol for symbol, coin in MARKETS.items() if coin == market.exchange_coin)
