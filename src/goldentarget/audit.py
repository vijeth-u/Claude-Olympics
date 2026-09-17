"""Defect rules.

Every rule here compares a row against what the authority actually returned, and every
finding it emits carries the retrieved value. A rule that cannot state the correction is
not allowed to fire - "this looks odd" is not a finding, it is noise, and precision costs
us more than it buys.
"""

from typing import Dict, List, Optional

from . import authority as auth
from .model import SEVERITY_ORDER, Finding
from .resolve import Resolution

EVIDENCE = auth.EVIDENCE_SOURCE

# External-id columns we know how to check, mapped to the authority's xref type.
XREF_COLUMNS = {"chembl": "ChEMBL", "ChEMBL": "ChEMBL"}

_ORGANISM_ALIASES = {
    "homo sapiens": {"homo sapiens", "human", "h. sapiens", "h sapiens", "hsapiens",
                     "homo sapiens (human)"},
}


def _norm_organism(text) -> str:
    return " ".join(str(text or "").lower().replace("(", " ").replace(")", " ").split())


def _organism_matches(declared: str, identity) -> bool:
    """True when the declared organism is a recognisable spelling of the authority's.

    Sources spell the same organism four different ways ("human", "Human",
    "H. sapiens", "Homo sapiens (Human)"). All of those are the same claim and none is a
    defect, so the test is deliberately generous: the declared tokens must be a subset of
    the union of the authority's scientific and common names, or an abbreviation of one.
    A genuinely different organism fails all of these.
    """
    declared_norm = _norm_organism(declared)
    if not declared_norm:
        return True
    authoritative = {_norm_organism(n) for n in identity.organism_names if n}
    if not authoritative:
        return True
    if declared_norm in authoritative:
        return True

    union_tokens = set()
    for name in authoritative:
        union_tokens.update(name.split())
    declared_tokens = set(declared_norm.split())
    if declared_tokens and declared_tokens <= union_tokens:
        return True

    for name in authoritative:
        parts = name.split()
        if len(parts) >= 2:
            abbreviated = "%s. %s" % (parts[0][0], " ".join(parts[1:]))
            if declared_norm in (abbreviated, abbreviated.replace(". ", " ")):
                return True
    return False


def _symbol_status(symbol: str, identity) -> str:
    """'approved' | 'synonym' | 'foreign'"""
    if not symbol:
        return "approved"
    upper = symbol.upper()
    if identity.approved_symbol and upper == identity.approved_symbol.upper():
        return "approved"
    if upper in {s.upper() for s in identity.synonyms}:
        return "synonym"
    return "foreign"


def _exact_symbol_entry(client, symbol: str, tax_id: str):
    """Live entry whose *approved* symbol is exactly ``symbol``. Used to prove that the
    row's gene is real and that it is the accession, not the symbol, that is wrong."""
    if not symbol:
        return None
    matches = [
        entry
        for entry in client.search_by_gene(symbol, tax_id)
        if (auth.gene_names(entry)[0] or "").upper() == symbol.upper()
    ]
    return matches[0] if len(matches) == 1 else None


