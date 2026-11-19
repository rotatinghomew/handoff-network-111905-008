import csv
import unittest
from pathlib import Path


class FixtureTests(unittest.TestCase):
    def test_handoffs_include_parallel_items_and_reassignment(self):
        path = Path(__file__).parents[1] / "fixtures" / "handoffs.csv"
        with path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        item_counts = {}
        kinds = set()
        for row in rows:
            item_counts.setdefault(row["case_id"], set()).add(row["work_item"])
            kinds.add(row["event_type"])
        self.assertTrue(any(len(items) > 1 for items in item_counts.values()))
        self.assertTrue({"rejected", "reassigned", "completed"} <= kinds)


if __name__ == "__main__":
    unittest.main()
