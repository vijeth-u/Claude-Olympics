# The Golden Target — reconciliation tool

Builds one trusted golden record per drug-discovery target from five overlapping source
extracts, and reports the real defects with retrieved evidence attached.

```bash
python3 solve.py <pack_dir>     # single JSON object on stdout
```

Stdlib only. No third-party dependencies, so nothing to resolve from JFrog.

---

## Results on `exam/`

| | |
|---|---|
| Rows read | 2,578 structured + 674 literature mentions |
| Unique targets | **609** |
| Findings | **65**, covering 81 defective rows |
| Authority calls | 10 |
| Wall clock | ~14 s (budget: 300 s) |

| Classification | Findings | Rows | Severity |
|---|---|---|---|
| `duplicate_identity` | 30 | 32 | high |
| `stale_symbol` | 22 | 22 | low |
| `missing_identifier` | 6 | 12 | medium |
| `wrong_mapping` | 2 | 2 | critical |
| `ambiguous_mention` | 2 | 7 | medium |
| `isoform_accession` | 2 | 5 | medium |
| `cross_reference_mismatch` | 1 | 1 | high |

The three headline defects:

- **PIM1 → Q9P1W9** in ChEMBL. That accession is PIM2. Corrected to **P11309** — and the
  row's own `chembl_id` CHEMBL2147 cross-references P11309, so the accession is the
  outlier, not the gene.
- **KISS1 → Q969F8** in BindingDB. That accession is KISS1R. Corrected to **Q15726**.
- **PDE10A → CHEMBL3012** in ChEMBL. That id belongs to Q13946/PDE7A. Corrected to
  **CHEMBL4409**, which the internal registry independently records for PDE10A.

---

## The authority

Everything is resolved against the **EBI Proteins API**
(`https://www.ebi.ac.uk/proteins/api/proteins`), used three ways:

| Pattern | Purpose |
|---|---|
| `?accession=A,B,C` | Bulk-resolve live accessions, 90 per call. 639 accessions → 8 calls. |
| `?gene=SYM&taxid=…&reviewed=true` | Resolve retired accessions and legacy symbols. Gene search matches UniProt *synonyms*, so `gene=JNK3` returns MAPK10. |
| `?protein=NAME` | Last-resort fallback when a row has no usable gene symbol. |

A merge is only ever asserted when the live entry's own `secondaryAccession` list
contains the retired accession. That is the authority stating the merge, not us inferring
it from a name that happens to match.

`source_publications.csv` `pmid` values are internal reference numbers. They are never
dereferenced — a test asserts the only outbound host in the package is `www.ebi.ac.uk`,
and a second asserts no pmid is ever passed to the client.

---

## How it works

```
sources.py   load 5 CSVs → common row shape (tolerant column matching)
resolve.py   every row → one authoritative identity, recording HOW it resolved
audit.py     compare row vs authority → findings; re-point mis-mapped rows
reconcile.py group by canonical primary accession → golden records
publications.py  attribute mentions; split ambiguous symbols by context_sentence
solve.py     orchestrate, emit JSON on stdout, diagnostics on stderr
```

Two design decisions carry most of the accuracy:

**Records are keyed on the canonical primary accession, not the gene symbol.** That is
what makes 30 retired accessions collapse into the records they belong to instead of
splitting one target across two rows, and what lets a row with a blank gene symbol still
land correctly.

**The audit runs before assembly and corrects what it proves.** When it establishes that
ChEMBL's PIM1 row carries PIM2's accession, it re-points that row at P11309. The
correction lands in the golden records, not just in the findings list beside them.

### Resolution ladder

| Method | Condition |
|---|---|
| `direct` | accession is live |
| `isoform` | `P12345-2` → canonical `P12345` |
| `merged` | 404, and a live entry lists it as a secondary accession |
| `corrected` | audit proved the accession belongs to a different protein |
| `recovered` | 404, merge unprovable, but gene or protein name resolves uniquely |
| `by_symbol` / `by_name` | no accession at all |
| `unresolved` | nothing matched — row carried on file values, never dropped |

---

## Output

```json
{
  "unique_target_count": 609,
  "golden_records": [{"gene": "...", "primary_accession": "...", "sources": ["..."]}],
  "findings": [{
    "gene": "PIM1",
    "observed": "Q9P1W9",
    "correct": "P11309",
    "retrieved_evidence": "Accession Q9P1W9 resolves to PIM2_HUMAN (PIM2), not to PIM1. …",
    "evidence_source": "EBI Proteins API (https://www.ebi.ac.uk/proteins/api/proteins)",
    "severity": "critical",
    "classification": "wrong_mapping",
    "field": "accession",
    "sources": ["chembl"],
    "occurrences": 1
  }]
}
```

`field`, `sources`, `occurrences` and `detail` are additions beyond the required contract.
`sources` is a golden record's provenance across all five extracts, including
`publications` when a literature mention resolves to that target.

---

## Tests

```bash
python3 tests/test_reconciler.py      # 31 tests, ~2 s, fully offline
```

`tests/fixtures/mini_pack/` is a 21-row pack where every defect class fires at least
once *and* several deliberately messy-but-valid rows do not. `tests/offline_client.py`
replays a recorded authority snapshot by overriding only the HTTP transport, so the real
resolution, merge-proof and audit logic is what gets exercised.

Notable cases under test: a mis-mapped row lands on the corrected record; a retired
accession collapses rather than duplicating; the *correct* ChEMBL id on a mis-mapped row
is not also flagged; four spellings of "human" produce no organism findings while
*Mus musculus* does; a dead network yields zero findings rather than guesses; stdout
parses as JSON in a real subprocess.

Regenerate the snapshot after changing the fixture pack:

```bash
python3 tools/snapshot_authority.py
```

---

## Operational notes

- **Budget.** Network work stops at 210 s (`GT_NETWORK_BUDGET`) so the run always finishes
  inside 300 s. Past the deadline the tool emits what it has and warns on stderr.
- **Failure.** Retries use exponential backoff with jitter on 429/5xx. If the authority is
  unreachable the tool emits golden records from file values and **no findings** — an
  unverified claim is worse than a missing one.
- **Never crashes.** `main()` wraps the pipeline; any exception falls back to a naive merge
  so stdout is always parseable JSON. A parse failure scores the same as a crash.
- **cwd-independent.** Bundled paths resolve from `__file__`, not the working directory.

---

## Layout

```
goldentarget_config.json   run manifest
solve.py                   entry point
requirements.txt           empty by design
src/goldentarget/          sources, authority, resolve, audit, reconcile, publications
tests/                     offline suite + fixture pack + authority snapshot
tools/snapshot_authority.py  dev-only snapshot capture
APPROACH.md                the seven-question write-up
```
