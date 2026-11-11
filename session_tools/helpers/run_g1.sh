#!/bin/bash
# run_g1.sh <games> <deck> [<deck> ...] : game-1 runs vs all 8 (Hardest, priorities, analytics), wait for all
G=$1; shift; REPO=grixis-lang/mtg-forge-sim
before=$(gh api "repos/$REPO/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
for D in "$@"; do
  gh api -X POST repos/$REPO/actions/workflows/simulate.yml/dispatches -f ref=main -f "inputs[games]=$G" -f "inputs[ours]=$D" \
    -F "inputs[sideboard]=false" -f "inputs[opponent]=all" -F "inputs[analytics]=true" -f "inputs[ai]=Hardest" -F "inputs[priorities]=true"
  sleep 5
done
sleep 20
IDS=$(gh api "repos/$REPO/actions/runs?per_page=10" --jq ".workflow_runs[] | select(.id > $before) | .id"); echo "runs: $IDS"
for R in $IDS; do for i in $(seq 1 240); do st=$(gh api repos/$REPO/actions/runs/$R --jq .status); [ "$st" = completed ] && break; sleep 15; done
  gh api repos/$REPO/actions/runs/$R --jq '"\(.id) \(.conclusion) \(.run_started_at) \(.updated_at)"'; done; echo ALLDONE
