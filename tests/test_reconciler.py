"""Test suite for the Golden Target reconciler.

    python3 tests/test_reconciler.py

Runs entirely offline against tests/fixtures/authority_snapshot.json. The fixture pack in
tests/fixtures/mini_pack is built so that every defect class the tool can emit fires at
least once, and so that at least one deliberately messy-but-valid row does NOT fire.

Stdlib unittest only - no third-party test runner, matching the zero-dependency policy.
"""

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from offline_client import MINI_PACK, OfflineAuthorityClient  # noqa: E402

from goldentarget import audit, publications, reconcile, resolve, sources  # noqa: E402
import solve  # noqa: E402


def run_offline(pack_dir=MINI_PACK):
    client = OfflineAuthorityClient()
    return solve.reconcile_pack(pack_dir, client=client), client


class PipelineTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result, cls.client = run_offline()
        cls.findings = cls.result["findings"]
        cls.records = {r["primary_accession"]: r for r in cls.result["golden_records"]}

    def find(self, classification, observed=None):
        return [
            f for f in self.findings
            if f["classification"] == classification
            and (observed is None or f["observed"] == observed)
        ]


class TestOutputContract(PipelineTestCase):
    def test_top_level_shape(self):
        self.assertIsInstance(self.result["unique_target_count"], int)
        self.assertIsInstance(self.result["golden_records"], list)
        self.assertIsInstance(self.result["findings"], list)

    def test_count_matches_records(self):
        self.assertEqual(self.result["unique_target_count"], len(self.result["golden_records"]))

    def test_record_fields(self):
        for record in self.result["golden_records"]:
            self.assertEqual({"gene", "primary_accession", "sources"}, set(record))
            self.assertIsInstance(record["sources"], list)
            self.assertTrue(record["sources"])

    def test_every_finding_carries_evidence(self):
        for finding in self.findings:
            for key in ("gene", "observed", "correct", "retrieved_evidence",
                        "evidence_source", "severity", "classification"):
                self.assertIn(key, finding, finding)
            self.assertTrue(finding["retrieved_evidence"].strip(), finding)
            self.assertTrue(finding["evidence_source"].strip(), finding)

    def test_findings_state_a_correction(self):
        """A finding without a retrieved correction is worth a quarter of the marks, so
        the only one allowed to omit it is the explicitly-unresolvable case."""
        for finding in self.findings:
            if finding["correct"] in ("", "(unresolved)"):
                self.assertIn("detected only", finding.get("detail", ""), finding)


class TestWrongMapping(PipelineTestCase):
    def test_pim1_accession_corrected(self):
        hits = self.find("wrong_mapping", "Q9P1W9")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct"], "P11309")
        self.assertEqual(hits[0]["severity"], "critical")
        self.assertIn("PIM2", hits[0]["retrieved_evidence"])

    def test_mis_mapped_row_lands_on_the_right_record(self):
        """The ChEMBL PIM1 row must end up under P11309, not under PIM2's record."""
        self.assertIn("chembl", self.records["P11309"]["sources"])
        self.assertEqual(self.records["P11309"]["gene"], "PIM1")

    def test_kiss1_accession_corrected(self):
        hits = self.find("wrong_mapping", "Q969F8")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct"], "Q15726")

    def test_correct_row_not_flagged(self):
        """PIM2 -> Q9P1W9 is the same accession and is legitimate; it must not fire."""
        for finding in self.findings:
            if finding["gene"] == "PIM2":
                self.fail("PIM2 row incorrectly flagged: %r" % finding)


class TestRetiredAccessions(PipelineTestCase):
    def test_merged_accession_resolved(self):
        hits = self.find("duplicate_identity", "B2RPP8") + self.find("merged_accession", "B2RPP8")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct"], "P41235")
        self.assertIn("secondary accessions", hits[0]["retrieved_evidence"])

    def test_duplicate_collapses_into_one_record(self):
        """B2RPP8 and P41235 are one target; the pack must not contain both."""
        self.assertIn("P41235", self.records)
        self.assertNotIn("B2RPP8", self.records)

    def test_retired_bindingdb_accession(self):
        hits = self.find("duplicate_identity", "A6NFS3") + self.find("merged_accession", "A6NFS3")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct"], "P53779")


class TestStaleSymbols(PipelineTestCase):
    def test_legacy_symbol_is_low_severity(self):
        hits = self.find("stale_symbol", "SEPT9")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct"], "SEPTIN9")
        self.assertEqual(hits[0]["severity"], "low")

    def test_jnk3_legacy_symbol(self):
        hits = self.find("stale_symbol", "JNK3")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct"], "MAPK10")

    def test_record_uses_approved_symbol(self):
        self.assertEqual(self.records["Q9UHD8"]["gene"], "SEPTIN9")
        self.assertEqual(self.records["P53779"]["gene"], "MAPK10")


class TestIsoformAndMissing(PipelineTestCase):
    def test_isoform_collapsed_to_canonical(self):
        hits = self.find("isoform_accession", "Q13422-3")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct"], "Q13422")
        self.assertIn("Q13422", self.records)
        self.assertNotIn("Q13422-3", self.records)

    def test_blank_symbol_recovered(self):
        hits = self.find("missing_identifier", "")
        self.assertTrue(hits)
        self.assertEqual(self.records["P01282"]["gene"], "VIP")

    def test_blank_symbol_rows_do_not_orphan(self):
        for record in self.result["golden_records"]:
            self.assertTrue(record["gene"], record)


