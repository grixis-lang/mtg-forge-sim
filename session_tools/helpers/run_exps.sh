#!/bin/bash
# run_exps.sh <games> <deck:opponent> [<deck:opponent> ...]
# Dispatches one sideboarded single-opponent run per experiment (Hardest AI,
# priorities on, analytics on), then waits until all of them finish.
G=$1; shift
REPO=grixis-lang/mtg-forge-sim
before=$(gh api "repos/$REPO/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
for pair in "$@"; do
  D=${pair%%:*}; O=${pair##*:}
  gh api -X POST repos/$REPO/actions/workflows/simulate.yml/dispatches -f ref=main \
    -f "inputs[games]=$G" -f "inputs[ours]=$D" -F "inputs[sideboard]=true" \
    -f "inputs[opponent]=$O" -F "inputs[analytics]=true" -f "inputs[ai]=Hardest" \
    -F "inputs[priorities]=true"
  sleep 5
done
sleep 20
IDS=$(gh api "repos/$REPO/actions/runs?per_page=20" --jq ".workflow_runs[] | select(.id > $before) | .id")
echo "runs: $(echo $IDS | wc -w)"
for R in $IDS; do
  for i in $(seq 1 240); do
    st=$(gh api repos/$REPO/actions/runs/$R --jq '.status'); [ "$st" = completed ] && break; sleep 15
  done
  gh api repos/$REPO/actions/runs/$R --jq '"\(.id) \(.status) \(.conclusion)"'
done
echo ALLDONE
