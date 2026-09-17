# Claude Olympics — The Golden Target
## Investigation transcript

**Session date:** 17 September 2026
**Model:** Claude Opus 5
**Artifacts produced:** `golden-target-reconciler` repo, `APPROACH.md`, this transcript
**Authority used:** EBI Proteins API — `https://www.ebi.ac.uk/proteins/api/proteins`

This is the record of the audit: what I checked, what I verified and how, what I
deliberately did not flag, and the two bugs the test suite caught before the exam run.

---

## Turn 1 — Setup and profiling

**Prompt:** uploaded `golden-target-data.zip` and `Claude-Olympics-Sample-Repo-main.zip`
with the challenge brief; asked for a complete solution in the sample repo format.

Unpacked both archives and read the sample repo end to end — `README.md`, `solve.py`,
`goldentarget_config.json`, `requirements.txt`, `Dockerfile` — plus
`verification_starter.md` shipped with the data.

Two constraints registered immediately from the sample README: stdout must parse as a
single JSON object (a stray `print()` scores the same as a crash), and `run_command` must
name only the interpreter and script because the harness appends `<pack_dir>` as the only
positional argument. Also noted the 5-minute per-pack budget and the hidden second dataset.

Profiled all five files:

| File | Rows | Columns |
|---|---|---|
| `source_chembl.csv` | 292 | chembl_id, pref_name, target_type, accession, gene_symbol, organism, tax_id, target_confidence |
| `source_uniprot.csv` | 610 | accession, entry_name, gene_names, protein_name, organism, reviewed, length, database |
| `source_bindingdb.csv` | 1,239 | target_name, species, uniprot_id, gene_symbol, assay_count, binding_type |
| `source_internal.csv` | 437 | internal_id, registered_name, gene_symbol, source_db, external_id, uniprot_ref, registered_by, registration_date, status |
| `source_publications.csv` | 674 | pmid, target_mention, context_sentence, journal, year |

Value distributions:

```
chembl  organism:   Homo sapiens 292 (only value)
chembl  tax_id:     9606 292 (only value)
uniprot organism:   Homo sapiens (Human) 610 (only value)
uniprot reviewed:   reviewed 600 | unreviewed 10
uniprot database:   Swiss-Prot 600 | TrEMBL 10
bindingdb species:  human 335 | Human 335 | H. sapiens 293 | Homo sapiens 276
internal status:    active 437 (only value)
internal source_db: UniProt 156 | ChEMBL 142 | Manual 139
```

Two things jumped out. Every row claims human, so an organism defect can only show up as
an accession that *isn't* human — which requires the authority to detect. And exactly 10
UniProt rows are flagged `unreviewed`/TrEMBL against 600 Swiss-Prot. Ten is a suspiciously
round number for a natural distribution; I marked it to come back to.

Also noted: BindingDB spells human four different ways. That is messiness, not a defect —
all four are the same claim. I flagged it early so I wouldn't later generate 1,239 false
positives.

---

## Turn 2 — Structural sweep before touching the network

I wanted to find anomalies *without* assuming what they'd be, so the checklist would be
derived from the data rather than from my expectations.

Normalised all four identifier-bearing sources onto `(source, gene, accession, name)` —
2,578 rows — and ran:

**Accession format.** 5 rows fail the UniProt accession regex, all isoform-suffixed:
`Q05086-3` (UBE3A, uniprot), `Q13422-3` (IKZF1, uniprot ×1 + bindingdb ×3).

**Genes carrying more than one accession — 8:**

```
ABTB3     A4FU41 / B3KXG3 / A6QL63
CCDC169   A6NC13 / A6NNP5 / A6NCT2
IFNA13    P01562 / D4Q9M8 / A0A087WWS6
HNF4A     B2RPP8 / A5JW41 / P41235
SRPK1     Q96SB4 / B4DS61
PIM1      Q9P1W9 (chembl) vs P11309 (uniprot, bindingdb, internal)
KISS1     Q15726 / Q969F8  (both bindingdb)
EPHA2     P29317 / B5A968
```

**Accessions carrying more than one gene symbol — 2:**

```
Q9P1W9   PIM1 (chembl)  +  PIM2 (chembl, uniprot, bindingdb, internal)
Q969F8   KISS1 (bindingdb)  +  KISS1R (chembl, uniprot, bindingdb, internal)
```

