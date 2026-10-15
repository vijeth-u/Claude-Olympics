"""Defect detectors. Each returns findings {"what", "where": [...], "proof"} and rules out the
legitimate look-alikes: approval before filing on Reliance / Mutual Recognition; shipment before
approval for MTO (bonded warehouse); blank readiness fields on Via Hub; setup after launch at a
Secondary site; date moves with a change-log entry; row counts explained by each pack list.
"""
import datetime as dt
from collections import defaultdict

from . import rules
from .model import COLUMNS, norm
from .slippage import (claim_date, demand_is_loaded, is_bonded, is_hub, is_reliance,
                       is_secondary, starting_rows, yes)

PROOF_MAX = 200


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _d(x):
    return x.isoformat() if isinstance(x, dt.date) else str(x)


def finding(code, where, proof):
    where = sorted(set(w for w in where if w))
    if not where:
        return []
    proof = " ".join(proof.split())
    if len(proof) > PROOF_MAX:
        proof = proof[:PROOF_MAX - 3] + "..."
    return [{"what": code, "where": where, "proof": proof}]


def country_whitespace(pack, ctx):
    hits, tabs = set(), set()
    for e in pack.extracts:
        for r in e.rows:
            raw = r["country_raw"] or ""
            if raw != raw.strip():
                hits.add(r["country"])
                tabs.add(e.name[-7:])
    return finding("COUNTRY_NAME_WHITESPACE", hits,
                   f"Trailing space on the name in extracts {', '.join(sorted(tabs))} splits it into "
                   "two markets when grouped; stripped before analysis.")


def status_values(pack, ctx):
    drift, invalid = defaultdict(set), defaultdict(set)
    for e in pack.extracts:
        for r in e.rows:
            raw = r.get("status")
            if raw is None:
                continue
            if norm(raw) in rules.KNOWN_STATUSES:
                if norm(raw) == rules.CANONICAL_STATUS.casefold() and raw != rules.CANONICAL_STATUS:
                    drift[r["country"]].add(repr(raw))
            else:
                invalid[r["country"]].add(repr(raw))
    out = []
    if drift:
        v = sorted({x for s in drift.values() for x in s})
        out += finding("STATUS_VALUE_DRIFT", drift,
                       f"Launch Status written {', '.join(v)} across extracts instead of 'Active': "
                       "same status, inconsistent casing and whitespace.")
    if invalid:
        v = sorted({x for s in invalid.values() for x in s})
        out += finding("STATUS_INVALID_ENTRY", invalid,
                       f"Launch Status {', '.join(v)} on one pack row (April) is not a real status; "
                       "the market's other rows say Active.")
    return out


def date_stored_as_number(pack, ctx):
    cols, tabs = set(), set()
    for e in pack.extracts:
        if e.numeric_date_columns:
            cols |= set(e.numeric_date_columns)
            tabs.add(e.name)
    return finding("DATE_STORED_AS_NUMBER", cols,
                   f"In {', '.join(sorted(tabs))} this column holds Excel serial integers (e.g. 46416) "
                   "with no date format, so it loads as int; converted from serial for the diff.")


def duplicate_rows(pack, ctx):
    hits, detail = set(), []
    for e in pack.extracts:
        groups = defaultdict(list)
        for r in e.rows:
            groups[(r["country"], r["sku"])].append(r)
        for (country, sku), rs in groups.items():
            if len(rs) < 2:
                continue
            conflicts = [COLUMNS[k] for k in COLUMNS if k != "country" and len({_d(r.get(k)) for r in rs}) > 1]
            if conflicts:
                hits.add(country)
                vals = " vs ".join(_d(r.get("expected_ship")) for r in rs)
                detail.append(f"{country} {sku} appears {len(rs)}x in {e.name} with conflicting "
                              f"{'; '.join(conflicts)} ({vals})")
    return finding("DUPLICATE_ROW", hits, "; ".join(detail) + ".")


def sku_not_in_reference(pack, ctx):
    hits, skus = set(), set()
    for e in pack.extracts:
        for r in e.rows:
            if r["sku"] and r["sku"].strip() not in pack.skus:
                hits.add(r["country"])
                skus.add(r["sku"].strip())
    return finding("SKU_NOT_IN_REFERENCE", hits,
                   f"{', '.join(sorted(skus))} is in every extract but not in Ref_SKUs, which lists "
                   "only 2/4/8/12MG packs.")


def approval_before_submission(pack, ctx):
    hits, excused = [], set()
    for c, rows in pack.latest.by_country().items():
        r = rows[0]
        a, s = r["actual_approval"], r["actual_submission"]
        if a and s and a < s:
            if is_reliance(r):
                excused.add(c)
            else:
                hits.append((c, f"{c} {_d(a)}<{_d(s)}"))
    return finding("APPROVAL_BEFORE_SUBMISSION", [h[0] for h in hits],
                   f"National pathway approved before its own filing ({'; '.join(h[1] for h in hits)}). "
                   f"{len(excused)} Reliance/Mutual Recognition markets show this legitimately.")


