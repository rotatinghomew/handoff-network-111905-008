import csv
from pathlib import Path
import unittest


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEADERS = {'duties': ['id', 'duty_type', 'holder', 'site', 'from_min', 'until_min', 'handoff_by'], 'availability': ['id', 'person', 'site', 'from_min', 'until_min'], 'qualifications': ['id', 'person', 'duty_type', 'from_min', 'until_min'], 'transfer_offers': ['id', 'from_person', 'to_person', 'site', 'from_min', 'until_min', 'mins', 'supervisor'], 'locks': ['id', 'person', 'duty', 'from_min', 'until_min', 'state'], 'proposals': ['id', 'duty', 'offer', 'deadline', 'status'], 'receipts': ['id', 'proposal', 'side', 'state', 'minute'], 'scenarios': ['id', 'kind', 'origin_ref', 'from_min', 'until_min']}
LINKS = [('proposals', 'duty', 'duties', 'id'), ('proposals', 'offer', 'transfer_offers', 'id'), ('receipts', 'proposal', 'proposals', 'id'), ('locks', 'duty', 'duties', 'id')]


def read(name):
    with (FIXTURES / (name + ".csv")).open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        return reader.fieldnames, list(reader)


class SourceRecords(unittest.TestCase):
    def test_records_match_source_columns(self):
        for name, expected in HEADERS.items():
            with self.subTest(table=name):
                header, rows = read(name)
                self.assertEqual(header, expected)
                self.assertTrue(rows)
                self.assertTrue(all(None not in row and None not in row.values() for row in rows))
                if "id" in header and (name != "receipts" or "batch" not in header):
                    ids = [row["id"] for row in rows]
                    self.assertTrue(all(ids))
                    self.assertEqual(len(ids), len(set(ids)))

    def test_record_references_exist(self):
        for source, field, target, key in LINKS:
            with self.subTest(table=source, column=field):
                _, source_rows = read(source)
                _, target_rows = read(target)
                keys = {row[key] for row in target_rows}
                self.assertTrue(all(row[field] in keys for row in source_rows))
