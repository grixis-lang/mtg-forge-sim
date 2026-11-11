"""
apply_ai_priorities.py
----------------------
Two "AI helpers" for simulations, used when the workflow's 'priorities'
switch is on:

  1. Threat priorities: makes the Forge AI treat chosen cards as bigger
     threats, so its removal goes to them first. A token is listed as
     "token:<token script name>", e.g. "token:c_0_0_a_construct_total_artifacts"
     (the files in Forge's res/tokenscripts folder).
     Forge's AI gives every creature a "threat score" (mostly from power and
     toughness) and points its removal at the highest score. A card script can
     add a bonus to that score with a line like:
         SVar:AIEvaluationModifier:300
     This script adds that line to the cards listed in a JSON file.

  2. Card overrides (optional): replaces a card's Forge script with a version
     in an overrides folder that the AI knows how to play. Example: a board
     wipe Forge builds from a "pump" effect the AI never casts can be replaced
     by a plain "destroy all" effect (like Meltdown) that it does cast.
     Our overrides live in the PRIVATE repo: engine/card_overrides/.

  It edits Forge's card scripts IN PLACE, so only run it on the throwaway copy
  used by the simulation (the GitHub Actions workflow does this).

How to run (Python 3.10+, no extra packages needed):
    python scripts/apply_ai_priorities.py <cardsfolder> <priorities.json> [<overrides folder>] [--quiet]

    --quiet   don't print the names of overridden cards (only how many). The
              public workflow uses this, because its logs are public and the
              overrides (kept in the private repo) show which cards we test.

Example (the overrides live in the private repo, cloned next to this one):
    python scripts/apply_ai_priorities.py forge-build/forge-gui/res/cardsfolder \\
        plans/ai_priorities.json ../mtg-private-list/engine/card_overrides
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import json
import sys
from pathlib import Path

MODIFIER = "SVar:AIEvaluationModifier:"


# ---------------------------------------------------------------------------
# 2. Find the script file for each card name
# ---------------------------------------------------------------------------
def card_name(path: Path) -> str | None:
    """The name on the first 'Name:' line of a card script, or None."""
    with path.open(encoding="utf-8", errors="ignore") as fh:
        first = fh.readline().strip()
    return first[5:].strip() if first.startswith("Name:") else None


def find_card_files(folder: Path, names: set[str]) -> dict[str, Path]:
    """Card name -> its script file in Forge's card folder."""
    found: dict[str, Path] = {}
    for f in folder.rglob("*.txt"):
        name = card_name(f)
        if name in names:
            found[name] = f
    return found


# ---------------------------------------------------------------------------
# 3. Add (or replace) the threat-score bonus on the card's front face
# ---------------------------------------------------------------------------
def set_modifier(path: Path, bonus: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    # Only the front face: stop at "ALTERNATE" (the back face of double-faced cards)
    end = next((i for i, ln in enumerate(lines) if ln.startswith("ALTERNATE")), len(lines))
    front = [ln for ln in lines[:end] if not ln.startswith(MODIFIER)]   # drop an old value
    # insert right after the Name/ManaCost/Types/PT header lines
    insert_at = next((i for i, ln in enumerate(front)
                      if ln.startswith(("K:", "A:", "T:", "S:", "SVar:", "Oracle:"))), len(front))
    front.insert(insert_at, f"{MODIFIER}{bonus}")
    path.write_text("\n".join(front + lines[end:]) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Replace card scripts with the versions in the overrides folder
# ---------------------------------------------------------------------------
def apply_overrides(folder: Path, overrides: Path, quiet: bool = False) -> None:
    replacements = {card_name(f): f for f in sorted(overrides.glob("*.txt"))}
    if None in replacements:
        sys.exit("Every override file must start with a 'Name:' line")
    targets = find_card_files(folder, set(replacements))
    missing = set(replacements) - set(targets)
    if missing:
        if quiet:   # don't name our cards in a public log
            sys.exit(f"{len(missing)} override file(s) match no Forge card (names hidden; run without --quiet locally)")
        sys.exit(f"No Forge card script to override for: {', '.join(sorted(missing))}")
    for name, src in replacements.items():
        targets[name].write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        if not quiet:
            print(f"  {name:<25} script replaced by {src.name}")
    if quiet:
        print(f"  {len(replacements)} card fix(es) applied (names hidden)")


# ---------------------------------------------------------------------------
# 5. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    # "--quiet" can go anywhere; everything else is a positional argument
    quiet = "--quiet" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if a != "--quiet"]
    if len(args) not in (2, 3):
        print(__doc__)
        sys.exit(1)
    folder, plan_path = Path(args[0]), Path(args[1])
    if not folder.is_dir():
        sys.exit(f"Card folder not found: {folder}")

    # Overrides first, so a threat bonus can still be added to an overridden card
    if len(args) == 3:
        if Path(args[2]).is_dir():
            apply_overrides(folder, Path(args[2]), quiet)
        else:
            print("  (no card fixes folder found, skipping card fixes)")

    plan = {k: v for k, v in json.loads(plan_path.read_text(encoding="utf-8")).items()
            if not k.startswith("_")}                    # skip notes like "_how_to_read"
    # Only whole numbers are allowed, so nothing unexpected gets written into Forge's files
    for name, bonus in plan.items():
        if not isinstance(bonus, int) or not 0 <= bonus <= 1000:
            sys.exit(f"Bonus for {name!r} must be a whole number from 0 to 1000, got {bonus!r}")
    # Tokens live in res/tokenscripts next to res/cardsfolder
    tokens_dir = folder.parent / "tokenscripts"
    token_names = {k for k in plan if k.startswith("token:")}
    files = find_card_files(folder, set(plan) - token_names)
    for key in token_names:
        token_file = tokens_dir / f"{key[6:]}.txt"
        if token_file.is_file():
            files[key] = token_file
    missing = set(plan) - set(files)
    if missing:
        sys.exit(f"No card script found for: {', '.join(sorted(missing))}")
    for name, bonus in plan.items():
        set_modifier(files[name], bonus)
        print(f"  {name:<25} threat bonus +{bonus}  ({files[name].name})")


if __name__ == "__main__":
    main()