Those two are the smoking guns for wrong mappings: one accession, two different proteins
claimed. The 4-to-1 vote in each case told me which claim was the outlier, but a vote is
not evidence — I needed the authority to say what Q9P1W9 and Q969F8 actually are.

The first group looked like a different pattern: several accessions per gene, one of which
is probably canonical and the rest retired. Also to be confirmed externally.

---

## Turn 3 — Choosing and probing the authority

Tested connectivity:

```
GET https://www.ebi.ac.uk/proteins/api/proteins/P11309   → 200
GET https://rest.uniprot.org/uniprotkb/P11309.json       → blocked (host not in allowlist)
GET https://est.uniprot.org/                             → unreachable
```

EBI Proteins it is, which the brief says is sufficient. The P11309 response gave me the
field inventory I'd need:

```json
{"accession":"P11309","id":"PIM1_HUMAN",
 "info":{"type":"Swiss-Prot","modified":"2026-09-02","version":265},
 "organism":{"taxonomy":9606,"names":[{"type":"scientific","value":"Homo sapiens"},
                                      {"type":"common","value":"Human"}]},
 "secondaryAccession":["Q38RT9","Q5T7H7","Q96RG3"],
 "protein":{"recommendedName":{"fullName":{"value":"Serine/threonine-protein kinase pim-1"}}}}
```

`secondaryAccession` is the key field — that's how a merge can be *proved* rather than
inferred.

Checked cross-references on the same entry:

```
dbReferences types: PDB 191, PDBsum 191, GO 27, DrugBank 22, EMBL 9, ...
ChEMBL:CHEMBL2147  GeneID:5292  HGNC:HGNC:8986  BindingDB:P11309
```

ChEMBL ids are available, so `chembl_id` and `external_id` are independently checkable.

**Probing the retired-accession case.** Direct lookup on a suspected secondary:

```
GET /proteins/B2RPP8 → 404
  {"errorMessage":["Can not find Entry for accession: {B2RPP8}; the accession may be
    obsolete, please check https://www.uniprot.org/ to verify it."]}
GET /proteins/P01562 → 404
GET /proteins/B4DS61 → 404
```

A 404 proves absence but not the correction, so it's worth only DETECTED marks. Tried the
search endpoint:

```
GET /proteins?accession=B2RPP8,B4DS61,P01562 → []   (search does not resolve secondaries)
```

Then tried gene search, which turned out to be the answer:

```
GET /proteins?gene=HNF4A&taxid=9606&reviewed=true
  P41235 HNF4A_HUMAN ['HNF4A'] SEC: ['A5JW41','B2RPP8','O00659','O00723','Q14540',...]
GET /proteins?gene=SRPK1&taxid=9606&reviewed=true
  Q96SB4 SRPK1_HUMAN ['SRPK1'] SEC: ['B4DS61','Q12890','Q5R364','Q5R365','Q8IY12']
GET /proteins?gene=EPHA2&taxid=9606&reviewed=true
  P29317 EPHA2_HUMAN ['EPHA2'] SEC: ['B5A968','Q8N3Z2']
GET /proteins?gene=IFNA13&taxid=9606&reviewed=true
  A0A087WWS6 IFNAD_HUMAN ['IFNA13'] SEC: ['D4Q9M8','P01562','Q14605',...]
GET /proteins?gene=KISS1&taxid=9606&reviewed=true
  Q15726 KISS1_HUMAN ['KISS1']   SEC: ['A8K6N0','Q9HBP1']
  Q969F8 KISSR_HUMAN ['KISS1R']  SEC: ['A5D8U2','B2RTV1','Q96QG0']
GET /proteins?gene=PIM1&taxid=9606&reviewed=true
  P11309 PIM1_HUMAN ['PIM1'] SEC: ['Q38RT9','Q5T7H7','Q96RG3']
```

Note `reviewed=true` was necessary — without it, gene search returns TrEMBL entries first
(HNF4A returned 8 `A0A...` TrEMBL hits before anything useful).

This gives a fully generic resolution mechanism with no hardcoding: when an accession
404s, search the row's gene symbol, and accept a candidate only if the retired accession
appears in its own `secondaryAccession` list. Also confirmed `Q969F8 = KISS1R`, so the
bindingdb `KISS1 → Q969F8` row is a genuine wrong mapping.

---

## Turn 4 — Bulk resolution of all 639 accessions

