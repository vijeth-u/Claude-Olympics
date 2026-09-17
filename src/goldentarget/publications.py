"""Attribute literature mentions to targets, and resolve ambiguous symbols.

The `pmid` column holds internal reference numbers, not PubMed identifiers, so this
module never dereferences them. When a mention maps to more than one target in the pack,
the decision is made from `context_sentence` alone, scored against a profile built from
what the authority returned for each candidate.

Scoring uses three weighted tiers so that naming evidence dominates background biology:

  tier A (x6)  protein names, short names, gene symbol and synonyms
  tier B (x2)  function / tissue specificity / subcellular location / disease comments
  tier C (x1)  GO term names and UniProt keywords

Token match is exact (3 points) or six-character stem (2 points). Loose substring
matching was tried and removed: it let generic fragments such as "protein" inside
"glycoprotein" carry weight, which flipped a decision the tiered exact match gets right.
"""

import re
from typing import Dict, List, Optional, Tuple

from . import authority as auth
from .model import Finding

EVIDENCE = auth.EVIDENCE_SOURCE

_STOPWORDS = {
    "the", "of", "and", "a", "an", "in", "to", "for", "with", "was", "were", "this",
    "that", "by", "as", "on", "at", "from", "its", "their", "been", "also", "which",
    "after", "these", "those", "have", "has", "had", "into", "than", "then", "when",
    "study", "studies", "levels", "level", "showed", "shown", "using", "used",
}

_TIER_WEIGHTS = (6, 2, 1)


def _tokens(text: str):
    return {
        word
        for word in re.findall(r"[a-z]{4,}", str(text or "").lower())
        if word not in _STOPWORDS
    }


def _similarity(sentence_tokens, profile_tokens) -> int:
    score = 0
    for word in sentence_tokens:
        best = 0
        for other in profile_tokens:
            if word == other:
                best = max(best, 3)
            elif len(word) >= 6 and len(other) >= 6 and word[:6] == other[:6]:
                best = max(best, 2)
        score += best
    return score


def score_context(sentence: str, identity) -> int:
    sentence_tokens = _tokens(sentence)
    if not sentence_tokens:
        return 0
    tiers = identity.profile or ["", "", ""]
    return sum(
        weight * _similarity(sentence_tokens, _tokens(tier))
        for weight, tier in zip(_TIER_WEIGHTS, tiers)
    )


def build_alias_index(identities: Dict[str, object], row_symbols: Dict[str, set]):
    """alias (upper) -> set of canonical accessions present in this pack."""
    index: Dict[str, set] = {}

    def add(alias, accession):
        if alias:
            index.setdefault(str(alias).strip().upper(), set()).add(accession)

    for accession, identity in identities.items():
        add(identity.approved_symbol, accession)
        for synonym in identity.synonyms:
            add(synonym, accession)
        # Protein short names ("PSA" for KLK3) are how literature abbreviates targets,
        # so they have to be indexed alongside gene-level aliases.
        for alias in identity.protein_aliases:
            add(alias, accession)
        for symbol in row_symbols.get(accession, ()):
            add(symbol, accession)
    return index


def attribute(publications, identities, alias_index, client, default_tax="9606"):
    """Returns (accession -> set(pmid), findings)."""
    attributions: Dict[str, set] = {}
    findings: List[Finding] = []
    # ambiguous mention -> chosen accession -> list of pmids
    ambiguous: Dict[str, Dict[str, List[str]]] = {}
    ambiguous_candidates: Dict[str, set] = {}

    for publication in publications:
        mention = (publication.get("mention") or "").strip()
        if not mention:
            continue
        candidates = set(alias_index.get(mention.upper(), ()))

        if not candidates:
            entries = client.search_by_gene(mention, default_tax)
            for entry in entries:
                accession = entry.get("accession")
                if accession in identities:
                    candidates.add(accession)

        if not candidates:
            continue

        if len(candidates) > 1:
            # A mention that IS the approved symbol of exactly one candidate is not
            # ambiguous - the others merely list it as a legacy alias.
            approved = {
                a for a in candidates
                if a in identities
                and (identities[a].approved_symbol or "").upper() == mention.upper()
            }
            if len(approved) == 1:
                candidates = approved

        if len(candidates) == 1:
            accession = next(iter(candidates))
            attributions.setdefault(accession, set()).add(publication.get("pmid", ""))
            continue

        context = publication.get("context", "")
        scored = sorted(
            ((score_context(context, identities[a]), a) for a in candidates if a in identities),
            reverse=True,
        )
        if not scored:
            continue
        chosen = scored[0][1]
        attributions.setdefault(chosen, set()).add(publication.get("pmid", ""))
        ambiguous.setdefault(mention, {}).setdefault(chosen, []).append(publication.get("pmid", ""))
        ambiguous_candidates.setdefault(mention, set()).update(candidates)

    for mention, by_target in ambiguous.items():
        candidate_lines = "; ".join(
            "%s = %s (%s)"
            % (
                accession,
                identities[accession].approved_symbol,
                identities[accession].recommended_name,
            )
            for accession in sorted(ambiguous_candidates.get(mention, ()))
            if accession in identities
        )
        for accession, pmids in by_target.items():
            identity = identities[accession]
            findings.append(
                Finding(
                    gene=identity.approved_symbol or mention,
                    observed=mention,
                    correct=identity.approved_symbol or accession,
                    retrieved_evidence=(
                        "'%s' resolves to more than one target in this pack [%s]. "
                        "The context sentence matches %s. %s"
                        % (mention, candidate_lines, identity.approved_symbol,
                           auth.evidence_line(identity))
                    ),
                    evidence_source=EVIDENCE,
                    severity="medium",
                    classification="ambiguous_mention",
                    field_name="target_mention",
                    sources=["publications"],
                    occurrences=len(pmids),
                    detail=(
                        "Resolved from context_sentence only; the pmid column holds internal "
                        "reference numbers and was deliberately not dereferenced. "
                        "Affected reference ids: %s" % ", ".join(sorted(pmids))
                    ),
                )
            )

    return attributions, findings
