"""
train_scorekeeper.py
--------------------
Step 2 of the new AI brain: learns how much each board number is really worth,
from the board snapshots our Forge games save (see scripts/positions.py and
forge_patches/19_log_positions.patch).

What it does, in plain words:
  1. Reads every snapshot table (CSV) from Hardest-AI runs.
  2. Puts 20% of the GAMES aside as a test set (whole games, so the test is fair:
     the scorekeeper never sees any turn of a test game while learning).
  3. Learns one set of weights per game stage (early / mid / late), with
     logistic regression: "given this board, how likely is this player to win?"
     Each input is "my number minus the opponent's number" (so both sides are
     valued the same way), plus whether it's my turn that is starting.
  4. Checks, on the test games, how often it picks the real winner, and compares
     that with two simpler scorekeepers:
       - "more life wins" (just life totals)
       - a Forge-style formula (the hand-written weights from Forge's
         GameStateEvaluator, rebuilt from the same board numbers)
  5. Checks it on decks it has NEVER seen: for each opponent deck, it learns
     from all the other matchups and is tested on that deck's games.
  6. Writes the weights (JSON), a short report (Markdown) that also shows what
     each number is "worth in life points", and the two lines our Brain AI
     profile needs (brain_settings.txt, read by Forge patch 21).

Good things never count against you: more life, cards in hand, lands, creatures,
power, toughness, planeswalkers, loyalty and other permanents can't get a
negative weight (if the best fit wants one, that number is left at 0 and the
rest is fitted again). Without this, numbers that move together (creatures,
power, toughness) share credit unevenly and one can end up below zero, which
would make the AI think, say, more power is bad.

Library, graveyard and exile sizes are NOT used as inputs: a deck always has
about 60 cards spread over library, hand, battlefield, graveyard and exile, so
those counts mostly tell the scorekeeper how long the game has gone (and which
deck it is), not what is good. Using them gave nonsense values such as
"one more creature = -9 life" with no gain in accuracy (tested Nov 24, 2026).

The weights only describe positions; nothing here changes how Forge plays.

How to run (Python 3.10+ and numpy: `pip install numpy`), from the private repo folder:
    python3 <public repo>/scripts/train_scorekeeper.py \\
        --data "decks/*/results/positions/*.csv" \\
        --weights engine/scorekeeper/weights_v1.json \\
        --report engine/scorekeeper/report_v1.md \\
        --settings engine/scorekeeper/brain_settings.txt
Options:
    --version 1   use only the board numbers every snapshot has (default)
    --version 2   also use mana held open and kinds of cards in hand
                  (only version-2 rows are used then)
    --ai Hardest  which AI level's runs to learn from (default Hardest)
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import argparse
import csv
import glob
import json
import os
import random
import sys
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:          # a clear message instead of a long error
    sys.exit("This script needs numpy. Install it with:  pip install numpy")

# ---------------------------------------------------------------------------
# 2. Settings
# ---------------------------------------------------------------------------
# Every board number in a snapshot (version 1), and the extra ones from version 2.
V1_NUMBERS = ["life", "poison", "hand", "library", "graveyard", "exile", "lands",
              "creatures", "power", "toughness", "evasive_power", "tokens",
              "walkers", "loyalty", "other_permanents"]
V2_EXTRA = ["untapped_lands", "hand_lands", "hand_creatures", "hand_instant_speed",
            "hand_counters", "hand_removal", "hand_mana_value"]

# Numbers that are NOT used as inputs (see the note at the top of this file).
NOT_INPUTS = {"library", "graveyard", "exile"}

# "Good" numbers: having more than the opponent can never lower your score.
NEVER_NEGATIVE = {"life", "hand", "lands", "creatures", "power", "toughness", "evasive_power",
                  "walkers", "loyalty", "other_permanents", "hand_counters", "hand_removal"}

# Game stages, by Forge's turn number (it counts BOTH players' turns,
# so turn 6 = the end of each player's 3rd turn).
STAGES = [("early", 1, 6), ("mid", 7, 12), ("late", 13, 10_000)]

TEST_SHARE = 0.20          # 20% of games are kept aside for testing
L2 = 1.0                   # small penalty that stops weights from growing wild
NEWTON_STEPS = 25          # learning steps (Newton's method converges fast)
MIN_UNSEEN_ROWS = 500      # an opponent deck needs this many mid-game rows for the unseen-deck test


# ---------------------------------------------------------------------------
# 3. Reading the data
# ---------------------------------------------------------------------------
def all_numbers(version: int) -> list[str]:
    """Every board number in the snapshots for this version."""
    return V1_NUMBERS + (V2_EXTRA if version == 2 else [])


def input_numbers(version: int) -> list[str]:
    """The board numbers the scorekeeper learns from."""
    return [k for k in all_numbers(version) if k not in NOT_INPUTS]


def load_rows(pattern: str, ai: str, version: int) -> tuple[list[dict], dict]:
    """Reads all matching CSVs. Each row gets a 'game_key' (file + game number)
    so a whole game can be kept together in the test set, and the opponent
    deck's name (from the file name) for the unseen-deck test."""
    rows, info = [], {"files": 0, "skipped_files": 0, "games": 0}
    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path)
        if f"_{ai}" not in name:          # e.g. skip "_Default_" check runs
            info["skipped_files"] += 1
            continue
        with open(path, newline="", encoding="utf-8") as f:
            file_rows = list(csv.DictReader(f))
        if version == 2:
            file_rows = [r for r in file_rows if r.get("version") == "2"]
        if not file_rows:
            continue
        info["files"] += 1
        info["games"] += len({r["game"] for r in file_rows})
        # file names look like "<our deck..>_vs_<opponent>_run<id>.csv"
        opponent = name.split("_vs_", 1)[-1].rsplit("_run", 1)[0]
        for r in file_rows:
            r["game_key"] = name + "#" + r["game"]
            r["opponent_deck"] = opponent
            rows.append(r)
    return rows, info