Batch size test: 100 accessions returned 86 entries in 0.9 s. Batching is essential for
the 5-minute budget, so I built the resolver around it.

Resolved all 639 unique accessions in 8 batches of 90:

```
609 resolved, 30 unresolved
```

All 609 came back `taxonomy: 9606` and `info.type: Swiss-Prot`. Two consequences:

1. **No organism defect exists in this pack.** Every row claims human and every resolvable
   accession *is* human. I still implemented the organism/tax_id check, because the hidden
   dataset shares the schema and may well contain one.
2. **The 10 `unreviewed`/TrEMBL rows in `source_uniprot.csv` are not unreviewed at all** —
   they are the unresolvable ones. Cross-checking confirmed it exactly:

```
B4DS61  SRPK1    NOT FOUND
B2RPP8  HNF4A    NOT FOUND
A6NC13  CCDC169  NOT FOUND
A4FU41  ABTB3    NOT FOUND
B3KXG3  ABTB3    NOT FOUND
P01562  IFNA13   NOT FOUND
A5JW41  HNF4A    NOT FOUND
D4Q9M8  IFNA13   NOT FOUND
B5A968  EPHA2    NOT FOUND
A6NCT2  CCDC169  NOT FOUND
```

Ten rows, six genes, each internally consistent — the row carries a plausible length, a
plausible entry name, a correct protein name. Nothing looks wrong until you ask the
authority. That is precisely the defect design the brief described.

**The other 20 unresolvable accessions** were all in BindingDB, and every one travelled
with a legacy gene symbol:

```
KIAA9001 A2AAU0    Bromodomain-containing protein 2
JNK3     A6NFS3    Mitogen-activated protein kinase 10
ASK1     A6NIA0    Mitogen-activated protein kinase kinase kinase 5
HAUSP    A6NMY8    Ubiquitin C-terminal hydrolase 7
NAK      A8K4S4    Serine/threonine-protein kinase TBK1
PRKM11   A8K730    Mitogen-activated protein kinase 11
ADRA2R   B0LPF6    Alpha-2A adrenergic receptor
SAK      B2RAL0    Serine/threonine-protein kinase PLK4
KMT6     B2RAQ1    Histone-lysine N-methyltransferase EZH2
ERBA2    B3KU79    Thyroid hormone receptor beta
RIP3     B4DJL9    Receptor-interacting serine/threonine-protein kinase 3
GFRP1    B4DML7    Nuclear receptor subfamily 4immunitygroup A member 1
KIAA0288 E9PGB9    Histone deacetylase 4
CSVP     O60226    Disintegrin and metalloproteinase domain-containing protein 17
MIRK     O75258    Dual specificity tyrosine-phosphorylation-regulated kinase 1B
GALNR    Q4VBL7    Galanin receptor type 1
G250     Q5T4R1    Carbonic anhydrase 9
ORXR2    Q5VTM0    Orexin receptor type 2
NMU2     Q7LC54    Neuromedin-U receptor 2
UFO      Q8N5L2    Tyrosine-protein kinase receptor UFO
```

Two separate defects per row: a stale symbol *and* a retired accession.

---

## Turn 5 — Verifying every merge claim individually

I would not accept 30 corrections on a name match. Gene search resolved each legacy
symbol, then I checked the retired accession against the candidate's own
`secondaryAccession` list:

