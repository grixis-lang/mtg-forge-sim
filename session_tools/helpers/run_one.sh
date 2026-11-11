#!/bin/bash
# run_one.sh <deck> <games> [sideboard true|false] : dispatch one run and wait for it
D=$1; G=$2; SB=${3:-false}; AI=${4:-Default}
before=$(gh api "repos/grixis-lang/mtg-forge-sim/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
gh api -X POST repos/grixis-lang/mtg-forge-sim/actions/workflows/simulate.yml/dispatches -f ref=main -f "inputs[games]=$G" -f "inputs[ours]=$D" -F "inputs[sideboard]=$SB" -f "inputs[ai]=$AI"
for i in $(seq 1 20); do R=$(gh api "repos/grixis-lang/mtg-forge-sim/actions/runs?per_page=1" --jq '.workflow_runs[0].id'); [ "$R" != "$before" ] && break; sleep 3; done
for i in $(seq 1 60); do st=$(gh api repos/grixis-lang/mtg-forge-sim/actions/runs/$R --jq '.status'); [ "$st" = completed ] && break; sleep 10; done
echo "$D ($G games, sb=$SB, ai=$AI): $(gh api repos/grixis-lang/mtg-forge-sim/actions/runs/$R --jq '.conclusion') simulate_jobs=$(gh api "repos/grixis-lang/mtg-forge-sim/actions/runs/$R/jobs?filter=all" --jq '[.jobs[]|select(.name|startswith("simulate"))]|length')"
