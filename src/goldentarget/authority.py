"""Client for the EBI Proteins API - the authoritative source used by this tool.

Endpoint: https://www.ebi.ac.uk/proteins/api/proteins

Three access patterns are used:

1. ``batch_accessions``  - ?accession=A,B,C   resolve up to 90 live accessions per call.
2. ``search_by_gene``    - ?gene=SYM&taxid=..&reviewed=true   resolves *retired* accessions
   (a 404 on the direct lookup) and legacy gene symbols, because gene search matches
   UniProt gene synonyms as well as the approved symbol.
3. ``search_by_protein`` - ?protein=NAME   last-resort fallback when a row has no usable
   gene symbol at all.

Everything is stdlib. Every call retries with exponential backoff, and the whole client
honours a wall-clock deadline so a slow or flaky network degrades the run rather than
blowing the 5-minute per-pack budget.
"""

import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .model import Identity

BASE_URL = "https://www.ebi.ac.uk/proteins/api/proteins"
EVIDENCE_SOURCE = "EBI Proteins API (https://www.ebi.ac.uk/proteins/api/proteins)"

# Comment types that carry discriminating biology for literature disambiguation.
_INFORMATIVE_COMMENTS = (
    "FUNCTION",
    "TISSUE_SPECIFICITY",
    "SUBCELLULAR_LOCATION",
    "SUBUNIT",
    "DISEASE",
    "CATALYTIC_ACTIVITY",
)


def _log(message: str) -> None:
    # stdout is reserved for the single JSON object; diagnostics go to stderr.
    print(message, file=sys.stderr)