def to_arrays(rows: list[dict], version: int):
    """Turns rows into number tables:
         RAW: every me_/opp_ number (used by the simpler scorekeepers)
         X:   the inputs, "me minus opp" for each input number, plus 'active'
         y:   1 = won, 0 = lost, 0.5 = draw
         turn, opponent deck per row"""
    raw_names = [p + k for k in all_numbers(version) for p in ("me_", "opp_")]
    RAW = np.array([[float(r.get(n, 0) or 0) for n in raw_names] for r in rows])
    col = {n: i for i, n in enumerate(raw_names)}
    inputs = input_numbers(version)
    X = np.column_stack([RAW[:, col["me_" + k]] - RAW[:, col["opp_" + k]] for k in inputs]
                        + [np.array([float(r["active"]) for r in rows])])
    names = ["diff_" + k for k in inputs] + ["active"]
    y = np.array([float(r["won"]) for r in rows])
    turn = np.array([int(r["turn"]) for r in rows])
    opponent = np.array([r["opponent_deck"] for r in rows])
    return RAW, col, X, names, y, turn, opponent


def split_games(rows: list[dict], seed: int):
    """Picks 20% of the games (not rows) for testing."""
    keys = sorted({r["game_key"] for r in rows})
    random.Random(seed).shuffle(keys)
    test_keys = set(keys[: int(len(keys) * TEST_SHARE)])
    is_test = np.array([r["game_key"] in test_keys for r in rows])
    return is_test, len(keys) - len(test_keys), len(test_keys)


# ---------------------------------------------------------------------------
# 4. Learning (logistic regression with Newton's method)
# ---------------------------------------------------------------------------
def sigmoid(z):
    """Turns a score into a win chance between 0 and 1."""
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X, y, never_negative=()):
    """Learns weights w and a bias b so that sigmoid(X @ w + b) ~ chance to win.
    Columns listed in never_negative can't get a weight below zero: if one would,
    it is fixed at 0 and the others are fitted again (repeated until none is negative)."""
    fixed = set()
    while True:
        keep = [i for i in range(X.shape[1]) if i not in fixed]
        w_part, b = _fit_logistic(X[:, keep], y)
        w = np.zeros(X.shape[1])
        w[keep] = w_part
        # the most negative "good" weight (measured in spread-adjusted units), if any
        bad = [(w[i] * X[:, i].std(), i) for i in never_negative if i not in fixed and w[i] < 0]
        if not bad:
            return w, b
        fixed.add(min(bad)[1])


