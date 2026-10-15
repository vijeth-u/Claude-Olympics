"""Contract and regression tests.  Run:  VELARA_PACK=/path/to/pack python3 -m unittest discover -s tests -t ."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.environ.get("VELARA_PACK", os.path.join(ROOT, "sample_pack"))
CODES = {
    "APPROVAL_BEFORE_SUBMISSION", "APPROVAL_TARGET_NOT_ACTUAL", "BLANK_TREATED_AS_PASS",
    "COUNTRY_NAME_WHITESPACE", "CURRENCY_UNIT_MISMATCH", "DATE_MOVED_WITHOUT_RECORD",
    "DATE_PASSED_NO_REPLAN", "DATE_STORED_AS_NUMBER", "DEMAND_LOADED_TOO_LATE",
    "DEMAND_NEVER_LOADED", "DUPLICATE_ROW", "FLAG_YES_BUT_NO_DATE", "NEGATIVE_QUANTITY",
    "PLACEHOLDER_DATE_VALUE", "PLANT_CAPACITY_EXCEEDED", "ROLLUP_ANY_NOT_ALL",
    "ROW_COUNT_INCONSISTENT", "SETUP_DATE_AFTER_SHIPMENT", "SHIPMENT_BEFORE_APPROVAL",
    "SHIPMENT_DATE_MISSING", "SKU_NOT_IN_REFERENCE", "STARTING_DOSE_UNAVAILABLE",
    "STATUS_INVALID_ENTRY", "STATUS_VALUE_DRIFT", "TIMEZONE_OFFSET_DRIFT",
}


@unittest.skipUnless(os.path.exists(os.path.join(PACK, "VELARA_tracker.xlsx")), "no pack")
class Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "solve.py"), PACK],
                              capture_output=True, text=True, timeout=300, cwd="/")
        cls.out = json.loads(proc.stdout)  # the whole stdout must be one JSON value

    def test_shape(self):
        self.assertEqual(set(self.out), {"findings", "risk_ranking"})

    def test_ranking(self):
        rr = self.out["risk_ranking"]
        names = [r["country"] for r in rr]
        self.assertEqual(len(names), 30)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual([r["rank"] for r in rr], list(range(1, 31)))
        for r in rr:
            if r["slippage_days"] == 0:
                self.assertEqual((r["impact_score"], r["driver"]), (0, "NONE"))
            else:
                self.assertIn(r["driver"], CODES)
        impacts = [r["impact_score"] for r in rr]
        self.assertEqual(impacts, sorted(impacts, reverse=True))

    def test_findings(self):
        for f in self.out["findings"]:
            self.assertIn(f["what"], CODES)
            self.assertTrue(f["where"])
            self.assertNotIn(",", f["where"])
            self.assertLessEqual(len(f["proof"]), 200)

    def test_legit_lookalikes_not_flagged(self):
        by = {f["what"]: set(f["where"].split(";")) for f in self.out["findings"]}
        self.assertFalse({"Brazil", "Germany", "Greece", "China"} & by.get("APPROVAL_BEFORE_SUBMISSION", set()))
        self.assertFalse({"Brazil", "Poland", "Korea", "Croatia"} & by.get("SHIPMENT_BEFORE_APPROVAL", set()))
        self.assertFalse({"Chile", "Estonia"} & by.get("BLANK_TREATED_AS_PASS", set()))
        self.assertFalse({"Chile", "Estonia"} & by.get("DATE_MOVED_WITHOUT_RECORD", set()))
        self.assertFalse({"Malaysia", "Greece", "China"} & by.get("SETUP_DATE_AFTER_SHIPMENT", set()))
        self.assertNotIn("ROW_COUNT_INCONSISTENT", by)


if __name__ == "__main__":
    unittest.main()