```
JNK3     → P53779 MK10_HUMAN   merged=YES  sec=['A6NFS3','A6NG28','B3KQ94','Q15707',...]
ASK1     → Q99683 M3K5_HUMAN   merged=YES  sec=['A6NIA0','B4DGB2','Q5THN3','Q99461']
HAUSP    → Q93009 UBP7_HUMAN   merged=YES  sec=['A6NMY8','B7Z815','H0Y3G8']
NAK      → Q9UHD2 TBK1_HUMAN   merged=YES  sec=['A8K4S4','Q8IYV3','Q9NUJ5']
PRKM11   → Q15759 MK11_HUMAN   merged=YES  sec=['A8K730','B0LPG1','B7Z630',...]
ADRA2R   → P08913 ADA2A_HUMAN  merged=YES  sec=['B0LPF6','Q2I8G2','Q2XN99',...]
SAK      → O00444 PLK4_HUMAN   merged=YES  sec=['B2RAL0','B7Z837','B7Z8G7',...]
KMT6     → Q15910 EZH2_HUMAN   merged=YES  sec=['B2RAQ1','B3KS30','B7Z1D6',...]
ERBA2    → P10828 THB_HUMAN    merged=YES  sec=['B3KU79','P37243','Q13986',...]
RIP3     → Q9Y572 RIPK3_HUMAN  merged=YES  sec=['B4DJL9','C4AM87','Q5J795',...]
GFRP1    → P22736 NR4A1_HUMAN  merged=YES  sec=['B4DML7','Q15627','Q53Y00','Q6IBU8']
KIAA0288 → P56524 HDAC4_HUMAN  merged=YES  sec=['E9PGB9','F5GX36','Q86YH7','Q9UND6']
CSVP     → P78536 ADA17_HUMAN  merged=YES  sec=['O60226']
MIRK     → Q9Y463 DYR1B_HUMAN  merged=YES  sec=['O75258','O75788','O75789']
GALNR    → P47211 GALR1_HUMAN  merged=YES  sec=['Q4VBL7']
G250     → Q16790 CAH9_HUMAN   merged=YES  sec=['Q5T4R1']
ORXR2    → O43614 OX2R_HUMAN   merged=YES  sec=['Q5VTM0']
NMU2     → Q9GZQ4 NMUR2_HUMAN  merged=YES  sec=['Q7LC54','Q96AM5','Q9NRA6']
UFO      → P30530 UFO_HUMAN    merged=YES  sec=['Q8N5L2','Q9UD27']
KIAA9001 → P25440 BRD2_HUMAN   merged=YES  sec=['A2AAU0','B0S7P0','B1AZT1',...]
```

30 for 30. Every retired accession is real and historical, not fabricated, and the
authority states the merge target explicitly. Three of the legacy symbols returned
multiple candidates (`NAK` → 3, `ADRA2R` → 3, `ERBA2` → 2, `GALNR` → 3) and were
disambiguated by matching the row's `target_name` against the candidate's recommended
name — `NAK` + "Serine/threonine-protein kinase TBK1" → Q9UHD2, not NR4A1 or AKAP8L.

---

## Turn 6 — Symbol audit across all 2,578 rows

With every accession resolved I no longer needed gene searches for the symbol check —
compare the row's symbol against the entry's approved name and synonyms directly.

**Row symbol is a recognised synonym (stale-but-valid):**

```
SEPT9  Q9UHD8 → approved SEPTIN9   [internal]
WHSC1  O96028 → approved NSD2      [internal]
```

Both accessions are correct. This is a label problem, not a corruption — low severity.

Pulling those two rows showed the pattern clearly:

```
TGT-2433  SEPT9  SEPT9  Manual  (no external_id)  Q9UHD8  jsmith  2019-03-15  active
TGT-2425  WHSC1  WHSC1  Manual  (no external_id)  O96028  jsmith  2019-03-15  active
```

Same registrar, same day, `TGT-2xxx` prefix against `TGT-1xxx` for 429 of 437 rows. A
2019 manual batch that never got re-symbolised.

**Row symbol is neither approved nor a synonym (wrong mapping):**

```
PIM1   Q9P1W9 → entry is PIM2   (synonyms: none)
KISS1  Q969F8 → entry is KISS1R (synonyms: AXOR12, GPR54)
```

**Rows with no gene symbol at all — 12 rows, 6 accessions, in bindingdb and internal:**

```
P0DJJ0      SRG2C_HUMAN  SRGAP2C     SLIT-ROBO Rho GTPase-activating protein 2C
P01282      VIP_HUMAN    VIP         VIP peptides
P01584      IL1B_HUMAN   IL1B        Interleukin-1 beta
O14964      HGS_HUMAN    HGS         Hepatocyte growth factor-regulated TK substrate
A0A075B6I3  LVK55_HUMAN  IGLV11-55   Probable non-functional Ig lambda variable 11-55
P26447      S10A4_HUMAN  S100A4      Protein S100-A4
```

All six carry `TGT-9xxx` internal ids, `Manual` source, no `external_id` — a second
distinct bad-intake batch. Recoverable from the accession, and important for reconciliation
accuracy: key on gene symbol and these become six orphan records with empty names.

---

## Turn 7 — Deciding which field is wrong

For PIM1 the vote said the accession, but I wanted independent evidence. Checked every
ChEMBL row's `chembl_id` against the authority's ChEMBL xref:

