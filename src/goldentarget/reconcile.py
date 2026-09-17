"""Assemble golden records - one per real target identity.

The key is the canonical primary accession *after* the authority has been consulted, not
the gene symbol as written. That single choice is what makes retired accessions collapse
into the record they belong to instead of splitting one target across two rows, and what
lets a row with a blank gene symbol still land on the right record.

Rows the authority could not resolve are not thrown away. They fall back to a key built
from whatever the file gave us, so a network failure degrades the output rather than
silently shrinking the target count.
"""

from typing import Dict, List

from .resolve import Resolution


def build_golden_records(resolutions: List[Resolution], attributions=None):
    """Returns (golden_records, identities_by_accession, row_symbols_by_accession)."""
    attributions = attributions or {}

    records: Dict[str, dict] = {}
    identities: Dict[str, object] = {}
    row_symbols: Dict[str, set] = {}

    for resolution in resolutions:
        row = resolution.row
        identity = resolution.identity

        if identity is not None:
            key = identity.accession
            identities.setdefault(key, identity)
            gene = identity.approved_symbol or row.gene or ""
            accession = identity.accession
        else:
            # Unresolved: fall back to the file's own values so the row still counts.
            key = "unresolved:%s" % (row.accession or row.gene or "row-%s-%d" % (row.source, row.index))
            gene = row.gene or ""
            accession = row.accession or ""

        record = records.setdefault(
            key, {"gene": gene, "primary_accession": accession, "sources": set()}
        )
        record["sources"].add(row.source)
        # An approved symbol always wins over a blank or legacy one.
        if not record["gene"] and gene:
            record["gene"] = gene
        if identity is not None and identity.approved_symbol:
            record["gene"] = identity.approved_symbol

        if row.gene:
            row_symbols.setdefault(key, set()).add(row.gene)

    for accession, pmids in attributions.items():
        if accession in records and pmids:
            records[accession]["sources"].add("publications")

    golden = [
        {
            "gene": record["gene"],
            "primary_accession": record["primary_accession"],
            "sources": sorted(record["sources"]),
        }
        for record in records.values()
    ]
    golden.sort(key=lambda r: (r["gene"] or "~", r["primary_accession"]))
    return golden, identities, row_symbols
