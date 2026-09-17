#!/usr/bin/env python3
"""Dev-only: capture real authority responses into tests/fixtures/authority_snapshot.json.

Run this when the fixture pack changes. It is NOT used at grading time - the shipped tool
always talks to the live authority. Its only job is to let the test suite exercise the
real resolution and audit code paths deterministically, with no network.

    python3 tools/snapshot_authority.py
"""

import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "tests", "fixtures", "authority_snapshot.json")
BASE = "https://www.ebi.ac.uk/proteins/api/proteins"

ACCESSIONS = [
    "Q9P1W9", "P11309", "Q9Y233", "Q13946", "P41235", "Q13422", "Q05086",
    "P53779", "P01282", "Q9UHD8", "P55786", "P07288", "Q15726", "Q969F8",
    "A6NFS3", "B2RPP8",  # retired - expected to come back empty
]
GENE_QUERIES = ["PIM1", "JNK3", "HNF4A", "KISS1", "PSA", "SEPT9", "MAPK10", "SEPTIN9"]
PROTEIN_QUERIES = []


_KEEP_XREFS = {"GO", "ChEMBL", "HGNC", "GeneID", "BindingDB", "DrugBank"}
_KEEP_COMMENTS = {"FUNCTION", "TISSUE_SPECIFICITY", "SUBCELLULAR_LOCATION", "SUBUNIT",
                  "DISEASE", "CATALYTIC_ACTIVITY"}


def prune(entry):
    """Keep only the fields the tool actually reads - a full entry is ~90% PDB xrefs."""
    return {
        "accession": entry.get("accession"),
        "id": entry.get("id"),
        "info": entry.get("info"),
        "organism": entry.get("organism"),
        "secondaryAccession": entry.get("secondaryAccession"),
        "protein": entry.get("protein"),
        "gene": entry.get("gene"),
        "keywords": entry.get("keywords"),
        "sequence": {"length": (entry.get("sequence") or {}).get("length")},
        "comments": [c for c in entry.get("comments") or []
                     if c.get("type") in _KEEP_COMMENTS],
        "dbReferences": [d for d in entry.get("dbReferences") or []
                         if d.get("type") in _KEEP_XREFS],
    }


def fetch(params):
    url = BASE + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print("  ! %s -> %r" % (params, exc), file=sys.stderr)
        return []


def main():
    snapshot = {"accession": {}, "gene": {}, "protein": {}}

    payload = fetch({"accession": ",".join(ACCESSIONS), "size": "100"})
    for entry in payload or []:
        snapshot["accession"][entry["accession"]] = prune(entry)
    print("accessions: %d/%d live" % (len(snapshot["accession"]), len(ACCESSIONS)))

    for symbol in GENE_QUERIES:
        entries = fetch({"gene": symbol, "taxid": "9606", "reviewed": "true", "size": "20"})
        snapshot["gene"]["%s|9606" % symbol.upper()] = [prune(e) for e in entries or []]
        print("gene %s -> %d" % (symbol, len(entries or [])))

    for name in PROTEIN_QUERIES:
        entries = fetch({"protein": name, "taxid": "9606", "reviewed": "true", "size": "10"})
        snapshot["protein"]["%s|9606" % name.lower()] = [prune(e) for e in entries or []]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle)
    print("wrote %s (%.1f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