```
mismatch  PIM1    Q9P1W9  CHEMBL2147  → authority says Q9P1W9 ↔ CHEMBL4523
mismatch  PDE10A  Q9Y233  CHEMBL3012  → authority says Q9Y233 ↔ CHEMBL4409
(2 of 292 rows)
```

Reverse-indexed the xrefs to see who owns the disputed ids:

```
CHEMBL2147 → P11309   (PIM1)
CHEMBL4523 → Q9P1W9   (PIM2)
CHEMBL3012 → Q13946   (PDE7A)
CHEMBL4409 → Q9Y233   (PDE10A)
```

That settles both:

- **PIM1 row:** `chembl_id` CHEMBL2147 is *correct* for PIM1, and `pref_name` is "pim-1".
  Gene, name and xref all say PIM1; only the accession says PIM2. **Correct the accession
  to P11309.** Critically, this also means the xref must *not* be reported as a mismatch —
  it's right, and the entry it disagrees with is the wrong one.
- **PDE10A row:** gene and accession agree with each other (Q9Y233 *is* PDE10A). The
  outlier is the `chembl_id`. **Correct it to CHEMBL4409.**

Cross-checked against the two files' own rows:

```
chembl  CHEMBL3012  PDE7A   Q13946  "High affinity 3',5'-cyclic-AMP phosphodiesterase 7A"
chembl  CHEMBL3012  PDE10A  Q9Y233  "cAMP and cAMP-inhibited cGMP ... 10A"    ← duplicated id
internal TGT-1392   PDE7A   Q13946  external_id CHEMBL3012
internal TGT-1390   PDE10A  Q9Y233  external_id CHEMBL4409                    ← independent confirmation
```

The internal registry records CHEMBL4409 for PDE10A. Two independent sources agree against
the ChEMBL extract.

Also found `chembl_id` duplicated three times total — the third and fourth being
`CHEMBL2068` (EPHA2, P29317 + B5A968) and `CHEMBL4375` (SRPK1, Q96SB4 + B4DS61), both
already covered as retired accessions.

I checked the internal registry's `external_id` too. Filtering to rows where it's actually
populated (74 of 142 `source_db=ChEMBL` rows) produced **zero** mismatches. My first pass
had reported 68, all of them blank `external_id` values — a sparse column, not a defect.
Fixed the check to skip nulls.

---

## Turn 8 — Field-level sweep of the UniProt extract

To make sure no defect class was hiding in the non-identifier columns, I compared every
UniProt row's `length`, `protein_name`, `entry_name` and `database` against the authority:

```
mismatches: {'name': 1}
  ('name', P14416, DRD2, 'D(2) dopamine receptor', authority: 'Dopamine receptor D2')
```

Lengths, entry names and Swiss-Prot/TrEMBL flags all match. One name difference.

**I chose not to flag DRD2.** It's a descriptive protein name that UniProt itself revised;
nothing joins on it, the accession is correct, and the row resolves to exactly the right
target. Reporting it would be reporting the authority's editorial history as a defect in
the data. This is categorically different from SEPT9→SEPTIN9, which is an *identifier*
other systems join on, governed by HGNC, where staleness is a real integration hazard.

**Name-similarity sweep.** To close off the name fields entirely, I compared every row's
protein name against the authority's recommended, alternative and short names, flagging
anything below 0.72 similarity. 217 outliers, and I read through them:

```
bindingdb  GPBAR1  Q8TDU6  'TGR5'          → legitimate alias
bindingdb  ACE     P12821  'ACE protein'   → BindingDB "SYMBOL protein" convention
bindingdb  SRC     P12931  'SRC protein'   → same
bindingdb  MET     P08581  'MET protein'   → same
... 213 more, all the same two patterns
```

Every one is BindingDB's naming convention or a case variant. No further defect class
in the name fields.

**The `4immunitygroup` corruption.** Scanning for injected words found:

```
chembl    pref_name        'Nuclear receptor subfamily 4immunitygroup A member 1'
uniprot   protein_name     'Nuclear receptor subfamily 4immunitygroup A member 1'
bindingdb target_name      'Nuclear receptor subfamily 4immunitygroup A member 1'  (×2)
internal  registered_name  'Nuclear receptor subfamily 4immunitygroup A member 1'
pubs      context_sentence '...modulators of Nuclear receptor subfamily 4immunitygroup A member 1.'
```

