# Lead interviews (velara-agent `ask_lead`)

All 20 questions of the round are spent. The answers are summarised in our own words; the
`[lead-ref]` tag is the evidence that each answer came from the lead. Questions without a usable
rule are listed so the budget is fully accounted for.

Lesson learned: the leads route on topic keywords. Multi-part questions, and questions using
another lead's vocabulary (for example "MTS" to the regulatory lead), were deflected but still
charged. Short, single-topic questions worked.

| # | Lead | Question (short form) | Rule used | Tag |
|---|---|---|---|---|
| 1 | manufacturing_lead | Four-part capacity question | Deflected | none |
| 2 | supply_chain_lead | Same capacity question, reworded | Deflected | none |
| 3 | manufacturing_lead | How do you check a site has capacity for its markets? | Earliest ship = demand-load date + site lead time; never loaded = RefreshDate + lead time | R4-Q03-8950 |
| 4 | demand_planner | How does a patient start; which dose must be available? | Patients titrate up from 2MG; a market goes live only when 2MG is ready | R4-Q04-611e |
| 5 | regulatory_lead | Target passed, Actual blank: what approval date to plan around? | Actual approval is the anchor; missed target = not approved; no numeric re-forecast rule | R4-Q05-3d55 |
| 6 | distribution_lead | Dashboard's three tests, and how rows combine | Approval = Actual else Target; readiness fails only on explicit "No"; schedule = date not past; GREEN if ANY row passes | R4-Q06-cbf5 |
| 7 | supply_chain_lead | Date slides every extract with empty log: how late is it? | Measure from the current claim; a claim already past slips to the RefreshDate | R4-Q07-9d48 |
| 8 | manufacturing_lead | Launch build = full Year-1 volume or a fraction? | Annual capacity covers every market the site supplies, against Year-1 volume | R4-Q08-3464 |
| 9 | manufacturing_lead | When builds exceed capacity, who is pushed back? | Queue by demand-load date; the back of the queue slips | R4-Q09-4ea3 |
| 10 | regulatory_lead | Can an MTS market ship before approval? | Deflected | none |
| 11 | manufacturing_lead | Same MTS question | Superseded by #15 | none |
| 12 | manufacturing_lead | Does lead time start from plant setup? | Setup can push a date later but does not restart the lead-time clock | R4-Q12-7c89 |
| 13 | manufacturing_lead | How do excess units become days? | Slip days = excess / (annual capacity / 365), rounded up | R4-Q13-29de |
| 14 | supply_chain_lead | Is total unrecorded movement the slippage? | No: unrecorded drift is a defect, not slippage by itself | R4-Q14-a89a |
| 15 | demand_planner | Non-bonded market: is approval the earliest ship date? | Yes: only bonded (MTO) markets ship before approval | R4-Q15-86d0 |
| 16 | manufacturing_lead | Does unloaded demand count toward site volume? | No: only loaded demand counts | R4-Q16-7d67 |
| 17 | distribution_lead | Via Hub with blank Demand Loaded: treat as loaded? | Yes: the hub holds the record | R4-Q17-f8f3 |
| 18 | demand_planner | Maximum time MTO stock may sit in bond? | No limit given; MTO pre-approval shipments left unflagged | none used |
| 19 | manufacturing_lead | Where does an undated Via Hub market sit in the queue; same-day ties? | Hub date governs; with no visible load date it sits behind dated loads; same-day loads are treated equally | R4-Q19-48ac |
| 20 | distribution_lead | Can MTS Via Hub stock sit at the hub before approval? | Deflected to manufacturing; rule #15 stands | R4-Q20-6683 |
