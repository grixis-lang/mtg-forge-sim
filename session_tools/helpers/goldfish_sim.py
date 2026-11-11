"""
goldfish_sim.py  (version 2)
----------------------------
"Goldfishing" = playing your deck with no opponent to measure consistency.
This script shuffles a deck many times and measures:

  1. How often the opening 7 is keepable, and how often you keep by 6 cards
  2. A list of "checks" for the deck, e.g. "can I cast Ashling on turn 2?"
     or "do I have blue-blue mana by turn 4?"

How to run (Python 3.10+, no extra packages needed):
    python goldfish_sim.py <deck-file> <preset>

    python goldfish_sim.py grixis_walkers_v1.txt walkers
    python goldfish_sim.py grixis_norman_main_v2.txt norman

The deck file uses import format ("4 Island"). A line "Sideboard" ends the
main deck. Checks are only measured on hands kept at 7 cards.

Simplifications (good to know when reading results):
  - Shock lands are always played untapped (you pay 2 life).
  - You always play a land each turn if you have one, in the best order.
    A tapped land is assumed to have been played on an earlier turn,
    unless every land you have is a tapped land.
  - "Keepable" only looks at land count (set per preset in KEEP_RULES)
    and having a blue source.
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import random                       # shuffling
import sys                          # command-line arguments
from dataclasses import dataclass, field
from functools import lru_cache     # remembers answers we already worked out
from itertools import combinations, product
from pathlib import Path

# ---------------------------------------------------------------------------
# 2. Settings
# ---------------------------------------------------------------------------
TRIALS = 50_000       # shuffles per run (more = more accurate, but slower)
RANDOM_SEED = 42      # same seed = same results every run
KEEP_MIN_LANDS = 2
KEEP_MAX_LANDS = 4    # default; each preset can override it (see KEEP_RULES)

# Land counts that make a 7-card hand keepable, per deck preset.
# Control decks with 26 lands are happy to keep 5-land hands.
KEEP_RULES = {
    "walkers": (2, 5),
    "norman": (2, 4),
}


# ---------------------------------------------------------------------------
# 3. How each land makes mana (see version 1 for details)
#    cond_colors are only available if you control another land with one of
#    the basic land types in cond_types (how "Verge" lands work).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Land:
    colors: frozenset
    cond_colors: frozenset = frozenset()
    cond_types: frozenset = frozenset()
    land_types: frozenset = frozenset()
    tapped: bool = False
    villain_any: bool = False


def L(colors, cond="", ctypes=(), types=(), tapped=False, villain=False):
    """Small helper so the land table below stays short and readable."""
    return Land(frozenset(colors), frozenset(cond), frozenset(ctypes),
                frozenset(types), tapped, villain)


LANDS = {
    "Island":             L("U", types=["Island"]),
    "Swamp":              L("B", types=["Swamp"]),
    "Mountain":           L("R", types=["Mountain"]),
    "Watery Grave":       L("UB", types=["Island", "Swamp"]),
    "Steam Vents":        L("UR", types=["Island", "Mountain"]),
    "Blood Crypt":        L("BR", types=["Swamp", "Mountain"]),
    "Thundering Falls":   L("UR", types=["Island", "Mountain"], tapped=True),
    "Gloomlake Verge":    L("U", cond="B", ctypes=["Island", "Swamp"]),
    "Riverpyre Verge":    L("R", cond="U", ctypes=["Island", "Mountain"]),
    "Blazemire Verge":    L("B", cond="R", ctypes=["Swamp", "Mountain"]),
    "Oscorp Industries":  L("UBR", tapped=True),
    "Villainous Hideout": L("C", villain=True),
}


# ---------------------------------------------------------------------------
# 4. Checks for each deck.
#    Each check is a dictionary:
#      name   - text shown in the results
#      turn   - the turn the check happens on
#      cost   - colored mana needed, e.g. {"U": 2} for blue-blue
#      total  - total mana needed (colored + generic)
#      cards  - (optional) at least one of these cards must be in hand
#      ashling_walker - (optional) special check, see section 7
# ---------------------------------------------------------------------------
WALKERS_4 = ["The Theorist, Jace Beleren", "Chandra, Torch of Defiance",
             "Ral, Crackling Wit"]

CHECK_PRESETS = {
    "walkers": [
        {"name": "Ashling castable on turn 2",
         "turn": 2, "cost": {"R": 1}, "total": 2, "cards": ["Ashling, Rekindled"]},
        {"name": "4-mana walker on turn 3 via Ashling",
         "turn": 3, "ashling_walker": True},
        # baselines: any colors, just enough lands (shows land-count misses)
        {"name": "BASELINE: 4 lands by turn 4",
         "turn": 4, "cost": {}, "total": 4},
        {"name": "BASELINE: 5 lands by turn 5",
         "turn": 5, "cost": {}, "total": 5},
        {"name": "UU available by turn 4 (Jace)",
         "turn": 4, "cost": {"U": 2}, "total": 4},
        {"name": "RR available by turn 4 (Chandra)",
         "turn": 4, "cost": {"R": 2}, "total": 4},
        {"name": "BB available by turn 5 (Cover-Up)",
         "turn": 5, "cost": {"B": 2}, "total": 5},
        {"name": "UU+RR available by turn 5 (Splatter)",
         "turn": 5, "cost": {"U": 2, "R": 2}, "total": 5},
    ],
    "norman": [
        {"name": "Norman castable on turn 2",
         "turn": 2, "cost": {"U": 1}, "total": 2, "cards": ["Norman Osborn"],
         "villain": True},
        {"name": "Transform Norman on turn 4 (UBR+1)",
         "turn": 4, "cost": {"U": 1, "B": 1, "R": 1}, "total": 4, "villain": True},
    ],
}


# ---------------------------------------------------------------------------
# 5. Load the deck
# ---------------------------------------------------------------------------
def load_deck(path: Path) -> list[str]:
    deck = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("sideboard"):
            break
        count, name = line.split(" ", 1)
        deck.extend([name.strip()] * int(count))
    return deck


def is_land(card: str) -> bool:
    return card in LANDS


def blue_source(name: str) -> bool:
    land = LANDS[name]
    return "U" in land.colors or "U" in land.cond_colors or land.villain_any


def keepable(hand: list[str]) -> bool:
    lands = [c for c in hand if is_land(c)]
    return (KEEP_MIN_LANDS <= len(lands) <= KEEP_MAX_LANDS
            and any(blue_source(l) for l in lands))


def keepable_after_mulligan(seven: list[str]) -> bool:
    """London mulligan to 6: is there one card we can bottom to get a keep?"""
    return any(keepable(seven[:i] + seven[i + 1:]) for i in range(7))


# ---------------------------------------------------------------------------
# 6. Can these lands pay a cost?
#    Each land makes 1 mana of one color. We try every combination of colors
#    (fast because there are only a few lands) and see if any one works.
#    lru_cache remembers results, so repeated land sets are instant.
# ---------------------------------------------------------------------------
def options_for(name: str, others: tuple, villain: bool) -> set:
    land = LANDS[name]
    if land.villain_any and villain:
        return {"U", "B", "R"}
    opts = set(land.colors)
    other_types = set()
    for o in others:
        other_types |= LANDS[o].land_types
    if land.cond_types & other_types:
        opts |= land.cond_colors
    return opts


@lru_cache(maxsize=None)
def can_pay(lands: tuple, cost: tuple, total: int, villain: bool) -> bool:
    if len(lands) < total:
        return False
    need = dict(cost)
    opts = [options_for(l, lands[:i] + lands[i + 1:], villain)
            for i, l in enumerate(lands)]
    for choice in product(*opts):
        if all(choice.count(c) >= n for c, n in need.items()):
            return True
    return False


@lru_cache(maxsize=None)
def best_lands_can_pay(seen: tuple, turn: int, cost: tuple, total: int,
                       villain: bool) -> bool:
    """Pick the best `turn` lands from those seen and test the cost."""
    k = min(turn, len(seen))
    for pick in set(combinations(seen, k)):
        usable = list(pick)
        # a tapped land can't be used if it is the only kind of land we have
        if all(LANDS[l].tapped for l in usable) and k == turn:
            usable = usable[:-1]
        if can_pay(tuple(sorted(usable)), cost, total, villain):
            return True
    return False


# ---------------------------------------------------------------------------
# 7. Special check: Ashling on turn 2, then a 4-mana planeswalker on turn 3.
#    Turn 3: pay {U} to transform Ashling. Ashling, Rimebound then adds two
#    mana of one color for spells costing 4+. With the 2 lands left over,
#    that is 4 mana for Jace, Chandra or Ral (Ashling's mana covers the colors).
# ---------------------------------------------------------------------------
def ashling_walker_ok(cards_t2, cards_t3, lands_t2, lands_t3) -> bool:
    if "Ashling, Rekindled" not in cards_t2 or len(lands_t3) < 3:
        return False
    if not best_lands_can_pay(tuple(sorted(lands_t2)), 2, (("R", 1),), 2, False):
        return False
    has_walker = any(w in cards_t3 for w in WALKERS_4)
    can_transform = best_lands_can_pay(tuple(sorted(lands_t3)), 3, (("U", 1),), 1, False)
    return has_walker and can_transform


# ---------------------------------------------------------------------------
# 8. Run everything
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in CHECK_PRESETS:
        print("Usage: python goldfish_sim.py <deck-file> <preset>")
        print("Presets:", ", ".join(CHECK_PRESETS))
        sys.exit(1)
    deck_path = Path(sys.argv[1])
    if not deck_path.is_file():
        print(f"Deck file not found: {deck_path}")
        sys.exit(1)

    deck = load_deck(deck_path)
    preset = sys.argv[2]
    checks = CHECK_PRESETS[preset]

    # Use this preset's keep rule (falls back to the defaults above)
    global KEEP_MIN_LANDS, KEEP_MAX_LANDS
    KEEP_MIN_LANDS, KEEP_MAX_LANDS = KEEP_RULES.get(
        preset, (KEEP_MIN_LANDS, KEEP_MAX_LANDS))
    rng = random.Random(RANDOM_SEED)

    keep7 = keep_by6 = 0
    hits = {(c["name"], play): 0 for c in checks for play in (True, False)}

    for _ in range(TRIALS):
        rng.shuffle(deck)
        if not keepable(deck[:7]):
            rng.shuffle(deck)
            keep_by6 += keepable_after_mulligan(deck[:7])
            continue
        keep7 += 1
        keep_by6 += 1

        for on_play in (True, False):
            extra = 0 if on_play else 1      # on the draw you see 1 more card
            for c in checks:
                t = c["turn"]
                seen = deck[:7 + (t - 1) + extra]
                lands = tuple(sorted(x for x in seen if is_land(x)))
                if c.get("ashling_walker"):
                    s2 = deck[:7 + 1 + extra]
                    l2 = [x for x in s2 if is_land(x)]
                    ok = ashling_walker_ok(s2, seen, l2, list(lands))
                else:
                    if "cards" in c and not any(x in seen for x in c["cards"]):
                        continue
                    cost = tuple(sorted(c["cost"].items()))
                    ok = best_lands_can_pay(lands, t, cost, c["total"],
                                            c.get("villain", False))
                hits[(c["name"], on_play)] += ok

    # -----------------------------------------------------------------------
    # 9. Print results
    # -----------------------------------------------------------------------
    pct = lambda n, d: f"{100 * n / d:5.1f}%" if d else "  n/a"
    print(f"Deck: {deck_path.name} ({len(deck)} cards, "
          f"{sum(map(is_land, deck))} lands) | {TRIALS:,} shuffles\n")
    print(f"Keep rule: {KEEP_MIN_LANDS}-{KEEP_MAX_LANDS} lands + a blue source")
    print(f"Keepable 7-card hand ......... {pct(keep7, TRIALS)}")
    print(f"Keepable by 6 (1 mulligan) ... {pct(keep_by6, TRIALS)}\n")
    print(f"{'Check (out of kept 7-card hands)':<40}{'Play':>8}{'Draw':>8}")
    for c in checks:
        print(f"{c['name']:<40}{pct(hits[(c['name'], True)], keep7):>8}"
              f"{pct(hits[(c['name'], False)], keep7):>8}")


if __name__ == "__main__":
    main()