Visibly corrupt — the real name is "Nuclear receptor subfamily 4 group A member 1" — but
it appears with the *identical* corruption in all five files. No source disagrees with any
other, so this is a generation artifact of the dataset, not a row-level data-quality
defect. **Not flagged.**

---

## Turn 9 — Isoform accessions

```
Q05086-3  UBE3A_HUMAN  Swiss-Prot  len 872  'Isoform III of Ubiquitin-protein ligase E3A'
Q13422-3  IKZF1_HUMAN  Swiss-Prot  len 432  'Isoform Ik3 of DNA-binding protein Ikaros'

Q05086    UBE3A_HUMAN  Swiss-Prot  len 875  'Ubiquitin-protein ligase E3A'      ['UBE3A']
Q13422    IKZF1_HUMAN  Swiss-Prot  len 519  'DNA-binding protein Ikaros'        ['IKZF1']
```

Both rows are internally consistent — the length matches the isoform, the name says
"Isoform". Nothing looks wrong until you check. But a target master's `primary_accession`
must be a primary accession, and an isoform id isn't one. Flagged at medium severity with
the canonical accession as the correction. Neither canonical id appears anywhere else in
the pack, so these would otherwise ship as the target's identity.

---

## Turn 10 — The literature table

The brief is explicit that `pmid` values are internal reference numbers, that some collide
with real PubMed records for unrelated papers, and that ambiguity is resolvable from
`context_sentence` alone. **I never fetched a pmid.** (I later added two tests to enforce
this structurally.)

Checked which mentions don't exist in any source's gene column:

```
674 mentions, 459 distinct
not in any source gene set: 1  →  ['PSA']
```

One. Pulled all seven PSA rows:

```
PSA  The cytosolic zinc aminopeptidase degraded tau and mitigated neurofibrillary
     pathology in transgenic models.
PSA  Sensitivity to puromycin distinguished this aminopeptidase activity in brain lysates.
PSA  Serum levels of the glycoprotein guided biopsy decisions in the population
     screening cohort.                                                          (×2 rows)
PSA  Rising titres after radical prostatectomy signalled biochemical recurrence
     ahead of imaging.
PSA  The kallikrein-family serine protease liquefies seminal coagulum through
     cleavage of semenogelins.
PSA  Free-to-total ratio of the circulating antigen improved specificity for
     malignant versus benign hyperplasia.
```

Two distinct entities. Resolved both against the authority:

```
GET /proteins?gene=PSA&taxid=9606&reviewed=true
  P55786 PSA_HUMAN  ['NPEPPS']  syn: ['PSA']        ← PSA is a GENE synonym
  P07602 SAP_HUMAN  ['PSAP']    syn: ['GLBA','SAP1']
  ... 4 more, none relevant

GET /proteins?gene=KLK3&taxid=9606&reviewed=true
  P07288 KLK3_HUMAN ['KLK3'] syn: ['APS']
    recommendedName: 'Prostate-specific antigen'  shortName: 'PSA'   ← PROTEIN short name
    altNames: ['Gamma-seminoprotein','Kallikrein-3','P-30 antigen','Semenogelase']
```

That's the shape of the trap: PSA reaches NPEPPS as a *gene* synonym and KLK3 as a
*protein* short name. An alias index built only from gene symbols finds one of them and
silently mis-attributes the other five rows.

I read the seven sentences and decided the split myself before writing any code:
**2 → NPEPPS, 5 → KLK3.**

Both targets are separately present in the pack (chembl CHEMBL2264/P55786 and
CHEMBL2099/P07288; uniprot, bindingdb likewise), so the attribution genuinely matters —
these are two real golden records competing for seven mentions.

### Building a scorer to reproduce that judgement

First attempt — flat token match across names, comments, GO terms and keywords: **5/6.**

```
OK  NPEPPS  {NPEPPS: 9,  KLK3: 1}   cytosolic zinc aminopeptidase ...
OK  NPEPPS  {NPEPPS: 15, KLK3: 4}   Sensitivity to puromycin ...
OK  KLK3    {NPEPPS: 2,  KLK3: 4}   Serum levels of the glycoprotein ...
OK  KLK3    {NPEPPS: 0,  KLK3: 4}   Rising titres after radical prostatectomy ...
OK  KLK3    {NPEPPS: 4,  KLK3: 21}  The kallikrein-family serine protease ...
BAD NPEPPS  {NPEPPS: 7,  KLK3: 5}   Free-to-total ratio of the circulating antigen ...
```

