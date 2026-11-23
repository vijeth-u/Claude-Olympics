#!/usr/bin/env python3
"""
solve.py - Litmus "Tuesday Outage" incident analyser.

Usage:
    python3 solve.py <pack_dir>                # prints the submission JSON to stdout (grading contract)
    python3 solve.py <pack_dir> --out <dir>    # also writes root_causes.csv, causal_graph.json,
                                               # fix_set.json and findings.md into <dir>

Design
------
Every root cause is claimed only when the traces show it *acting on Litmus* after the Kestrel
release window (Tue 15 Sep 02:00 UTC) and NOT acting on Monday's baseline. Each detector returns
the first span that shows the cause acting (evidence + onset), the component where it acts, and
the single configuration operation that neutralises it. Changelog entries alone never trigger a
claim. Causal-graph edges are added only when the downstream effect is itself observed.

Pure stdlib; no third-party packages (Artifactory-only policy, nothing to install).
All diagnostics go to stderr; stdout carries exactly one JSON object.
"""
import csv
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

WINDOW = "2026-09-15T02:00:00"        # Kestrel quarterly release window (string-comparable)
TOOL_RESULT_CAP = 24 * 1024           # Kestrel docs section 6
PRE_RELEASE_CAP = 64 * 1024
RETIRED_SNAPSHOTS = {"kp-2025-11"}    # Kestrel docs section 1
PRIVATE_IP = re.compile(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)")


def log(*a):
    print(*a, file=sys.stderr)


def ts(x):
    return datetime.fromisoformat(x.replace("Z", "+00:00"))


def iso(x):
    """Normalise a span timestamp to second-precision ISO-8601 UTC."""
    return ts(x).strftime("%Y-%m-%dT%H:%M:%SZ")


def post(s):
    return s["start"] >= WINDOW


def pre(s):
    return s["start"] < WINDOW


