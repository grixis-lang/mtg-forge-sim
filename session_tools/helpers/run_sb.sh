#!/bin/bash
# run_sb.sh <deck> <opponent> <games> <ai> : sideboarded run vs one opponent with analytics, wait for it
D=$1; O=$2; G=$3; AI=${4:-Hardest}; PRIO=${5:-false}; REPO=grixis-lang/mtg-forge-sim
before=$(gh api "repos/$REPO/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
gh api -X POST repos/$REPO/actions/workflows/simulate.yml/dispatches -f ref=main -f "inputs[games]=$G" -f "inputs[ours]=$D" -F "inputs[sideboard]=true" -f "inputs[opponent]=$O" -F "inputs[analytics]=true" -f "inputs[ai]=$AI" -F "inputs[priorities]=$PRIO"
for i in $(seq 1 20); do R=$(gh api "repos/$REPO/actions/runs?per_page=1" --jq '.workflow_runs[0].id'); [ "$R" != "$before" ] && break; sleep 3; done
echo "run $R"
for i in $(seq 1 200); do st=$(gh api repos/$REPO/actions/runs/$R --jq '.status'); [ "$st" = completed ] && break; sleep 15; done
gh api repos/$REPO/actions/runs/$R --jq '"\(.status) \(.conclusion)"'
