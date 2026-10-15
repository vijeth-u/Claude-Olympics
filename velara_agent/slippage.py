"""How late can each market really ship, and why.

Claimed date = earliest Expected Launch Shipment Date across the market's packs in the latest
extract [R4-Q07-9d48]. Real launch date = the LATEST of every constraint, because a market
launches only when all hold at once:
  starting_dose  2MG pack's own ship date (patients start on 2MG) [R4-Q04-611e]
  demand         demand-load date + site lead time; never loaded on a Direct route ->
                 RefreshDate + lead time [R4-Q03-8950]; Via Hub counts as loaded [R4-Q17-f8f3]
  setup          Primary-site plant/warehouse setup of the 2MG pack [R4-Q12-7c89]
  approval       non-MTO markets ship on/after approval [R4-Q15-86d0]; missed target with no
                 actual -> best case today [R4-Q05-3d55]
  passed         a claim already in the past slips to RefreshDate [R4-Q07-9d48]
  capacity       site overload queue [R4-Q09-4ea3, R4-Q13-29de, R4-Q16-7d67, R4-Q19-48ac]
Unrecorded drift is not slippage by itself [R4-Q14-a89a].
"""
import datetime as dt
import math
from collections import defaultdict

from . import rules
from .model import norm

DRIVER_CODES = {
    "starting_dose": "STARTING_DOSE_UNAVAILABLE", "demand_late": "DEMAND_LOADED_TOO_LATE",
    "demand_never": "DEMAND_NEVER_LOADED", "setup": "SETUP_DATE_AFTER_SHIPMENT",
    "approval_pending": "SHIPMENT_BEFORE_APPROVAL", "approval_missed": "APPROVAL_TARGET_NOT_ACTUAL",
    "passed": "DATE_PASSED_NO_REPLAN", "capacity": "PLANT_CAPACITY_EXCEEDED",
}
PRIORITY = ["approval_missed", "approval_pending", "starting_dose", "demand_never", "demand_late",
            "setup", "capacity", "passed"]


def yes(v):
    return isinstance(v, str) and v.strip().casefold() == "yes"


def is_hub(r):
    return norm(r.get("supply_route")) in rules.HUB_ROUTES


def is_bonded(r):
    return norm(r.get("demand_strategy")) in rules.BONDED_STRATEGIES


def is_secondary(r):
    return norm(r.get("site_role")) in rules.SECONDARY_ROLES


def is_reliance(r):
    return norm(r.get("pathway")) in rules.RELIANCE_PATHWAYS


def demand_is_loaded(r):
    return yes(r.get("demand_flag")) or r.get("demand_date") is not None or is_hub(r)


def claim_date(rows):
    ds = [r["expected_ship"] for r in rows if r["expected_ship"]]
    return min(ds) if ds else None


def starting_rows(rows):
    return [r for r in rows if norm(r.get("dose")) == rules.STARTING_DOSE.casefold()]


def trusted_approval(r):
    """Actual approval, unless it predates the market's own filing on a National pathway."""
    a, s = r.get("actual_approval"), r.get("actual_submission")
    if a and s and a < s and not is_reliance(r):
        return None
    return a


def capacity_queue(pack):
    """Per site: loaded Year-1 volume vs annual capacity, and which markets are pushed back.

    Markets queue by demand-load date; a hub market with no visible date sits behind dated loads;
    same-day loads are one bucket and are treated equally. Buckets are taken from the back until
    the excess is covered; each pushed market slips ceil(excess / (capacity / 365)) days.
    """
    by_site = defaultdict(list)
    for c, rows in pack.latest.by_country().items():
        r0 = rows[0]
        site = (r0.get("site") or "").strip()
        if site in pack.sites and demand_is_loaded(r0):
            by_site[site].append((r0.get("demand_date") or dt.date.max, c,
                                  pack.countries.get(c, {}).get("volume", 0)))
    out = {}
    for site, members in by_site.items():
        cap = pack.sites[site]["capacity"]
        loaded = sum(m[2] for m in members)
        excess = loaded - cap
        res = {"capacity": cap, "loaded": loaded, "excess": max(0, excess),
               "markets": sorted(m[1] for m in members), "pushed": [], "slip_days": 0}
        if excess > 0 and cap > 0:
            res["slip_days"] = math.ceil(excess / (cap / rules.DAYS_PER_YEAR))
            buckets = defaultdict(list)
            for d, c, v in members:
                buckets[d].append((c, v))
            covered = 0
            for d in sorted(buckets, reverse=True):
                if covered >= excess:
                    break
                for c, v in buckets[d]:
                    res["pushed"].append(c)
                    covered += v
        out[site] = res
    return out


def assess(pack):
    today = pack.today
    cap = capacity_queue(pack)
    pushed = {c: r["slip_days"] for r in cap.values() for c in r["pushed"]}
    by_country = pack.latest.by_country()
    results = {}
    for c in pack.all_countries():
        rows = by_country.get(c, [])
        volume = pack.countries.get(c, {}).get("volume", 0)
        claim = claim_date(rows)
        if not rows or claim is None:
            results[c] = {"country": c, "claim": None, "real": None, "slip": 0, "volume": volume,
                          "driver": None, "constraints": {}}
            continue
        r0 = rows[0]
        site = pack.sites.get((r0.get("site") or "").strip())
        start = starting_rows(rows) or rows
        cons = {"starting_dose": claim_date(start), "passed": today}
        if site:
            lead = dt.timedelta(days=site["lead_days"])
            if r0.get("demand_date"):
                cons["demand_late"] = r0["demand_date"] + lead
            elif not demand_is_loaded(r0):
                cons["demand_never"] = today + lead
        if not is_secondary(r0):
            setups = [d for r in start for d in (r.get("plant_date"), r.get("wh_date")) if d]
            if setups:
                cons["setup"] = max(setups)
        if not is_bonded(r0):
            appr, target = trusted_approval(r0), r0.get("target_approval")
            if appr:
                cons["approval_pending"] = appr
            elif target and target >= today:
                cons["approval_pending"] = target
            elif target:
                cons["approval_missed"] = today
        if c in pushed:
            cons["capacity"] = claim + dt.timedelta(days=pushed[c])
        real = max(d for d in cons.values() if d)
        slip = max(0, (real - claim).days)
        driver = None
        if slip > 0:
            driver = min((k for k, d in cons.items() if d == real), key=PRIORITY.index)
        results[c] = {"country": c, "claim": claim, "real": real, "slip": slip, "volume": volume,
                      "driver": driver, "constraints": cons, "site": (r0.get("site") or "").strip()}
    return results, cap