def approval_target_not_actual(pack, ctx):
    hits = []
    for c, rows in pack.latest.by_country().items():
        r = rows[0]
        if r["actual_approval"] is None and r["target_approval"] and r["target_approval"] < pack.today:
            hits.append((c, f"{c} {_d(r['target_approval'])}"))
    return finding("APPROVAL_TARGET_NOT_ACTUAL", [h[0] for h in hits],
                   f"Target approval passed, Actual still blank ({', '.join(h[1] for h in hits)}); "
                   f"dashboard uses Target as approval [lead-ref: {rules.REF_DASHBOARD}].")


def shipment_before_approval(pack, ctx):
    hits, excused = [], set()
    for c, rows in pack.latest.by_country().items():
        r0 = rows[0]
        anchor = r0["actual_approval"] or r0["target_approval"]
        if anchor is None:
            continue
        early = [r for r in rows if (r["expected_ship"] and r["expected_ship"] < anchor)
                 or (r["actual_ship"] and r["actual_ship"] < anchor)]
        if not early:
            continue
        if is_bonded(r0):
            excused.add(c)
        else:
            hits.append((c, f"{c} {_d(claim_date(rows))}<{_d(anchor)}"))
    return finding("SHIPMENT_BEFORE_APPROVAL", [h[0] for h in hits],
                   f"MTS (no bonded warehouse) ships before approval: {'; '.join(h[1] for h in hits)}. "
                   f"{len(excused)} MTO bonded markets excused [lead-ref: {rules.REF_SHIP_ON_OR_AFTER_APPROVAL}].")


def blank_and_never_loaded(pack, ctx):
    blank, never = set(), set()
    for c, rows in pack.latest.by_country().items():
        for r in rows:
            if is_hub(r):
                continue
            if any(_blank(r[k]) for k in ("plant_flag", "wh_flag", "order_flag", "demand_flag")):
                blank.add(c)
            if not demand_is_loaded(r):
                never.add(c)
    for e in pack.extracts:  # "never" = not loaded in ANY extract
        for r in e.rows:
            if r["country"] in never and demand_is_loaded(r):
                never.discard(r["country"])
    out = finding("BLANK_TREATED_AS_PASS", blank,
                  "Direct-route markets leave Plant setup and Demand Loaded blank, not No; dashboard "
                  f"fails only an explicit No [lead-ref: {rules.REF_DASHBOARD}]. Via Hub blanks are legit.")
    out += finding("DEMAND_NEVER_LOADED", never,
                   "Demand Loaded blank and undated in all 4 extracts on a Direct route: factory never "
                   f"got a number; earliest = refresh + lead time [lead-ref: {rules.REF_LEAD_TIME}].")
    return out


def flag_yes_but_no_date(pack, ctx):
    hits, fields = set(), set()
    pairs = (("plant_flag", "plant_date"), ("wh_flag", "wh_date"), ("demand_flag", "demand_date"))
    for c, rows in pack.latest.by_country().items():
        for r in rows:
            if is_hub(r):
                continue
            for f, d in pairs:
                if yes(r[f]) and r[d] is None:
                    hits.add(c)
                    fields.add(COLUMNS[d])
    return finding("FLAG_YES_BUT_NO_DATE", hits,
                   f"Flag says Yes but {', '.join(sorted(fields))} is blank on a Direct route (not a "
                   "hub market), so the Yes is unevidenced.")


def demand_loaded_too_late(pack, ctx):
    hits = []
    for c, rows in pack.latest.by_country().items():
        site = pack.sites.get((rows[0]["site"] or "").strip())
        dd = rows[0]["demand_date"]
        if not site or dd is None:
            continue
        earliest = dd + dt.timedelta(days=site["lead_days"])
        claim = claim_date(rows)
        if claim and earliest > claim:
            hits.append((c, f"{c} {_d(dd)}+{site['lead_weeks']}w={_d(earliest)}>{_d(claim)}"))
    return finding("DEMAND_LOADED_TOO_LATE", [h[0] for h in hits],
                   "Load + site lead time lands after claim: " + "; ".join(h[1] for h in hits)
                   + f" [lead-ref: {rules.REF_LEAD_TIME}].")


def setup_after_shipment(pack, ctx):
    hits, excused = [], set()
    for c, rows in pack.latest.by_country().items():
        late = [r for r in rows if r["expected_ship"] and (
            (r["plant_date"] and r["plant_date"] > r["expected_ship"])
            or (r["wh_date"] and r["wh_date"] > r["expected_ship"]))]
        if not late:
            continue
        if is_secondary(rows[0]):
            excused.add(c)
            continue
        r = min(late, key=lambda x: x["expected_ship"])
        setup = max(d for d in (r["plant_date"], r["wh_date"]) if d)
        hits.append((c, f"{c} {_d(setup)}>{_d(r['expected_ship'])}"))
    return finding("SETUP_DATE_AFTER_SHIPMENT", [h[0] for h in hits],
                   "Primary-site plant setup after ship: " + "; ".join(h[1] for h in hits)
                   + ". Secondary sites set up post-launch by design, excluded.")


