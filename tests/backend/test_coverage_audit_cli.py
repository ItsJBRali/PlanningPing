from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools.coverage_audit import main


class CoverageAuditCliTests(unittest.TestCase):
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
