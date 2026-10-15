# velara-agent — VELARA launch-risk audit

Reads the four monthly VELARA tracker extracts, finds the data defects behind the dashboard's
"29 of 30 green", works out how many days late each market can really ship, and ranks markets
by impact (slippage days × Year-1 forecast volume).

Business rules come from interviews with the five launch leads over the **velara-agent** MCP
server (`ask_lead`). Every rule in `velara_agent/rules.py` carries the `[lead-ref]` tag of the
answer it came from; `docs/LEAD_INTERVIEWS.md` lists the questions.

## Contract

    python3 solve.py <pack_dir>

`<pack_dir>` contains `VELARA_tracker.xlsx` and `dashboard_status.csv`. The tool prints exactly
one JSON object (`findings`, `risk_ranking`) to stdout and nothing else. Runtime is well under a
second.

## Dependencies

None. The `.xlsx` is read with the standard library (`zipfile` + `xml.etree`), so
`requirements.txt` holds only comments: nothing is installed from public PyPI and nothing has to
resolve from Lilly's JFrog Artifactory. If you add a package later, pin it exactly and install it
through Artifactory (see Artifactory | Developer Platform Front Door).

## Layout

| Path | Purpose |
|---|---|
| `velara_config.json` | Tells the evaluation agent how to run the tool (Python 3.11) |
| `solve.py` | Entry point |
| `velara_agent/xlsx_reader.py` | Stdlib xlsx reader; keeps "date stored as number" visible |
| `velara_agent/model.py` | Loads and cleans the pack (whitespace, serial dates, per-month extracts) |
| `velara_agent/rules.py` | Lead-confirmed rules with their `[lead-ref]` tags |
| `velara_agent/slippage.py` | Real launch date = latest of all constraints; capacity queue |
| `velara_agent/detectors.py` | One detector per defect code, legitimate look-alikes excluded |
| `velara_agent/report.py` | Builds the contract JSON and ranking |
| `scripts/write_csvs.py` | Local helper: writes `findings.csv` / `risk_ranking.csv` |
| `submission/` | The two CSVs produced from the v00 pack, for review |
| `docs/` | Lead interview log and Approach Summary |
| `tests/` | Contract and regression tests |

## Local checks

    python3 solve.py /path/to/pack | python3 -m json.tool > /dev/null && echo valid
    VELARA_PACK=/path/to/pack python3 -m unittest discover -s tests -t .
    python3 scripts/write_csvs.py /path/to/pack submission

## Before submitting

Push this folder as the root of your own repository, add the **ClaudeOlympic** GitHub team with
at least Read access, and run the platform preflight check.
VELARA is a fictional medicine and dataset; do not reference it outside the challenge.
