import unittest
from pathlib import Path

from app.workers.replay import run_replay


class ReplayTests(unittest.TestCase):
    def test_versioned_fixture_produces_expected_event(self):
        fixture = Path(__file__).parent / "fixtures" / "hype_breakout.json"

        first = run_replay(fixture)
        second = run_replay(fixture)

        self.assertEqual(first, second)
        self.assertEqual([event["status"] for event in first], ["confirmed"])


if __name__ == "__main__":
    unittest.main()
