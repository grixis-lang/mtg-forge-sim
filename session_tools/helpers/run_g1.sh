#!/bin/bash
# ---------------------------------------------------------------------------
# run_g1.sh <games> <opponent> <deck> [<deck> ...]
#
# Game-1 tests (Hardest AI, AI helpers, analytics) of our decks against ONE
# opponent, without an AI profile. It used to start every deck at once; now it
# runs them one after another through run_seq.sh, following the user's rules
# (one opponent per test, one test at a time).
# ---------------------------------------------------------------------------
set -euo pipefail
G=$1; OPP=$2; shift 2
exec "$(dirname "$0")/run_seq.sh" "$G" "$OPP" "" "$@"
