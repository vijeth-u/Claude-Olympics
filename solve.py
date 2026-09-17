#!/usr/bin/env python3
"""Golden Target reconciliation tool.

    python3 solve.py <pack_dir>

Prints exactly one JSON object on stdout:

    {"unique_target_count": int,
     "golden_records": [{"gene", "primary_accession", "sources"}],
     "findings":       [{"gene", "observed", "correct", "retrieved_evidence",
                         "evidence_source", "severity", "classification", ...}]}

Diagnostics go to stderr. stdout carries the JSON and nothing else - the grader parses
the entire captured stream as one value, so a stray print here is a zero, not a partial.
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(HERE, "src") not in sys.path:
    sys.path.insert(0, os.path.join(HERE, "src"))

from goldentarget import audit, authority, publications, reconcile, resolve, sources  # noqa: E402

# The graded budget is 5 minutes per pack. We stop talking to the network well before
# that and finish with whatever we have, because a late answer scores the same as none.
NETWORK_BUDGET_SECONDS = float(os.environ.get("GT_NETWORK_BUDGET", "210"))
DEFAULT_TAX = "9606"


def log(message):
    print(message, file=sys.stderr)


def reconcile_pack(pack_dir, network_budget=NETWORK_BUDGET_SECONDS, client=None):
    started = time.time()

    rows = sources.load_rows(pack_dir)
    pubs = sources.load_publications(pack_dir)
    log("loaded %d structured rows, %d literature mentions" % (len(rows), len(pubs)))

    if client is None:
        client = authority.AuthorityClient(deadline=started + network_budget)

    resolutions = resolve.resolve_rows(rows, client, default_tax=DEFAULT_TAX)
    resolved = sum(1 for r in resolutions if r.identity is not None)
    log("resolved %d/%d rows against the authority" % (resolved, len(resolutions)))

    # Audit BEFORE assembling records: the audit proves which rows are mis-mapped and
    # re-points them at the protein they actually describe, so the corrections are
    # reflected in the golden records rather than only reported alongside them.
    findings = audit.run_audit(resolutions, client, default_tax=DEFAULT_TAX)

    golden, identities, row_symbols = reconcile.build_golden_records(resolutions)

    alias_index = publications.build_alias_index(identities, row_symbols)
    attributions, pub_findings = publications.attribute(
        pubs, identities, alias_index, client, default_tax=DEFAULT_TAX
    )
    attributed = sum(len(v) for v in attributions.values())
    log("attributed %d literature mentions to %d targets" % (attributed, len(attributions)))

    # Fold literature provenance into the records we already built.
    by_accession = {record["primary_accession"]: record for record in golden}
    for accession, pmids in attributions.items():
        record = by_accession.get(accession)
        if record is not None and pmids and "publications" not in record["sources"]:
            record["sources"] = sorted(set(record["sources"]) | {"publications"})

    findings.extend(pub_findings)
    findings = audit.merge_findings(findings)

    log("audit produced %d findings (%d http calls, %.1fs elapsed)"
        % (len(findings), client.calls, time.time() - started))
    if client.degraded:
        log("WARNING: authority calls degraded; some rows may be unverified")

    return {
        "unique_target_count": len(golden),
        "golden_records": golden,
        "findings": [finding.to_json() for finding in findings],
    }


def main(argv):
    if len(argv) < 2:
        print(json.dumps({"unique_target_count": 0, "golden_records": [], "findings": []}))
        return 0
    pack_dir = argv[1]
    try:
        output = reconcile_pack(pack_dir)
    except Exception as exc:  # never crash: a parse failure scores the same as a crash
        log("FATAL: %r" % (exc,))
        try:
            rows = sources.load_rows(pack_dir)
            fallback = {}
            for row in rows:
                key = (row.accession or row.gene or "").upper()
                if not key:
                    continue
                record = fallback.setdefault(
                    key, {"gene": row.gene or "", "primary_accession": row.accession or "",
                          "sources": set()}
                )
                record["sources"].add(row.source)
            records = [
                {"gene": r["gene"], "primary_accession": r["primary_accession"],
                 "sources": sorted(r["sources"])}
                for r in fallback.values()
            ]
        except Exception:
            records = []
        output = {
            "unique_target_count": len(records),
            "golden_records": records,
            "findings": [],
        }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
