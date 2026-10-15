"""Turn assessments + findings into the contract JSON (findings, risk_ranking)."""
from . import rules
from .slippage import DRIVER_CODES

EVIDENCE = {
    "starting_dose": "2MG start pack ships {real}; claim {claim} is a higher-dose pack",
    "demand_never": "demand never loaded; {today}+{lw}w lead = {real} vs claim {claim}",
    "demand_late": "demand loaded {dd}+{lw}w lead = {real} vs claim {claim}",
    "setup": "Primary plant setup {real} after claim {claim}",
    "approval_pending": "MTS cannot ship before approval {real}; claim {claim}",
    "approval_missed": "target approval passed, no actual; best case {real} vs claim {claim}",
    "passed": "claim {claim} already passed, no replan; earliest {real}",
    "capacity": "site over capacity, back of queue; {real} vs claim {claim}",
}
REFS = {
    "starting_dose": rules.REF_STARTING_DOSE, "demand_never": rules.REF_LEAD_TIME,
    "demand_late": rules.REF_LEAD_TIME, "setup": rules.REF_LEAD_TIME_NOT_FROM_SETUP,
    "approval_pending": rules.REF_SHIP_ON_OR_AFTER_APPROVAL, "approval_missed": rules.REF_APPROVAL_ANCHOR,
    "passed": rules.REF_SLIPPAGE_MEASURE, "capacity": rules.REF_CAPACITY_DAYS,
}


def _evidence(pack, a, drifted):
    if not a["slip"]:
        if a["claim"] is None:
            return "no tracker rows; nothing to assess"
        if a["country"] in drifted:
            return (f"date slid silently each extract (flagged) but every launch condition is met by "
                    f"current claim {a['claim'].isoformat()}; drift alone is not slippage "
                    f"[lead-ref: {rules.REF_DRIFT_NOT_SLIPPAGE}]")
        return f"no problem found: every launch condition met by claim {a['claim'].isoformat()}"
    k = a["driver"]
    site = pack.sites.get(a.get("site"), {})
    rows = pack.latest.by_country().get(a["country"], [])
    dd = rows[0].get("demand_date") if rows else None
    txt = EVIDENCE[k].format(real=a["real"].isoformat(), claim=a["claim"].isoformat(),
                             today=pack.today.isoformat(), lw=site.get("lead_weeks", "?"),
                             dd=dd.isoformat() if dd else "")
    return f"{txt}: {a['slip']}d x {a['volume']:,} [lead-ref: {REFS[k]}]"


def build(pack, assessments, findings):
    drifted = {c for f in findings if f["what"] == "DATE_MOVED_WITHOUT_RECORD" for c in f["where"]}
    ranked = sorted(assessments.values(),
                    key=lambda a: (-(a["slip"] * a["volume"]), -a["slip"], -a["volume"], a["country"]))
    risk = [{
        "country": a["country"], "rank": i, "slippage_days": int(a["slip"]),
        "impact_score": int(a["slip"] * a["volume"]),
        "driver": DRIVER_CODES[a["driver"]] if a["slip"] else "NONE",
        "evidence": _evidence(pack, a, drifted),
    } for i, a in enumerate(ranked, start=1)]
    return {"findings": [{"what": f["what"], "where": ";".join(f["where"]), "proof": f["proof"]}
                         for f in findings],
            "risk_ranking": risk}