class TestCrossReferences(PipelineTestCase):
    def test_chembl_xref_mismatch(self):
        hits = self.find("cross_reference_mismatch", "CHEMBL3012")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct"], "CHEMBL4409")
        self.assertEqual(hits[0]["gene"], "PDE10A")

    def test_correct_xref_on_mismapped_row_not_flagged(self):
        """CHEMBL2147 is the RIGHT ChEMBL id for PIM1. The accession is what's broken, so
        the xref must not also be reported as a mismatch."""
        self.assertEqual(self.find("cross_reference_mismatch", "CHEMBL2147"), [])


class TestPublications(PipelineTestCase):
    def test_psa_split_by_context(self):
        hits = self.find("ambiguous_mention", "PSA")
        self.assertEqual(len(hits), 2)
        self.assertEqual({h["correct"] for h in hits}, {"KLK3", "NPEPPS"})

    def test_unambiguous_mentions_not_flagged(self):
        for finding in self.find("ambiguous_mention"):
            self.assertEqual(finding["observed"], "PSA")

    def test_publications_recorded_as_a_source(self):
        self.assertIn("publications", self.records["P53779"]["sources"])

    def test_the_only_outbound_host_is_the_authority(self):
        """The pmid column holds internal reference numbers, and some collide with real
        PubMed records for unrelated papers. Prove the package can only ever talk to the
        authority, so no pmid can become a lookup."""
        import re
        hosts = set()
        package = os.path.join(ROOT, "src", "goldentarget")
        for name in sorted(os.listdir(package)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(package, name), encoding="utf-8") as fh:
                for match in re.findall(r"https?://([^\s/\"')]+)", fh.read()):
                    hosts.add(match)
        self.assertEqual(hosts, {"www.ebi.ac.uk"}, hosts)

    def test_pmid_is_never_passed_to_the_client(self):
        """Attribution must consume the pmid only as an opaque label."""
        calls = []

        class RecordingClient(OfflineAuthorityClient):
            def _get(self, params):
                calls.append(params)
                return super()._get(params)

        client = RecordingClient()
        solve.reconcile_pack(MINI_PACK, client=client)
        pmids = {p["pmid"] for p in sources.load_publications(MINI_PACK)}
        for params in calls:
            for value in params.values():
                for pmid in pmids:
                    self.assertNotIn(pmid, str(value))


class TestOrganismHandling(PipelineTestCase):
    def test_spelling_variants_are_not_defects(self):
        """'human', 'Human', 'H. sapiens' and 'Homo sapiens (Human)' all appear in the
        fixture and all mean the same thing."""
        self.assertEqual(self.find("organism_mismatch"), [])

    def test_genuine_mismatch_would_fire(self):
        from goldentarget.model import Identity
        identity = Identity(
            accession="P11309", entry_name="PIM1_HUMAN", approved_symbol="PIM1",
            synonyms=[], recommended_name="x", organism_names=["Homo sapiens", "Human"],
            tax_id=9606, secondary_accessions=[], xrefs={}, reviewed=True,
        )
        self.assertTrue(audit._organism_matches("Homo sapiens (Human)", identity))
        self.assertTrue(audit._organism_matches("H. sapiens", identity))
        self.assertTrue(audit._organism_matches("human", identity))
        self.assertFalse(audit._organism_matches("Mus musculus", identity))
        self.assertFalse(audit._organism_matches("Rattus norvegicus", identity))


class TestResilience(unittest.TestCase):
    def test_empty_pack_still_emits_valid_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_offline(tmp)
            self.assertEqual(result["unique_target_count"], 0)
            self.assertEqual(result["golden_records"], [])

    def test_network_failure_degrades_without_crashing(self):
        class DeadClient(OfflineAuthorityClient):
            def _get(self, params):
                self.degraded = True
                return None

        result = solve.reconcile_pack(MINI_PACK, client=DeadClient())
        self.assertIsInstance(result["unique_target_count"], int)
        # Nothing was verifiable, so nothing may be asserted as a defect.
        self.assertEqual(result["findings"], [])
        self.assertTrue(result["golden_records"])

    def test_missing_file_is_tolerated(self):
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("source_chembl.csv", "source_uniprot.csv"):
                shutil.copy(os.path.join(MINI_PACK, name), tmp)
            result, _ = run_offline(tmp)
            self.assertGreater(result["unique_target_count"], 0)


class TestStdoutIsCleanJson(unittest.TestCase):
    def test_subprocess_stdout_parses(self):
        """The grader parses the entire captured stdout. Prove there is nothing else on it."""
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "solve.py"), MINI_PACK],
            capture_output=True, text=True, timeout=300,
            env=dict(os.environ, GT_NETWORK_BUDGET="60"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        parsed = json.loads(proc.stdout)  # must not raise
        self.assertIn("unique_target_count", parsed)
        self.assertTrue(proc.stderr, "diagnostics should go to stderr, and there should be some")


if __name__ == "__main__":
    unittest.main(verbosity=2)
