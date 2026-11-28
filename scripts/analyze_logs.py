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
 10. Combat report (added 2026-11-25): how often each side's attackers get
     blocked, round by round; which creatures block most; and what happens
     when an attack is blocked (attacker dies, blocker dies, both, neither)
 12. Turn-1 choices (added 2026-11-28, needs patch 03's opening-hand lines): for
     each card we cast on our first turn, how often it was cast then when it was
     in the opening hand with a land, what was cast instead, and head-to-head
     results when two such cards were in the same opening hand (shows which
     1-drop Forge's AI prefers)
 11. Sacrifice report (added 2026-11-27, needs Forge patch 23): what each side
     sacrificed and what made it sacrifice; life lost without damage
     ("drains", paid life) by the card that caused it; how often a sacrifice of
     ours was followed by a drain; win % by sacrifices and drains per game.
     With patch 23 the "finishing blow" also names the card behind a life loss

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
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Regular expressions for the log lines we care about
TURN = re.compile(r"^Turn: Turn (\d+) \((.+)\)$")
MULLIGAN_KEPT = re.compile(r"^Mulligan: (.+) has kept a hand of (\d+) cards")
# "Mulligan: Ai(1)-deck opening hand: Card A ; Card B ; ..." (from our Forge patch 03)
OPENING_HAND = re.compile(r"^Mulligan: (.+?) opening hand: (.*)$")
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
# Patch 22 (22_log_creatures): one line per creature at the start of every turn, with its size and counters:
# "Turn: Creature turn=7 power=5 toughness=5 p1p1=0 m1m1=2 id=123 card=Some Creature player=Ai(1)-deck"
CREATURE = re.compile(r"^Turn: Creature turn=(\d+) power=(-?\d+) toughness=(-?\d+) p1p1=(\d+) m1m1=(\d+) "
                      r"id=(\d+) card=(.+?) player=(.+)$")
# Patch 23 (23_log_sacrifices): one line per sacrificed permanent, and one line per non-damage life loss:
# "Zone Change: Sacrifice turn=9 id=57 creature=yes token=no | card=X | cause=Y | cause_player=P1 | player=P1"
# "Life: LifeLoss turn=9 amount=1 | source=Z | source_player=P1 | player=P2"
SACRIFICE = re.compile(r"^Zone Change: Sacrifice turn=(\d+) id=(\d+) creature=(yes|no) token=(yes|no) "
                       r"\| card=(.+?) \| cause=(.+?) \| cause_player=(.+?) \| player=(.+)$")
LIFELOSS = re.compile(r"^Life: LifeLoss turn=(\d+) amount=(\d+) \| source=(.+?) \| source_player=(.+?) "
                      r"\| player=(.+)$")
# Temporary debug patch 99 (99_debug_cast_choice): the AI's creature-spell order and its answer for each one
# "Turn: AiDebug order turn=1 phase=MAIN1 | A, B | player=P" / "Turn: AiDebug opinion turn=1 phase=MAIN1 | card=A | opinion=WillPlay | player=P"
AIDEBUG_ORDER = re.compile(r"^Turn: AiDebug order turn=(\d+) phase=(\S+) \| (.+) \| player=(.+)$")
AIDEBUG_OPINION = re.compile(r"^Turn: AiDebug opinion turn=(\d+) phase=(\S+) \| card=(.+?) \| opinion=(\S+) \| player=(.+)$")
# patch 99 v2: "AiDebug chosen ... | A, B (land) | player=P", "AiDebug play ... | card=A | via=X < Y | player=P",
# "AiDebug castfailed ... | card=A | failedBefore=false | player=P"
AIDEBUG_OTHER = re.compile(r"^Turn: AiDebug (chosen|play|castfailed|canplay) turn=(\d+) phase=(\S+) \| (.+) \| player=(.+)$")
OUTCOME_TURN = re.compile(r"^Game Outcome: Turn (\d+)")
OUTCOME_LOST = re.compile(r"^Game Outcome: (.+?) has lost because (.+)$")
RESULT_WIN = re.compile(r"Game Result: Game (\d+) ended in \d+ ms\. (.+?) has won!")
RESULT_DRAW = re.compile(r"Game Result: Game (\d+) ended in a Draw")
CARD_WITH_ID = re.compile(r"([^,\[\]]+?) \((\d+)\)")   # "Mossborn Hydra (88)" -> name, id
CARD_ID = re.compile(r"\s*\(\d+\)$")                     # removes " (88)" from "Mossborn Hydra (88)"
# One card in a combat list: name (may contain commas), its id, then ", ", " and " or the end
COMBAT_CARD = re.compile(r"(?:and\s+)?([^()]+?) \((\d+)\)(?:,\s*|\s+and\s+|\s*$)")

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
        "opening": [],            # our kept opening hand (card names), if the log has it
        "our_land_names": set(),  # names of lands we played (to tell lands apart in the opening hand)
        "attacks": [],            # (forge turn, attacking side, [(name, id)]) one entry per attack line
        "blocks": [],             # (forge turn, blocking side, [(blocker name, id)], [(attacker name, id)])
        "creature_sizes": [],     # patch 22: (forge turn, side, id, name, power, toughness, p1p1, m1m1)
        # patch 23: each entry is a dict; "n" is the line number inside the game, so we know the order
        "sacrifices": [],         # {"n", "turn", "id", "card", "creature", "token", "cause", "cause_side", "side"}
        "life_loss": [],          # {"n", "turn", "amount", "source", "source_side", "side"} (not damage)
        "line_no": 0,
        "turn_side": {},          # forge turn -> "us"/"opp": whose turn it was
        "first_turn_lines": [],   # patch 99 (temporary): every log line of our first turn, in order
        "ai_debug": [],           # patch 99 (temporary): ("order", phase, text) / ("opinion", phase, card, answer), ours only
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

    def split_combat(text: str) -> list[tuple[str, str]]:
        """Cards in an attack/block line, keeping names that contain commas."""
        return [(n.strip(), i) for n, i in COMBAT_CARD.findall(text.strip().rstrip('.'))]

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
        g["line_no"] += 1

        m = TURN.match(line)
        if m and g["our_turn_index"] == 1 and g.get("capturing"):
            g["capturing"] = False                     # our first turn is over
        if g.get("capturing") and line.startswith(("Turn: AiDebug", "Add To Stack", "Land:", "Zone Change")):
            g["first_turn_lines"].append(line[:300])
        if m:
            turn_no, player = int(m.group(1)), m.group(2)
            g["turns"] = max(g["turns"], turn_no)
            g["forge_turn"] = turn_no
            g["snapshots"].append(snapshot(turn_no))
            g["turn_side"][turn_no] = side(player)
            if g["on_play"] is None:
                g["on_play"] = is_ours(player)         # who took turn 1
            if is_ours(player):
                g["our_turn_index"] += 1                # our 1st, 2nd, 3rd turn...
                g["capturing"] = g["our_turn_index"] == 1
            continue

        m = AIDEBUG_ORDER.match(line)
        if m:
            if is_ours(m.group(4)):
                g["ai_debug"].append(("order", m.group(2), m.group(3).strip()))
            continue

        m = AIDEBUG_OPINION.match(line)
        if m:
            if is_ours(m.group(5)):
                g["ai_debug"].append(("opinion", m.group(2), m.group(3).strip(), m.group(4)))
            continue

        m = AIDEBUG_OTHER.match(line)
        if m:
            if is_ours(m.group(5)):
                g["ai_debug"].append((m.group(1), m.group(3), m.group(4).strip()))
            continue

        m = SACRIFICE.match(line)
        if m:
            cause_player = m.group(7).strip()
            g["sacrifices"].append({
                "n": g["line_no"], "turn": int(m.group(1)), "id": m.group(2),
                "creature": m.group(3) == "yes", "token": m.group(4) == "yes",
                "card": m.group(5).strip(), "cause": m.group(6).strip(),
                "cause_side": None if cause_player == "unknown" else side(cause_player),
                "side": side(m.group(8))})
            continue

        m = LIFELOSS.match(line)
        if m:
            source, source_player, who = m.group(3).strip(), m.group(4).strip(), side(m.group(5))
            g["life_loss"].append({
                "n": g["line_no"], "turn": int(m.group(1)), "amount": int(m.group(2)), "source": source,
                "source_side": None if source_player == "unknown" else side(source_player), "side": who})
            # the finishing-blow report can now name the card instead of "life loss effect"
            if source != "unknown":
                g["last_hit"][who], g["last_event"][who] = f"{source} (life loss)", "life"
            continue

        m = CREATURE.match(line)
        if m:
            g["creature_sizes"].append((int(m.group(1)), side(m.group(8)), m.group(6), m.group(7).strip(),
                                        int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))))
            continue

        m = OPENING_HAND.match(line)
        if m:
            if is_ours(m.group(1)):
                g["opening"] = [c.strip() for c in m.group(2).split(" ; ") if c.strip()]
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
                g["our_land_names"].add(m.group(2).strip())
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
            attackers = split_cards(m.group(2))
            for nm, cid in attackers:
                g["owner"][cid] = side(m.group(1))
                seen_in_play(side(m.group(1)), nm, cid)
            # remember the attack for the combat report
            g["attacks"].append((g["forge_turn"], side(m.group(1)), split_combat(m.group(2))))
            continue

        m = BLOCK.match(line)
        if m:
            blockers, blocked = split_cards(m.group(2)), split_cards(m.group(3))
            for nm, cid in blockers:
                g["owner"][cid] = side(m.group(1))
                seen_in_play(side(m.group(1)), nm, cid)
            for _, cid in blocked:    # the blocked attacker belongs to the other side
                g["owner"].setdefault(cid, "opp" if side(m.group(1)) == "us" else "us")
            # remember the block for the combat report
            g["blocks"].append((g["forge_turn"], side(m.group(1)), split_combat(m.group(2)), split_combat(m.group(3))))
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
def opening_hand_report(games: list[dict]) -> list[str]:
    """Win rate by what was in our kept opening hand: number of lands, and each card."""
    with_hand = [g for g in games if g["opening"]]
    if not with_hand:
        return []
    land_names = set().union(*(g["our_land_names"] for g in games))
    out = ["", "## Opening hands", "",
           f"Our kept opening hand in {len(with_hand)} games (after any mulligan). "
           "Win rate by how many lands it had, and by the cards in it.", "",
           "| Lands in opening hand | Games | Win % |", "|---|---:|---:|"]
    # 6a. by number of lands
    buckets = defaultdict(list)
    for g in with_hand:
        n = sum(1 for c in g["opening"] if c in land_names)
        buckets["0-1" if n <= 1 else "5+" if n >= 5 else str(n)].append(g)
    for key in ("0-1", "2", "3", "4", "5+"):
        gs = buckets.get(key, [])
        if gs:
            out.append(f"| {key} | {len(gs)} | {pct(sum(score(g) for g in gs), len(gs))} |")
    # 6b. by card (non-lands, most common first)
    out += ["", "| Card in opening hand | Games | Win % with it | Win % without |", "|---|---:|---:|---:|"]
    counts = Counter(c for g in with_hand for c in set(g["opening"]) if c not in land_names)
    for card, n in counts.most_common(25):
        have = [g for g in with_hand if card in g["opening"]]
        lack = [g for g in with_hand if card not in g["opening"]]
        out.append(f"| {card} | {n} | {pct(sum(score(g) for g in have), len(have))} | "
                   f"{pct(sum(score(g) for g in lack), len(lack))} |")
    # 6c. by PAIR of non-land cards kept together (what a good starting hand looks like).
    #     Only pairs seen in enough games to mean something; best win rate first.
    min_games = 12
    pair_games = defaultdict(list)
    for g in with_hand:
        spells = sorted(set(c for c in g["opening"] if c not in land_names))
        for i in range(len(spells)):
            for j in range(i + 1, len(spells)):
                pair_games[(spells[i], spells[j])].append(g)
    pairs = [(p, gs) for p, gs in pair_games.items() if len(gs) >= min_games]
    if pairs:
        pairs.sort(key=lambda x: (-sum(score(g) for g in x[1]) / len(x[1]), -len(x[1])))
        out += ["", f"| Card pair in opening hand (seen in {min_games}+ games) | Games | Wins | Win % |",
                "|---|---:|---:|---:|"]
        for (a, b), gs in pairs[:15]:
            wins = sum(score(g) for g in gs)
            out.append(f"| {a} + {b} | {len(gs)} | {wins:g} | {pct(wins, len(gs))} |")
    return out


