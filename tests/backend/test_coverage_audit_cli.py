from __future__ import annotations

import sys
import tempfile
import unittest
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools.coverage_audit import main
from unittest import mock


class CoverageAuditCliTests(unittest.TestCase):
    def test_bounded_live_reports_mark_every_uncontacted_row_not_checked_in_json_and_csv(self) -> None:
        class Response:
            text = "ok"

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "tools.coverage_audit.CouncilHttpClient.get", return_value=Response()
        ):
            json_report = Path(directory) / "live.json"
            csv_report = Path(directory) / "live.csv"
            self.assertEqual(0, main(["--live-smoke", "--limit", "1", "--format", "json", "--report", str(json_report)]))
            self.assertEqual(0, main(["--live-smoke", "--limit", "1", "--format", "csv", "--report", str(csv_report)]))
            json_rows = json.loads(json_report.read_text(encoding="utf-8"))
            with csv_report.open(encoding="utf-8") as csv_file:
                csv_rows = list(csv.DictReader(csv_file))
            for rows in (json_rows, csv_rows):
                self.assertEqual(1, sum(row["result"] == "pass" for row in rows))
                self.assertGreater(sum(row["result"] == "not_checked" for row in rows), 0)
                self.assertEqual(0, sum(row["result"] == "fail" for row in rows))

    def test_offline_audit_writes_json_or_csv_without_live_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            json_report = Path(directory) / "audit.json"
            csv_report = Path(directory) / "audit.csv"

            self.assertEqual(0, main(["--format", "json", "--report", str(json_report)]))
            self.assertEqual(0, main(["--format", "csv", "--report", str(csv_report)]))

            self.assertTrue(json_report.read_text(encoding="utf-8").lstrip().startswith("["))
            self.assertTrue(csv_report.read_text(encoding="utf-8").startswith("authority,family,endpoint,checked_at,result,error"))


if __name__ == "__main__":
    unittest.main()
