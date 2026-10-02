import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.persistence import SqlAlchemyUnitOfWork, create_engine, create_session_factory
from app.persistence.serialization import rule_from_dict


async def seed(path: Path) -> None:
    payload = json.loads(path.read_text())
    rule = rule_from_dict(payload.get("rule", payload))
    engine = create_engine()
    session_factory = create_session_factory(engine)
    try:
        async with SqlAlchemyUnitOfWork(session_factory) as unit_of_work:
            market = await unit_of_work.markets.ensure(rule.market)
            await unit_of_work.rules.add_version(rule, market.id, datetime.now(UTC))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Register an immutable alert rule version")
    parser.add_argument("definition", type=Path)
    arguments = parser.parse_args()
    asyncio.run(seed(arguments.definition))


if __name__ == "__main__":
    main()