Added weight tiers (names ×6, comments ×2, GO/keywords ×1) — still **5/6**, now failing on
a different sentence:

```
BAD KLK3 got NPEPPS  {NPEPPS: 5, KLK3: 3}  Serum levels of the glycoprotein ...
```

Rather than tune the weights until the number moved, I looked for the cause. The scorer
had a loose substring rule (`w in v or v in w`, score 1) which meant "glyco**protein**"
matched anything containing "protein" — for both candidates. Generic fragments were
carrying weight.

Checked the discriminating evidence actually available:

```
P55786 keywords: [... 'Aminopeptidase','Cytoplasm','Hydrolase','Metalloprotease',
                  'Nucleus','Protease','Zinc']
P07288 keywords: [... 'Disulfide bond','Glycoprotein','Hydrolase','Protease',
                  'Secreted','Serine protease','Signal','Zymogen']
```

"Glycoprotein" and "Secreted" are KLK3-only. Dropped the substring rule, keeping exact
match (3) and six-character stem match (2) — which preserves "prostatectomy"~"prostate"
and "semenogelins"~"semenogelase" while killing the generic fragments:

```
OK  NPEPPS  {NPEPPS: 48, KLK3: 0}
OK  NPEPPS  {NPEPPS: 72, KLK3: 3}
OK  KLK3    {NPEPPS: 0,  KLK3: 3}    ← 'glycoprotein' now exact-matches the keyword
OK  KLK3    {NPEPPS: 0,  KLK3: 14}
OK  KLK3    {NPEPPS: 3,  KLK3: 56}
OK  KLK3    {NPEPPS: 12, KLK3: 30}
6 / 6
```

Matches the split I'd read by hand. No hardcoding — the profile is built entirely from
what the authority returns for each candidate.

---

## Turn 11 — Building the tool

Structure: `sources` (load + tolerant column matching) → `resolve` (row → identity,
recording *how*) → `audit` (row vs authority → findings) → `reconcile` (group → golden
records) → `publications` (attribute + disambiguate) → `solve` (orchestrate, emit).

Stdlib only, which removes JFrog availability, wheel-build and `pip check` conflict risk
from the submission entirely.

Two decisions that carry most of the accuracy:

1. **Key records on the canonical primary accession, not the gene symbol.** That's what
   collapses the 30 retired accessions into the records they belong to, and what lets the
   12 blank-symbol rows land correctly.
2. **Run the audit before assembly and correct what it proves.** When the audit
   establishes that ChEMBL's PIM1 row carries PIM2's accession, it re-points that row at
   P11309 so the correction lands in the golden record rather than sitting beside it.

First end-to-end run: 15 s, but **667 findings**. Three problems.

### Bug 1 — 600 false organism positives

```
ORG SAMPLE: [('Homo sapiens (Human)', 'Homo sapiens', ['uniprot']), ...]
```

My normaliser stripped parentheses to "homo sapiens human" and then failed an equality
test against `{"homo sapiens", "human"}`. Exactly the false-positive class I'd spotted in
Turn 1 and then walked into anyway.

Replaced equality with a token-subset test against the union of the authority's scientific
and common names, plus abbreviation handling. `{homo, sapiens, human} ⊆ {homo, sapiens,
human}` passes; `{mus, musculus}` fails. All four spellings now accepted; a genuinely
different organism still caught.

### Bug 2 — spurious ambiguity

```
GAGE12I | GAGE12I -> GAGE12I  ['GAGE12I' resolves to more than one target: O76087 = GAGE7; P0CL82 = GAGE12I]
PIM1    | PIM1    -> PIM2     ['PIM1' resolves to more than one target: P11309 = PIM1; Q9P1W9 = PIM2]
```

Two causes. GAGE12I is the *approved* symbol of P0CL82 and merely a legacy alias of
GAGE7 — so it isn't ambiguous at all. Added a rule: a mention that is the approved symbol
of exactly one candidate resolves to it outright.

PIM1 was a downstream symptom of the mis-mapping. The ChEMBL PIM1 row was contributing the
symbol "PIM1" to Q9P1W9's alias set. It vanished once corrections were applied before
assembly — and it confirmed the ordering decision was doing real work.

### Bug 3 — mis-mapped rows landing in the wrong record

