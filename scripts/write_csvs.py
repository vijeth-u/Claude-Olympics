#!/usr/bin/env python3
"""Local helper: run the tool and write findings.csv + risk_ranking.csv in the template shape.
Usage:  python3 scripts/write_csvs.py <pack_dir> [out_dir]
(The evaluation agent builds the CSVs itself from solve.py's JSON.)"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from solve import run  # noqa: E402


def main():
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "output"
    os.makedirs(out_dir, exist_ok=True)
    out = run(os.path.abspath(sys.argv[1]))
    with open(os.path.join(out_dir, "findings.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["what", "where", "proof"], lineterminator="\n")
        w.writeheader()
        w.writerows(out["findings"])
    cols = ["country", "rank", "slippage_days", "impact_score", "driver", "evidence"]
    with open(os.path.join(out_dir, "risk_ranking.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(out["risk_ranking"])
    print(f"wrote {out_dir}/findings.csv and {out_dir}/risk_ranking.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
