"""
convert_decks.py
----------------
Converts decklists from the common import format (the format MTG Arena and
MTGO use) into Forge's .dck deck format, so Forge's AI can play them.

  Input  (decks/*.txt):     4 Kili the Resourceful
  Output (forge_decks/*.dck): 4 Kíli the Resourceful

Forge spells some names with accents (Kíli, Dáin). This script looks up every
card in Forge's own card files and uses Forge's exact spelling, ignoring
accents and capital letters when matching. Any card Forge doesn't know is
reported, and the script stops with an error so a broken deck is never used.

How to run (Python 3.10+, no extra packages needed):
    python scripts/convert_decks.py <forge-cardsfolder> <decks-dir> <output-dir> [--standard]

Example:
    python scripts/convert_decks.py forge/forge-gui/res/cardsfolder decks forge_decks --standard

--standard also checks Standard legality using Forge's official format file
(forge-gui/res/formats/Sanctioned/Standard.txt): every card must be printed
in a Standard set and must not be banned. Illegal decks stop the run.
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import sys                  # command-line arguments and exit codes
import unicodedata          # removes accents: "Kíli" -> "Kili"
from pathlib import Path    # safe, cross-platform file paths


# ---------------------------------------------------------------------------
# 2. Turn a card name into a simple lookup key
#    "Kíli the Resourceful" and "kili the resourceful" both become the same key.
# ---------------------------------------------------------------------------
def simple_key(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)          # split í into i + accent
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(no_accents.lower().split())               # lowercase, tidy spaces


# ---------------------------------------------------------------------------
# 3. Read every card name Forge knows
#    Each Forge card is a text file containing a line like "Name:Opt".
# ---------------------------------------------------------------------------
def load_forge_names(cardsfolder: Path) -> dict[str, str]:
    names = {}
    for card_file in cardsfolder.rglob("*.txt"):
        text = card_file.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.startswith("Name:"):
                forge_name = line[len("Name:"):].strip()
                names.setdefault(simple_key(forge_name), forge_name)
    return names


# ---------------------------------------------------------------------------
# 3b. Standard legality: which cards are legal, and which are banned
#     Forge's format file lists the legal set codes and the banned cards.
#     Each set's file in res/editions lists its cards as "123 R Card Name @Artist".
# ---------------------------------------------------------------------------
def load_standard(res_dir: Path) -> tuple[set[str], set[str]]:
    fmt = (res_dir / "formats" / "Sanctioned" / "Standard.txt").read_text(encoding="utf-8")
    sets, banned = set(), set()
    for line in fmt.splitlines():
        if line.startswith("Sets:"):
            sets = {code.strip() for code in line[len("Sets:"):].split(",")}
        elif line.startswith("Banned:"):
            banned = {simple_key(c) for c in line[len("Banned:"):].split(";") if c.strip()}

    legal = set()
    for edition in (res_dir / "editions").glob("*.txt"):
        text = edition.read_text(encoding="utf-8", errors="ignore")
        code = next((l[5:].strip() for l in text.splitlines() if l.startswith("Code=")), None)
        if code not in sets:
            continue
        in_cards = False
        for line in text.splitlines():
            if line.startswith("["):
                in_cards = line.strip().lower() not in ("[metadata]",) and "=" not in line
                continue
            if not in_cards or "=" in line or not line.strip():
                continue
            parts = line.split(" ", 2)          # number, rarity, "Name @Artist"
            if len(parts) == 3:
                legal.add(simple_key(parts[2].split(" @")[0]))
    return legal, banned


# ---------------------------------------------------------------------------
# 4. Read one import-format decklist into (main, sideboard) lists
#    Each list holds (count, card name) pairs.
# ---------------------------------------------------------------------------
def read_decklist(path: Path) -> tuple[list, list]:
    main, side = [], []
    current = main
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("sideboard"):
            current = side                     # everything after this is sideboard
            continue
        count, _, name = line.partition(" ")
        if not count.isdigit():
            raise ValueError(f"{path.name}: can't read line '{line}'")
        current.append((int(count), name.strip()))
    return main, side


# ---------------------------------------------------------------------------
# 5. Write a Forge .dck file
# ---------------------------------------------------------------------------
def write_dck(path: Path, deck_name: str, main: list, side: list) -> None:
    lines = ["[metadata]", f"Name={deck_name}", "[Main]"]
    lines += [f"{n} {card}" for n, card in main]
    lines += ["[Sideboard]"]
    lines += [f"{n} {card}" for n, card in side]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check_standard = "--standard" in sys.argv
    if len(args) != 3:
        print(__doc__)
        sys.exit(1)

    cardsfolder, decks_dir, out_dir = (Path(a) for a in args)
    if not cardsfolder.is_dir():
        sys.exit(f"Forge card folder not found: {cardsfolder}")
    if not decks_dir.is_dir():
        sys.exit(f"Decks folder not found: {decks_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    forge_names = load_forge_names(cardsfolder)
    print(f"Forge knows {len(forge_names):,} cards")
    legal = banned = None
    if check_standard:
        legal, banned = load_standard(cardsfolder.parent)   # cardsfolder is inside res/
        print(f"Standard: {len(legal):,} legal cards, {len(banned)} banned")

    problems = 0
    for deck_file in sorted(decks_dir.glob("*.txt")):
        main_list, side_list = read_decklist(deck_file)

        # Swap each name for Forge's exact spelling; collect any unknown cards
        fixed_main, fixed_side, missing = [], [], []
        for source, target in ((main_list, fixed_main), (side_list, fixed_side)):
            for count, name in source:
                forge_name = forge_names.get(simple_key(name))
                if forge_name is None:
                    missing.append(name)
                else:
                    target.append((count, forge_name))

        main_count = sum(n for n, _ in fixed_main)
        illegal = []
        if check_standard:
            for _, card in fixed_main + fixed_side:
                key = simple_key(card)
                if key in banned:
                    illegal.append(f"{card} (banned)")
                elif key not in legal:
                    illegal.append(f"{card} (not in a Standard set)")
        status = "OK"
        if illegal:
            status = f"NOT STANDARD-LEGAL: {', '.join(sorted(set(illegal)))}"
            problems += 1
        elif missing:
            status = f"MISSING CARDS: {', '.join(missing)}"
            problems += 1
        elif main_count != 60:
            status = f"WARNING: main deck has {main_count} cards, not 60"
            problems += 1

        deck_name = deck_file.stem                    # file name without ".txt"
        if not missing and not illegal:
            write_dck(out_dir / f"{deck_name}.dck", deck_name, fixed_main, fixed_side)
        print(f"  {deck_name:<25} {main_count:>3} cards  {status}")

    # A non-zero exit code makes GitHub Actions stop instead of using bad decks
    if problems:
        sys.exit(f"\n{problems} deck(s) need fixing before simulating.")
    print("\nAll decks converted.")


if __name__ == "__main__":
    main()