def _fit_logistic(X, y):
    """Plain logistic regression. Inputs are scaled first (so big numbers don't dominate),
    and the weights are scaled back at the end, so they work on raw board numbers."""
    mean, std = X.mean(axis=0), X.std(axis=0)
    std[std == 0] = 1.0                                  # a number that never changes
    Z = np.hstack([(X - mean) / std, np.ones((len(X), 1))])
    w = np.zeros(Z.shape[1])
    penalty = np.full(Z.shape[1], L2)
    penalty[-1] = 0.0                                    # don't penalize the bias
    for _ in range(NEWTON_STEPS):
        p = sigmoid(Z @ w)
        gradient = Z.T @ (p - y) + penalty * w
        hessian = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(penalty)
        step = np.linalg.solve(hessian, gradient)
        w -= step
        if np.max(np.abs(step)) < 1e-7:                  # stopped improving
            break
    raw_w = w[:-1] / std                                 # back to raw board numbers
    raw_b = w[-1] - np.sum(w[:-1] * mean / std)
    return raw_w, raw_b


# ---------------------------------------------------------------------------
# 5. The two simpler scorekeepers we compare against
# ---------------------------------------------------------------------------
def life_score(RAW, col):
    """'More life wins': my life minus their life."""
    return RAW[:, col["me_life"]] - RAW[:, col["opp_life"]]


# Forge's hand-written values (forge-ai GameStateEvaluator + CreatureEvaluator),
# rebuilt from our board numbers. Approximate: Forge looks at each real card,
# we only have counts, so creatures/lands/permanents use typical values.
FORGE_WEIGHTS = {
    "life": 2, "hand": 5,                 # opponent's cards in hand count -4 (below)
    "lands": 106,                         # 3 + 100 per mana + ~3 per color
    "creatures": 80, "power": 15, "toughness": 10, "evasive_power": 10,
    "walkers": 170, "loyalty": 2,         # 50 + 30 x ~4 mana value, + 2 per loyalty
    "other_permanents": 140,              # 50 + 30 x ~3 mana value
}


def forge_score(RAW, col):
    """Forge-style formula: my side's value minus the opponent's side."""
    s = np.zeros(len(RAW))
    for k, v in FORGE_WEIGHTS.items():
        s += v * RAW[:, col["me_" + k]]
        s -= (4 if k == "hand" else v) * RAW[:, col["opp_" + k]]
    return s


def calibrate(score_train, y_train):
    """Fits one scale + bias so a simple score becomes a win chance (fair comparison
    for 'how sure' it is; it doesn't change which side it picks)."""
    w, b = fit_logistic(score_train[:, None], y_train)
    return lambda s: sigmoid(s * w[0] + b)


# ---------------------------------------------------------------------------
# 6. Measuring
# ---------------------------------------------------------------------------
def measure(p, y):
    """Accuracy: how often it picks the real winner (draws left out).
    Log loss: how good its win chances are (lower is better)."""
    decided = y != 0.5
    acc = float(np.mean((p[decided] > 0.5) == (y[decided] == 1))) if decided.any() else float("nan")
    eps = 1e-9
    loss = float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))
    return acc, loss


def unseen_deck_test(X, y, RAW, col, turn, opponent, nonneg):
    """For each opponent deck: learn from all OTHER matchups (mid game), then test
    on that deck's games. Shows whether the scorekeeper works on new decks."""
    mid = (turn >= STAGES[1][1]) & (turn <= STAGES[1][2])
    results = []
    for deck in sorted(set(opponent.tolist())):
        te = mid & (opponent == deck)
        tr = mid & (opponent != deck)
        if te.sum() < MIN_UNSEEN_ROWS:
            continue
        w, b = fit_logistic(X[tr], y[tr], nonneg)
        learned = measure(sigmoid(X[te] @ w + b), y[te])[0]
        forge = measure(calibrate(forge_score(RAW[tr], col), y[tr])(forge_score(RAW[te], col)), y[te])[0]
        results.append((deck, int(te.sum()), learned, forge))
    return results


