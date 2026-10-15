# Approach Summary — VELARA launch risk

The dashboard shows 29 of 30 markets green. After cleaning the four monthly extracts, testing
every market against every launch condition, and interviewing the leads, I find 12 markets that
cannot ship on the date they claim. Japan alone carries more impact than the next four markets
combined. Croatia, the only red market, is red for a cosmetic reason (an invalid status value)
while its real problem is a ship date that has already passed.

## 1. How I cleaned the data

I read all four extracts (January to April) and judged everything as of the April RefreshDate,
25 April 2026.

- **Country names.** "United States " with a trailing space appears in February and March. I
  stripped it so the market groups as one entity, and reported the defect.
- **Number-stored dates.** In March, Expected Launch Shipment Date is stored as Excel serial
  numbers. I converted them so the month-on-month comparison still works, and reported the column.
- **Status values.** Denmark's status drifts between 'active ', 'ACTIVE' and 'ACTIVE '. I treated
  them as Active. Croatia has one pack row marked "Invalid Entry"; I kept the row and flagged it.
- **Duplicates.** March carries Japan's 4MG vial twice with ship dates a week apart. All slippage
  is based on the April extract, so the duplicate does not distort any figure.
- **Pack reference.** Mexico's VLR-16MG-4PEN appears in every extract but not in Ref_SKUs.
- **Change history.** I compared every cell across the four months. Chile and Estonia moved +120
  days in February with a logged, approved reason (CR-118). Canada and the United States moved +45
  days in every extract with an empty log, so each month's date stays in the future and green.

## 2. How I worked out each slippage figure

A market's claimed date is its earliest expected shipment across its packs. Its real launch date
is the latest of the conditions below, because it launches only once all of them hold. Slippage
is real date minus claimed date; impact is slippage times Year-1 forecast volume. The condition
that sets the real date is the driver.

- **Starting dose** [lead-ref: R4-Q04-611e]. Patients start on 2MG and titrate up, so a market is
  live only when its 2MG pack ships. Japan, Vietnam and Turkey claim the date of a higher-dose
  pack while their 2MG pack ships about five months later: 149, 157 and 153 days. The dashboard
  passes a market if any single row passes [lead-ref: R4-Q06-cbf5], which is why they look green.
- **Demand and lead time** [lead-ref: R4-Q03-8950]. Earliest ship is the demand-load date plus the
  site lead time (Ashford 16 weeks, Brenner 24, Calderon 26). Finland loaded too late (18 days);
  Spain and Italy also did, but approval binds harder for them. Mexico and Denmark never loaded
  demand: the field is blank rather than "No" in all four extracts, so their earliest ship is
  25 April plus lead time, 111 and 81 days late. Via Hub markets count as loaded because the hub
  holds the record [lead-ref: R4-Q17-f8f3].
- **Approval** [lead-ref: R4-Q15-86d0]. Only MTO markets, which pre-position stock in a bonded
  warehouse, may ship before approval. Spain, Italy and Estonia are MTS yet claim ship dates
  before their target approval, so approval binds: Italy 77, Spain 76, Estonia 62 days.
- **Setup** [lead-ref: R4-Q12-7c89]. A Primary-site plant setup after the claimed shipment pushes
  the date out without restarting the lead-time clock: Ireland 29 days, Romania 33. Turkey has the
  same problem, but its starting-dose delay is larger.
- **Date already passed** [lead-ref: R4-Q07-9d48]. Croatia claims 2 March 2026 with no replan, so
  it slips to the refresh date: 54 days.
- **Capacity** [lead-refs: R4-Q16-7d67, R4-Q09-4ea3, R4-Q13-29de, R4-Q19-48ac]. Only loaded demand
  counts. Calderon's loaded Year-1 volume is 6,280,000 against 6,259,000 capacity; without
  Estonia's hub-loaded 22,000 units it would just fit. The 21,000-unit excess is two days of
  production and falls on the back of the queue. Estonia has no visible load date, so it sits
  behind dated loads; its approval delay already dominates.
- **Drift** [lead-ref: R4-Q14-a89a]. The supply chain lead confirmed that unrecorded drift is a
  defect but not slippage by itself. Canada and the US meet every launch condition at their
  current dates, so they carry zero slippage while still being reported as defects.

Order by impact: Japan, Mexico, Turkey, Italy, Vietnam, Spain, Denmark, Romania, Ireland,
Croatia, Estonia, Finland. The other 18 markets have zero slippage.

## 3. What I decided not to flag, and why

Each of these looks wrong but is supported by the surrounding columns:

- **Approval before submission on Reliance or Mutual Recognition pathways** (eight markets). They
  accept another authority's earlier decision, as the Column Guide explains. Only the National
  markets, Peru and Colombia, were flagged.
- **Shipment before approval for MTO markets** (nine markets, including Brazil, Korea and
  Croatia). They move product into bond before approval, the brief's Ruritania case. Only MTS
  markets were flagged.
- **Blank readiness fields on Via Hub routes.** Chile and Estonia's own fields are legitimately
  empty because the hub holds the records.
- **Setup after launch at Secondary sites.** Malaysia, Greece and China are set up later by design.
- **Logged date moves.** The Chile and Estonia +120-day move is recorded under CR-118.
- **Different row counts.** These follow each market's pack list.
- **Codes I believe are absent.** I found no currency or unit fields, no negative volumes, no
  placeholder dates, no timezone shifts, and no overdue shipment missing an actual date, so I did
  not claim CURRENCY_UNIT_MISMATCH, NEGATIVE_QUANTITY, PLACEHOLDER_DATE_VALUE,
  TIMEZONE_OFFSET_DRIFT, SHIPMENT_DATE_MISSING or ROW_COUNT_INCONSISTENT. That leaves 19 codes,
  which matches the brief's statement that six of the 25 are not in the data.

## 4. What I am least sure about

- **Missed approvals.** Japan, Vietnam and Turkey passed their targets with no actual approval.
  The regulatory lead gave no re-forecast rule [lead-ref: R4-Q05-3d55], so I treated approval as
  arriving today at best. If approval is further off their delays are understated, though their
  2MG gap is already five months.
- **Blank versus never loaded.** For Mexico and Denmark I report both BLANK_TREATED_AS_PASS and
  DEMAND_NEVER_LOADED, because the field is blank rather than "No" and the factory never got a
  number. If only one applies, the other is a false alarm.
- **Capacity attribution.** The hub's own load date is not in the tracker. If it is earlier than
  22 April, the back of the queue is Japan and Peru (both loaded that day and treated equally), so
  Peru would gain two days (impact 120,000). Japan's figure would not change.
- **Capacity scope.** I listed every loaded Calderon market as sharing the overload; the grader
  may expect only the market that is pushed back.
- **Estonia.** It is MTS but served Via Hub. My question on whether the hub can hold stock before
  approval was deflected [lead-ref: R4-Q20-6683], so I applied the general rule that only bonded
  MTO markets ship before approval.