def cast_by_turn_report(games: list[dict], last_turn: int = 6) -> list[str]:
    """How many games each of our cards was cast on our turns 1..last_turn, and how
    often we cast nothing at all. A spell cast on the opponent's turn counts for
    our turn before it (e.g. a counterspell on their turn 2 counts as our turn 2)."""
    reached = {t: sum(1 for g in games if g["our_turn_index"] >= t) for t in range(1, last_turn + 1)}
    by_card = defaultdict(Counter)
    did_something = Counter()
    for g in games:
        turns_with_cast = set()
        for card, t in g["cast"]:
            if 1 <= t <= last_turn:
                by_card[card][t] += 1
                turns_with_cast.add(t)
        for t in turns_with_cast:
            did_something[t] += 1
    head = " | ".join(f"T{t}" for t in range(1, last_turn + 1))
    out = ["", "## Cards cast by turn (early game)", "",
           "Number of games where we cast the card on our turn N (or on the opponent's turn right after). "
           "The last row shows how often we cast nothing at all on that turn.", "",
           f"| Card | {head} |", "|---|" + "---:|" * last_turn]
    early = sorted(by_card.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    for card, cnt in early:
        out.append(f"| {card} | " + " | ".join(str(cnt[t]) if cnt[t] else "" for t in range(1, last_turn + 1)) + " |")
    out.append("| **Nothing cast** | " + " | ".join(
        f"{reached[t] - did_something[t]} of {reached[t]}" for t in range(1, last_turn + 1)) + " |")
    return out


def turn_one_report(games: list[dict], min_casts: int = 3, min_pair_games: int = 8) -> list[str]:
    """Section: what we cast on our first turn, compared with what was in the opening hand.

    "Turn-1 cards" = cards we cast on our first turn in at least min_casts games (in practice: our
    1-mana spells). Only games whose kept opening hand is in the log (Forge patch 03) and has at
    least one land count. On the draw our first turn also has the card we drew, so a few turn-1
    casts come from outside the opening hand.
    Part 1 - for each turn-1 card in the opening hand: how often it was cast on turn 1, and what
             we cast instead when it wasn't (or "nothing").
    Part 2 - when two different turn-1 cards were both in the opening hand: which one came first.
    """
    out = ["", "## Turn-1 choices (which 1-drop does the AI play first?)", ""]
    land_names = set().union(*(g["our_land_names"] for g in games)) if games else set()
    first_turn = []                                   # (opening hand, [cards cast on our turn 1, in order])
    for g in games:
        if not g["opening"]:
            continue
        if not any(c in land_names for c in g["opening"]):
            continue
        first_turn.append((g["opening"], [card for card, t in g["cast"] if t == 1]))
    if not first_turn:
        return out + ["No opening hands with a land in these logs (they need Forge patch 03).", ""]
    t1_count = Counter(c for _, casts in first_turn for c in set(casts))
    cards = sorted(c for c, k in t1_count.items() if k >= min_casts)
    if not cards:
        return out + ["No card was cast on our first turn often enough to report.", ""]

    # --- 1. Each turn-1 card: cast on turn 1 when it was in the opening hand? -----------------------
    out += [f"Games with a kept opening hand that had a land: {len(first_turn)}.", "",
            "| Card | In the opening hand | Cast on turn 1 | Not cast: what we cast instead |",
            "|---|---:|---:|---|"]
    for card in sorted(cards, key=lambda c: -t1_count[c]):
        held = [(hand, casts) for hand, casts in first_turn if card in hand]
        if not held:
            continue
        cast = sum(1 for _, casts in held if card in casts)
        instead = Counter((casts[0] if casts else "nothing") for _, casts in held if card not in casts)
        out.append(f"| {card} | {len(held)} | {cast} ({pct(cast, len(held))}) | "
                   + ", ".join(f"{c} {k}" for c, k in instead.most_common(4)) + " |")

    # --- 2. Head to head: both cards in the same opening hand ---------------------------------------
    rows = []
    for i, a in enumerate(cards):
        for b in cards[i + 1:]:
            both = [casts for hand, casts in first_turn if a in hand and b in hand]
            if len(both) < min_pair_games:
                continue
            first_a = sum(1 for casts in both if a in casts and (b not in casts or casts.index(a) < casts.index(b)))
            first_b = sum(1 for casts in both if b in casts and (a not in casts or casts.index(b) < casts.index(a)))
            rows.append((a, b, len(both), first_a, first_b, len(both) - first_a - first_b))
    if rows:
        out += ["", f"When two turn-1 cards were in the same opening hand ({min_pair_games}+ games): which was cast first.", "",
                "| Card A | Card B | Games | A first | B first | Neither |", "|---|---|---:|---:|---:|---:|"]
        for a, b, n, fa, fb, nn in sorted(rows, key=lambda r: -r[2]):
            out.append(f"| {a} | {b} | {n} | {fa} | {fb} | {nn} |")
    out.append("")
    return out


def ai_debug_report(games: list[dict]) -> list[str]:
    """Section (only when temporary Forge patch 99 is on): on our first two turns, the order in which
    Forge's AI considered our creature spells, and its answer for each one it checked."""
    if not any(g["ai_debug"] for g in games):
        return []
    out = ["", "## AI debug: creature spells considered on our first two turns (temporary patch 99)", ""]
    orders = Counter((d[1], d[2]) for g in games for d in g["ai_debug"] if d[0] == "order")
    out += ["Order in which the AI looked at our creature spells (most common lists):", "",
            "| Phase | Order (first one is checked first) | Times |", "|---|---|---:|"]
    out += [f"| {ph} | {o} | {k} |" for (ph, o), k in orders.most_common(15)]
    answers = Counter((d[2], d[1], d[3]) for g in games for d in g["ai_debug"] if d[0] == "opinion")
    out += ["", "The AI's answer for each creature spell it checked:", "",
            "| Card | Phase | Answer | Times |", "|---|---|---|---:|"]
    out += [f"| {c} | {ph} | {a} | {k} |" for (c, ph, a), k in sorted(answers.items(), key=lambda kv: (kv[0][0], -kv[1]))]
    chosen = Counter((d[1], d[2]) for g in games for d in g["ai_debug"] if d[0] == "chosen")
    if chosen:
        out += ["", "What the AI decided to play (one line per decision; lands marked):", "",
                "| Phase | Chosen | Times |", "|---|---|---:|"]
        out += [f"| {ph} | {c} | {k} |" for (ph, c), k in chosen.most_common(20)]
    plays = Counter((d[1], d[2]) for g in games for d in g["ai_debug"] if d[0] == "play")
    if plays:
        out += ["", "Creature spells the AI cast, and the code path that asked for it (most recent call first):", "",
                "| Phase | Card and path | Times |", "|---|---|---:|"]
        out += [f"| {ph} | {c} | {k} |" for (ph, c), k in plays.most_common(25)]
    # examples: games whose opening hand had two or more different cards that we cast on turn 1 in some game
    t1_cards = {c for c, k in Counter(c for x in games for c, t in x["cast"] if t == 1).items() if k >= 3}
    samples = [g for g in games if g["first_turn_lines"] and len(t1_cards & set(g["opening"])) >= 2]
    shown = 0
    for g in samples:
        if not [c for c, t in g["cast"] if t == 1]:
            continue
        out += ["", f"Example: our first turn, line by line (opening hand: {' ; '.join(g['opening'])}):", "", "```"]
        out += [ln.replace("Turn: AiDebug ", "") for ln in g["first_turn_lines"][:60]]
        out.append("```")
        shown += 1
        if shown >= 3:
            break
    canplay = Counter((d[1], d[2]) for g in games for d in g["ai_debug"] if d[0] == "canplay")
    if canplay:
        out += ["", "Every \"can I play this creature?\" answer and who asked (most recent call first):", "",
                "| Phase | Card, answer and caller | Times |", "|---|---|---:|"]
        out += [f"| {ph} | {c} | {k} |" for (ph, c), k in canplay.most_common(40)]
    failed = Counter((d[1], d[2]) for g in games for d in g["ai_debug"] if d[0] == "castfailed")
    if failed:
        out += ["", "Casts that didn't happen:", "", "| Phase | Card | Times |", "|---|---|---:|"]
        out += [f"| {ph} | {c} | {k} |" for (ph, c), k in failed.most_common(15)]
    out.append("")
    return out


def combat_stats(games: list[dict], attacker_side: str):
    """Attacks by one side ("us" or "opp") and how the other side blocked them.

    Returns (per_round, blockers, attackers, outcomes):
      per_round[key][round] = [attacking creatures, of which blocked], key = "all", "win" or "loss"
      blockers[name]  = [blocks made, the blocker died, the attacker it blocked died]
      attackers[name] = [attacks, times blocked, died when blocked]
      outcomes        = Counter of what happened in each blocked attack
    A creature "died in the block" only if it died that turn AND took damage from its
    combat partner, so attackers sacrificed at end of turn (mobilize tokens) don't count.
    """
    blocker_side = "opp" if attacker_side == "us" else "us"
    per_round = {k: defaultdict(lambda: [0, 0]) for k in ("all", "win", "loss")}
    blockers = defaultdict(lambda: [0, 0, 0])
    attackers = defaultdict(lambda: [0, 0, 0])
    outcomes = Counter()
    for g in games:
        result = g["result"] if g["result"] in ("win", "loss") else None
        died = {(t, cid) for t, _, cid in g["deaths"]}
        damaged = {(t, src, tgt) for t, _, src, tgt in g["card_damage"]}   # (turn, source id, target id)
        blocked_by = defaultdict(list)            # (turn, attacker id) -> [(blocker name, id)]
        for t, bside, blks, atts in g["blocks"]:
            if bside == blocker_side:
                for _, aid in atts:
                    blocked_by[(t, aid)].extend(blks)
        counted = set()                           # an attacker counts once per turn
        for t, aside, atts in g["attacks"]:
            if aside != attacker_side:
                continue
            rnd = (t + 1) // 2
            for name, aid in atts:
                if (t, aid) in counted:
                    continue
                counted.add((t, aid))
                blocked = (t, aid) in blocked_by
                for key in ("all", result):
                    if key:
                        per_round[key][rnd][0] += 1
                        per_round[key][rnd][1] += blocked
                attackers[name][0] += 1
                if not blocked:
                    continue
                blks = blocked_by[(t, aid)]
                attacker_died = (t, aid) in died and any((t, bid, aid) in damaged for _, bid in blks)
                dead_blockers = {bid for _, bid in blks if (t, bid) in died and (t, aid, bid) in damaged}
                attackers[name][1] += 1
                attackers[name][2] += attacker_died
                for bname, bid in blks:
                    blockers[bname][0] += 1
                    blockers[bname][1] += bid in dead_blockers
                    blockers[bname][2] += attacker_died
                if attacker_died and dead_blockers:
                    outcomes["both died (trade)"] += 1
                elif attacker_died:
                    outcomes["only the attacker died"] += 1
                elif dead_blockers:
                    outcomes["only the blocker died"] += 1
                else:
                    outcomes["neither died"] += 1
    return per_round, blockers, attackers, outcomes


def arrival_report(games: list[dict], rounds: tuple = (2, 3, 4, 5, 6)) -> list[str]:
    """Section (needs patch 22's Creature lines): how fast each of our creatures reaches the battlefield, cast or
    returned from the graveyard, and how many times it entered in a game. Forge gives a card a new id each time it
    changes zones, so each different id seen on the battlefield is one more time it entered (a Bloodghast that came
    back twice = 3 ids). Read at the start of each turn, so a creature that entered and left in the same turn is missed."""
    out = ["", "## How fast our creatures reach the battlefield", ""]
    if not any(g["creature_sizes"] for g in games):
        return out + ["No creature lines in these logs (they need Forge patch 22_log_creatures).", ""]
    n = len(games)
    first = defaultdict(list)                 # card -> first round seen, one per game where it was seen
    entries = defaultdict(list)               # card -> number of different ids seen, one per game where it was seen
    for g in games:
        seen_round, ids = {}, defaultdict(set)
        for turn, who, cid, name, *_ in g["creature_sizes"]:
            if who != "us" or name.endswith("Token"):
                continue
            rnd = (turn + 1) // 2
            seen_round[name] = min(seen_round.get(name, rnd), rnd)
            ids[name].add(cid)
        for name, rnd in seen_round.items():
            first[name].append(rnd)
            entries[name].append(len(ids[name]))
    out += ["Counted at the start of each turn. \"By round N\" = share of ALL games where it was on the battlefield by then.", "",
            "| Card | Games on the battlefield | First round (average) | " + " | ".join(f"By round {r}" for r in rounds)
            + " | Times it entered per game (when seen) |",
            "|---|---:|---:|" + "---:|" * len(rounds) + "---:|"]
    for name, rs in sorted(first.items(), key=lambda kv: -len(kv[1]))[:14]:
        cells = [pct(sum(1 for x in rs if x <= r), n) for r in rounds]
        out.append(f"| {name} | {len(rs)} | {statistics.mean(rs):.1f} | " + " | ".join(cells)
                   + f" | {statistics.mean(entries[name]):.1f} |")
    out.append("")
    return out


def creature_size_report(games: list[dict]) -> list[str]:
    """Section: how big creatures with +1/+1 or -1/-1 counters are, round by round (needs patch 22).

    Part 1 - our creatures that had -1/-1 counters (for example a creature that enters with -1/-1
    counters and loses one each time something happens): their size at the start of each round,
    and how often and how fast they lose every counter ("full size").
    Part 2 - creatures with +1/+1 counters on either side: how big they get.
    A "round" is one turn for each player, as in the other reports; the size is read at the start
    of each round (the start of turn 1, 3, 5... of the game).
    """
    out = ["", "## Creature size report (counters)", ""]
    if not any(g["creature_sizes"] for g in games):
        return out + ["No creature size lines in these logs (they need Forge patch 22_log_creatures).", ""]
    out += ["Read at the start of each round. \"Full size\" = every -1/-1 counter is gone.", ""]

    # --- 1. Follow each creature (one game + one card id) through the game -------------------------
    # tracks[(game index, side, id)] = {"name": ..., "seen": [(round, power, toughness, p1p1, m1m1)]}
    tracks = {}
    for gi, g in enumerate(games):
        for turn, who, cid, name, pw, tg, p1, m1 in g["creature_sizes"]:
            if turn % 2 == 0:                          # keep one snapshot per round: its first turn
                continue
            rnd = (turn + 1) // 2
            tr = tracks.setdefault((gi, who, cid), {"name": name, "won": score(g), "seen": []})
            tr["seen"].append((rnd, pw, tg, p1, m1))

    rounds = list(range(2, 9))

    # --- 2. Our shrinking creatures (they had -1/-1 counters at some point) --------------------------
    shrink = defaultdict(list)                         # card name -> list of tracks
    for (gi, who, cid), tr in tracks.items():
        if who == "us" and any(s[4] > 0 for s in tr["seen"]):
            shrink[tr["name"]].append(tr)
    if shrink:
        out += ["### Our creatures with -1/-1 counters (do they grow to full size?)", "",
                "| Card | Copies seen | " + " | ".join(f"Round {r}" for r in rounds)
                + " | Reached full size | Rounds to full size | Win % if full size / not |",
                "|---|---:|" + "---|" * len(rounds) + "---:|---:|---|"]
        for name, trs in sorted(shrink.items(), key=lambda kv: -len(kv[1])):
            cells = []
            for r in rounds:
                sizes = [(s[1], s[2]) for tr in trs for s in tr["seen"] if s[0] == r]
                cells.append(f"{statistics.mean(p for p, _ in sizes):.1f}/{statistics.mean(t for _, t in sizes):.1f} ({len(sizes)})"
                             if sizes else "-")
            full, to_full, won_full, won_not = 0, [], [], []
            for tr in trs:
                first = tr["seen"][0][0]
                done = next((s[0] for s in tr["seen"] if s[4] == 0), None)
                if done is not None:
                    full += 1; to_full.append(done - first); won_full.append(tr["won"])
                else:
                    won_not.append(tr["won"])
            speed = f"{statistics.mean(to_full):.1f}" if to_full else "-"
            wf = pct(sum(won_full), len(won_full)) if won_full else "-"
            wn = pct(sum(won_not), len(won_not)) if won_not else "-"
            out.append(f"| {name} | {len(trs)} | " + " | ".join(cells)
                       + f" | {full} ({pct(full, len(trs))}) | {speed} | {wf} / {wn} |")
        out += ["", "Each cell is the average power/toughness of the copies on the battlefield at the start of "
                "that round (how many copies in brackets). Rounds to full size counts from the first round the "
                "copy was seen.", ""]
        # By which round were copies at full size? (share of all copies seen)
        out += ["| Card | Full size by round 3 | by round 4 | by round 5 | by round 6 | by round 7 |",
                "|---|---:|---:|---:|---:|---:|"]
        for name, trs in sorted(shrink.items(), key=lambda kv: -len(kv[1])):
            cells = []
            for r in range(3, 8):
                n = sum(1 for tr in trs if any(s[0] <= r and s[4] == 0 for s in tr["seen"]))
                cells.append(pct(n, len(trs)))
            out.append(f"| {name} | " + " | ".join(cells) + " |")
        out.append("")

    # --- 3. Creatures with +1/+1 counters (both sides) ------------------------------------------------
    grow = defaultdict(list)                           # (side, card name) -> list of tracks
    for (gi, who, cid), tr in tracks.items():
        if any(s[3] > 0 for s in tr["seen"]):
            grow[(who, tr["name"])].append(tr)
    if grow:
        out += ["### Creatures with +1/+1 counters (how big do they get?)", "",
                "| Side | Card | Copies seen | " + " | ".join(f"Round {r}" for r in rounds)
                + " | Biggest (median) | Biggest (max) |",
                "|---|---|---:|" + "---|" * len(rounds) + "---:|---:|"]
        for (who, name), trs in sorted(grow.items(), key=lambda kv: -len(kv[1]))[:10]:
            cells = []
            for r in rounds:
                pw = [s[1] for tr in trs for s in tr["seen"] if s[0] == r]
                cells.append(f"{statistics.mean(pw):.1f} ({len(pw)})" if pw else "-")
            biggest = [max(s[1] for s in tr["seen"]) for tr in trs]
            out.append(f"| {'ours' if who == 'us' else 'theirs'} | {name} | {len(trs)} | " + " | ".join(cells)
                       + f" | {statistics.median(biggest):.0f} | {max(biggest)} |")
        out += ["", "Each cell is the average power at the start of that round (how many copies in brackets).", ""]
    return out


def sacrifice_report(games: list[dict]) -> list[str]:
    """Section: sacrifices and drains (needs Forge patch 23).

    Part 1 - what we sacrificed (creatures), and what made us do it: our own card (a cost, a
             trigger) or one of theirs (an "each opponent sacrifices" effect).
    Part 2 - what they sacrificed, in short.
    Part 3 - life lost WITHOUT damage, by the card that caused it: drains ("target opponent loses
             1 life"), paid life, "you lose 1 life" draw engines.
    Part 4 - did our sacrifices lead to damage? For each creature we sacrificed, the life they lost
             to our cards after it, in the same turn, before our next sacrifice.
    Part 5 - win % by how many creatures we sacrificed and how much we drained per game.
    """
    out = ["", "## Sacrifice report (sacrifices and drains)", ""]
    if not any(g["sacrifices"] or g["life_loss"] for g in games):
        return out + ["No Sacrifice or LifeLoss lines in these logs (they need Forge patch 23_log_sacrifices).", ""]
    n = len(games)

    def whose(s: str | None) -> str:
        return {"us": "ours", "opp": "theirs"}.get(s, "?")

    # --- 1. Our creature sacrifices ------------------------------------------------------------------
    ours = [(gi, s) for gi, g in enumerate(games) for s in g["sacrifices"] if s["side"] == "us"]
    our_cre = [(gi, s) for gi, s in ours if s["creature"]]
    out += [f"Creatures we sacrificed: **{len(our_cre)}** in {n} games ({len(our_cre) / n:.1f} per game). "
            f"Other permanents we sacrificed (lands, Treasures...): {len(ours) - len(our_cre)}.", ""]
    if our_cre:
        by_card = defaultdict(lambda: {"times": 0, "games": set(), "token": False})
        for gi, s in our_cre:
            row = by_card[s["card"]]
            row["times"] += 1; row["games"].add(gi); row["token"] |= s["token"]
        out += ["### What we sacrificed", "", "| Creature | Times | Per game | Games with one | Token? |",
                "|---|---:|---:|---:|---|"]
        for card, row in sorted(by_card.items(), key=lambda kv: -kv[1]["times"])[:12]:
            out.append(f"| {card} | {row['times']} | {row['times'] / n:.2f} | {len(row['games'])} | "
                       f"{'yes' if row['token'] else 'no'} |")
        causes = Counter((s["cause"], whose(s["cause_side"])) for _, s in our_cre)
        on_our_turn = Counter(s["cause"] for gi, s in our_cre if games[gi]["turn_side"].get(s["turn"]) == "us")
        out += ["", "### What made us sacrifice", "", "| Card | Whose card | Times | Per game | On our turn |",
                "|---|---|---:|---:|---:|"]
        for (cause, side_), k in causes.most_common(10):
            out.append(f"| {cause} | {side_} | {k} | {k / n:.2f} | {pct(on_our_turn[cause], k)} |")
        out.append("")

    # --- 2. Their sacrifices (short) -------------------------------------------------------------------
    theirs = [s for g in games for s in g["sacrifices"] if s["side"] == "opp" and s["creature"]]
    if theirs:
        top = Counter((s["cause"], whose(s["cause_side"])) for s in theirs).most_common(5)
        out += [f"Creatures they sacrificed: {len(theirs)} ({len(theirs) / n:.1f} per game). Most often because of: "
                + "; ".join(f"{c} ({w}, {k})" for (c, w), k in top) + ".", ""]

    # --- 3. Life lost without damage ---------------------------------------------------------------------
    rows = defaultdict(lambda: {"total": 0, "games": set()})
    for gi, g in enumerate(games):
        for ll in g["life_loss"]:
            row = rows[(ll["source"], whose(ll["source_side"]), "us" if ll["side"] == "us" else "them")]
            row["total"] += ll["amount"]; row["games"].add(gi)
    if rows:
        out += ["### Life lost without damage (drains, paid life)", "",
                "| Card | Whose card | Who lost the life | Total | Per game | Games |", "|---|---|---|---:|---:|---:|"]
        for (src, side_, loser), row in sorted(rows.items(), key=lambda kv: -kv[1]["total"])[:12]:
            out.append(f"| {src} | {side_} | {loser} | {row['total']} | {row['total'] / n:.2f} | {len(row['games'])} |")
        out.append("")

    def drained(g: dict) -> int:
        """Life the opponent lost to our cards without damage in this game."""
        return sum(ll["amount"] for ll in g["life_loss"] if ll["side"] == "opp" and ll["source_side"] == "us")

    wins = [g for g in games if g["result"] == "win"]
    losses = [g for g in games if g["result"] == "loss"]
    total = sum(drained(g) for g in games)
    out += [f"Our cards drained **{total}** life from them without damage ({total / n:.1f} per game; "
            f"{sum(map(drained, wins)) / max(len(wins), 1):.1f} per win, "
            f"{sum(map(drained, losses)) / max(len(losses), 1):.1f} per loss).", ""]

    # --- 4. Sacrifice -> drain ----------------------------------------------------------------------------
    if our_cre:
        after = defaultdict(list)                         # cause card -> drain after each sacrifice
        for g in games:
            sacs = sorted((s for s in g["sacrifices"] if s["side"] == "us" and s["creature"]), key=lambda s: s["n"])
            for i, s in enumerate(sacs):
                stop = sacs[i + 1]["n"] if i + 1 < len(sacs) and sacs[i + 1]["turn"] == s["turn"] else 10 ** 9
                life = sum(ll["amount"] for ll in g["life_loss"]
                           if ll["side"] == "opp" and ll["source_side"] == "us"
                           and ll["turn"] == s["turn"] and s["n"] < ll["n"] < stop)
                after[s["cause"]].append(life)
        every = [x for v in after.values() for x in v]
        out += ["### Did our sacrifices make them lose life?", "",
                "For each creature we sacrificed: the life they lost to our cards (not damage) right after it, "
                "in the same turn, before our next sacrifice. A drain card that triggers on deaths shows up here.", "",
                "| What made us sacrifice | Sacrifices | Followed by a drain | Average life drained |",
                "|---|---:|---:|---:|"]
        out.append(f"| **All** | {len(every)} | {pct(sum(1 for x in every if x > 0), len(every))} | "
                   f"{statistics.mean(every):.2f} |")
        for cause, vals in sorted(after.items(), key=lambda kv: -len(kv[1]))[:8]:
            out.append(f"| {cause} | {len(vals)} | {pct(sum(1 for x in vals if x > 0), len(vals))} | "
                       f"{statistics.mean(vals):.2f} |")
        out.append("")

    # --- 5. Win % by sacrifices and drains per game ------------------------------------------------------------
    def bucket(x: int, edges: list[tuple[int, int, str]]) -> str:
        return next(label for lo, hi, label in edges if lo <= x <= hi)
    sac_edges = [(0, 0, "0"), (1, 2, "1-2"), (3, 4, "3-4"), (5, 10 ** 6, "5+")]
    drain_edges = [(0, 0, "0"), (1, 2, "1-2"), (3, 5, "3-5"), (6, 10 ** 6, "6+")]
    by_sac, by_drain = defaultdict(list), defaultdict(list)
    for g in games:
        k = sum(1 for s in g["sacrifices"] if s["side"] == "us" and s["creature"])
        by_sac[bucket(k, sac_edges)].append(score(g))
        by_drain[bucket(drained(g), drain_edges)].append(score(g))
    out += ["| Creatures we sacrificed in the game | Games | Win % |", "|---|---:|---:|"]
    out += [f"| {lab} | {len(by_sac[lab])} | {pct(sum(by_sac[lab]), len(by_sac[lab]))} |"
            for _, _, lab in sac_edges if by_sac[lab]]
    out += ["", "| Life we drained in the game | Games | Win % |", "|---|---:|---:|"]
    out += [f"| {lab} | {len(by_drain[lab])} | {pct(sum(by_drain[lab]), len(by_drain[lab]))} |"
            for _, _, lab in drain_edges if by_drain[lab]]
    out += ["", "Longer games have more of both, so a higher win % in a row is a hint, not proof.", ""]
    return out


def combat_report(games: list[dict]) -> list[str]:
    """Section: how often attacks get blocked, by whom, and what happens."""
    out = ["", "## Combat report (attacks and blocks)", "",
           "Each attacking creature counts once per turn. \"Died in the block\" = it died that turn and "
           "took damage from the creature it fought (so mobilize tokens sacrificed at end of turn don't count).", ""]
    rounds = list(range(2, 10))
    for attacker_side, title, blocker_word in (("us", "Our attacks", "Their"), ("opp", "Their attacks", "Our")):
        per_round, blockers, attackers, outcomes = combat_stats(games, attacker_side)
        total_att = sum(per_round["all"][r][0] for r in per_round["all"])
        total_blk = sum(per_round["all"][r][1] for r in per_round["all"])
        out += [f"### {title}", "",
                f"{total_att} attacking creatures, {total_blk} blocked ({pct(total_blk, total_att)}).", "",
                "| Round | Attackers | Blocked | Blocked % | Blocked % in our wins | Blocked % in our losses |",
                "|---|---:|---:|---:|---:|---:|"]
        for r in rounds:
            a, b = per_round["all"][r]
            if not a:
                continue
            w, l = per_round["win"][r], per_round["loss"][r]
            out.append(f"| {r} | {a} | {b} | {pct(b, a)} | {pct(w[1], w[0])} | {pct(l[1], l[0])} |")
        if outcomes:
            n_blocked = sum(outcomes.values())
            out += ["", "What happened when an attack was blocked: " + ", ".join(
                f"{k} {v} ({pct(v, n_blocked)})" for k, v in outcomes.most_common()) + "."]
        out += ["", f"| {blocker_word} blocker | Blocks | Blocker died in the block | Attacker died in the block |",
                "|---|---:|---:|---:|"]
        for name, (n_b, b_died, a_died) in sorted(blockers.items(), key=lambda kv: -kv[1][0])[:8]:
            out.append(f"| {name} | {n_b} | {b_died} ({pct(b_died, n_b)}) | {a_died} ({pct(a_died, n_b)}) |")
        out += ["", f"| {'Our' if attacker_side == 'us' else 'Their'} attacker | Attacks | Blocked | Blocked % | Died when blocked |",
                "|---|---:|---:|---:|---:|"]
        for name, (n_a, n_bl, d) in sorted(attackers.items(), key=lambda kv: -kv[1][0])[:10]:
            out.append(f"| {name} | {n_a} | {n_bl} | {pct(n_bl, n_a)} | {d} |")
        out.append("")
    return out


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

    # --- Their cards: which of the opponent's cards hurt us? -------------------
    # Same idea as the table above, seen from the other side: OUR win rate in games where the
    # opponent cast the card, next to games where they didn't. A card far below our overall rate
    # is one that beats us. Their sideboard cards are only cast in games 2-3, so compare those with
    # our games 2-3 result (in the results file), not with game 1.
    out += ["", "## Their card performance (which of their cards beat us?)", "",
            "Our win rate in games where the opponent cast the card at least once, next to games where "
            "they didn't. Far below our overall rate = a card that beats us. Cards they only bring in "
            "after game 1 are cast in games 2-3 only.", "",
            "| Their card | Games cast | Our win % when they cast it | Our win % when they didn't | Avg round first cast |",
            "|---|---:|---:|---:|---:|"]
    opp_cast_in = defaultdict(list)
    opp_first = defaultdict(list)
    for g in games:
        seen = {}
        for turn in sorted(g["casts_by_turn"]):
            for who, card in g["casts_by_turn"][turn]:
                if who == "opp":
                    seen.setdefault(card, (turn + 1) // 2)
        for card, rnd in seen.items():
            opp_cast_in[card].append(g)
            opp_first[card].append(rnd)
    opp_rows = []
    for card, gs in opp_cast_in.items():
        if len(gs) < 5:                                   # too few games to say anything
            continue
        w = sum(score(g) for g in gs)
        not_cast = [g for g in games if g not in gs]
        wn = sum(score(g) for g in not_cast)
        opp_rows.append((w / len(gs), card, len(gs), pct(w, len(gs)), pct(wn, len(not_cast)),
                         sum(opp_first[card]) / len(opp_first[card])))
    for _, card, cnt, wc, wnc, ft in sorted(opp_rows):    # worst for us first
        out.append(f"| {card} | {cnt} | {wc} | {wnc} | {ft:.1f} |")
    if not opp_rows:
        out.append("| (none) | | | | |")

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

    # --- Combat: attacks and blocks -----------------------------------------
    out += combat_report(games)
    out += creature_size_report(games)
    out += arrival_report(games)
    out += sacrifice_report(games)

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

    # --- Opening hands (needs Forge patch 03, which logs the kept hand) -------
    out += opening_hand_report(games)

    # --- Cards cast by turn (what we actually did early) ---------------------
    out += cast_by_turn_report(games)
    out += turn_one_report(games)
    out += ai_debug_report(games)

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
    # Debug lines from temporary Forge debug patches ("AIDEBUG <message>"), counted by message.
    # Only appears when a debug patch is active; used to see why the AI makes a choice.
    debug = Counter()
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "AIDEBUG" in line:
            debug[line.split("AIDEBUG", 1)[1].strip()] += 1
    if debug:
        text += "\n## AI debug lines (temporary debug patch)\n\n| Count | Message |\n|---:|---|\n"
        text += "\n".join(f"| {n} | {msg} |" for msg, n in debug.most_common(60)) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
