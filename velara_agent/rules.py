"""Business rules, each tied to the ask_lead answer that established it ([lead-ref] tag).
docs/LEAD_INTERVIEWS.md lists the question behind every tag."""

# Earliest ship = Demand Loaded Date + site lead time; never loaded -> RefreshDate + lead time.
REF_LEAD_TIME = "R4-Q03-8950"
# Late setup pushes a date later but does not restart the lead-time clock.
REF_LEAD_TIME_NOT_FROM_SETUP = "R4-Q12-7c89"

# Patients titrate up from 2MG; a market goes live only when its 2MG pack is ready.
STARTING_DOSE = "2MG"
REF_STARTING_DOSE = "R4-Q04-611e"

# Actual Approval Date is the anchor; a passed Target with blank Actual means NOT approved, and
# there is no numeric re-forecast rule (best case: approval today).
REF_APPROVAL_ANCHOR = "R4-Q05-3d55"
RELIANCE_PATHWAYS = {"reliance", "mutual recognition"}      # Column Guide
# Only MTO markets pre-position stock in a customs-bonded warehouse; all others ship on/after approval.
BONDED_STRATEGIES = {"mto"}
REF_SHIP_ON_OR_AFTER_APPROVAL = "R4-Q15-86d0"

# Dashboard: approval = Actual else Target; readiness fails only on explicit "No"; schedule =
# expected date not past; a market is GREEN if ANY of its pack rows passes.
REF_DASHBOARD = "R4-Q06-cbf5"

# Slippage is measured from the CURRENT claimed date; a claim already past slips to RefreshDate.
REF_SLIPPAGE_MEASURE = "R4-Q07-9d48"
# Unrecorded drift is a defect, but not slippage by itself.
REF_DRIFT_NOT_SLIPPAGE = "R4-Q14-a89a"

# Capacity: annual capacity covers every market a site supplies, against Year-1 volume; only
# loaded demand counts; queue by demand-load date, the back of the queue slips
# ceil(excess / (capacity / 365)) days. Via Hub queues on the hub's date; with no visible date it
# sits behind dated loads; same-day loads are treated equally.
REF_CAPACITY_SCOPE = "R4-Q08-3464"
REF_CAPACITY_QUEUE = "R4-Q09-4ea3"
REF_CAPACITY_DAYS = "R4-Q13-29de"
REF_CAPACITY_LOADED_ONLY = "R4-Q16-7d67"
REF_CAPACITY_QUEUE_TIES = "R4-Q19-48ac"
DAYS_PER_YEAR = 365

# Via Hub markets legitimately leave their own setup/demand fields blank; demand counts as loaded.
HUB_ROUTES = {"via hub"}
REF_VIA_HUB_LOADED = "R4-Q17-f8f3"

# A Secondary site is set up after launch by design.
SECONDARY_ROLES = {"secondary"}

CANONICAL_STATUS = "Active"
KNOWN_STATUSES = {"active", "inactive", "on hold", "paused", "withdrawn", "cancelled", "launched"}
