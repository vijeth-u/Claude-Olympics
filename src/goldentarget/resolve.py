"""Resolve every source row to one authoritative identity.

Each row gets a ``Resolution`` that records not just *what* it resolved to but *how* -
which is what lets the audit stage emit evidence-carrying findings rather than guesses.

Resolution ladder, per row:

  1. direct       accession is live -> that entry.
  2. isoform      accession is 'P12345-2' and P12345 is live -> the canonical entry.
  3. merged       accession 404s, and some live entry lists it in secondaryAccession.
                  Found via gene-symbol search (which also matches legacy symbols),
                  falling back to protein-name search.
  4. by_symbol    no usable accession at all -> unique reviewed hit for the gene symbol.
  5. by_name      no usable accession or symbol -> unique reviewed hit for the protein name.
  6. unresolved   nothing matched; the row is carried on its file values alone.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import authority as auth
from .model import Identity, SourceRow


@dataclass
class Resolution:
    row: SourceRow
    identity: Optional[Identity] = None
    method: str = "unresolved"
    notes: List[str] = field(default_factory=list)

    @property
    def canonical_accession(self) -> Optional[str]:
        return self.identity.accession if self.identity else None


def _unique_reviewed(entries, symbol=None):
    """Pick a single entry when a search returns several.

    Exact approved-symbol match wins outright; otherwise a lone candidate is accepted
    and anything genuinely ambiguous is refused (better unresolved than wrong).
    """
    reviewed = [e for e in entries if (e.get("info") or {}).get("type") == "Swiss-Prot"]
    pool = reviewed or list(entries)
    pool = [e for e in pool if e.get("accession") and "-" not in e["accession"]]
    if not pool:
        return None
    if symbol:
        exact = [e for e in pool if (auth.gene_names(e)[0] or "").upper() == symbol.upper()]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None
    return pool[0] if len(pool) == 1 else None


def _best_by_name(entries, protein_name, symbol=None):
    """Disambiguate search hits using the row's own protein name."""
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]
    target = _norm(protein_name)
    if not target:
        return _unique_reviewed(entries, symbol)
    scored = []
    for entry in entries:
        best = max((_overlap(target, _norm(n)) for n in auth.protein_names(entry)), default=0.0)
        scored.append((best, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored[0][0] >= 0.6 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
        return scored[0][1]
    return _unique_reviewed(entries, symbol)


def _norm(text) -> str:
    return " ".join(str(text or "").lower().replace("-", " ").split())


def _overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    left, right = set(a.split()), set(b.split())
    return len(left & right) / float(max(len(left), len(right)))


def resolve_rows(rows: List[SourceRow], client: auth.AuthorityClient,
                 default_tax="9606") -> List[Resolution]:
    client.prefetch_accessions(r.accession for r in rows if r.accession)

    identity_cache: Dict[str, Identity] = {}

    def identity_for(entry) -> Identity:
        accession = entry.get("accession", "")
        if accession not in identity_cache:
            identity_cache[accession] = auth.to_identity(entry)
        return identity_cache[accession]

    resolutions: List[Resolution] = []
    for row in rows:
        tax = row.tax_id or default_tax
        resolution = Resolution(row=row)

        if row.accession:
            entry = client.entry(row.accession)
            if entry is not None:
                resolution.identity = identity_for(entry)
                resolution.method = "isoform" if auth.is_isoform(row.accession) else "direct"
            else:
                merged = client.find_merge_target(
                    row.accession, symbol=row.gene, protein_name=row.protein_name, tax_id=tax
                )
                if merged is not None:
                    resolution.identity = identity_for(merged)
                    resolution.method = "merged"
                else:
                    # Accession is dead and we cannot prove a merge. Fall back to the
                    # row's other identifiers so the record is not simply lost.
                    fallback = None
                    if row.gene:
                        fallback = _unique_reviewed(client.search_by_gene(row.gene, tax), row.gene)
                    if fallback is None and row.protein_name:
                        fallback = _best_by_name(
                            client.search_by_protein(row.protein_name, tax), row.protein_name, row.gene
                        )
                    if fallback is not None:
                        resolution.identity = identity_for(fallback)
                        resolution.method = "recovered"
                    else:
                        resolution.notes.append("accession not resolvable and no fallback matched")
        elif row.gene:
            entry = _unique_reviewed(client.search_by_gene(row.gene, tax), row.gene)
            if entry is not None:
                resolution.identity = identity_for(entry)
                resolution.method = "by_symbol"
        elif row.protein_name:
            entry = _best_by_name(
                client.search_by_protein(row.protein_name, tax), row.protein_name, None
            )
            if entry is not None:
                resolution.identity = identity_for(entry)
                resolution.method = "by_name"

        resolutions.append(resolution)
    return resolutions
