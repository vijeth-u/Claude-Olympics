# Approach summary

*~1,180 words.*

## 1. Audit strategy — what was checked for every row, and how I decided the list was complete

I derived the checklist from the join keys rather than from guesses about what might be
broken. A golden record needs an identity (accession), a label (gene symbol), a species,
and provenance. So every row is checked on: does the accession resolve at all; is it a
primary accession or an isoform; is the gene symbol the approved symbol, a recognised
synonym, or foreign to that protein; does the declared organism and tax id match; and
does any external cross-reference on the row belong to that protein.

I decided the list was complete by attacking the data from the opposite direction too —
looking for structural anomalies without assuming what they would be, then confirming
each cluster had a check that would catch it. That sweep found: 8 gene symbols carrying
more than one accession, 2 accessions carrying more than one gene symbol, 5 accessions
with isoform suffixes, 12 rows with blank gene symbols, 3 duplicated `chembl_id` values,
and 1 literature mention absent from every source's gene column. Each maps onto a check
above. I also ran a name-similarity sweep comparing each row's protein name against the
authority's recommended and alternative names; its 217 outliers were all BindingDB's
"`SYMBOL` protein" naming convention and case variants, which told me there was no
further defect class hiding in the name fields.

## 2. Validating identities — fields relied on, and where I cross-checked

The EBI Proteins API is the single authority. All 639 unique accessions resolve in 8
batched calls; 609 came back live and 30 returned nothing.

For a live accession I rely on `accession`, `id`, `gene[].name` and `gene[].synonyms`,
`organism.names` and `organism.taxonomy`, `info.type`, and `dbReferences`.

For the 30 that returned nothing, a 404 alone proves only absence, not the correction.
I resolved each by searching the row's gene symbol — which works even when the symbol is
itself legacy, because gene search matches synonyms, so `gene=JNK3` returns MAPK10 — and
then requiring that the candidate's own `secondaryAccession` list contains the retired
accession. All 30 passed that test, which is the authority stating the merge rather than
me inferring it from a name that happens to match.

Where a row had independent identifiers I cross-checked them against each other. PIM1 is
the clearest case: the accession says PIM2, but the `chembl_id` CHEMBL2147
cross-references P11309/PIM1 and the `pref_name` is pim-1. Three fields against one told
me which field was the defect, which is the difference between correcting the accession
and wrongly correcting the gene.

## 3. Decision rules — defect versus legitimately messy

A row is a defect only when the authority returns a *different value* for a field, not
merely a differently-spelled one, and only when I can state the correction. Concretely:

- **Wrong mapping** — the identifier points at a different protein. The row's other
  fields decide which identifier is the outlier.
- **Duplicate identity / merged accession** — a retired accession, proved by
  `secondaryAccession`. It is a duplicate when the live primary is *also* in the pack
  (the naive merge would produce two records for one target), otherwise merely stale.
- **Stale symbol** — a legacy label the authority still lists as a synonym. The identity
  is correct, so this is low severity, not a corruption.
- **Isoform accession** — `Q13422-3` where a target master needs `Q13422`.
- **Missing identifier** — a blank required field that the authority can fill.
- **Ambiguous mention** — a literature symbol mapping to more than one target *in this
  pack*.

Everything else is messiness. Format, case, spelling and naming-convention differences
between sources are not defects if they resolve to the same identity. The rule that
carries the most weight: a check that cannot produce a correction does not fire. "This
looks wrong" earns a quarter of the marks and costs precision, so the tool stays quiet
unless it has the retrieved answer.

## 4. Something investigated and not flagged

`source_uniprot.csv` records P14416 as "D(2) dopamine receptor" while the authority now
returns "Dopamine receptor D2". It looked like a stale label, and my first instinct was to
flag it next to the SEPT9 and WHSC1 symbol findings.

I didn't, because the two are different in kind. SEPT9→SEPTIN9 is a *gene symbol*: an
identifier other systems join on, governed by HGNC, where the stale value is a real
integration hazard. "D(2) dopamine receptor" is a *descriptive protein name* that UniProt
itself revised; nothing joins on it, the accession is correct, and the row resolves to
exactly the right target. Flagging it would report the authority's own editorial history
as a defect in the data.

Two related calls went the same way. BindingDB spells human four ways — `human`, `Human`,
`H. sapiens`, `Homo sapiens` — across 1,239 rows; all four are the same claim, so the
organism check accepts any spelling whose tokens are a subset of the authority's
scientific and common names. And "Nuclear receptor subfamily 4immunitygroup A member 1"
is visibly corrupted, but it appears with that identical corruption in all five files, so
no source disagrees with any other; it is a generation artifact, not a row defect.
Flagging 1,239 organism rows or 8 name rows would have buried 65 real findings.

## 5. Validating the tool before running it on the exam

I built a 21-row fixture pack in which every defect class fires at least once, alongside
control rows that must stay silent — PIM2→Q9P1W9 (the same accession, legitimately), the
correct CHEMBL2147 on the mis-mapped PIM1 row, and all four human spellings. The 31-test
suite runs fully offline against a recorded authority snapshot, overriding only the HTTP
transport, so the real resolution, merge-proof and audit logic is what gets exercised.

Tests cover the output contract (shape, types, every finding carrying evidence), each
defect class, the negative controls, and three failure modes: an empty pack, a dead
network, and a pack missing two of five files. The dead-network test asserts *zero*
findings — degraded means silent, not speculative. A final test runs `solve.py` as a real
subprocess and parses its entire stdout, since a stray print scores the same as a crash.

The suite caught two real bugs. Organism matching initially rejected "Homo sapiens
(Human)" and produced 600 false positives. And an early version reported PSA correctly
but also flagged GAGE12I and PIM1 as ambiguous — the first because a mention that *is*
one candidate's approved symbol is not ambiguous, the second a downstream symptom of the
PIM1 mis-mapping that vanished once corrections were applied before assembly.

## 6. Working with Claude

I used Claude to do the systematic sweeps I would otherwise have done by hand — profiling
the five files, enumerating gene/accession conflicts, probing the API's behaviour on
retired accessions, and drafting the module structure.

I verified the things that determine the score. Every one of the 30 merges I confirmed
against `secondaryAccession` directly rather than accepting a name match. I checked the
PIM1 and PDE10A corrections against a second field on the row before accepting which
identifier was wrong. I read all seven PSA context sentences myself and decided the 2/5
split before building a scorer to reproduce it — and when the scorer got 5 of 6, I found
the cause (loose substring matching letting "protein" inside "glycoprotein" carry weight)
rather than tuning weights until the number moved.

## 7. What I would harden before production

Pin the authority's release version in every finding, so a correction is reproducible
after the reference database moves; the brief's own instruction to re-verify at submission
time is the same problem at a smaller scale. Add a persistent accession cache with TTL,
since the pack is re-processed far more often than UniProt changes. Promote `detail` and
`field` to a first-class per-row audit trail so a steward can see which of 2,578 rows a
finding came from, not just the aggregate count. Add a second authority (HGNC for symbols)
so gene-symbol corrections come from two independent sources, and alert on silent
disagreement between them. Make thresholds — the disambiguation tier weights, the
name-similarity cutoffs — configuration rather than constants, and track precision against
a steward-reviewed set so a rule change that starts crying wolf is caught by the build.
