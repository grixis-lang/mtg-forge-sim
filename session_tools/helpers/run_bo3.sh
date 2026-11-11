#!/bin/bash
# RETIRED (user rules 2026-11-11): this script started several GitHub runs at the same
# time, which breaks 'one job at a time'. Use run_seq.sh (one opponent, one test after
# another) instead. The old code is kept below for reference but never runs.
echo "Retired: starts runs side by side. Use session_tools/helpers/run_seq.sh <games> <opponent> <profile> <deck>..." >&2
exit 1
# run_bo3.sh <deck> <opponent> <games> <ai> [priorities true/false]
# Dispatches the game-1 run (main deck) and the sideboarded run vs one opponent,
# both with analytics, then waits until both finish.
D=$1; O=$2; G=$3; AI=${4:-Hardest}; PRIO=${5:-false}
REPO=grixis-lang/mtg-forge-sim
dispatch () {   # $1 = true/false for sideboard
  gh api -X POST repos/$REPO/actions/workflows/simulate.yml/dispatches -f ref=main \
    -f "inputs[games]=$G" -f "inputs[ours]=$D" -F "inputs[sideboard]=$1" \
    -f "inputs[opponent]=$O" -F "inputs[analytics]=true" -f "inputs[ai]=$AI" -F "inputs[priorities]=$PRIO"
}
before=$(gh api "repos/$REPO/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
dispatch false; sleep 8; dispatch true; sleep 15
IDS=$(gh api "repos/$REPO/actions/runs?per_page=5" --jq ".workflow_runs[] | select(.id > $before) | .id")
echo "runs: $IDS"
for R in $IDS; do
  for i in $(seq 1 200); do
    st=$(gh api repos/$REPO/actions/runs/$R --jq '.status'); [ "$st" = completed ] && break; sleep 15
  done
  gh api repos/$REPO/actions/runs/$R --jq '"\(.id) \(.status) \(.conclusion)"'
done
