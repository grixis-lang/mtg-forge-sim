"""
positions.py
------------
Step 1 of the new AI brain: collects the board snapshots that our Forge patch
19_log_positions writes at the start of every turn, and pairs each one with
who won that game. The result is a CSV table: the training data for the
learned "scorekeeper" (step 2 teaches it which board numbers really predict
a win in our matchups).

Each row is one snapshot seen from ONE player's side ("me"), with the other
player's numbers next to it ("opp"), and whether "me" went on to win.
Every turn gives two rows: one from our side, one from the opponent's side.
That way the scorekeeper learns from both decks' games, not only ours.

The log lines it reads look like (one per player, every turn):
    Turn: Position turn=7 active=yes life=14 poison=0 hand=3 ... player=Ai(1)-our_deck
    Game Result: Game 3 ended in 41234 ms. Ai(2)-boros_dwarves has won!

No card names or deck names go into the CSV: only numbers, and "us"/"opp"
for which side the row is seen from.

How to run (Python 3.10+, no extra packages):
    python3 scripts/positions.py <game log> <our deck name> <output .csv>
Example:
    python3 scripts/positions.py logs/boros_dwarves.log esper_control positions.csv
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import csv
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 2. What the log lines look like
# ---------------------------------------------------------------------------
# "Turn: Position turn=7 active=yes life=14 ... player=Ai(1)-our_deck"
# The player's name is always last (it can contain spaces).
POSITION = re.compile(r"^Turn: Position (.*?) player=(.+)$")
KEY_VALUE = re.compile(r"(\w+)=(\S+)")
RESULT_WIN = re.compile(r"Game Result: Game (\d+) ended in \d+ ms\. (.+?) has won!")
RESULT_DRAW = re.compile(r"Game Result: Game (\d+) ended in a Draw")

# The numbers each snapshot has, in the order they go into the CSV.
# (Must match the patch's PositionLog.java; a missing number is written as 0.)
NUMBERS = ["life", "poison", "hand", "library", "graveyard", "exile", "lands",
           "creatures", "power", "toughness", "evasive_power", "tokens",
           "walkers", "loyalty", "other_permanents"]


# ---------------------------------------------------------------------------
# 3. Helpers
# ---------------------------------------------------------------------------
def is_ours(player: str, ours: str) -> bool:
    """Forge names players "Ai(1)-<deck>". Sideboarded copies of our deck are
    named "<ours>__sb__<opponent>", so they count as ours too."""
    name = player.split(")-", 1)[-1].strip()
    return name == ours or name.startswith(ours + "__sb__")


def read_games(log_text: str, ours: str) -> list[dict]:
    """Splits the log into games. Each game is:
         {"turns": {forge turn: {"us": snapshot, "opp": snapshot, "active": "us"/"opp"}},
          "winner": "us" / "opp" / "draw"}
    A snapshot is a dict of the NUMBERS above."""
    games = []
    turns: dict[int, dict] = {}
    for line in log_text.splitlines():
        line = line.strip()

        # --- a board snapshot ---
        m = POSITION.match(line)
        if m:
            values = dict(KEY_VALUE.findall(m.group(1)))
            side = "us" if is_ours(m.group(2), ours) else "opp"
            try:
                turn = int(values.get("turn", "0"))
                snapshot = {k: int(values.get(k, "0")) for k in NUMBERS}
            except ValueError:
                continue                      # a damaged line: skip it, don't stop
            entry = turns.setdefault(turn, {})
            entry[side] = snapshot
            if values.get("active") == "yes":
                entry["active"] = side
            continue

        # --- the end of a game: who won ---
        m = RESULT_WIN.search(line)
        winner = None
        if m:
            winner = "us" if is_ours(m.group(2), ours) else "opp"
        elif RESULT_DRAW.search(line):
            winner = "draw"
        if winner:
            games.append({"turns": turns, "winner": winner})
            turns = {}                         # start collecting the next game
    return games


def rows_for(games: list[dict]) -> list[dict]:
    """Turns the games into CSV rows: two per turn (one from each side)."""
    rows = []
    for number, game in enumerate(games, start=1):
        for turn in sorted(game["turns"]):
            entry = game["turns"][turn]
            if "us" not in entry or "opp" not in entry:
                continue                       # need both sides to compare the board
            for me, other in (("us", "opp"), ("opp", "us")):
                # did "me" win this game? draws count as half a win
                if game["winner"] == "draw":
                    won = 0.5
                else:
                    won = 1.0 if game["winner"] == me else 0.0
                row = {"game": number, "turn": turn, "side": me,
                       "active": 1 if entry.get("active") == me else 0, "won": won}
                for k in NUMBERS:
                    row["me_" + k] = entry[me][k]
                    row["opp_" + k] = entry[other][k]
                rows.append(row)
    return rows


def life_check(rows: list[dict]) -> tuple[int, float]:
    """A quick sanity check that the data makes sense: from turn 7 on, how
    often did the side with MORE life go on to win? (Should be well over 50%.)"""
    ahead = [r for r in rows if r["turn"] >= 7 and r["me_life"] > r["opp_life"] and r["won"] != 0.5]
    if not ahead:
        return 0, 0.0
    return len(ahead), 100.0 * sum(r["won"] for r in ahead) / len(ahead)


# ---------------------------------------------------------------------------
# 4. Main: read the log, write the CSV, print a short summary
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    log_path, ours, out_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])

    games = read_games(log_path.read_text(encoding="utf-8", errors="replace"), ours)
    rows = rows_for(games)

    # Write the table (even when empty, so the workflow always has a file to save)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["game", "turn", "side", "active", "won"] \
        + [p + k for k in NUMBERS for p in ("me_", "opp_")]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    # Summary (the workflow hides this; it's for checking the file by hand)
    count, percent = life_check(rows)
    with_snapshots = sum(1 for g in games if g["turns"])
    print(f"Games: {len(games)} ({with_snapshots} with board snapshots)")
    print(f"Rows written: {len(rows)} -> {out_path}")
    if with_snapshots == 0 and games:
        print("No snapshots found: was Forge built with patch 19_log_positions?")
    if count:
        print(f"Sanity check: from turn 7, the side with more life won {percent:.0f}% of {count} positions")


if __name__ == "__main__":
    main()
