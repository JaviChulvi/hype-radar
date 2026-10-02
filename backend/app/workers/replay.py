import argparse
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.domain.markets import MarketIdentity
from app.domain.observations import MarketObservation, Metric, OracleRegime
from app.engine import ObservationWindowStore, RuleEngine, RuleRuntimeState
from app.persistence.serialization import rule_from_dict


def run_replay(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    rule = rule_from_dict(payload["rule"])
    windows = ObservationWindowStore()
    engine = RuleEngine(windows)
    runtime = RuleRuntimeState(rule.id)
    events = []

    for item in payload["observations"]:
        observation = _observation(item)
        if observation.market != rule.market:
            raise ValueError("Replay observation does not match the rule market")
        windows.add(observation)
        outcome = engine.evaluate(rule, runtime, evaluated_at=observation.received_at)
        runtime = outcome.runtime
        if outcome.event is not None:
            events.append(
                {
                    "id": str(outcome.event.id),
                    "status": outcome.event.status.value,
                    "condition": outcome.event.condition.value,
                    "quality": outcome.event.quality.value,
                    "fingerprint": outcome.event.fingerprint,
                    "evaluated_at": outcome.event.evaluated_at.isoformat(),
                }
            )

    expected = payload.get("expected_event_statuses")
    actual = [event["status"] for event in events]
    if expected is not None and actual != expected:
        raise AssertionError(f"Expected event statuses {expected}, received {actual}")
    return events


def _observation(data: dict) -> MarketObservation:
    market = MarketIdentity(**data["market"])
    return MarketObservation(
        market=market,
        source=data.get("source", "replay"),
        channel=data.get("channel", "fixture"),
        observed_at=datetime.fromisoformat(data["observed_at"]),
        received_at=datetime.fromisoformat(data.get("received_at", data["observed_at"])),
        values={Metric(metric): Decimal(value) for metric, value in data["values"].items()},
        gap=data.get("gap", False),
        out_of_order=data.get("out_of_order", False),
        oracle_regime=OracleRegime(data.get("oracle_regime", "unverified")),
        raw_payload=data,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay deterministic market observations")
    parser.add_argument("fixture", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(run_replay(arguments.fixture), indent=2))


if __name__ == "__main__":
    main()
