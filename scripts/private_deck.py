"""
private_deck.py
---------------
Finds one of our decks inside the PRIVATE repo (MTG-Private-List) so the
public workflow can test it without the deck ever being saved here.

Private repo layout (one folder per deck):
    decks/<family>/deck.txt                 the deck itself
    decks/<family>/plans.json               its sideboard plans
    decks/<family>/experiments/<name>.txt   test versions of the deck
    decks/<family>/versions/<name>.txt      older versions
    decks/<family>/results/                 where results get saved

How to run:
    python3 scripts/private_deck.py <private repo folder> <deck name> [public candidates folder]

It prints three lines in GitHub's "output" format:
    family=<deck folder name>       e.g. my_deck
    deck=<path to the .txt>         or empty if not found anywhere
    plans=<path to the plans .json> or empty if there are none

If the deck is not in the private repo yet, it falls back to the public
candidates/ folder (so old tests keep working while we move files), and
guesses the family by comparing card lists.
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 2. Helper: card names in a deck file ("4 Card Name" lines)
# ---------------------------------------------------------------------------
def card_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*\d+\s+(.+?)\s*$", line)
        if m:
            names.add(m.group(1).split("|")[0].strip())
    return names


# ---------------------------------------------------------------------------
# 3. Safety: deck names may only use letters, numbers, _ and -
#    (stops anyone from passing "../../something" as a deck name)
# ---------------------------------------------------------------------------
def safe_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        sys.exit(f"Bad deck name: {name!r} (use letters, numbers, _ and - only)")
    return name


def main() -> None:
    private = Path(sys.argv[1])
    name = safe_name(sys.argv[2])
    public = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("candidates")
    decks = private / "decks"

    family, deck, plans = "", None, None

    # -----------------------------------------------------------------------
    # 4. Look in the private repo: the deck's own folder first,
    #    then every deck's experiments/ and versions/ folders
    # -----------------------------------------------------------------------
    if (decks / name / "deck.txt").is_file():
        family, deck = name, decks / name / "deck.txt"
        if (decks / name / "plans.json").is_file():
            plans = decks / name / "plans.json"
    else:
        for sub in ("experiments", "versions"):
            hits = sorted(decks.glob(f"*/{sub}/{name}.txt"))
            if hits:
                deck = hits[0]
                family = deck.parent.parent.name
                p = deck.with_suffix(".json")
                plans = p if p.is_file() else None
                break

    # -----------------------------------------------------------------------
    # 5. Not private yet: use the public copy and guess its family
    # -----------------------------------------------------------------------
    if deck is None and (public / f"{name}.txt").is_file():
        deck = public / f"{name}.txt"
        mine = card_names(deck)
        best, score = name, 0.0
        for d in sorted(decks.glob("*/deck.txt")):
            s = len(mine & card_names(d)) / max(1, len(mine))
            if s > score:
                best, score = d.parent.name, s
        family = best if score >= 0.6 else name  # brand-new deck = its own folder

    print(f"family={family or name}")
    print(f"deck={deck or ''}")
    print(f"plans={plans or ''}")


if __name__ == "__main__":
    main()
