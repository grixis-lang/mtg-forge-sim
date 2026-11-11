#!/bin/bash
# run_seq.sh <games> <profile> <deck> [<deck> ...] : game-1 runs vs all 8, one after another
G=$1; P=$2; shift 2; REPO=grixis-lang/mtg-forge-sim
for D in "$@"; do
  before=$(gh api "repos/$REPO/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
  gh api -X POST repos/$REPO/actions/workflows/simulate.yml/dispatches -f ref=main -f "inputs[games]=$G" -f "inputs[ours]=$D" \
    -F "inputs[sideboard]=false" -f "inputs[opponent]=all" -F "inputs[analytics]=true" -f "inputs[ai]=Hardest" \
    -F "inputs[priorities]=true" -f "inputs[shards]=2" -f "inputs[our_profile]=$P"
  sleep 15; R=$(gh api "repos/$REPO/actions/runs?per_page=3" --jq ".workflow_runs[] | select(.id > $before) | .id" | tail -1)
  echo "[$(date +%H:%M)] $D run $R"
  for i in $(seq 1 300); do st=$(gh api repos/$REPO/actions/runs/$R --jq .status); [ "$st" = completed ] && break; sleep 20; done
  echo "[$(date +%H:%M)] $D $(gh api repos/$REPO/actions/runs/$R --jq .conclusion)"
done
echo ALLDONE