class AuthorityClient:
    def __init__(self, timeout=30.0, max_retries=4, deadline=None, batch_size=90):
        self.timeout = timeout
        self.max_retries = max_retries
        self.deadline = deadline
        self.batch_size = batch_size
        self._entries: Dict[str, dict] = {}
        self._gene_cache: Dict[str, List[dict]] = {}
        self._protein_cache: Dict[str, List[dict]] = {}
        self._miss: set = set()
        self.calls = 0
        self.degraded = False

    # ---------------------------------------------------------------- transport

    def _out_of_time(self) -> bool:
        return self.deadline is not None and time.time() >= self.deadline

    def _get(self, params: dict):
        """GET with retry/backoff. Returns parsed JSON, or None on 404/exhaustion."""
        if self._out_of_time():
            self.degraded = True
            return None
        url = BASE_URL + "?" + urllib.parse.urlencode(params)
        for attempt in range(self.max_retries):
            if self._out_of_time():
                self.degraded = True
                return None
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "golden-target-reconciler/1.0",
                    },
                )
                self.calls += 1
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (400, 404):
                    # "no such accession" is an answer, not a failure.
                    return None
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(min(8.0, (2 ** attempt)) + random.random())
                    continue
                self.degraded = True
                return None
            except Exception:
                if attempt < self.max_retries - 1:
                    time.sleep(min(8.0, (2 ** attempt)) + random.random())
                    continue
                self.degraded = True
                return None
        return None

    # ------------------------------------------------------------ bulk accession

    def prefetch_accessions(self, accessions) -> None:
        """Resolve many accessions up front, in batches, to keep call volume low."""
        wanted = []
        for accession in accessions:
            base = base_accession(accession)
            if base and base not in self._entries and base not in self._miss:
                wanted.append(base)
        wanted = sorted(set(wanted))
        for start in range(0, len(wanted), self.batch_size):
            chunk = wanted[start : start + self.batch_size]
            payload = self._get({"accession": ",".join(chunk), "size": str(len(chunk))})
            returned = set()
            for entry in payload or []:
                accession = entry.get("accession")
                if accession:
                    self._entries[accession] = entry
                    returned.add(accession)
            if payload is None:
                # Network trouble: don't mark the whole chunk as "definitely absent".
                continue
            for accession in chunk:
                if accession not in returned:
                    self._miss.add(accession)
        _log("authority: %d entries cached, %d unresolved, %d http calls"
             % (len(self._entries), len(self._miss), self.calls))

    def entry(self, accession: str) -> Optional[dict]:
        base = base_accession(accession)
        if not base:
            return None
        if base in self._entries:
            return self._entries[base]
        if base in self._miss:
            return None
        payload = self._get({"accession": base, "size": "1"})
        for item in payload or []:
            if item.get("accession"):
                self._entries[item["accession"]] = item
        if base in self._entries:
            return self._entries[base]
        if payload is not None:
            self._miss.add(base)
        return None

    def is_known_absent(self, accession: str) -> bool:
        return base_accession(accession) in self._miss

    # ------------------------------------------------------------------ searches

    def search_by_gene(self, symbol: str, tax_id="9606") -> List[dict]:
        if not symbol:
            return []
        key = "%s|%s" % (symbol.upper(), tax_id)
        if key in self._gene_cache:
            return self._gene_cache[key]
        params = {"gene": symbol, "reviewed": "true", "size": "20"}
        if tax_id:
            params["taxid"] = str(tax_id)
        payload = self._get(params) or []
        if not payload and tax_id:
            # Retry without the organism filter - the row's organism may itself be wrong.
            payload = self._get({"gene": symbol, "reviewed": "true", "size": "20"}) or []
        entries = [e for e in payload if e.get("accession") and "-" not in e["accession"]]
        for entry in entries:
            self._entries.setdefault(entry["accession"], entry)
        self._gene_cache[key] = entries
        return entries

    def search_by_protein(self, name: str, tax_id="9606") -> List[dict]:
        if not name or len(name) < 5:
            return []
        key = "%s|%s" % (name.lower(), tax_id)
        if key in self._protein_cache:
            return self._protein_cache[key]
        params = {"protein": name, "reviewed": "true", "size": "10"}
        if tax_id:
            params["taxid"] = str(tax_id)
        payload = self._get(params) or []
        entries = [e for e in payload if e.get("accession") and "-" not in e["accession"]]
        for entry in entries:
            self._entries.setdefault(entry["accession"], entry)
        self._protein_cache[key] = entries
        return entries

    def find_merge_target(self, retired_accession: str, symbol=None, protein_name=None,
                          tax_id="9606") -> Optional[dict]:
        """Find the live entry that ``retired_accession`` was merged into.

        The proof we require is explicit: the candidate's own ``secondaryAccession``
        list must contain the retired accession. That is the authority stating the
        merge, not us inferring it from a name match.
        """
        base = base_accession(retired_accession)
        if not base:
            return None
        for entry in self._entries.values():
            if base in (entry.get("secondaryAccession") or []):
                return entry
        candidates = []
        if symbol:
            candidates.extend(self.search_by_gene(symbol, tax_id))
        if protein_name:
            candidates.extend(self.search_by_protein(protein_name, tax_id))
        for entry in candidates:
            if base in (entry.get("secondaryAccession") or []):
                return entry
        return None


# ---------------------------------------------------------------- entry parsing


def base_accession(accession) -> Optional[str]:
    """Strip an isoform suffix: 'Q13422-3' -> 'Q13422'."""
    if not accession:
        return None
    return str(accession).strip().split("-")[0].upper() or None


def is_isoform(accession) -> bool:
    return bool(accession) and "-" in str(accession).strip()


def _names_from(block) -> List[str]:
    if not block:
        return []
    out = [block.get("fullName", {}).get("value", "")]
    out.extend(short.get("value", "") for short in block.get("shortName", []) or [])
    return [n for n in out if n]


def protein_names(entry: dict) -> List[str]:
    protein = entry.get("protein") or {}
    out = list(_names_from(protein.get("recommendedName")))
    for alt in protein.get("alternativeName") or []:
        out.extend(_names_from(alt))
    for sub in protein.get("submittedName") or []:
        out.extend(_names_from(sub))
    return out


def short_protein_aliases(entry: dict) -> List[str]:
    """Symbol-like abbreviations drawn from the protein name block.

    Literature abbreviates targets by protein short name as often as by gene symbol
    ("PSA" is a *protein* short name of KLK3, not a gene synonym), so these have to be
    indexed as aliases. Restricted to short, symbol-shaped tokens to avoid dragging
    whole descriptive names into the alias index.
    """
    out = []
    for name in protein_names(entry):
        token = name.strip()
        if 2 <= len(token) <= 12 and " " not in token and re.match(r"^[A-Za-z0-9][A-Za-z0-9./-]*$", token):
            out.append(token)
    return out