# ---------------------------------------------------------------------------
# 7. The Brain profile's settings line
# ---------------------------------------------------------------------------
def brain_settings(names, scale, weights_out) -> str:
    """One line for LEARNED_EVAL_WEIGHTS. No "=" allowed inside (Forge's profile reader would
    silently drop the line), so it uses "key:value" parts joined by ";":
        inputs:life,hand,...,active;scale:310.5;early:1,6,BIAS,W1,W2,...;mid:...;late:..."""
    inputs = [n.replace("diff_", "", 1) for n in names]          # Forge counts the raw names
    parts = ["inputs:" + ",".join(inputs), f"scale:{scale:.6g}"]
    for stage, ws in weights_out.items():
        lo, hi = ws["turns"]
        nums = [str(lo), str(min(hi, 10_000)), f"{ws['bias']:.6g}"] + [f"{ws['weights'][n]:.6g}" for n in names]
        parts.append(f"{stage}:" + ",".join(nums))
    line = ";".join(parts)
    assert "=" not in line
    return line


def check_settings(settings, X, turn, names, weights_out, scale) -> None:
    """Reads the line back the way Forge patch 21 does, scores 1,000 rows with it, and stops
    if it doesn't match the trained weights (so a broken line never reaches Forge)."""
    inputs, stages = None, []
    for part in settings.split(";"):
        key, value = part.split(":", 1)
        if key == "inputs":
            inputs = value.split(",")
        elif key != "scale":
            nums = value.split(",")
            stages.append((int(nums[0]), int(nums[1]), float(nums[2]), [float(v) for v in nums[3:]]))
    assert inputs and all(len(st[3]) == len(inputs) for st in stages), "settings line has the wrong shape"
    rng = np.random.default_rng(0)
    for i in rng.choice(len(X), size=min(1000, len(X)), replace=False):
        lo, hi, bias, w = next(st for st in stages if st[0] <= turn[i] <= st[1]) if any(
            st[0] <= turn[i] <= st[1] for st in stages) else stages[-1]
        from_line = bias + sum(wi * xi for wi, xi in zip(w, X[i]))
        stage = next(s for s, ws in weights_out.items() if ws["turns"][0] <= turn[i] <= ws["turns"][1])
        from_model = weights_out[stage]["bias"] + sum(weights_out[stage]["weights"][n] * X[i][j] for j, n in enumerate(names))
        assert abs(from_line - from_model) < 1e-3 * max(1.0, abs(from_model)), "settings line doesn't match the weights"
    print("  settings line checked: it gives the same scores as the trained weights")


