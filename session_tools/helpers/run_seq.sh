#!/bin/bash
# ---------------------------------------------------------------------------
# run_seq.sh <games> <opponent> <profile> <deck> [<deck> ...]
#
# Game-1 tests of one or more of our decks against ONE opponent, one test after
# another (each waits for the previous one to finish).
# User rules (2026-11-11): one opponent per test while tuning; one test at a time,
# its games split over 4 machines that play at the same time.
#   <opponent> = a file name in decks/ without .txt, e.g. boros_dwarves
#   <profile>  = our AI profile name, or "" for none
# ---------------------------------------------------------------------------
set -euo pipefail
G=$1; OPP=$2; P=$3; shift 3
REPO=grixis-lang/mtg-forge-sim

# 1. Check the opponent is ONE meta deck (no "all", no comma lists)
if [[ "$OPP" == "all" || "$OPP" == *,* || ! "$OPP" =~ ^[a-z0-9_]+$ ]]; then
  echo "Pick ONE opponent (a file name in decks/)"; exit 1
fi

# 2. Start each test, wait for it to finish, then start the next
for D in "$@"; do
  before=$(gh api "repos/$REPO/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
  gh api -X POST repos/$REPO/actions/workflows/simulate.yml/dispatches -f ref=main -f "inputs[games]=$G" -f "inputs[ours]=$D" \
    -F "inputs[sideboard]=false" -f "inputs[opponent]=$OPP" -F "inputs[analytics]=true" -f "inputs[ai]=Hardest" \
    -F "inputs[priorities]=true" -f "inputs[shards]=4" -f "inputs[our_profile]=$P"
  sleep 15
  R=$(gh api "repos/$REPO/actions/runs?per_page=3" --jq ".workflow_runs[] | select(.id > $before) | .id" | tail -1)
  echo "[$(date +%H:%M)] $D vs $OPP: run $R"
  for i in $(seq 1 600); do                          # wait up to about 3 hours 20 minutes
    st=$(gh api repos/$REPO/actions/runs/$R --jq .status); [ "$st" = completed ] && break; sleep 20
  done
  echo "[$(date +%H:%M)] $D vs $OPP: $(gh api repos/$REPO/actions/runs/$R --jq .conclusion)"
done
echo ALLDONE