Related to Bug 2 but worse for scoring. Because Q9P1W9 resolves fine, the ChEMBL PIM1 row
was silently folding into PIM2's golden record — adding `chembl` to PIM2's sources and
removing it from PIM1's. Same for KISS1/Q969F8. Reordered the pipeline so the audit's
corrections are reflected in assembly.

After all three fixes:

```
loaded 2578 structured rows, 674 literature mentions
authority: 609 entries cached, 30 unresolved, 8 http calls
resolved 2578/2578 rows
attributed 674 literature mentions to 459 targets
audit produced 65 findings (10 http calls, 12.7s)
unique_target_count: 609
```

Verified the corrections landed:

```
PIM1    P11309  ['bindingdb','chembl','internal','publications','uniprot']   ← chembl restored
PIM2    Q9P1W9  ['bindingdb','chembl','internal','publications','uniprot']
KISS1   Q15726  ['bindingdb']
KISS1R  Q969F8  ['bindingdb','chembl','internal','publications','uniprot']
UBE3A   Q05086  ['uniprot']                                                  ← isoform collapsed
IKZF1   Q13422  ['bindingdb','publications','uniprot']
SEPTIN9 Q9UHD8  ['internal']                                                 ← approved symbol
NSD2    O96028  ['internal']
VIP     P01282  ['bindingdb','internal']                                     ← recovered from blank
MAPK10  P53779  ['bindingdb','chembl','internal','publications','uniprot']    ← JNK3 row merged in
AXL     P30530  ['bindingdb','chembl','internal','publications','uniprot']    ← UFO row merged in
```

---

## Turn 12 — Test suite

Built a 21-row fixture pack where every defect class fires at least once *and* several
deliberately messy-but-valid rows do not — PIM2→Q9P1W9 (the same accession, legitimately),
the correct CHEMBL2147 on the mis-mapped PIM1 row, all four human spellings.

`tests/offline_client.py` replays a recorded authority snapshot by overriding **only**
`_get`, the HTTP transport. Everything about the resolution, merge-proof and audit logic is
the real code. Snapshot pruned from 2.9 MB to 445 KB by keeping only the fields the tool
reads (a full entry is ~90% PDB xrefs).

31 tests: output contract, each defect class, the negative controls, and three failure
modes — empty pack, dead network, pack missing two of five files. The dead-network test
asserts **zero** findings; degraded means silent, not speculative. A final test runs
`solve.py` as a real subprocess and parses its entire stdout.

Two tests enforce the pmid rule structurally rather than by convention: one asserts the
only outbound host anywhere in the package is `www.ebi.ac.uk`, the other records every
client call and asserts no pmid value ever appears in any request parameter.

```
Ran 31 tests in 2.5s
OK
```

(My first version of the host test grepped for the string "pubmed" and failed on a
docstring saying pmids are *not* PubMed ids. Replaced it with the host assertion, which
tests the actual property.)

Verified 3.11 compatibility by AST feature-version check across all files, since
`goldentarget_config.json` declares 3.11 and an unsupported value silently falls back.

---

## Final result

```
unique_target_count: 609
findings: 65, covering 81 defective rows
wall clock: 12s   |   authority calls: 10   |   budget: 300s

duplicate_identity        30 findings /  32 rows   high
stale_symbol              22 findings /  22 rows   low
missing_identifier         6 findings /  12 rows   medium
wrong_mapping              2 findings /   2 rows   critical
ambiguous_mention          2 findings /   7 rows   medium
isoform_accession          2 findings /   5 rows   medium
cross_reference_mismatch   1 finding  /   1 row    high
```

### Not flagged, deliberately

| Observation | Reason |
|---|---|
| Four spellings of human across 1,239 BindingDB rows | Same claim, different spelling. Resolves to the same identity. |
| `D(2) dopamine receptor` vs `Dopamine receptor D2` | Descriptive name UniProt itself revised. Nothing joins on it; accession correct. |
| `4immunitygroup` corruption | Identical in all five files — generation artifact, no source disagreement. |
| 217 name-similarity outliers | All BindingDB `SYMBOL protein` convention or case variants. |
| 68 blank `external_id` on `source_db=ChEMBL` rows | Sparse column, not a wrong value. |
| All 674 pmids | Internal reference numbers. Never fetched, per the brief. |

Flagging the organism spellings alone would have buried 65 real findings under 1,200 false
ones. Precision counts against you when you get it wrong.