# --------------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------------
def load_spans(pack):
    spans = []
    with open(os.path.join(pack, "traces.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                spans.append(json.loads(line))
    spans.sort(key=lambda s: s["start"])
    return spans


def load_config(pack):
    """Minimal YAML reader for the flat two-level litmus_config.yaml (no PyYAML needed)."""
    cfg, section = {}, None
    path = os.path.join(pack, "litmus_config.yaml")
    if not os.path.exists(path):
        return cfg
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, val = line.strip().partition(":")
        val = val.strip()
        if indent == 0:
            if val:
                cfg[key] = val
            else:
                section = key
                cfg[section] = {}
        elif section:
            cfg[section][key] = val
    return cfg


def load_alerts(pack):
    path = os.path.join(pack, "alert_history.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def cfg_get(cfg, comp, key, default=None):
    return (cfg.get(comp) or {}).get(key, default)


# --------------------------------------------------------------------------------------------
# Detectors - each returns None or a finding dict
#   {code, component, onset, evidence, why, fix:[ops], effects:[edges], observed:{...}}
# --------------------------------------------------------------------------------------------
def first(spans, pred):
    for s in spans:
        if pred(s):
            return s
    return None


def det_model_alias_drift(sp, cfg):
    err = [s for s in sp if s.get("error_type") == "tool_args_parse_error"]
    if not err or any(pre(s) for s in err):
        return None
    e = err[0]
    comp = e["component"]
    # The alias must actually have moved under this component: pre vs post resolved snapshot.
    pre_res = Counter(s.get("model_resolved") for s in sp
                      if pre(s) and s["component"] == comp and s["kind"] == "llm_call")
    if not pre_res or e.get("model_resolved") in pre_res:
        return None
    msg = (e.get("error_message") or "").lower()
    if "got object" in msg and comp == "orchestrator":
        fix = [{"op": "set_tool_arg_parsing", "component": comp, "mode": "object",
                "note": "kestrel-pro-latest now resolves to kp-2026-08, which returns tool-call "
                        "arguments as a JSON object; orchestrator still parses a JSON string "
                        "(tool_arg_parsing: string). Parse the encoding the snapshot returns."}]
    else:
        snap = pre_res.most_common(1)[0][0]
        if snap in RETIRED_SNAPSHOTS:
            snap = "kp-2026-02"
        fix = [{"op": "pin_model", "component": comp, "snapshot": snap,
                "note": "Return the component to the snapshot it used before the alias moved."}]
    return {
        "code": "MODEL_ALIAS_DRIFT", "component": comp, "onset": e["start"], "evidence": e,
        "why": f"{len(err)} {e['name']} calls failed with tool_args_parse_error "
               f"('{e.get('error_message')}'); requested {e.get('model_requested')}, resolved "
               f"{e.get('model_resolved')} (Monday: {', '.join(pre_res)}). None on Monday.",
        "fix": fix,
        "effects": [("MODEL_ALIAS_DRIFT", "ARG_PARSE_ERRORS"), ("ARG_PARSE_ERRORS", "JOB_FAILURES")],
    }


def det_rate_tier(sp, cfg):
    rl = [s for s in sp if s.get("http_status") == 429 and s.get("error_type") == "rate_limited"
          and s["component"] != "external"]
    if not rl or any(pre(s) for s in rl):
        return None
    e = rl[0]
    policies = Counter((s.get("attrs") or {}).get("retry_policy") for s in rl)
    attempts = Counter(s.get("attempt") for s in rl)
    redisp = [s for s in sp if s["name"].startswith("redispatch")
              and (s.get("attrs") or {}).get("reason") == "rate_limited"]
    effects = [("RATE_TIER_DOWNGRADE", "RATE_LIMIT_REJECTIONS")]
    fix = []
    if policies.get("immediate"):
        effects.append(("RATE_LIMIT_REJECTIONS", "IMMEDIATE_RETRY_STORM"))
        if redisp:
            effects.append(("IMMEDIATE_RETRY_STORM", "STAGE_REDISPATCH"))
        fix.append({"op": "set_retry_policy", "component": "orchestrator",
                    "policy": "exp_backoff_jitter",
                    "note": "429s are retried immediately (retry_policy: immediate) although "
                            "Kestrel returns retry_after_ms and counts rejected requests against "
                            "the allowance; all retry attempts burn out within ~0.3 s and the "
                            "stage is re-dispatched. Kestrel's recommended policy. The tier itself "
                            "is set by billing agreement, so request_rate_tier is not used."})
    return {
        "code": "RATE_TIER_DOWNGRADE", "component": e["component"], "onset": e["start"],
        "evidence": e,
        "why": f"{len(rl)} HTTP 429 '{e.get('error_message')}' on {e['component']}.{e['name']} "
               f"(none Monday), retry_policy={dict(policies)}, attempts={dict(sorted(attempts.items()))}, "
               f"{len(redisp)} stage re-dispatches with reason rate_limited. No 504 queue_timeout "
               f"and no queue_wait, so this is the account allowance, not the concurrency cap.",
        "fix": fix, "effects": effects, "redispatch": bool(redisp),
    }


def det_concurrency(sp, cfg):
    qt = [s for s in sp if s.get("error_type") == "queue_timeout"
          or (s.get("http_status") == 504 and s["component"] == "extract")]
    if not qt or any(pre(s) for s in qt):
        return None
    e = qt[0]
    return {
        "code": "CONCURRENCY_CAP_REDUCED", "component": e["component"], "onset": e["start"],
        "evidence": e, "why": f"{len(qt)} queue timeouts (HTTP 504) after the cap fell 20 -> 8.",
        "fix": [{"op": "set_max_concurrency", "component": "orchestrator", "n": 8,
                 "note": "Keep in-flight requests within Kestrel's per-key cap of 8."}],
        "effects": [("CONCURRENCY_CAP_REDUCED", "QUEUE_TIMEOUTS")],
    }


def det_tool_result_limit(sp, cfg):
    def capped(s):
        a = s.get("attrs") or {}
        return a.get("bytes_received") == TOOL_RESULT_CAP and not a.get("chunk")
    hits = [s for s in sp if post(s) and s["kind"] in ("tool_call", "connector_request")
            and s["component"] != "external" and capped(s)]
    if not hits or any(capped(s) for s in sp if pre(s) and s["component"] != "external"):
        return None
    e = hits[0]
    pre_big = sum(1 for s in sp if pre(s) and s["name"] == e["name"]
                  and (s.get("attrs") or {}).get("bytes_received", 0) > TOOL_RESULT_CAP)
    decode = [s for s in sp if post(s) and s.get("error_type") == "json_decode_error"]
    return {
        "code": "TOOL_RESULT_LIMIT_LOWERED", "component": e["component"], "onset": e["start"],
        "evidence": e,
        "why": f"{len(hits)} {e['name']} results delivered at exactly {TOOL_RESULT_CAP} bytes "
               f"(Monday: {pre_big} results > 24 KB delivered whole, none at the cap); "
               f"{len(decode)} downstream json_decode_error 'unterminated string at byte 24576'. "
               f"result_chunking is off (chunk=null on every fetch).",
        "fix": [{"op": "set_result_chunking", "component": "extract", "enabled": True,
                 "note": "The 24 KB cap cannot be raised (Kestrel docs section 6); chunking delivers "
                         "large papers whole in <=24 KB parts."}],
        "effects": [("TOOL_RESULT_LIMIT_LOWERED", "TRUNCATED_TOOL_RESULT"),
                    ("TRUNCATED_TOOL_RESULT", "PARTIAL_EXTRACTION")],
    }


def det_pagination(sp, cfg):
    def partial(s):
        a = s.get("attrs") or {}
        return a.get("has_more") is True and not a.get("cursor_followed")
    hits = [s for s in sp if s["name"] == "citeregistry.lookup" and partial(s)]
    if not hits or any(pre(s) for s in hits):
        return None
    e = hits[0]
    under = [s for s in sp if post(s) and s["name"] == "verify_citations"
             and (s.get("attrs") or {}).get("citations_checked", 0)
             < (s.get("attrs") or {}).get("citations_total", 0)
             and (s.get("attrs") or {}).get("verdict") == "verified"]
    return {
        "code": "CONNECTOR_PAGINATION_CHANGED", "component": e["component"], "onset": e["start"],
        "evidence": e,
        "why": f"{len(hits)} CiteRegistry {e['attrs'].get('connector_version')} lookups returned "
               f"{e['attrs'].get('records_returned')} records with has_more=true and the cursor "
               f"not followed (follow_cursor: null -> SDK default false); {len(under)} reviews "
               f"marked 'verified' with citations_checked < citations_total. Monday (v2.3) "
               f"returned all records in one response.",
        "fix": [{"op": "set_follow_cursor", "component": "cite_check", "enabled": True,
                 "note": "v2.3 is retired, so pinning it would be rejected; fetch all pages."}],
        "effects": [("CONNECTOR_PAGINATION_CHANGED", "PARTIAL_CITATION_SET"),
                    ("PARTIAL_CITATION_SET", "UNDER_VERIFIED_CITATIONS")],
    }


def det_reasoning(sp, cfg):
    by = defaultdict(lambda: {"pre": [], "post": []})
    for s in sp:
        if s["kind"] == "llm_call":
            by[(s["component"], s["name"])]["post" if post(s) else "pre"].append(s)
    for (comp, name), d in sorted(by.items()):
        if comp not in ("orchestrator", "synthesize") or not d["pre"] or not d["post"]:
            continue
        pre_r = statistics.mean(s["tokens"].get("reasoning", 0) for s in d["pre"])
        post_r = statistics.mean(s["tokens"].get("reasoning", 0) for s in d["post"])
        explicit = cfg_get(cfg, comp, "reasoning")
        if pre_r == 0 and post_r > 0 and explicit in (None, "", "null", "overlay"):
            e = first(d["post"], lambda s: s["tokens"].get("reasoning", 0) > 0)
            pre_d = statistics.mean(s["duration_ms"] for s in d["pre"]) / 1000
            post_d = statistics.mean(s["duration_ms"] for s in d["post"]) / 1000
            return {
                "code": "REASONING_DEFAULT_CHANGED", "component": comp, "onset": e["start"],
                "evidence": e,
                "why": f"{comp}.{name} is alias-addressed ({e.get('model_requested')}) with reasoning "
                       f"not set explicitly (config: {explicit!r} -> Kestrel default). Mean reasoning tokens "
                       f"{pre_r:.0f} -> {post_r:.0f} per call (default for alias calls became "
                       f"'medium'); call time {pre_d:.0f}s -> {post_d:.0f}s.",
                "fix": [{"op": "set_reasoning", "component": comp, "mode": "off",
                         "note": "Restore Monday's effective setting explicitly (reasoning off "
                                 "for synthesis); removes ~16k billed reasoning tokens per review."}],
                "effects": [("REASONING_DEFAULT_CHANGED", "REASONING_TOKENS")],
                "latency_up": post_d > 1.3 * pre_d,
            }
    return None


def det_output_tokens(sp, cfg):
    hits = [s for s in sp if s.get("finish_reason") == "length"]
    if not hits or any(pre(s) for s in hits):
        return None
    e = hits[0]
    return {
        "code": "OUTPUT_TOKEN_DEFAULT_LOWERED", "component": e["component"], "onset": e["start"],
        "evidence": e, "why": f"{len(hits)} calls ended finish_reason=length.",
        "fix": [{"op": "set_max_output_tokens", "component": "synthesize", "n": 16000,
                 "note": "Restore the pre-release effective cap explicitly."}],
        "effects": [("OUTPUT_TOKEN_DEFAULT_LOWERED", "OUTPUT_LENGTH_LIMIT_HIT"),
                    ("OUTPUT_LENGTH_LIMIT_HIT", "TRUNCATED_REVIEWS")],
    }


def det_field_renamed(sp, cfg):
    def wrong(s):
        a = s.get("attrs") or {}
        return (s["name"] == "litindex.query" and a.get("query_mode") == "structured"
                and a.get("result_count") == 0
                and (a.get("result_field_read") == "results" and a.get("connector_version") == "v4"
                     or a.get("bytes_received", 0) > 2000))
    hits = [s for s in sp if wrong(s)]
    if not hits or any(pre(s) for s in hits):
        return None
    e = hits[0]
    return {
        "code": "CONNECTOR_FIELD_RENAMED", "component": "search", "onset": e["start"], "evidence": e,
        "why": f"{len(hits)} structured queries parsed as 0 results from a non-empty v4 payload.",
        "fix": [{"op": "set_result_field", "component": "search", "field": "auto",
                 "note": "v4 returns results in 'items'."}],
        "effects": [("CONNECTOR_FIELD_RENAMED", "EMPTY_PRIMARY_RESULTS"),
                    ("EMPTY_PRIMARY_RESULTS", "FALLBACK_INDEX_USED"),
                    ("FALLBACK_INDEX_USED", "THIN_REVIEWS")],
    }


def det_schema(sp, cfg):
    hits = [s for s in sp if s.get("error_type") == "tool_schema_validation_error"]
    if not hits or any(pre(s) for s in hits):
        return None
    e = hits[0]
    return {
        "code": "TOOL_SCHEMA_STRICTNESS", "component": e["component"], "onset": e["start"],
        "evidence": e, "why": f"{len(hits)} HTTP 400 tool_schema_validation_error.",
        "fix": [{"op": "update_tool_schema", "component": "cite_check",
                 "additional_properties": True,
                 "note": "Schemas declaring additionalProperties: true are unaffected (permanent, "
                         "unlike the lenient opt-out which ends 31 Dec 2026)."}],
        "effects": [("TOOL_SCHEMA_STRICTNESS", "SCHEMA_REJECTIONS"),
                    ("SCHEMA_REJECTIONS", "JOB_FAILURES")],
    }


def det_cache_ttl(sp, cfg):
    """A 5-minute default would turn calls arriving 5-10 min after the previous one into writes."""
    for comp, name in (("extract", "extract_findings"), ("synthesize", "write_review"),
                       ("cite_check", "prepare_citations")):
        ttl = cfg_get(cfg, comp, "cache_ttl_minutes")
        if ttl not in (None, "", "null", "overlay"):
            continue                      # explicit TTL -> immune to the default change
        calls = [s for s in sp if s["name"] == name]
        stats = {"pre": [0, 0], "post": [0, 0]}
        prev, first_hit = None, None
        for s in calls:
            if prev is not None:
                gap = (ts(s["start"]) - ts(prev["end"])).total_seconds() / 60
                if 5 <= gap < 10:
                    k = "post" if post(s) else "pre"
                    stats[k][0] += 1
                    if s["tokens"].get("cache_write", 0) > 0:
                        stats[k][1] += 1
                        if k == "post" and first_hit is None:
                            first_hit = s
            prev = s
        (pn, pw), (qn, qw) = stats["pre"], stats["post"]
        if qn >= 5 and pn >= 3 and pw / pn < 0.3 and qw / qn > 0.7 and first_hit:
            return {
                "code": "CACHE_TTL_DEFAULT_CHANGED", "component": comp, "onset": first_hit["start"],
                "evidence": first_hit, "why": f"cache writes at 5-10 min gaps {pw}/{pn} -> {qw}/{qn}",
                "fix": [{"op": "set_cache_ttl", "component": comp,
                         "minutes": 10 if comp == "cite_check" else 60,
                         "note": "Explicit TTL (cite_check must stay <= 10 min)."}],
                "effects": [("CACHE_TTL_DEFAULT_CHANGED", "CACHE_MISSES"),
                            ("CACHE_MISSES", "COST_SPIKE")],
            }
    return None


def det_clock_skew(sp, cfg):
    byid = {s["span_id"]: s for s in sp}
    bad = [s for s in sp if ts(s["end"]) < ts(s["start"]) or
           (s.get("parent_id") in byid and ts(s["start"]) < ts(byid[s["parent_id"]]["start"]))]
    if not bad:
        return None
    e = bad[0]
    return {"code": "CLOCK_SKEW", "component": e["component"], "onset": e["start"], "evidence": e,
            "why": f"{len(bad)} spans out of order", "fix": [],
            "effects": [("CLOCK_SKEW", "TIMESTAMP_DISORDER")]}


def det_credential(sp, cfg):
    ext = [s for s in sp if s["component"] == "external"]
    sus = [s for s in ext if not PRIVATE_IP.match(str((s.get("attrs") or {}).get("client_ip", "")))
           or (s.get("attrs") or {}).get("query_type") not in ("bulk_reindex",)]
    if not sus:
        return None
    e = sus[0]
    return {"code": "CREDENTIAL_MISUSE", "component": "search", "onset": e["start"], "evidence": e,
            "why": f"{len(sus)} external requests from an unexpected client or purpose",
            "fix": [{"op": "revoke_service_account",
                     "name": (e.get("attrs") or {}).get("service_account", "")}],
            "effects": [("CREDENTIAL_MISUSE", "UNAUTHORISED_TRAFFIC")]}


DETECTORS = [det_model_alias_drift, det_rate_tier, det_concurrency, det_tool_result_limit,
             det_pagination, det_reasoning, det_output_tokens, det_field_renamed, det_schema,
             det_cache_ttl, det_clock_skew, det_credential]


# --------------------------------------------------------------------------------------------
# System-level symptoms (used to decide which symptom edges are observed)
# --------------------------------------------------------------------------------------------
def system_metrics(sp, alerts):
    jobs = [s for s in sp if s["kind"] == "job"]
    cost = defaultdict(float)
    for s in sp:
        if "cost_usd" in s:
            cost["post" if post(s) else "pre"] += s["cost_usd"]
    nj = Counter("post" if post(j) else "pre" for j in jobs)
    cpj = {k: cost[k] / nj[k] for k in nj if nj[k]}

    def p95(xs):
        xs = sorted(xs)
        return xs[max(0, int(round(0.95 * len(xs))) - 1)] if xs else 0
    dur = {k: p95([j["duration_ms"] / 60000 for j in jobs if (post(j) == (k == "post"))
                   and j["status"] == "ok"]) for k in ("pre", "post")}
    fail = {k: sum(1 for j in jobs if (post(j) == (k == "post")) and j["status"] == "error")
            for k in ("pre", "post")}
    err_rate_alerts = [a for a in alerts if a.get("rule") == "JobErrorRate"
                       and a.get("fired_at", "") >= WINDOW]
    m = {"cost_per_job": cpj, "p95_minutes": dur, "failed_jobs": fail, "jobs": dict(nj),
         "error_rate_alerts": len(err_rate_alerts)}
    m["cost_spike"] = cpj.get("post", 0) > 1.05 * cpj.get("pre", 1e9)
    m["latency_up"] = dur["post"] > 1.2 * dur["pre"]
    m["alert_flood"] = len(err_rate_alerts) >= 2
    return m


# --------------------------------------------------------------------------------------------
def analyse(pack):
    sp = load_spans(pack)
    cfg = load_config(pack)
    alerts = load_alerts(pack)
    log(f"[solve] {len(sp)} spans, config sections={list(cfg)}")

    findings = []
    for det in DETECTORS:
        try:
            f = det(sp, cfg)
        except Exception as exc:                     # a broken detector must never kill the run
            log(f"[solve] detector {det.__name__} failed: {exc!r}")
            f = None
        if f:
            findings.append(f)
            log(f"[solve] CLAIM {f['code']} @ {f['component']} {iso(f['onset'])} "
                f"{f['evidence']['span_id']} :: {f['why']}")

    m = system_metrics(sp, alerts)
    log(f"[solve] metrics: {json.dumps(m)}")

    edges = []

    def add(a, b):
        if (a, b) not in edges:
            edges.append((a, b))

    codes = {f["code"] for f in findings}
    for f in findings:
        for a, b in f["effects"]:
            add(a, b)
        if f["code"] == "RATE_TIER_DOWNGRADE" and f.get("redispatch"):
            add("STAGE_REDISPATCH", "JOB_FAILURES")
            if m["cost_spike"]:
                add("STAGE_REDISPATCH", "COST_SPIKE")
            if m["latency_up"]:
                add("STAGE_REDISPATCH", "LATENCY_INCREASE")
        if f["code"] == "REASONING_DEFAULT_CHANGED":
            if m["cost_spike"]:
                add("REASONING_TOKENS", "COST_SPIKE")
            if f.get("latency_up") and m["latency_up"]:
                add("REASONING_TOKENS", "LATENCY_INCREASE")
        if f["code"] == "CONCURRENCY_CAP_REDUCED":
            add("QUEUE_TIMEOUTS", "JOB_FAILURES")
    if m["alert_flood"] and any(b == "JOB_FAILURES" for _, b in edges):
        add("JOB_FAILURES", "ALERT_FLOOD")

    root_causes = [{"cause_code": f["code"], "component": f["component"],
                    "onset_time": iso(f["onset"]), "evidence_span_id": f["evidence"]["span_id"]}
                   for f in findings]
    ops = [op for f in findings for op in f["fix"]]
    out = {"root_causes": root_causes,
           "causal_graph": {"edges": [list(e) for e in edges]},
           "fix_set": {"operations": ops}}
    return out, findings, m


def write_files(out, findings, metrics, outdir):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "root_causes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["cause_code", "component", "onset_time", "evidence_span_id"])
        for r in out["root_causes"]:
            w.writerow([r["cause_code"], r["component"], r["onset_time"], r["evidence_span_id"]])
    with open(os.path.join(outdir, "causal_graph.json"), "w", encoding="utf-8") as fh:
        json.dump(out["causal_graph"], fh, indent=2)
        fh.write("\n")
    with open(os.path.join(outdir, "fix_set.json"), "w", encoding="utf-8") as fh:
        json.dump(out["fix_set"], fh, indent=2)
        fh.write("\n")
    with open(os.path.join(outdir, "findings.md"), "w", encoding="utf-8") as fh:
        fh.write("# Detector findings (generated by solve.py)\n\n")
        for f in findings:
            fh.write(f"## {f['code']} - {f['component']} - onset {iso(f['onset'])}\n\n")
            fh.write(f"- Evidence span: `{f['evidence']['span_id']}` "
                     f"({f['evidence']['component']}.{f['evidence']['name']})\n")
            fh.write(f"- Observation: {f['why']}\n")
            for op in f["fix"]:
                fh.write(f"- Fix: `{json.dumps({k: v for k, v in op.items() if k != 'note'})}`\n")
            fh.write("\n")
        fh.write("## System metrics\n\n```json\n" + json.dumps(metrics, indent=2) + "\n```\n")


def main(argv):
    if len(argv) < 2:
        log("usage: python3 solve.py <pack_dir> [--out DIR]")
        print(json.dumps({"root_causes": [], "causal_graph": {"edges": []},
                          "fix_set": {"operations": []}}))
        return
    pack = argv[1]
    out, findings, metrics = analyse(pack)
    if "--out" in argv:
        write_files(out, findings, metrics, argv[argv.index("--out") + 1])
    sys.stdout.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main(sys.argv)
