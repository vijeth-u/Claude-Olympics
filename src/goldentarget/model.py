"""Core data structures shared across the reconciliation pipeline."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Severity ladder. Ordered most -> least serious; used only for sorting output.
SEVERITY_ORDER = ["critical", "high", "medium", "low"]

# Classification vocabulary. Every finding we emit uses exactly one of these.
#
#   wrong_mapping            an identifier points at a *different* protein than the row claims
#   cross_reference_mismatch an external DB id on the row belongs to a different protein
#   organism_mismatch        declared organism / tax id disagrees with the authority
#   duplicate_identity       a retired accession whose live primary is ALSO in the pack, so the
#                            naive merge yields two golden records for one real target
#   merged_accession         a retired accession whose live primary is not otherwise in the pack
#   isoform_accession        an isoform-level accession used where a primary accession belongs
#   stale_symbol             a legacy gene symbol that the authority still recognises as an alias
#   missing_identifier       a required identifier is blank but recoverable from the authority
#   ambiguous_mention        a literature symbol that maps to >1 target in this pack
CLASSIFICATIONS = (
    "wrong_mapping",
    "cross_reference_mismatch",
    "organism_mismatch",
    "duplicate_identity",
    "merged_accession",
    "isoform_accession",
    "stale_symbol",
    "missing_identifier",
    "ambiguous_mention",
)


@dataclass
class SourceRow:
    """One row of one source extract, normalised onto a common shape."""

    source: str
    index: int
    gene: Optional[str]
    accession: Optional[str]
    protein_name: Optional[str]
    organism: Optional[str]
    tax_id: Optional[str]
    external_id: Optional[str] = None
    external_db: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Identity:
    """An authoritative protein identity, as returned by the authority."""

    accession: str
    entry_name: str
    approved_symbol: Optional[str]
    synonyms: List[str]
    recommended_name: Optional[str]
    organism_names: List[str]
    tax_id: Optional[int]
    secondary_accessions: List[str]
    xrefs: Dict[str, List[str]]
    reviewed: bool
    # Symbol-like abbreviations from the protein name block (e.g. "PSA" for KLK3).
    protein_aliases: List[str] = field(default_factory=list)
    # Free-text profile used for literature disambiguation, split into weight tiers.
    profile: List[str] = field(default_factory=list)


@dataclass
class Finding:
    """A reported defect. Field names follow the evidence contract exactly."""

    gene: str
    observed: str
    correct: str
    retrieved_evidence: str
    evidence_source: str
    severity: str
    classification: str
    field_name: str = ""
    sources: List[str] = field(default_factory=list)
    occurrences: int = 1
    detail: str = ""

    def to_json(self) -> Dict[str, Any]:
        out = {
            "gene": self.gene,
            "observed": self.observed,
            "correct": self.correct,
            "retrieved_evidence": self.retrieved_evidence,
            "evidence_source": self.evidence_source,
            "severity": self.severity,
            "classification": self.classification,
        }
        if self.field_name:
            out["field"] = self.field_name
        if self.sources:
            out["sources"] = sorted(set(self.sources))
        out["occurrences"] = self.occurrences
        if self.detail:
            out["detail"] = self.detail
        return out

    @property
    def dedupe_key(self):
        return (self.classification, self.field_name, self.gene, self.observed, self.correct)
