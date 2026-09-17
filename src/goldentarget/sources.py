"""Load the five source extracts and normalise them onto a common row shape.

The pack schema is fixed, but column names are matched tolerantly (case-insensitive,
first-match-wins over a list of candidates) so a cosmetic header change in the hidden
pack does not take the tool down.
"""

import csv
import os
from typing import Dict, List, Optional

from .model import SourceRow

# source label -> (filename, column map)
SOURCE_SPECS = {
    "chembl": {
        "file": "source_chembl.csv",
        "gene": ["gene_symbol", "gene_name", "gene"],
        "accession": ["accession", "uniprot_id", "uniprot_ref"],
        "protein_name": ["pref_name", "protein_name", "target_name"],
        "organism": ["organism", "species"],
        "tax_id": ["tax_id", "taxid"],
        "external_id": ["chembl_id"],
        "external_db": "ChEMBL",
    },
    "uniprot": {
        "file": "source_uniprot.csv",
        "gene": ["gene_names", "gene_symbol", "gene"],
        "accession": ["accession", "uniprot_id"],
        "protein_name": ["protein_name", "pref_name"],
        "organism": ["organism", "species"],
        "tax_id": ["tax_id", "taxid"],
        "external_id": [],
        "external_db": None,
    },
    "bindingdb": {
        "file": "source_bindingdb.csv",
        "gene": ["gene_symbol", "gene_name", "gene"],
        "accession": ["uniprot_id", "accession"],
        "protein_name": ["target_name", "protein_name"],
        "organism": ["species", "organism"],
        "tax_id": ["tax_id", "taxid"],
        "external_id": [],
        "external_db": None,
    },
    "internal": {
        "file": "source_internal.csv",
        "gene": ["gene_symbol", "gene_name", "gene"],
        "accession": ["uniprot_ref", "accession", "uniprot_id"],
        "protein_name": ["registered_name", "protein_name"],
        "organism": ["organism", "species"],
        "tax_id": ["tax_id", "taxid"],
        "external_id": ["external_id"],
        "external_db": None,  # taken per-row from source_db
    },
}

PUBLICATIONS_FILE = "source_publications.csv"


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Guard against literal null markers that show up in hand-maintained extracts.
    if text.lower() in {"na", "n/a", "nan", "none", "null", "-"}:
        return None
    return text


def _pick(row: Dict[str, str], lower_map: Dict[str, str], candidates: List[str]) -> Optional[str]:
    for name in candidates:
        key = lower_map.get(name.lower())
        if key is not None:
            value = _clean(row.get(key))
            if value is not None:
                return value
    return None


def _read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_rows(pack_dir: str) -> List[SourceRow]:
    """Return every structured row from the four identifier-bearing sources."""
    rows: List[SourceRow] = []
    for source, spec in SOURCE_SPECS.items():
        raw_rows = _read_csv(os.path.join(pack_dir, spec["file"]))
        if not raw_rows:
            continue
        lower_map = {k.lower(): k for k in raw_rows[0].keys()}
        for index, raw in enumerate(raw_rows):
            external_db = spec["external_db"]
            if source == "internal":
                external_db = _pick(raw, lower_map, ["source_db"])
            rows.append(
                SourceRow(
                    source=source,
                    index=index,
                    gene=_pick(raw, lower_map, spec["gene"]),
                    accession=_pick(raw, lower_map, spec["accession"]),
                    protein_name=_pick(raw, lower_map, spec["protein_name"]),
                    organism=_pick(raw, lower_map, spec["organism"]),
                    tax_id=_pick(raw, lower_map, spec["tax_id"]),
                    external_id=_pick(raw, lower_map, spec["external_id"]),
                    external_db=external_db,
                    raw=raw,
                )
            )
    return rows


def load_publications(pack_dir: str) -> List[Dict[str, str]]:
    """Return literature-mention rows.

    NOTE: `pmid` here is an internal reference number, not a PubMed identifier. We never
    dereference it. Ambiguity is resolved purely from `context_sentence`.
    """
    raw_rows = _read_csv(os.path.join(pack_dir, PUBLICATIONS_FILE))
    if not raw_rows:
        return []
    lower_map = {k.lower(): k for k in raw_rows[0].keys()}
    out = []
    for index, raw in enumerate(raw_rows):
        out.append(
            {
                "index": index,
                "pmid": _pick(raw, lower_map, ["pmid", "reference_id"]) or "",
                "mention": _pick(raw, lower_map, ["target_mention", "gene_symbol", "mention"]) or "",
                "context": _pick(raw, lower_map, ["context_sentence", "context", "sentence"]) or "",
            }
        )
    return out
