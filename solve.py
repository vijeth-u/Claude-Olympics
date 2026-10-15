#!/usr/bin/env python3
"""VELARA launch-risk audit.

Usage:  python3 solve.py <pack_dir>
<pack_dir> holds VELARA_tracker.xlsx and dashboard_status.csv. Prints exactly one JSON object
({"findings": [...], "risk_ranking": [...]}) to stdout; diagnostics go to stderr. Stdlib only.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from velara_agent import detectors, report  # noqa: E402
from velara_agent.model import Pack  # noqa: E402
from velara_agent.slippage import assess  # noqa: E402


def run(pack_dir):
    pack = Pack(pack_dir)
    assessments, capacity = assess(pack)
    findings = detectors.run_all(pack, {"capacity": capacity})
    return report.build(pack, assessments, findings)


def main(argv):
    if len(argv) < 2:
        print("usage: python3 solve.py <pack_dir>", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(run(os.path.abspath(argv[1])), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