def recommended_name(entry: dict) -> Optional[str]:
    names = protein_names(entry)
    return names[0] if names else None


def gene_names(entry: dict):
    approved, synonyms = None, []
    for gene in entry.get("gene") or []:
        name = (gene.get("name") or {}).get("value")
        if name and approved is None:
            approved = name
        elif name:
            synonyms.append(name)
        synonyms.extend(s.get("value") for s in gene.get("synonyms") or [] if s.get("value"))
        for key in ("orfNames", "olnNames"):
            synonyms.extend(s.get("value") for s in gene.get(key) or [] if s.get("value"))
    return approved, [s for s in synonyms if s]


def _profile_tiers(entry: dict) -> List[str]:
    """Three weighted free-text tiers used for literature disambiguation."""
    tier_a = protein_names(entry)
    approved, synonyms = gene_names(entry)
    if approved:
        tier_a.append(approved)
    tier_a.extend(synonyms)

    tier_b = []
    for comment in entry.get("comments") or []:
        if comment.get("type") not in _INFORMATIVE_COMMENTS:
            continue
        for text in comment.get("text") or []:
            if text.get("value"):
                tier_b.append(text["value"])
        for location in comment.get("locations") or []:
            value = (location.get("location") or {}).get("value")
            if value:
                tier_b.append(value)
        for reaction in [comment.get("reaction")] if comment.get("reaction") else []:
            if reaction.get("name"):
                tier_b.append(reaction["name"])

    tier_c = []
    for reference in entry.get("dbReferences") or []:
        if reference.get("type") == "GO":
            term = (reference.get("properties") or {}).get("term")
            if term:
                tier_c.append(term)
    for keyword in entry.get("keywords") or []:
        if keyword.get("value"):
            tier_c.append(keyword["value"])

    return [" ".join(tier_a), " ".join(tier_b), " ".join(tier_c)]


def to_identity(entry: dict) -> Identity:
    approved, synonyms = gene_names(entry)
    organism = entry.get("organism") or {}
    xrefs: Dict[str, List[str]] = {}
    for reference in entry.get("dbReferences") or []:
        ref_type, ref_id = reference.get("type"), reference.get("id")
        if ref_type and ref_id:
            xrefs.setdefault(ref_type, []).append(ref_id)
    return Identity(
        accession=entry.get("accession", ""),
        entry_name=entry.get("id", ""),
        approved_symbol=approved,
        synonyms=synonyms,
        recommended_name=recommended_name(entry),
        organism_names=[n.get("value") for n in organism.get("names") or [] if n.get("value")],
        tax_id=organism.get("taxonomy"),
        secondary_accessions=list(entry.get("secondaryAccession") or []),
        xrefs=xrefs,
        reviewed=(entry.get("info") or {}).get("type") == "Swiss-Prot",
        protein_aliases=short_protein_aliases(entry),
        profile=_profile_tiers(entry),
    )


def evidence_line(identity: Identity) -> str:
    """One-line, human-readable restatement of what the authority returned."""
    parts = ["accession=%s" % identity.accession]
    if identity.entry_name:
        parts.append("entry=%s" % identity.entry_name)
    if identity.approved_symbol:
        parts.append("approved gene symbol=%s" % identity.approved_symbol)
    if identity.synonyms:
        parts.append("synonyms=[%s]" % ", ".join(identity.synonyms[:8]))
    if identity.recommended_name:
        parts.append("recommended name=%s" % identity.recommended_name)
    if identity.organism_names:
        parts.append("organism=%s" % identity.organism_names[0])
    if identity.tax_id:
        parts.append("taxid=%s" % identity.tax_id)
    parts.append("reviewed=%s" % ("Swiss-Prot" if identity.reviewed else "TrEMBL"))
    return "; ".join(parts)
