from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.markets import MarketIdentity
from app.domain.observations import MarketObservation, Metric, OracleRegime


class HyperliquidNormalizer:
    def asset_context(
        self,
        market: MarketIdentity,
        payload: Mapping[str, Any],
        observed_at: datetime,
        received_at: datetime,
        oracle_regime: OracleRegime = OracleRegime.UNVERIFIED,
        gap: bool = False,
    ) -> MarketObservation:
        fields = {
            Metric.MARK_PRICE: "markPx",
            Metric.ORACLE_PRICE: "oraclePx",
            Metric.OPEN_INTEREST: "openInterest",
            Metric.FUNDING_RATE: "funding",
        }
        values = {
            metric: self._decimal(payload[field], field)
            for metric, field in fields.items()
            if payload.get(field) is not None
        }
        if not values:
            raise ValueError("Asset context does not contain supported market values")
        return MarketObservation(
            market=market,
            source="hyperliquid",
            channel="activeAssetCtx",
            observed_at=observed_at,
            received_at=received_at,
            values=values,
            gap=gap,
            oracle_regime=oracle_regime,
            raw_payload=payload,
        )

    def order_book(
        self,
        market: MarketIdentity,
        levels: Sequence[Sequence[Mapping[str, Any]]],
        observed_at: datetime,
        received_at: datetime,
        gap: bool = False,
    ) -> MarketObservation:
        if len(levels) != 2:
            raise ValueError("Order book must contain bid and ask sides")
        bids, asks = levels
        values = {}
        if bids:
            values[Metric.BID_PRICE] = self._decimal(bids[0]["px"], "bid price")
            values[Metric.BID_DEPTH] = sum(
                (self._decimal(level["px"], "bid price") * self._decimal(level["sz"], "bid size") for level in bids),
                Decimal(0),
            )
        if asks:
            values[Metric.ASK_PRICE] = self._decimal(asks[0]["px"], "ask price")
            values[Metric.ASK_DEPTH] = sum(
                (self._decimal(level["px"], "ask price") * self._decimal(level["sz"], "ask size") for level in asks),
                Decimal(0),
            )
        if bids and asks:
            values[Metric.MID_PRICE] = (values[Metric.BID_PRICE] + values[Metric.ASK_PRICE]) / Decimal(2)
        return MarketObservation(
            market=market,
            source="hyperliquid",
            channel="l2Book",
            observed_at=observed_at,
            received_at=received_at,
            values=values,
            gap=gap,
            raw_payload={"levels": levels},
        )

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError(f"Invalid {field}") from error
        if not result.is_finite():
            raise ValueError(f"Invalid {field}")
        return result
