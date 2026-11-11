"""
analyze_logs.py
---------------
Reads Forge's full game log (saved by run_matchup.py --log-out) and reports:

  1. Card performance: how often we win in games where each of our cards
     was cast, compared with our overall win rate
  2. Game length: which turn games end on, for wins and for losses
  3. How games are decided: position round by round (lands, creatures on the
     board and life) in wins vs losses,
     who dealt the finishing blow, and our main damage sources in wins
  4. Turning points: comebacks (we were behind and won) and collapses
     (we were ahead and lost), what we were behind/ahead on, and the cards
     played when it swung
  5. Removal report: what each of our targeted spells hit, and how often
     the target actually died that turn (e.g. fight spells)
  6. Threat growth: how hard each opponent creature hits us, round by round
     Abilities report: our activated abilities and triggers (e.g. a creature
     that exiles cards from graveyards) and what they targeted
     Wipe report: each time we cast a board wipe, how many of their creatures
     and how many of ours died that turn
     Counterspell report: what our counterspells were aimed at
  7. Running out of gas: how many spells we cast per turn, early vs late
  8. Mana, play/draw and mulligans
  9. Biggest threats and how we lose

How to run (Python 3.10+, no extra packages needed):
    python scripts/analyze_logs.py <game-log> <our deck name> <output.md> [<cardsfolder>]

Example:
    python scripts/analyze_logs.py logs/mono_green_landfall.log our_deck report.md \
        forge/forge-gui/res/cardsfolder

The optional cardsfolder (Forge's card scripts) tells the script which cards
are creatures, so it can count each side's board. Without it, board counts
only include creatures seen attacking or blocking.

The log lines it reads look like:
    Turn: Turn 3 (Ai(1)-our_deck)
    Mulligan: Ai(1)-our_deck has kept a hand of 6 cards
    Land: Ai(1)-our_deck played Swamp (19)
    Add To Stack: Ai(1)-our_deck cast Some Fight Spell targeting [Our Creature (12), Mossborn Hydra (88)]
    Damage: Mossborn Hydra (88) deals 6 combat damage to Ai(1)-our_deck.
    Damage: Our Creature (12) deals 4 damage to Mossborn Hydra (88).
    Life: Life: Ai(1)-our_deck 20 > 14
    Combat: Ai(2)-mono_green_landfall assigned Mossborn Hydra (88) to attack Ai(1)-our_deck.
    Zone Change: Mossborn Hydra (88) was put into Graveyard from Battlefield.
    Game Outcome: Turn 9
    Game Outcome: Ai(1)-our_deck has lost because life total reached 0
    Game Result: Game 3 ended in 41234 ms. Ai(2)-mono_green_landfall has won!

Forge counts every player's turn ("Turn 7" = the 4th turn of the player on
the play). This report turns that into ROUNDS: round 4 = each player's 4th turn.
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Regular expressions for the log lines we care about
TURN = re.compile(r"^Turn: Turn (\d+) \((.+)\)$")
MULLIGAN_KEPT = re.compile(r"^Mulligan: (.+) has kept a hand of (\d+) cards")
LAND = re.compile(r"^Land: (.+?) played (.+?) \((\d+)\)")
CAST = re.compile(r"^Add To Stack: (.+?) cast (.+?)(?: targeting (.*))?$")
# Activated abilities and triggers: "... activated Scavenging Ooze targeting [Fabled Passage (41)]"
ABILITY = re.compile(r"^Add To Stack: (.+?) (activated|triggered) (.+?)(?: targeting (.*))?$")
# Damage to a player:   "<source> deals 6 combat damage to <player>."
DAMAGE_PLAYER = re.compile(r"^Damage: (.+?) deals (\d+) (combat|non-combat) damage to (.+?)\.?$")
# Damage to a creature: "<source> deals 4 damage to <card> (88)."  (optional "(Deathtouch)")
DAMAGE_CARD = re.compile(r"^Damage: (.+?) deals (\d+) damage(?: \([^)]*\))? to (.+?) \((\d+)\)\.?$")
LIFE = re.compile(r"^Life: (?:Life: )?(.+?) (-?\d+) > (-?\d+)$")
ATTACK = re.compile(r"^(?:Combat: )?(.+?) assigned (.+) to attack (.+?)\.?$")
BLOCK = re.compile(r"^(?:Combat: )?(.+?) assigned (.+) to block (.+?)\.?$")
DIES = re.compile(r"^Zone Change: (.+?) \((\d+)\) was put into (\w+) from Battlefield")
OUTCOME_TURN = re.compile(r"^Game Outcome: Turn (\d+)")
OUTCOME_LOST = re.compile(r"^Game Outcome: (.+?) has lost because (.+)$")
RESULT_WIN = re.compile(r"Game Result: Game (\d+) ended in \d+ ms\. (.+?) has won!")
RESULT_DRAW = re.compile(r"Game Result: Game (\d+) ended in a Draw")
CARD_WITH_ID = re.compile(r"([^,\[\]]+?) \((\d+)\)")   # "Mossborn Hydra (88)" -> name, id
CARD_ID = re.compile(r"\s*\(\d+\)$")                     # removes " (88)" from "Mossborn Hydra (88)"

STARTING_LIFE = 20

# Position score used for "ahead" and "behind" (explained in the report):
#   each extra creature or planeswalker on board = 1 point
#   every 3 more power on board                  = 1 point   (a 16/16 Hydra ~ 6 points)
#   each extra land                              = 1 point
#   every 5 life more than the opponent          = 1 point
BOARD_POINTS, POWER_PER_POINT, LAND_POINTS, LIFE_PER_POINT = 1, 3, 1, 5
PLANESWALKER_POWER = 3       # planeswalkers have no power; count them like a 3-power creature
TOKEN_POWER = 1              # power we assume for a token until it deals damage
SWING = 3          # a game counts as "behind"/"ahead" only past this many points


def load_wipe_names(folder: Path | None) -> set[str]:
    """Names of board wipes, read from Forge's card scripts: cards whose front
    face has a "destroy all", "exile all", "sacrifice all" or "-X/-X to all"
    effect (e.g. Day of Judgment, Massacre Wurm's enters trigger)."""
    wipes: set[str] = set()
    if not folder or not folder.is_dir():
        return wipes
    for f in folder.rglob("*.txt"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        front = text.split("\nALTERNATE")[0]
        if not front.startswith("Name:"):
            continue
        name = front.split("\n", 1)[0][5:].strip()
        if ("DB$ DestroyAll" in front or "SP$ DestroyAll" in front or "ChangeZoneAll" in front and "Destination$ Exile" in front
                or ("PumpAll" in front and "NumDef$ -" in front)):
            wipes.add(name)
    return wipes


def load_counter_names(folder: Path | None) -> set[str]:
    """Names of cards that can counter a spell (a "Counter" effect on the front face)."""
    names: set[str] = set()
    if not folder or not folder.is_dir():
        return names
    for f in folder.rglob("*.txt"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        front = text.split("\nALTERNATE")[0]
        if front.startswith("Name:") and ("SP$ Counter" in front or "DB$ Counter" in front):
            names.add(front.split("\n", 1)[0][5:].strip())
    return names


def load_card_kinds(folder: Path | None) -> dict[str, tuple[str, int]]:
    """Card name -> (kind, printed power). kind is "creature", "planeswalker" or
    "other". Read from Forge's card scripts (lines like "Name:Mossborn Hydra",
    "Types:Creature Hydra" and "PT:0/0")."""
    kinds: dict[str, tuple[str, int]] = {}
    if not folder or not folder.is_dir():
        return kinds
    for f in folder.rglob("*.txt"):
        name = types = pt = None
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("Name:") and name is None:
                name = line[5:].strip()
            elif line.startswith("Types:") and types is None:
                types = line[6:]
            elif line.startswith("PT:") and pt is None:
                pt = line[3:]
            elif line.startswith("ALTERNATE"):          # stop at the back face
                break
        if name and types:
            power = pt.split("/")[0].strip() if pt else "0"
            kind = ("creature" if "Creature" in types else
                    "planeswalker" if "Planeswalker" in types else
                    "land" if "Land" in types else "other")
            kinds[name] = (kind, int(power) if power.isdigit() else 0)
    return kinds


# ---------------------------------------------------------------------------
# 2. Split the log into games and pull out the facts for each game
# ---------------------------------------------------------------------------
def new_game() -> dict:
    return {
        "turns": 0, "forge_turn": 0, "on_play": None, "kept": 7,
        "lands": Counter(), "cast": [], "damage_taken": Counter(),
        "loss_reason": None, "result": None, "our_turn_index": 0,
        # --- new: life, damage and board events -------------------------
        "life": {"us": STARTING_LIFE, "opp": STARTING_LIFE},
        # snapshot at the start of each turn:
        # (forge turn, our life, their life, our lands, their lands,
        #  our creature count, their creature count, our power, their power)
        "snapshots": [],
        "land_count": {"us": 0, "opp": 0},
        # what's on each board: key -> [name, power]. The key is the card id once
        # we've seen it ("88"); right after being cast it is "cast:<name>:<n>"
        "board": {"us": {}, "opp": {}},
        "cast_no": 0,
        "dealt": Counter(),       # our sources -> damage we dealt to the opponent
        "last_hit": {"us": None, "opp": None},   # last source that damaged each player
        "last_event": {"us": None, "opp": None}, # "damage" or "life" (life loss without damage)
        "casts_by_turn": defaultdict(list),      # forge turn -> [(side, card)]
        "targets": [],            # (forge turn, our card, [(name, id)] targets)
        "abilities": [],          # (forge turn, "activated"/"triggered", our card, [(name, id)] targets)
        "raw_targets": [],        # (forge turn, our card, target text as Forge wrote it)
        "deaths": [],             # (forge turn, name, id) creatures/planeswalkers that left play
        "card_damage": [],        # (forge turn, source name, source id, target id)
        "opp_hits": [],           # (round, opponent source, damage) combat hits on us
        "owner": {},              # card id -> "us"/"opp" (from attacks, blocks, lands)
        "our_names": set(), "opp_names": set(),  # card names each side cast
    }


def parse(log_text: str, ours: str, kinds: dict[str, tuple[str, int]] | None = None) -> list[dict]:
    kinds = kinds or {}
    # Forge names players "Ai(1)-<deck>"; match ours by the part after the dash
    def is_ours(player: str) -> bool:
        name = player.split(")-", 1)[-1].strip()
        # sideboarded copies are named "<ours>__sb__<opponent>"; count them as ours too
        return name == ours or name.startswith(ours + "__sb__")

    def side(player: str) -> str:
        return "us" if is_ours(player) else "opp"

    def split_cards(text: str) -> list[tuple[str, str]]:
        return [(n.strip(), i) for n, i in CARD_WITH_ID.findall(text)]

    def printed_power(name: str) -> int:
        kind, power = kinds.get(name, ("creature", TOKEN_POWER))
        return PLANESWALKER_POWER if kind == "planeswalker" else power

    def cast_key(who: str, name: str) -> str | None:
        """A board entry for this name that we haven't matched to an id yet."""
        return next((k for k, (nm, _) in g["board"][who].items()
                     if k.startswith("cast:") and nm == name), None)

    def seen_in_play(who: str, name: str, cid: str) -> None:
        """A card attacked or blocked: make sure it's on that side's board under
        its id (catches tokens and creatures we didn't see being cast)."""
        board = g["board"][who]
        if cid in board:
            return
        k = cast_key(who, name)
        board[cid] = board.pop(k) if k else [name, printed_power(name)]

    def saw_damage(cid: str | None, amount: int) -> None:
        """A creature dealt this much damage: it has at least that much power
        (this is how we learn that a Hydra with counters is a 16/16)."""
        for who in ("us", "opp"):
            if cid in g["board"][who]:
                g["board"][who][cid][1] = max(g["board"][who][cid][1], amount)

    def snapshot(turn: int) -> tuple:
        b = g["board"]
        return (turn, g["life"]["us"], g["life"]["opp"], g["land_count"]["us"],
                g["land_count"]["opp"], len(b["us"]), len(b["opp"]),
                sum(pw for _, pw in b["us"].values()), sum(pw for _, pw in b["opp"].values()))

    games, g = [], new_game()
    for raw in log_text.splitlines():
        line = raw.strip()

        m = TURN.match(line)
        if m:
            turn_no, player = int(m.group(1)), m.group(2)
            g["turns"] = max(g["turns"], turn_no)
            g["forge_turn"] = turn_no
            g["snapshots"].append(snapshot(turn_no))
            if g["on_play"] is None:
                g["on_play"] = is_ours(player)         # who took turn 1
            if is_ours(player):
                g["our_turn_index"] += 1                # our 1st, 2nd, 3rd turn...
            continue

        m = MULLIGAN_KEPT.match(line)
        if m and is_ours(m.group(1)):
            g["kept"] = int(m.group(2))
            continue

        m = LAND.match(line)
        if m:
            g["owner"][m.group(3)] = side(m.group(1))
            g["land_count"][side(m.group(1))] += 1
            if is_ours(m.group(1)):
                g["lands"][g["our_turn_index"]] += 1
            continue

        m = ABILITY.match(line)
        if m:
            if is_ours(m.group(1)):
                g["abilities"].append((g["forge_turn"], m.group(2), m.group(3).strip(),
                                       split_cards(m.group(4) or "")))
            continue

        m = CAST.match(line)
        if m:
            who, card, targets = side(m.group(1)), m.group(2).strip(), m.group(3)
            g["casts_by_turn"][g["forge_turn"]].append((who, card))
            (g["our_names"] if who == "us" else g["opp_names"]).add(card)
            if kinds.get(card, ("other", 0))[0] in ("creature", "planeswalker"):
                g["cast_no"] += 1                         # assume it resolved and entered
                g["board"][who][f"cast:{card}:{g['cast_no']}"] = [card, printed_power(card)]
            if who == "us":
                g["cast"].append((card, g["our_turn_index"]))
                if targets:
                    g["raw_targets"].append((g["forge_turn"], card, targets))
                    g["targets"].append((g["forge_turn"], card, split_cards(targets)))
            continue

        m = DAMAGE_PLAYER.match(line)
        if m:
            source, amount, kind, target = CARD_ID.sub("", m.group(1)), int(m.group(2)), m.group(3), m.group(4)
            who = side(target)
            sid = CARD_WITH_ID.search(m.group(1))
            if sid and kind == "combat":                 # a creature hitting a player belongs to the other side
                g["owner"].setdefault(sid.group(2), "opp" if who == "us" else "us")
                saw_damage(sid.group(2), amount)
            g["last_hit"][who], g["last_event"][who] = source, "damage"
            if who == "us":
                g["damage_taken"][source] += amount
                if kind == "combat":
                    g["opp_hits"].append(((g["forge_turn"] + 1) // 2, source, amount))
            else:
                g["dealt"][source] += amount
            continue

        m = DAMAGE_CARD.match(line)
        if m:
            src = m.group(1)
            src_id = (CARD_WITH_ID.search(src).group(2) if CARD_WITH_ID.search(src) else None)
            g["card_damage"].append((g["forge_turn"], CARD_ID.sub("", src), src_id, m.group(4)))
            saw_damage(src_id, int(m.group(2)))
            continue

        m = LIFE.match(line)
        if m:
            who, old, new = side(m.group(1)), int(m.group(2)), int(m.group(3))
            g["life"][who] = new
            # a drop in life that wasn't from damage (e.g. "lose 2 life" from Massacre Wurm)
            if new < old and g["last_event"][who] != "damage":
                g["last_hit"][who] = "life loss effect"
            g["last_event"][who] = "life"
            continue

        m = ATTACK.match(line)
        if m:
            for nm, cid in split_cards(m.group(2)):
                g["owner"][cid] = side(m.group(1))
                seen_in_play(side(m.group(1)), nm, cid)
            continue

        m = BLOCK.match(line)
        if m:
            for nm, cid in split_cards(m.group(2)):
                g["owner"][cid] = side(m.group(1))
                seen_in_play(side(m.group(1)), nm, cid)
            for _, cid in split_cards(m.group(3)):    # the blocked attacker belongs to the other side
                g["owner"].setdefault(cid, "opp" if side(m.group(1)) == "us" else "us")
            continue

        m = DIES.match(line)
        if m:
            name, cid = m.group(1).strip(), m.group(2)
            # take it off the board of whichever side it belonged to
            removed = False
            for who in ("us", "opp"):
                if cid in g["board"][who]:
                    del g["board"][who][cid]
                    removed = True
            if not removed:                               # not matched to an id yet
                who = g["owner"].get(cid)
                sides = [who] if who else [w for w in ("us", "opp") if cast_key(w, name)]
                if len(sides) == 1 and cast_key(sides[0], name):
                    del g["board"][sides[0]][cast_key(sides[0], name)]
                    removed = True
            # only creatures/planeswalkers count as deaths (not fetch lands, Treasures...)
            if removed:
                g["deaths"].append((g["forge_turn"], name, cid))
            continue

        m = OUTCOME_LOST.match(line)
        if m and is_ours(m.group(1)):
            g["loss_reason"] = m.group(2).strip()
            continue

        m = OUTCOME_TURN.match(line)
        if m:
            g["final_turn"] = int(m.group(1))       # Forge's count: rounds of turns
            continue

        m = RESULT_WIN.search(line)
        if m:
            g["result"] = "win" if is_ours(m.group(2)) else "loss"
        elif RESULT_DRAW.search(line):
            g["result"] = "draw"
        if g["result"]:                              # end of this game
            g["snapshots"].append(snapshot(g["forge_turn"] + 1))
            games.append(g)
            g = new_game()
    return games


# ---------------------------------------------------------------------------
# 3. Small helpers for the report
# ---------------------------------------------------------------------------
def pct(wins: float, games: int) -> str:
    return f"{100 * wins / games:.0f}%" if games else "n/a"


def score(g: dict) -> float:                         # draws count as half a win
    return {"win": 1.0, "draw": 0.5}.get(g["result"], 0.0)


def round_of(g: dict) -> int:
    return g.get("final_turn") or (g["turns"] + 1) // 2


def owner_of(g: dict, name: str, cid: str) -> str | None:
    """Which side a card belongs to: by its id if we saw it attack/block/played,
    otherwise by name if only one side ever cast a card with that name."""
    if cid in g["owner"]:
        return g["owner"][cid]
    if name in g["our_names"] and name not in g["opp_names"]:
        return "us"
    if name in g["opp_names"] and name not in g["our_names"]:
        return "opp"
    return None


def killer_of(g: dict, turn: int, name: str, cid: str) -> str:
    """Best guess at which of OUR cards removed an opponent's creature."""
    # 1) one of our targeted spells this turn named it as a target
    for t, card, tgts in reversed(g["targets"]):
        if t == turn and any(i == cid for _, i in tgts):
            return card
    # 2) one of our creatures damaged it this turn (combat, fight, blocks)
    for t, src, src_id, tid in reversed(g["card_damage"]):
        if t == turn and tid == cid and owner_of(g, src, src_id) == "us":
            return f"{src} (damage)"
    # 3) we cast an untargeted spell this turn (board wipes)
    ours = [c for who, c in g["casts_by_turn"].get(turn, []) if who == "us"]
    targeted = {c for t, c, _ in g["targets"] if t == turn}
    untargeted = [c for c in ours if c not in targeted]
    if untargeted:
        return f"{untargeted[-1]} (likely)"
    return "other / unknown"


def position(snap: tuple) -> float:
    """Position score for one snapshot: positive = we're ahead."""
    _, life_us, life_opp, lands_us, lands_opp, board_us, board_opp, pow_us, pow_opp = snap
    return (BOARD_POINTS * (board_us - board_opp) + (pow_us - pow_opp) / POWER_PER_POINT
            + LAND_POINTS * (lands_us - lands_opp) + (life_us - life_opp) / LIFE_PER_POINT)


def lead_series(g: dict) -> list[tuple[tuple, float]]:
    """(snapshot, position score) at the start of each turn."""
    return [(snap, position(snap)) for snap in g["snapshots"]]


def behind_on(snap: tuple) -> list[str]:
    """Which areas we were behind on in this snapshot."""
    _, life_us, life_opp, lands_us, lands_opp, board_us, board_opp, pow_us, pow_opp = snap
    areas = []
    if lands_us < lands_opp:
        areas.append("lands (mana)")
    if board_us < board_opp:
        areas.append("creature count")
    if pow_us < pow_opp:
        areas.append("board power")
    if life_us < life_opp:
        areas.append("life")
    return areas


def ahead_on(snap: tuple) -> list[str]:
    """Which areas we were ahead on in this snapshot."""
    _, life_us, life_opp, lands_us, lands_opp, board_us, board_opp, pow_us, pow_opp = snap
    areas = []
    if lands_us > lands_opp:
        areas.append("lands (mana)")
    if board_us > board_opp:
        areas.append("creature count")
    if pow_us > pow_opp:
        areas.append("board power")
    if life_us > life_opp:
        areas.append("life")
    return areas


# ---------------------------------------------------------------------------
# 4. Build the Markdown report
# ---------------------------------------------------------------------------
def report(games: list[dict], ours: str, opponent: str,
           kinds: dict[str, tuple[str, int]] | None = None,
           wipes: set[str] | None = None,
           counters: set[str] | None = None) -> str:
    kinds_lookup = {name: kind for name, (kind, _) in (kinds or {}).items()}
    n = len(games)
    total = sum(score(g) for g in games)
    wins = [g for g in games if g["result"] == "win"]
    losses = [g for g in games if g["result"] == "loss"]
    out = [f"# Game analysis: {ours} vs {opponent}", "",
           f"{n} games, overall win rate **{pct(total, n)}**. "
           "Forge AI games; small groups (under ~10 games) are mostly luck.", "",
           "\"Round 4\" means each player's 4th turn.", ""]

    # --- Card performance -------------------------------------------------
    out += ["## Card performance", "",
            "Win rate in games where we cast the card at least once, next to "
            "games where we didn't. A card well above the overall rate is pulling "
            "its weight; well below may be too slow or weak here.", "",
            "| Card | Games cast | Win % when cast | Win % when not cast | Avg turn first cast |",
            "|---|---:|---:|---:|---:|"]
    cast_in = defaultdict(list)                      # card -> games where cast
    first_turn = defaultdict(list)
    for g in games:
        seen = {}
        for card, turn in g["cast"]:
            seen.setdefault(card, turn)
        for card, turn in seen.items():
            cast_in[card].append(g)
            first_turn[card].append(turn)
    rows = []
    for card, gs in cast_in.items():
        w = sum(score(g) for g in gs)
        not_cast = [g for g in games if g not in gs]
        wn = sum(score(g) for g in not_cast)
        rows.append((w / len(gs), card, len(gs), pct(w, len(gs)),
                     pct(wn, len(not_cast)), sum(first_turn[card]) / len(first_turn[card])))
    for _, card, cnt, wc, wnc, ft in sorted(rows, reverse=True):
        out.append(f"| {card} | {cnt} | {wc} | {wnc} | {ft:.1f} |")

    # --- Game length -------------------------------------------------------
    out += ["", "## Game length", "",
            "| Ended by round | Wins | Losses |", "|---|---:|---:|"]
    buckets = [(5, "5 or earlier"), (7, "6-7"), (9, "8-9"), (99, "10 or later")]
    low = 0
    for high, label in buckets:
        w = sum(1 for g in wins if low < round_of(g) <= high)
        l = sum(1 for g in losses if low < round_of(g) <= high)
        out.append(f"| {label} | {w} | {l} |")
        low = high
    for kind, gs in (("win", wins), ("loss", losses)):
        if gs:
            out.append(f"\nAverage {kind}: round {sum(round_of(g) for g in gs) / len(gs):.1f}")

    # --- Position by round -----------------------------------------------------
    out += ["", "## Position by round (when does the game tilt?)", "",
            "Averages at the START of each round, shown as us-them. "
            "**Score** combines them: each extra creature/planeswalker = "
            f"{BOARD_POINTS} point, every {POWER_PER_POINT} power ahead = 1 point, "
            f"each extra land = {LAND_POINTS} point, every {LIFE_PER_POINT} life "
            "ahead = 1 point. Positive = we're ahead. Watch the round where wins "
            "and losses split apart.", "",
            "Power starts at the printed value and goes up once a creature deals "
            "damage (so a Hydra that hits for 16 counts as 16 power from then on)."]
    for label, gs in (("Wins", wins), ("Losses", losses)):
        out += ["", f"**{label}**", "",
                "| Round | Lands | Creatures | Power | Life | Score | Games |",
                "|---|---:|---:|---:|---:|---:|---:|"]
        for r in range(2, 10):
            snaps = []
            for g in gs:
                first = [sn for sn in g["snapshots"] if (sn[0] + 1) // 2 == r]
                if first:
                    snaps.append(first[0])
            if not snaps:
                continue
            k = len(snaps)
            col = lambda i: sum(sn[i] for sn in snaps) / k
            out.append(f"| {r} | {col(3):.1f}-{col(4):.1f} | {col(5):.1f}-{col(6):.1f} | "
                       f"{col(7):.0f}-{col(8):.0f} | {col(1):.0f}-{col(2):.0f} | {sum(position(sn) for sn in snaps) / k:+.1f} | {k} |")

    # --- How we win ------------------------------------------------------------
    out += ["", "## How we win", ""]
    if wins:
        dealt, finishers = Counter(), Counter()
        for g in wins:
            dealt.update(g["dealt"])
            finishers[g["last_hit"]["opp"] or "unknown"] += 1
        out += ["Our cards that dealt the most damage in wins:", "",
                "| Card | Damage in wins | Avg per win |", "|---|---:|---:|"]
        for card, d in dealt.most_common(8):
            out.append(f"| {card} | {d} | {d / len(wins):.1f} |")
        out += ["", "Finishing blow (last thing that hurt them):", ""]
        out += [f"- {c}: {k} wins" for c, k in finishers.most_common(6)]
    else:
        out.append("- no wins")

    # --- How we lose -------------------------------------------------------------
    out += ["", "## How we lose", ""]
    if losses:
        finishers = Counter(g["last_hit"]["us"] or "unknown" for g in losses)
        reasons = Counter(g["loss_reason"] or "unknown" for g in losses)
        out += ["Finishing blow (last thing that hurt us):", ""]
        out += [f"- {c}: {k} losses" for c, k in finishers.most_common(6)]
        out += ["", "Forge's loss reasons:", ""]
        out += [f"- {r}: {c}" for r, c in reasons.most_common()]
    else:
        out.append("- no losses")

    # --- Turning points ------------------------------------------------------------
    # Comeback = a win where our position score fell to -SWING or worse. Its
    #   turning point is our LOWEST score; the cards we cast in that round and
    #   the next are what pulled us out.
    # Collapse = a loss where our score reached +SWING or better. Its turning
    #   point is our HIGHEST score; what the opponent did in that round and the
    #   next is what beat us.
    out += ["", "## Turning points", "",
            f"Uses the position score above. A **comeback** is a win where we were "
            f"behind by {SWING}+ points; the cards listed are what we cast in the round "
            f"we were furthest behind and the round after. A **collapse** is a loss where "
            f"we were ahead by {SWING}+ points; the cards are from the round we were "
            f"furthest ahead and the round after. Rounds 1-2 are skipped (too early to mean much).", ""]
    comeback_cards, collapse_ours, collapse_theirs = Counter(), Counter(), Counter()
    comeback_behind, collapse_ahead, loss_behind = Counter(), Counter(), Counter()
    comeback_rounds, collapse_rounds = [], []
    n_comeback = n_collapse = n_never = 0

    def casts_in_rounds(g, rounds, who_wanted):
        found = set()
        for t, casts in g["casts_by_turn"].items():
            if (t + 1) // 2 in rounds:
                found |= {c for who, c in casts if who == who_wanted}
        return found

    for g in games:
        series = [(sn, sc) for sn, sc in lead_series(g) if (sn[0] + 1) // 2 >= 3]
        if not series:
            continue
        low_snap, low = min(series, key=lambda x: x[1])
        high_snap, high = max(series, key=lambda x: x[1])
        if g["result"] == "win" and low <= -SWING:
            r = (low_snap[0] + 1) // 2
            n_comeback += 1
            comeback_rounds.append(r)
            comeback_behind.update(behind_on(low_snap))
            comeback_cards.update(casts_in_rounds(g, {r, r + 1}, "us"))
        elif g["result"] == "loss" and high >= SWING:
            r = (high_snap[0] + 1) // 2
            n_collapse += 1
            collapse_rounds.append(r)
            collapse_ahead.update(ahead_on(high_snap))
            collapse_ours.update(casts_in_rounds(g, {r, r + 1}, "us"))
            collapse_theirs.update(casts_in_rounds(g, {r, r + 1}, "opp"))
            # their creatures that hit us in those rounds
            collapse_theirs.update({src for rr, src, _ in g["opp_hits"] if rr in (r, r + 1)})
        elif g["result"] == "loss":
            n_never += 1                                    # never really ahead
            loss_behind.update(behind_on(low_snap))

    def avg(xs):
        return f"{sum(xs) / len(xs):.1f}" if xs else "-"

    def areas(counter, total):
        return ", ".join(f"{a} {k}/{total}" for a, k in counter.most_common()) or "-"

    out += [f"- Comebacks: **{n_comeback}** of {len(wins)} wins. Furthest behind in round "
            f"{avg(comeback_rounds)} on average. Behind on: {areas(comeback_behind, n_comeback)}",
            f"- Collapses: **{n_collapse}** of {len(losses)} losses. Furthest ahead in round "
            f"{avg(collapse_rounds)} on average. Ahead on: {areas(collapse_ahead, n_collapse)}",
            f"- Losses where we were never ahead: **{n_never}**. At our worst point we were "
            f"behind on: {areas(loss_behind, n_never)}", ""]
    if comeback_cards:
        out += ["Our cards cast when we turned the game around:", "",
                "| Card | Comebacks it was part of |", "|---|---:|"]
        out += [f"| {c} | {k} |" for c, k in comeback_cards.most_common(8)]
    if collapse_theirs:
        out += ["", "Opponent cards (cast or attacking) when the game slipped away:", "",
                "| Card | Collapses it was part of |", "|---|---:|"]
        out += [f"| {c} | {k} |" for c, k in collapse_theirs.most_common(8)]
    if collapse_ours:
        out += ["", "What we were doing on those turns:", "",
                "| Our card | Collapses |", "|---|---:|"]
        out += [f"| {c} | {k} |" for c, k in collapse_ours.most_common(6)]

    # --- Removal report ---------------------------------------------------------------
    # For each of our targeted spells: what it targeted and whether that creature died that turn
    out += ["", "## Removal report", "",
            "Our targeted spells, what they aimed at, and how often the opponent's "
            "target died that same turn. Fight spells list both creatures; "
            "only the opponent's one counts as the target here.", "",
            "| Our card | Casts | Target died | Most common targets |", "|---|---:|---:|---|"]
    spell_casts, spell_kills, spell_targets = Counter(), Counter(), defaultdict(Counter)
    for g in games:
        died = {(t, cid) for t, _, cid in g["deaths"]}
        for t, card, tgts in g["targets"]:
            theirs = [(nm, cid) for nm, cid in tgts if owner_of(g, nm, cid) != "us"]
            if not theirs:
                continue
            spell_casts[card] += 1
            if any((t, cid) in died for _, cid in theirs):
                spell_kills[card] += 1
            for nm, _ in theirs:
                spell_targets[card][nm] += 1
    for card, c in spell_casts.most_common():
        top = ", ".join(f"{nm} ({k})" for nm, k in spell_targets[card].most_common(3))
        out.append(f"| {card} | {c} | {pct(spell_kills[card], c)} | {top} |")

    # What actually killed their creatures (all sources, including combat and wipes)
    kills, killed = Counter(), Counter()
    for g in games:
        for t, nm, cid in g["deaths"]:
            if owner_of(g, nm, cid) == "opp":            # only count creatures we know were theirs
                kills[killer_of(g, t, nm, cid)] += 1
                killed[nm] += 1
    if kills:
        out += ["", "What removed their creatures (all games):", "",
                "| Our card / source | Creatures removed |", "|---|---:|"]
        out += [f"| {c} | {k} |" for c, k in kills.most_common(10)]
        out += ["", "Their creatures we removed most:", "",
                "| Their creature | Times removed |", "|---|---:|"]
        out += [f"| {c} | {k} |" for c, k in killed.most_common(8)]

    # --- Abilities report -----------------------------------------------------------------
    out += ["", "## Abilities report (activated abilities and triggers)", "",
            "Our cards' activated abilities and triggered abilities, how often they "
            "were used, and what they targeted. 'Their lands' counts targets we know "
            "were the opponent's lands (e.g. fetch lands in their graveyard).", "",
            "| Our card | Type | Times used | Games | Their lands | Their other cards | Our cards | Most common targets |",
            "|---|---|---:|---:|---:|---:|---:|---|"]
    uses, games_with = Counter(), defaultdict(set)
    their_lands, their_other, our_cards, ab_targets = Counter(), Counter(), Counter(), defaultdict(Counter)
    for gi, g in enumerate(games):
        for _, kind, card, tgts in g["abilities"]:
            key = (card, kind)
            uses[key] += 1
            games_with[key].add(gi)
            for nm, cid in tgts:
                ab_targets[key][nm] += 1
                who = owner_of(g, nm, cid)
                if who == "us":
                    our_cards[key] += 1
                elif who == "opp" and kinds_lookup.get(nm) == "land":
                    their_lands[key] += 1
                elif who == "opp":
                    their_other[key] += 1
    for key, c in uses.most_common(12):
        card, kind = key
        top = ", ".join(f"{nm} ({k})" for nm, k in ab_targets[key].most_common(3)) or "-"
        out.append(f"| {card} | {kind} | {c} | {len(games_with[key])} | {their_lands[key]} | "
                   f"{their_other[key]} | {our_cards[key]} | {top} |")
    if not uses:
        out.append("| (none) | | | | | | | |")

    # --- Wipe report ------------------------------------------------------------------------
    # For each of our board wipes: creatures that died that same turn, theirs vs ours.
    # (Counts every death that turn, so a combat on the same turn adds a little.)
    wipe_stats = defaultdict(lambda: {"casts": 0, "theirs": 0, "ours": 0, "games": set(), "won": 0.0})
    for gi, g in enumerate(games):
        for card, t in [(c, tt) for tt, casts in g["casts_by_turn"].items() for who, c in casts
                        if who == "us" and c in (wipes or set())]:
            st = wipe_stats[card]
            st["casts"] += 1
            for dt, nm, cid in g["deaths"]:
                if dt == t:
                    side = owner_of(g, nm, cid)
                    if side == "opp":
                        st["theirs"] += 1
                    elif side == "us":
                        st["ours"] += 1
            if gi not in st["games"]:
                st["games"].add(gi)
                st["won"] += score(g)
    if wipes is not None:
        out += ["", "## Wipe report (our board wipes)", "",
                "Each time we cast a board wipe: how many of their creatures and how many "
                "of ours died that turn. A good wipe kills several of theirs and few of ours.", "",
                "| Wipe | Casts | Their creatures died (avg) | Ours died (avg) | Win % when cast |",
                "|---|---:|---:|---:|---:|"]
        for card, st in sorted(wipe_stats.items(), key=lambda x: -x[1]["casts"]):
            c = st["casts"]
            out.append(f"| {card} | {c} | {st['theirs'] / c:.1f} | {st['ours'] / c:.1f} | "
                       f"{pct(st['won'], len(st['games']))} |")
        if not wipe_stats:
            out.append("| (no wipes cast) | | | | |")

    # --- Counterspell report -----------------------------------------------------------------
    # A counterspell targets a spell on the stack; Forge writes that target as the spell's
    # text, so we look for the opponent's card names inside it.
    if counters:
        aimed, casts = defaultdict(Counter), Counter()
        for g in games:
            for _, card, text in g["raw_targets"]:
                if card not in counters:
                    continue
                casts[card] += 1
                hit = [n for n in g["opp_names"] if n and n in text]
                aimed[card][max(hit, key=len) if hit else "(couldn't tell)"] += 1
        if casts:
            out += ["", "## Counterspell report", "",
                    "What our counterspells were aimed at (spells the opponent cast).", "",
                    "| Our counterspell | Casts | Most common targets |", "|---|---:|---|"]
            for card, c in casts.most_common():
                top = ", ".join(f"{n} ({k})" for n, k in aimed[card].most_common(4))
                out.append(f"| {card} | {c} | {top} |")

    # --- Threat growth -------------------------------------------------------------------
    out += ["", "## Threat growth (how hard their creatures hit us)", "",
            "Average combat damage per hit, by round. Shows how fast their "
            "creatures grow. Empty = didn't hit us that round.", ""]
    hits = defaultdict(lambda: defaultdict(list))   # source -> round -> [damage]
    totals = Counter()
    for g in games:
        for r, src, dmg in g["opp_hits"]:
            hits[src][r].append(dmg)
            totals[src] += dmg
    top = [src for src, _ in totals.most_common(6)]
    if top:
        rounds = list(range(2, 10))
        out += ["| Creature | " + " | ".join(f"R{r}" for r in rounds) + " | Biggest hit |",
                "|---|" + "---:|" * (len(rounds) + 1)]
        for src in top:
            cells = [f"{sum(hits[src][r]) / len(hits[src][r]):.0f}" if hits[src][r] else ""
                     for r in rounds]
            biggest = max(d for r in hits[src] for d in hits[src][r])
            out.append(f"| {src} | " + " | ".join(cells) + f" | {biggest} |")

    # --- Running out of gas ------------------------------------------------
    out += ["", "## Spells cast per turn (running out of gas?)", "",
            "Average number of spells we cast on each of our turns. If late-turn "
            "numbers drop in losses, the deck is running out of things to do.", "",
            "| Our turn | In wins | In losses |", "|---|---:|---:|"]
    for t in range(1, 11):
        cells = []
        for gs in (wins, losses):
            gs = [g for g in gs if g["our_turn_index"] >= t]
            casts = [sum(1 for _, ct in g["cast"] if ct == t) for g in gs]
            cells.append(f"{sum(casts) / len(casts):.1f} ({len(gs)})" if gs else "-")
        out.append(f"| {t} | {cells[0]} | {cells[1]} |")
    out.append("\n(Number in brackets = games that lasted to that turn.)")

    # --- Lands -------------------------------------------------------------
    screw = [g for g in games if sum(g["lands"][t] for t in range(1, 5)) < 3]
    flood = [g for g in games if sum(g["lands"][t] for t in range(1, 7)) >= 6
             and g["our_turn_index"] >= 6]
    out += ["", "## Mana", "",
            f"- Fewer than 3 lands played by our turn 4: {len(screw)} games, "
            f"win rate {pct(sum(score(g) for g in screw), len(screw))}",
            f"- A land on every turn through our turn 6: {len(flood)} games, "
            f"win rate {pct(sum(score(g) for g in flood), len(flood))}"]

    # --- Play/draw and mulligans --------------------------------------------
    out += ["", "## Play/draw and mulligans", ""]
    for label, gs in (("On the play", [g for g in games if g["on_play"]]),
                      ("On the draw", [g for g in games if g["on_play"] is False]),
                      ("Kept 7 cards", [g for g in games if g["kept"] >= 7]),
                      ("Mulliganed", [g for g in games if g["kept"] < 7])):
        out.append(f"- {label}: {len(gs)} games, win rate {pct(sum(score(g) for g in gs), len(gs))}")

    # --- Biggest threats -----------------------------------------------------
    dmg = Counter()
    in_losses = Counter()
    for g in games:
        dmg.update(g["damage_taken"])
        if g["result"] == "loss":
            in_losses.update(g["damage_taken"].keys())
    out += ["", "## Opponent cards that dealt us the most damage", "",
            "| Card | Total damage | Losses where it hit us |", "|---|---:|---:|"]
    for card, d in dmg.most_common(10):
        out.append(f"| {card} | {d} | {in_losses[card]} |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 5. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) not in (4, 5):
        print(__doc__)
        sys.exit(1)
    log_path, ours, out_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    if not log_path.is_file():
        sys.exit(f"Log not found: {log_path}")
    kinds = load_card_kinds(Path(sys.argv[4])) if len(sys.argv) == 5 else {}
    wipes = load_wipe_names(Path(sys.argv[4])) if len(sys.argv) == 5 else None
    counters = load_counter_names(Path(sys.argv[4])) if len(sys.argv) == 5 else None
    if len(sys.argv) == 5 and not kinds:
        print(f"Warning: no card scripts found in {sys.argv[4]}; board counts will be partial.")
    games = parse(log_path.read_text(encoding="utf-8", errors="ignore"), ours, kinds)
    if not games:
        sys.exit("No finished games found in the log.")
    opponent = log_path.stem
    text = report(games, ours, opponent, kinds, wipes, counters)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
