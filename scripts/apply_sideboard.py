"""
apply_sideboard.py
------------------
Builds a "post-sideboard" copy of our deck for each opponent, using a
sideboard plan file. Forge's AI can't sideboard by itself, so we make the
swaps before the games start.

  Input:  candidates/<deck>.txt          (main deck + "Sideboard" section)
          plans/<deck>.json              (what to bring in/out per opponent)
  Output: <out-dir>/<deck>__sb__<opponent>.txt   (one 60-card deck per opponent)

How to run (Python 3.10+, no extra packages needed):
    python scripts/apply_sideboard.py candidates/my_deck.txt \
        plans/my_deck.json sideboarded

If a plan asks for a card the deck doesn't have (or too many copies), the
script stops with a clear error instead of building a wrong deck.
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import json
import sys
from collections import Counter     # a dictionary that counts things
from pathlib import Path


# ---------------------------------------------------------------------------
# 2. Read a decklist into two Counters: main deck and sideboard
# ---------------------------------------------------------------------------
def read_deck(path: Path) -> tuple[Counter, Counter]:
    main, side = Counter(), Counter()
    current = main
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("sideboard"):
            current = side
            continue
        count, _, name = line.partition(" ")
        current[name.strip()] += int(count)
    return main, side


# ---------------------------------------------------------------------------
# 3. Apply one plan: take cards out of the main deck, bring cards in
# ---------------------------------------------------------------------------
def apply_plan(main: Counter, side: Counter, plan: dict, opponent: str) -> Counter:
    cards_in, cards_out = plan.get("in", {}), plan.get("out", {})
    problems = []

    # The deck must stay at the same size, so IN and OUT must match
    if sum(cards_in.values()) != sum(cards_out.values()):
        problems.append(f"in ({sum(cards_in.values())}) and out "
                        f"({sum(cards_out.values())}) don't add up to the same number")
    for name, n in cards_out.items():
        if main[name] < n:
            problems.append(f"can't take out {n} {name}: main deck has {main[name]}")
    for name, n in cards_in.items():
        if side[name] < n:
            problems.append(f"can't bring in {n} {name}: sideboard has {side[name]}")
    if problems:
        sys.exit(f"Plan for {opponent} has problems:\n  - " + "\n  - ".join(problems))

    new_main = main.copy()
    new_main.subtract(cards_out)     # remove the "out" cards
    new_main.update(cards_in)        # add the "in" cards
    return +new_main                 # "+" drops cards whose count reached 0


# ---------------------------------------------------------------------------
# 4. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    deck_path, plan_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    for p in (deck_path, plan_path):
        if not p.is_file():
            sys.exit(f"File not found: {p}")
    out_dir.mkdir(parents=True, exist_ok=True)

    main_deck, sideboard = read_deck(deck_path)
    plans = json.loads(plan_path.read_text(encoding="utf-8"))

    for opponent, plan in plans.items():
        if opponent.startswith("_"):          # skip notes like "_how_to_read"
            continue
        new_main = apply_plan(main_deck, sideboard, plan, opponent)
        out_file = out_dir / f"{deck_path.stem}__sb__{opponent}.txt"
        lines = [f"{n} {name}" for name, n in new_main.items()]
        out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        swaps = ", ".join(f"+{n} {c}" for c, n in plan["in"].items())
        print(f"  {opponent:<22} {sum(new_main.values())} cards  ({swaps})")


if __name__ == "__main__":
    main()
