#!/bin/bash
# RETIRED (user rules 2026-11-11): this script started several GitHub runs at the same
# time, which breaks 'one job at a time'. Use run_seq.sh (one opponent, one test after
# another) instead. The old code is kept below for reference but never runs.
echo "Retired: starts runs side by side. Use session_tools/helpers/run_seq.sh <games> <opponent> <profile> <deck>..." >&2
exit 1
# run_pair.sh <games> <ai> <deckA> <deckB>: start two runs side by side, wait for both
G=$1; AI=$2; shift 2
before=$(gh api "repos/grixis-lang/mtg-forge-sim/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
for D in "$@"; do gh api -X POST repos/grixis-lang/mtg-forge-sim/actions/workflows/simulate.yml/dispatches -f ref=main -f "inputs[games]=$G" -f "inputs[ours]=$D" -F "inputs[sideboard]=false" -f "inputs[ai]=$AI"; sleep 5; done
sleep 10
IDS=$(gh api "repos/grixis-lang/mtg-forge-sim/actions/runs?per_page=5" --jq ".workflow_runs[] | select(.id > $before) | .id")
for i in $(seq 1 58); do
  left=0; for R in $IDS; do st=$(gh api repos/grixis-lang/mtg-forge-sim/actions/runs/$R --jq '.status'); [ "$st" != completed ] && left=$((left+1)); done
  [ $left -eq 0 ] && break; sleep 10
done
for R in $IDS; do gh api repos/grixis-lang/mtg-forge-sim/actions/runs/$R --jq '"\(.display_title): \(.status) \(.conclusion)"'; done
