#!/bin/bash
# run_vs.sh <deck> <opponent> <games> <ai> : one-opponent analytics run, wait for it
D=$1; O=$2; G=$3; AI=${4:-Hardest}
before=$(gh api "repos/grixis-lang/mtg-forge-sim/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
gh api -X POST repos/grixis-lang/mtg-forge-sim/actions/workflows/simulate.yml/dispatches -f ref=main -f "inputs[games]=$G" -f "inputs[ours]=$D" -F "inputs[sideboard]=false" -f "inputs[opponent]=$O" -F "inputs[analytics]=true" -f "inputs[ai]=$AI"
for i in $(seq 1 20); do R=$(gh api "repos/grixis-lang/mtg-forge-sim/actions/runs?per_page=1" --jq '.workflow_runs[0].id'); [ "$R" != "$before" ] && break; sleep 3; done
echo "run $R"
for i in $(seq 1 170); do st=$(gh api repos/grixis-lang/mtg-forge-sim/actions/runs/$R --jq '.status'); [ "$st" = completed ] && break; sleep 10; done
gh api repos/grixis-lang/mtg-forge-sim/actions/runs/$R --jq '"\(.status) \(.conclusion)"'
