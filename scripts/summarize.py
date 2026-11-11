"""
summarize.py
------------
Combines the per-matchup JSON files from run_matchup.py into one table.
When a matchup was split into shards (several jobs), their files are added together.

How to run (Python 3.10+, no extra packages needed):
    python scripts/summarize.py <results-folder> <output-markdown-file>

It prints the table and also saves it as Markdown, which GitHub shows nicely
on the run's summary page and in the repository.
"""

# ---------------------------------------------------------------------------
# 1. Imports and settings
# ---------------------------------------------------------------------------
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

WARN_BELOW = 45.0   # flag matchups where our win rate is under this percent


# ---------------------------------------------------------------------------
# 2. Build the Markdown table
# ---------------------------------------------------------------------------
def build_table(results: list[dict]) -> str:
    ours = results[0]["ours"] if results else "?"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Forge AI results: {ours}",
        "",
        f"Run finished {now}. Draws count as half a win. "
        "Forge's AI is weaker than a human and can't choose its own sideboard cards "
        "(sideboarding uses our written plans), so read these as rough signals, "
        "not exact win rates. Note \"they sideboarded\" = the opponent played its "
        "sideboard plan against us too.",
        "",
        "| Opponent | W | L | D | Games | Win % | Note |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]

    total_w = total_l = total_d = 0
    # Sort from worst matchup to best so problems are at the top
    def win_pct(r):
        games = r["wins"] + r["losses"] + r["draws"]
        return 100 * (r["wins"] + 0.5 * r["draws"]) / games if games else 0.0

    for r in sorted(results, key=win_pct):
        games = r["wins"] + r["losses"] + r["draws"]
        pct = win_pct(r)
        note = ""
        if games < r.get("games_requested", games):
            note = f"only {games} of {r['games_requested']} games finished"
        elif pct < WARN_BELOW:
            note = "weak matchup"
        if r.get("opponent_sideboarded"):
            note = f"{note}, they sideboarded" if note else "they sideboarded"
        lines.append(f"| {r['opponent']} | {r['wins']} | {r['losses']} | {r['draws']} "
                     f"| {games} | {pct:.0f}% | {note} |")
        total_w, total_l, total_d = total_w + r["wins"], total_l + r["losses"], total_d + r["draws"]

    total = total_w + total_l + total_d
    overall = 100 * (total_w + 0.5 * total_d) / total if total else 0.0
    lines.append(f"| **Overall** | **{total_w}** | **{total_l}** | **{total_d}** "
                 f"| **{total}** | **{overall:.0f}%** | |")
    per = max(r.get("games_requested", 0) for r in results)
    margin = round(100 / (per ** 0.5)) if per else 0   # rough 95% margin for a 50/50 matchup
    lines += ["", f"With {per} games per matchup, a result can be off by roughly "
              f"±{margin} percentage points by luck alone. More games narrow that."]
    # Any Forge crashes: how many games were played again, and the error seen
    notes = [(r["opponent"], r.get("replayed_games", 0), r.get("problems", [])) for r in results]
    notes = [n for n in notes if n[1] or n[2]]
    if notes:
        lines += ["", "**Problems during the run** (crashed games were played again):", ""]
        for opp, replayed, probs in notes:
            lines.append(f"- {opp}: {replayed} games replayed")
            lines += [f"  - {p}" for p in probs[:3]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 3. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    results_dir, out_file = Path(sys.argv[1]), Path(sys.argv[2])

    parts = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(results_dir.rglob("*.json"))]
    if not parts:
        sys.exit(f"No result files found in {results_dir}")

    # A matchup split into shards has one file per shard: add them together
    merged: dict[str, dict] = {}
    for r in parts:
        m = merged.setdefault(r["opponent"], {**r, "wins": 0, "losses": 0, "draws": 0,
                                              "games_requested": 0, "replayed_games": 0,
                                              "problems": []})
        for k in ("wins", "losses", "draws", "games_requested"):
            m[k] += r.get(k, 0)
        m["replayed_games"] += r.get("replayed_games", 0)
        m["problems"] += r.get("problems", [])
    results = list(merged.values())

    table = build_table(results)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(table, encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
