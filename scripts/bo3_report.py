"""
bo3_report.py
-------------
Combines game-1 results (main deck) with post-sideboard results to estimate
best-of-three MATCH win rates.

  Input:  results/<deck>.md               (game 1: main deck vs each opponent)
          results/<deck>_sideboarded.md   (games 2 and 3: sideboarded deck)
  Output: results/<deck>_bo3.md

How to run (Python 3.10+, no extra packages needed):
    python scripts/bo3_report.py my_deck            (Default AI)
    python scripts/bo3_report.py my_deck _Hardest   (Hardest AI files)

How the estimate works, with g1 = game-1 win chance, g2 = post-board win chance:
  win the match = win 2-0, or 2-1 after losing one of the first two
  = g1*g2  +  g1*(1-g2)*g2  +  (1-g1)*g2*g2
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import re
import sys
from pathlib import Path

# A results table row looks like: | boros_dwarves | 36 | 64 | 0 | 100 | 36% | weak matchup |
ROW = re.compile(r"^\|\s*([a-z0-9_]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|")


# ---------------------------------------------------------------------------
# 2. Read a results table into {opponent: win rate between 0 and 1}
# ---------------------------------------------------------------------------
def read_rates(path: Path) -> dict[str, float]:
    rates = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            opponent, w, l, d = m.group(1), *map(int, m.group(2, 3, 4))
            games = w + l + d
            if games:
                rates[opponent] = (w + 0.5 * d) / games   # draws count half
    return rates


# ---------------------------------------------------------------------------
# 3. Best-of-three match win chance
# ---------------------------------------------------------------------------
def match_win(g1: float, g2: float) -> float:
    return g1 * g2 + g1 * (1 - g2) * g2 + (1 - g1) * g2 * g2


# ---------------------------------------------------------------------------
# 4. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        sys.exit(1)
    deck = sys.argv[1]
    suffix = sys.argv[2] if len(sys.argv) == 3 else ""     # e.g. "_Hardest"
    game1_file = Path(f"results/{deck}{suffix}.md")
    board_file = Path(f"results/{deck}_sideboarded{suffix}.md")
    for p in (game1_file, board_file):
        if not p.is_file():
            sys.exit(f"Missing results file: {p}")

    g1, g2 = read_rates(game1_file), read_rates(board_file)
    lines = [
        f"# Best-of-three estimate: {deck}",
        "",
        "Game 1 uses the main deck; games 2-3 use the sideboarded deck from "
        f"`plans/{deck}.json`. Opponents play their main decks in all games "
        "(their sideboard plans are unknown). Forge AI results: rough signals.",
        "",
        "| Opponent | Game 1 | After sideboard | Change | Match win (Bo3) |",
        "|---|---:|---:|---:|---:|",
    ]
    matches = []
    for opp in sorted(g1, key=lambda o: match_win(g1[o], g2.get(o, g1[o]))):
        post = g2.get(opp, g1[opp])          # no plan for this opponent -> same as game 1
        mw = match_win(g1[opp], post)
        matches.append(mw)
        change = 100 * (post - g1[opp])
        lines.append(f"| {opp} | {100*g1[opp]:.0f}% | {100*post:.0f}% | "
                     f"{change:+.0f} | {100*mw:.0f}% |")
    avg = 100 * sum(matches) / len(matches) if matches else 0
    lines += [f"| **Average** | | | | **{avg:.0f}%** |", ""]

    out = Path(f"results/{deck}{suffix}_bo3.md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
