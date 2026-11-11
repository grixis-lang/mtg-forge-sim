"""
ai_audit.py
-----------
Checks every card in a deck for things the Forge AI is known to play badly,
so we can write a sim-only fix (in the PRIVATE repo's engine/card_overrides/)
or a hint BEFORE testing.

What it flags (each one found in our testing so far):
  - X costs (mana or life): the AI pays the LARGEST X it can
  - spells built from a "Pump" wrapper with no stats: the AI never casts them
  - "destroy all artifacts and creatures": the AI judges it by mana value of all
      permanents and almost never casts it
  - Gift: the AI may never promise the gift, so the bonus never happens
  - optional sacrifice costs: the AI may sacrifice the wrong thing
  - "any number of target players": the AI might target itself
  - modal / spree cards: usually fine, listed so the reports get checked
  - counterspells: depend on the AI profile (test with a control AI profile)
  - Forge's own "AI can't use this well" flags (AI:RemoveDeck)
Cards that already have a fix in the overrides folder are marked as fixed.
(Examples of fixed cards are kept in the private repo, not here, because
they show which cards we test.)

How to run (Python 3.10+, no extra packages):
    python scripts/ai_audit.py <deck.txt> <forge cardsfolder> [overrides folder]

    The overrides folder defaults to ../mtg-private-list/engine/card_overrides
    (the private repo cloned next to this one).

Example:
    python scripts/ai_audit.py ../mtg-private-list/decks/<deck>/deck.txt \\
        ../card-forge/forge/forge-gui/res/cardsfolder
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import re
import sys
from pathlib import Path

# Card fixes live in the private repo (cloned next to this public one)
DEFAULT_OVERRIDES = Path("../mtg-private-list/engine/card_overrides")


# ---------------------------------------------------------------------------
# 2. Read the deck and find each card's Forge script
# ---------------------------------------------------------------------------
def deck_cards(deck: Path) -> list[str]:
    """All card names in a deck file (main deck and sideboard), without counts."""
    names = []
    for line in deck.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(\d+)\s+(.+?)\s*$", line)
        if m and m.group(2) not in names:
            names.append(m.group(2))
    return names


def script_files(folder: Path, names: set[str]) -> dict[str, Path]:
    found = {}
    for f in folder.rglob("*.txt"):
        with f.open(encoding="utf-8", errors="ignore") as fh:
            first = fh.readline().strip()
        if first.startswith("Name:") and first[5:].strip() in names:
            found[first[5:].strip()] = f
    return found


# ---------------------------------------------------------------------------
# 3. The checks
# ---------------------------------------------------------------------------
def problems(text: str) -> list[str]:
    front = text.split("\nALTERNATE")[0]
    found = []
    first_spell = next((ln for ln in front.splitlines() if ln.startswith("A:SP$")), "")
    if re.search(r"^ManaCost:.*X", front, re.M) or re.search(r"Cost\$[^|]*X", front):
        found.append("X cost: the AI pays the largest X it can -> give it fixed choices")
    if re.search(r"PayLife<X>", front):
        found.append("pay X life: the AI may pay almost all its life -> cap X")
    if first_spell.startswith("A:SP$ Pump") and not re.search(r"NumAtt|NumDef|KW\$|Curse|AILogic", first_spell):
        found.append("built from a Pump wrapper: the AI may never cast it -> rewrite with a plain effect")
    if first_spell.startswith("A:SP$ PumpAll") and "NumDef$ -" not in first_spell:
        found.append("built from a PumpAll wrapper: the AI may never cast it -> rewrite as DestroyAll")
    if re.search(r"DestroyAll \| ValidCards\$ (Artifact,Creature|Creature,Artifact)", front):
        found.append("destroys artifacts AND creatures: the AI almost never casts it -> split into two wipes")
    if "Gift" in front:
        found.append("gift: the AI may never promise it -> make the bonus always happen (and give the gift)")
    # we sacrifice ANOTHER permanent as a cost (not "sacrifice this", not the opponent's sacrifice)
    own_costs = "\n".join(ln for ln in front.splitlines() if not ln.startswith("K:Ward"))   # ward = the opponent pays
    if re.search(r"Sac<\d+/(?!CARDNAME|Self)[A-Za-z]", own_costs) and "AIPreference" not in front:
        found.append("sacrifice cost: add an AIPreference hint so it sacrifices tokens/cheap creatures first")
    if re.search(r"TargetMin\$ 0 \| TargetMax\$ MaxTgts|any number of target players", front, re.I):
        found.append("any number of target players: the AI might target itself -> target opponents only")
    if re.search(r"SP\$ Counter|DB\$ Counter", front):
        found.append("counterspell: test with a control AI profile")
    if re.search(r"SP\$ Charm", front):
        found.append("modal/spree: usually fine, check the reports that every mode gets used")
    if re.search(r"^AI:RemoveDeck:All", text, re.M):
        found.append("Forge says the AI can't use this card (RemoveDeck:All) -> needs a fix or avoid it")
    elif re.search(r"^AI:RemoveDeck:Random", text, re.M):
        found.append("Forge flag RemoveDeck:Random (only 'not for random decks'; usually fine)")
    return found


# ---------------------------------------------------------------------------
# 4. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    deck, folder = Path(sys.argv[1]), Path(sys.argv[2])
    overrides = Path(sys.argv[3]) if len(sys.argv) == 4 else DEFAULT_OVERRIDES
    fixed = {}
    if overrides.is_dir():
        for f in overrides.glob("*.txt"):
            with f.open(encoding="utf-8") as fh:
                fixed[fh.readline().strip()[5:].strip()] = f.name
    names = deck_cards(deck)
    files = script_files(folder, set(names))
    print(f"AI audit for {deck.name}: {len(names)} different cards\n")
    clean, flagged = [], 0
    for name in names:
        if name not in files:
            print(f"  ?? {name}: no Forge script found")
            continue
        text = files[name].read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^Types:[^\n]*Land", text.split("\nALTERNATE")[0], re.M):
            continue
        found = problems(text)
        if name in fixed:
            print(f"  FIXED {name}  (sim version: {fixed[name]})")
        elif found:
            flagged += 1
            print(f"  CHECK {name}")
            for f in found:
                print(f"        - {f}")
        else:
            clean.append(name)
    print(f"\n{len(clean)} cards look fine for the AI; {flagged} to check.")


if __name__ == "__main__":
    main()