# ---------------------------------------------------------------------------
# 8. Main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Train the AI brain's learned scorekeeper.")
    ap.add_argument("--data", default="decks/*/results/positions/*.csv")
    ap.add_argument("--ai", default="Hardest")
    ap.add_argument("--version", type=int, choices=[1, 2], default=1)
    ap.add_argument("--weights", default="engine/scorekeeper/weights_v1.json")
    ap.add_argument("--report", default="engine/scorekeeper/report_v1.md")
    ap.add_argument("--settings", default="engine/scorekeeper/brain_settings.txt")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    # --- 1. read ---
    rows, info = load_rows(args.data, args.ai, args.version)
    if not rows:
        sys.exit(f"No {args.ai} snapshot rows found for: {args.data}")
    RAW, col, X, names, y, turn, opponent = to_arrays(rows, args.version)
    nonneg = [i for i, n in enumerate(names) if n.replace("diff_", "", 1) in NEVER_NEGATIVE]
    is_test, n_train_games, n_test_games = split_games(rows, args.seed)
    print(f"Read {info['games']} games ({len(rows)} rows) from {info['files']} files; "
          f"learning from {n_train_games} games, testing on {n_test_games}.")

    # --- 2. learn one scorekeeper per game stage, and compare on the test games ---
    results, weights_out = [], {}
    for stage, lo, hi in STAGES:
        in_stage = (turn >= lo) & (turn <= hi)
        tr, te = in_stage & ~is_test, in_stage & is_test
        if tr.sum() < 200 or te.sum() < 50:
            print(f"  {stage}: not enough rows, skipped")
            continue
        w, b = fit_logistic(X[tr], y[tr], nonneg)
        learned = measure(sigmoid(X[te] @ w + b), y[te])
        life = measure(calibrate(life_score(RAW[tr], col), y[tr])(life_score(RAW[te], col)), y[te])
        forge = measure(calibrate(forge_score(RAW[tr], col), y[tr])(forge_score(RAW[te], col)), y[te])
        results.append((stage, lo, hi, int(te.sum()), learned, forge, life))
        weights_out[stage] = {"turns": [lo, hi], "bias": float(b),
                              "weights": {n: float(v) for n, v in zip(names, w)}}
        print(f"  {stage:5s} turns {lo}-{hi if hi < 10_000 else '+'}: picks the winner "
              f"learned {learned[0]:.1%} | Forge-style {forge[0]:.1%} | life only {life[0]:.1%}")

    # --- 3. unseen decks ---
    unseen = unseen_deck_test(X, y, RAW, col, turn, opponent, nonneg)
    if unseen:
        avg_l = sum(r[2] for r in unseen) / len(unseen)
        avg_f = sum(r[3] for r in unseen) / len(unseen)
        wins = sum(1 for r in unseen if r[2] > r[3])
        print(f"  unseen decks (mid game): learned {avg_l:.1%} | Forge-style {avg_f:.1%} "
              f"| learned better on {wins} of {len(unseen)} decks")

    # --- 4. what each number is worth, in life points (per stage) vs Forge ---
    worth = {}
    for stage, ws in weights_out.items():
        life_w = ws["weights"]["diff_life"]
        worth[stage] = {k: (ws["weights"]["diff_" + k] / life_w if life_w else float("nan"))
                        for k in input_numbers(args.version) if k != "life"}

    # --- 5. the size of one point: make the learned score spread about as much as Forge's own
    #        score does (mid game), so the look-ahead's bonuses and margins keep their weight ---
    mid_tr = (turn >= STAGES[1][1]) & (turn <= STAGES[1][2]) & ~is_test
    mid_w = weights_out["mid"]["weights"] if "mid" in weights_out else next(iter(weights_out.values()))["weights"]
    mid_logit = X[mid_tr] @ np.array([mid_w[n] for n in names])
    scale = float(np.std(forge_score(RAW[mid_tr], col)) / max(np.std(mid_logit), 1e-9))
    print(f"  point size: 1 learned win-score unit = {scale:.1f} Forge points")

    # --- 6. save the weights (numbers only) ---
    os.makedirs(os.path.dirname(args.weights) or ".", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(args.weights, "w", encoding="utf-8") as f:
        json.dump({
            "about": "Learned scorekeeper for the AI brain. Win chance = 1 / (1 + e^-(bias + sum of weight x input)). "
                     "Inputs: diff_<number> = my number minus the opponent's (patch 19 board numbers), "
                     "active = 1 if it's my turn starting. Pick the stage by Forge's turn number.",
            "snapshot_version": args.version, "ai": args.ai, "trained": stamp,
            "games": info["games"], "train_games": n_train_games, "test_games": n_test_games,
            "inputs": names, "scale": scale, "stages": weights_out,
        }, f, indent=1)

    # --- 7. the Brain profile's two lines (read by Forge patch 21's LearnedEvaluator) ---
    settings = brain_settings(names, scale, weights_out)
    check_settings(settings, X, turn, names, weights_out, scale)
    os.makedirs(os.path.dirname(args.settings) or ".", exist_ok=True)
    with open(args.settings, "w", encoding="utf-8") as f:
        f.write(f"# AI brain: learned scorekeeper ({stamp}, {info['games']} {args.ai} games, snapshot version {args.version})\n"
                "# Added to Forge's Hardest profile to make the Brain profile (see the simulate workflow).\n"
                "LEARNED_EVAL=true\n"
                f"LEARNED_EVAL_WEIGHTS={settings}\n")

    # --- 6. write the report ---
    lines = [f"# Learned scorekeeper (snapshot version {args.version})", "",
             f"Trained {stamp} on {info['games']} {args.ai}-AI games from {info['files']} snapshot files "
             f"({len(rows)} rows). {n_test_games} games ({TEST_SHARE:.0%}) were kept aside for testing; "
             "the numbers below are from those test games only.", "",
             "## How often each scorekeeper picks the real winner", "",
             "| Game stage | Test rows | Learned | Forge-style formula | Life only |",
             "|---|---|---|---|---|"]
    for stage, lo, hi, n, learned, forge, life in results:
        span = f"turns {lo}-{hi}" if hi < 10_000 else f"turns {lo}+"
        lines.append(f"| {stage} ({span}) | {n} | **{learned[0]:.1%}** | {forge[0]:.1%} | {life[0]:.1%} |")
    lines += ["", "Log loss (how good the win chances are; lower is better):", "",
              "| Game stage | Learned | Forge-style formula | Life only |", "|---|---|---|---|"]
    for stage, lo, hi, n, learned, forge, life in results:
        lines.append(f"| {stage} | {learned[1]:.3f} | {forge[1]:.3f} | {life[1]:.3f} |")
    if unseen:
        lines += ["", "## Decks it has never seen (mid game)", "",
                  "For each opponent deck, the scorekeeper learned from all the other matchups only, "
                  "then was tested on that deck's games.", "",
                  "| Opponent deck | Rows | Learned | Forge-style |", "|---|---|---|---|"]
        for deck, n, l, fo in unseen:
            lines.append(f"| {deck} | {n} | **{l:.1%}** | {fo:.1%} |")
        lines.append(f"| **Average** | | **{avg_l:.1%}** | {avg_f:.1%} |")
        lines.append(f"\nLearned was better on {wins} of {len(unseen)} decks.")
    lines += ["", f"One learned win-score unit = {scale:.1f} Forge points (so the learned score is about the "
              "same size as Forge's own score, and the look-ahead's bonuses keep their weight).",
              "", "## What each board number is worth, in life points", "",
              "How many of your own life points one more of each is worth (compared with the opponent). "
              "Forge = its hand-written formula (approximate, the same at every stage).", "",
              "| One more... | " + " | ".join(f"Learned ({s})" for s in worth) + " | Forge |",
              "|---|" + "---|" * len(worth) + "---|"]
    for k in input_numbers(args.version):
        if k == "life":
            continue
        cells = " | ".join(f"{worth[s][k]:+.1f}" for s in worth)
        lines.append(f"| {k.replace('_', ' ')} | {cells} | {FORGE_WEIGHTS.get(k, 0) / FORGE_WEIGHTS['life']:+.1f} |")
    lines += ["", "A creature's value is 'creatures' + 'power' x its power + 'toughness' x its toughness "
              "(+ 'evasive power' x power if it flies, has menace or trample; + 'tokens' if it's a token).",
              "", "Notes:",
              "- Forge-style formula is rebuilt from counts (Forge scores each real card), so it is approximate.",
              "- 'Learned' values describe what predicted wins in AI-vs-AI games, not what causes them.",
              "- Library, graveyard and exile sizes are left out on purpose (they mostly track game length).",
              "- Good numbers (life, cards, lands, creatures, power, toughness, planeswalkers, loyalty, other "
              "permanents) are never allowed below zero; a value of +0.0 means the fit wanted it negative.",
              "- Values will change as more matchups and version-2 snapshots are added."]
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Saved weights  -> {args.weights}\nSaved report   -> {args.report}\nSaved settings -> {args.settings}")


if __name__ == "__main__":
    main()