def plant_capacity_exceeded(pack, ctx):
    out = []
    for site, res in ctx["capacity"].items():
        if res["excess"] > 0:
            out += finding("PLANT_CAPACITY_EXCEEDED", res["markets"],
                           f"{site} loaded Y1 volume {res['loaded']:,} > capacity {res['capacity']:,} "
                           f"(+{res['excess']:,} = {res['slip_days']}d); unloaded demand excluded "
                           f"[lead-ref: {rules.REF_CAPACITY_LOADED_ONLY}].")
    return out


def date_moved_without_record(pack, ctx):
    hits, detail = set(), defaultdict(list)

    def index(extract):
        g = defaultdict(list)
        for r in extract.rows:
            g[(r["country"], r["sku"])].append(r)
        return {k: v[0] for k, v in g.items() if len(v) == 1}

    for prev, cur in zip(pack.extracts, pack.extracts[1:]):
        p, q = index(prev), index(cur)
        for key in p.keys() & q.keys():
            a, b = p[key]["expected_ship"], q[key]["expected_ship"]
            if not a or not b or a == b:
                continue
            now, before = q[key]["change_log"], p[key]["change_log"]
            if _blank(now) or norm(now) == norm(before):
                hits.add(key[0])
                detail[key[0]].append((b - a).days)
    parts = [f"{c} +{sorted(set(v))[0]}d every extract" for c, v in sorted(detail.items())]
    return finding("DATE_MOVED_WITHOUT_RECORD", hits,
                   "Expected ship slides with empty change log: " + "; ".join(parts)
                   + ". Logged moves (CR-118) excluded.")


def date_passed_no_replan(pack, ctx):
    hits = []
    for c, rows in pack.latest.by_country().items():
        past = [r for r in rows if r["expected_ship"] and r["expected_ship"] < pack.today
                and _blank(r["change_log"])
                and (r["actual_ship"] is None or r["actual_ship"] < r["expected_ship"])]
        if past:
            hits.append((c, f"{c} {_d(min(r['expected_ship'] for r in past))}"))
    return finding("DATE_PASSED_NO_REPLAN", [h[0] for h in hits],
                   f"Committed ship date already before RefreshDate {_d(pack.today)}, no revised date "
                   f"or log: {'; '.join(h[1] for h in hits)} [lead-ref: {rules.REF_SLIPPAGE_MEASURE}].")


def starting_dose_and_rollup(pack, ctx):
    hits = []
    for c, rows in pack.latest.by_country().items():
        claim = claim_date(rows)
        start = claim_date(starting_rows(rows))
        if claim and (start is None or start > claim):
            hits.append((c, claim, start))
    if not hits:
        return []
    txt = "; ".join(f"{c} {_d(s)} vs {_d(k)}" for c, k, s in hits)
    green = [c for c, _, _ in hits if pack.dashboard.get(c) == "GREEN"]
    out = finding("STARTING_DOSE_UNAVAILABLE", [h[0] for h in hits],
                  f"2MG start pack ships after the claimed date: {txt} [lead-ref: {rules.REF_STARTING_DOSE}].")
    out += finding("ROLLUP_ANY_NOT_ALL", green,
                   "GREEN because a higher-dose pack passes while the 2MG start pack is not ready at the "
                   f"claimed date; dashboard passes a market if ANY row passes [lead-ref: {rules.REF_DASHBOARD}].")
    return out


def absent_pattern_checks(pack, ctx):
    """Reported only if genuinely present."""
    neg = [c for c, i in pack.countries.items() if i["volume"] < 0]
    out = finding("NEGATIVE_QUANTITY", neg, "Forecast Volume Year 1 recorded as a negative number.")
    ph_dates = {dt.date(1900, 1, 1), dt.date(1899, 12, 31), dt.date(1970, 1, 1),
                dt.date(2099, 12, 31), dt.date(9999, 12, 31)}
    ph = {r["country"] for e in pack.extracts for r in e.rows
          for k in ("target_approval", "actual_approval", "expected_ship", "actual_ship",
                    "plant_date", "wh_date", "demand_date") if r.get(k) in ph_dates}
    out += finding("PLACEHOLDER_DATE_VALUE", ph, "Date field holds a default/placeholder value.")
    return out


ALL = (country_whitespace, status_values, date_stored_as_number, duplicate_rows, sku_not_in_reference,
       approval_before_submission, approval_target_not_actual, shipment_before_approval,
       blank_and_never_loaded, flag_yes_but_no_date, demand_loaded_too_late, setup_after_shipment,
       plant_capacity_exceeded, date_moved_without_record, date_passed_no_replan,
       starting_dose_and_rollup, absent_pattern_checks)


def run_all(pack, ctx):
    out = []
    for det in ALL:
        out.extend(det(pack, ctx))
    return out
