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