def run_audit(resolutions: List[Resolution], client, default_tax="9606") -> List[Finding]:
    findings: List[Finding] = []

    # Accessions that appear in the pack and resolve to themselves (i.e. are live).
    live_in_pack = {
        r.identity.accession
        for r in resolutions
        if r.identity and r.method in ("direct", "isoform")
    }

    for resolution in resolutions:
        row, identity = resolution.row, resolution.identity

        # --- rows we could not resolve at all -------------------------------------
        if identity is None:
            if row.accession and client.is_known_absent(row.accession):
                findings.append(
                    Finding(
                        gene=row.gene or "(unknown)",
                        observed=row.accession,
                        correct="(unresolved)",
                        retrieved_evidence=(
                            "No entry for accession %s; the authority reports it as absent "
                            "or obsolete, and no live entry lists it as a secondary accession."
                            % row.accession
                        ),
                        evidence_source=EVIDENCE,
                        severity="high",
                        classification="wrong_mapping",
                        field_name="accession",
                        sources=[row.source],
                        detail="Correction could not be retrieved; reported as detected only.",
                    )
                )
            continue

        evidence = auth.evidence_line(identity)

        # --- 1. retired accession ---------------------------------------------------
        if resolution.method == "merged":
            duplicate = identity.accession in live_in_pack
            findings.append(
                Finding(
                    gene=identity.approved_symbol or row.gene or "(unknown)",
                    observed=row.accession,
                    correct=identity.accession,
                    retrieved_evidence=(
                        "%s lists %s among its secondary accessions, so %s was merged into it. %s"
                        % (identity.accession, row.accession, row.accession, evidence)
                    ),
                    evidence_source=EVIDENCE,
                    severity="high",
                    classification="duplicate_identity" if duplicate else "merged_accession",
                    field_name="accession",
                    sources=[row.source],
                    detail=(
                        "The live primary accession is also present in this pack, so keying on "
                        "the file value would split one target into two golden records."
                        if duplicate
                        else "Retired identifier; no duplicate record results, but the value is stale."
                    ),
                )
            )
        elif resolution.method == "recovered":
            findings.append(
                Finding(
                    gene=identity.approved_symbol or row.gene or "(unknown)",
                    observed=row.accession,
                    correct=identity.accession,
                    retrieved_evidence=(
                        "Accession %s returns no entry. The row's other identifiers resolve to %s"
                        % (row.accession, evidence)
                    ),
                    evidence_source=EVIDENCE,
                    severity="high",
                    classification="wrong_mapping",
                    field_name="accession",
                    sources=[row.source],
                )
            )

        # --- 2. isoform accession used as a primary accession -----------------------
        if resolution.method == "isoform":
            findings.append(
                Finding(
                    gene=identity.approved_symbol or row.gene or "(unknown)",
                    observed=row.accession,
                    correct=identity.accession,
                    retrieved_evidence=(
                        "%s is an isoform of canonical entry %s. %s"
                        % (row.accession, identity.accession, evidence)
                    ),
                    evidence_source=EVIDENCE,
                    severity="medium",
                    classification="isoform_accession",
                    field_name="accession",
                    sources=[row.source],
                    detail="A target master keys on the canonical primary accession, not an isoform.",
                )
            )

        # --- 3. gene symbol vs authority --------------------------------------------
        if not row.gene:
            if identity.approved_symbol:
                findings.append(
                    Finding(
                        gene=identity.approved_symbol,
                        observed="",
                        correct=identity.approved_symbol,
                        retrieved_evidence="Resolved from accession %s: %s" % (row.accession, evidence),
                        evidence_source=EVIDENCE,
                        severity="medium",
                        classification="missing_identifier",
                        field_name="gene_symbol",
                        sources=[row.source],
                        detail="Row carries no gene symbol; recovered from the accession.",
                    )
                )
        else:
            status = _symbol_status(row.gene, identity)
            if status == "synonym":
                findings.append(
                    Finding(
                        gene=identity.approved_symbol or row.gene,
                        observed=row.gene,
                        correct=identity.approved_symbol or row.gene,
                        retrieved_evidence=(
                            "%s is listed as a synonym of approved symbol %s. %s"
                            % (row.gene, identity.approved_symbol, evidence)
                        ),
                        evidence_source=EVIDENCE,
                        severity="low",
                        classification="stale_symbol",
                        field_name="gene_symbol",
                        sources=[row.source],
                        detail="Legacy but still-recognised label; the identity itself is correct.",
                    )
                )
            elif status == "foreign":
                tax = row.tax_id or default_tax
                intended = _exact_symbol_entry(client, row.gene, tax)
                if intended is not None and intended.get("accession") != identity.accession:
                    intended_identity = auth.to_identity(intended)
                    # Re-point the row at the protein it actually describes, so the
                    # golden record is built from the corrected identity rather than
                    # inheriting the defect we just proved.
                    resolution.identity = intended_identity
                    resolution.method = "corrected"
                    resolution.notes.append(
                        "accession %s corrected to %s" % (row.accession, intended_identity.accession)
                    )
                    findings.append(
                        Finding(
                            gene=row.gene,
                            observed=row.accession,
                            correct=intended_identity.accession,
                            retrieved_evidence=(
                                "Accession %s resolves to %s (%s), not to %s. The approved symbol "
                                "%s resolves to %s."
                                % (
                                    row.accession,
                                    identity.entry_name,
                                    identity.approved_symbol,
                                    row.gene,
                                    row.gene,
                                    auth.evidence_line(intended_identity),
                                )
                            ),
                            evidence_source=EVIDENCE,
                            severity="critical",
                            classification="wrong_mapping",
                            field_name="accession",
                            sources=[row.source],
                            detail="The accession points at a different protein than every other field on the row.",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            gene=identity.approved_symbol or row.gene,
                            observed=row.gene,
                            correct=identity.approved_symbol or "(unresolved)",
                            retrieved_evidence=(
                                "Accession %s is neither approved as nor a synonym of %s. %s"
                                % (row.accession, row.gene, evidence)
                            ),
                            evidence_source=EVIDENCE,
                            severity="critical",
                            classification="wrong_mapping",
                            field_name="gene_symbol",
                            sources=[row.source],
                        )
                    )

        # --- 4. organism / taxonomy --------------------------------------------------
        if row.organism and not _organism_matches(row.organism, identity):
            findings.append(
                Finding(
                    gene=identity.approved_symbol or row.gene or "(unknown)",
                    observed=row.organism,
                    correct=identity.organism_names[0] if identity.organism_names else "",
                    retrieved_evidence=evidence,
                    evidence_source=EVIDENCE,
                    severity="critical",
                    classification="organism_mismatch",
                    field_name="organism",
                    sources=[row.source],
                )
            )
        if row.tax_id and identity.tax_id and str(row.tax_id).strip() != str(identity.tax_id):
            findings.append(
                Finding(
                    gene=identity.approved_symbol or row.gene or "(unknown)",
                    observed=str(row.tax_id),
                    correct=str(identity.tax_id),
                    retrieved_evidence=evidence,
                    evidence_source=EVIDENCE,
                    severity="critical",
                    classification="organism_mismatch",
                    field_name="tax_id",
                    sources=[row.source],
                )
            )

        # --- 5. external cross-references --------------------------------------------
        xref_type = XREF_COLUMNS.get(row.external_db or "", None)
        if xref_type is None and row.external_id and str(row.external_id).upper().startswith("CHEMBL"):
            xref_type = "ChEMBL"
        if xref_type and row.external_id:
            known = identity.xrefs.get(xref_type) or []
            identity_consistent = (
                _symbol_status(row.gene, identity) in ("approved", "synonym")
                and resolution.method in ("direct", "isoform")
            )
            if known and row.external_id not in known and identity_consistent:
                findings.append(
                    Finding(
                        gene=identity.approved_symbol or row.gene or "(unknown)",
                        observed=row.external_id,
                        correct=known[0],
                        retrieved_evidence=(
                            "%s cross-references %s:%s. The row's %s belongs to a different entry. %s"
                            % (identity.accession, xref_type, known[0], row.external_id, evidence)
                        ),
                        evidence_source=EVIDENCE,
                        severity="high",
                        classification="cross_reference_mismatch",
                        field_name="external_id",
                        sources=[row.source],
                    )
                )

    return findings


def merge_findings(findings: List[Finding]) -> List[Finding]:
    """Collapse identical defects seen on several rows into one reported finding."""
    merged: Dict[tuple, Finding] = {}
    for finding in findings:
        key = finding.dedupe_key
        if key in merged:
            existing = merged[key]
            existing.occurrences += finding.occurrences
            existing.sources = sorted(set(existing.sources) | set(finding.sources))
        else:
            merged[key] = finding
    ordered = sorted(
        merged.values(),
        key=lambda f: (
            SEVERITY_ORDER.index(f.severity) if f.severity in SEVERITY_ORDER else 99,
            f.classification,
            f.gene,
            f.observed,
        ),
    )
    return ordered
