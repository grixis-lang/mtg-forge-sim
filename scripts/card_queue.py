"""
card_queue.py
-------------
Tests candidate cards one at a time against a baseline deck, following our
testing rules: each candidate goes in as 4 copies, in place of the same 4
"flex" slots every time, so every result compares to the same baseline
(the baseline itself is the "0 copies" step of the count ladder).

All deck files and results live in the PRIVATE repo (MTG-Private-List):
    decks/<baseline>/deck.txt                     the baseline deck
    decks/<baseline>/experiments/exp_*.txt        test decks this script writes
    decks/<baseline>/results/                     results (the workflow saves them here)
    decks/<baseline>/results/<baseline>_card_tests.md   the summary table

For each candidate it:
  1. writes decks/<baseline>/experiments/exp_<deck>_<card>.txt
     (sideboard copies of the card removed, so the deck never has more than 4)
  2. checks the deck is Standard-legal (convert_decks.py)
  3. pushes it to the private repo, runs game 1 against all meta decks on
     GitHub Actions (public repo) and waits
  4. adds a row to the card tests table and pushes it

How to run, from the public repo folder (needs the GitHub CLI "gh" logged in):
    python3 scripts/card_queue.py <baseline deck> <cardsfolder> <queue file> \\
        --flex "1 Card A" "1 Card B" "1 Card C" "1 Card D" \\
        --profile MyProfile [--private ../mtg-private-list]

(--profile is the name of an AI profile in the private repo's
engine/ai_profiles/, without .ai. The real commands we use are in the
private repo's HANDOFF.md.)

The queue file has one card name per line.
"""

# ---------------------------------------------------------------------------
# 1. Imports and settings
# ---------------------------------------------------------------------------
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = "grixis-lang/mtg-forge-sim"   # public repo: runs the tests only


def sh(cmd: list[str], check: bool = True, cwd: Path | None = None) -> str:
    """Run a command (optionally in another folder) and return what it printed."""
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{out.stdout}\n{out.stderr}")
    return out.stdout


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def push_private(private: Path, message: str) -> None:
    """Commit everything in the private repo and push it (retry if another run pushed first)."""
    sh(["git", "add", "-A"], cwd=private)
    sh(["git", "commit", "-q", "-m", message], check=False, cwd=private)
    for _ in range(3):
        sh(["git", "pull", "--rebase", "-q"], check=False, cwd=private)
        if subprocess.run(["git", "push", "-q"], cwd=private).returncode == 0:
            return
        time.sleep(5)
    raise RuntimeError("could not push to the private repo")


# ---------------------------------------------------------------------------
# 2. Build one test deck
# ---------------------------------------------------------------------------
def build_variant(base: str, flex: list[str], card: str) -> str:
    main, side = base.split("Sideboard")
    for line in flex:                                   # free the 4 flex slots
        if f"{line}\n" not in main:
            raise ValueError(f"flex line not in main deck: {line}")
        main = main.replace(f"{line}\n", "", 1)
    main = main.rstrip("\n") + f"\n4 {card}\n\n"
    side_lines = [ln for ln in side.splitlines() if not re.match(rf"^\d+\s+{re.escape(card)}$", ln.strip())]
    return main + "Sideboard" + "\n".join(side_lines) + "\n"


# ---------------------------------------------------------------------------
# 3. Run one test on GitHub and wait for it
# ---------------------------------------------------------------------------
def run_test(deck: str, games: int, shards: int, profile: str) -> int:
    before = json.loads(sh(["gh", "api", f"repos/{REPO}/actions/runs?per_page=1"]))["workflow_runs"][0]["id"]
    sh(["gh", "api", "-X", "POST", f"repos/{REPO}/actions/workflows/simulate.yml/dispatches",
        "-f", "ref=main", "-f", f"inputs[games]={games}", "-f", f"inputs[ours]={deck}",
        "-F", "inputs[sideboard]=false", "-f", "inputs[opponent]=all", "-F", "inputs[analytics]=true",
        "-f", "inputs[ai]=Hardest", "-F", "inputs[priorities]=true", "-f", f"inputs[shards]={shards}",
        "-f", f"inputs[our_profile]={profile}"])
    run_id = None
    for _ in range(40):                                 # find the run we just started
        time.sleep(3)
        runs = json.loads(sh(["gh", "api", f"repos/{REPO}/actions/runs?per_page=5"]))["workflow_runs"]
        new = [r["id"] for r in runs if r["id"] > before]
        if new:
            run_id = min(new)
            break
    if run_id is None:
        raise RuntimeError("could not find the started run")
    while True:                                         # wait until it finishes
        time.sleep(30)
        st = json.loads(sh(["gh", "api", f"repos/{REPO}/actions/runs/{run_id}"]))
        if st["status"] == "completed":
            if st["conclusion"] != "success":
                raise RuntimeError(f"run {run_id} ended with {st['conclusion']}")
            return run_id


# ---------------------------------------------------------------------------
# 4. Read the results
# ---------------------------------------------------------------------------
ROW = re.compile(r"^\|\s*([a-z0-9_]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|")


def read_table(path: Path) -> dict[str, tuple[int, int, int]]:
    return {m.group(1): tuple(map(int, m.group(2, 3, 4)))
            for line in path.read_text().splitlines() if (m := ROW.match(line))}


def card_stats(results: Path, deck: str, profile: str, card: str) -> tuple[int, float]:
    """Games the card was cast in, and win % in those games, over all matchups."""
    n = w = 0.0
    for f in (results / "analytics").glob(f"{deck}_Hardest_prio_{profile}_vs_*.md"):
        sec = f.read_text().split("## Card performance")[1].split("## Game length")[0]
        for line in sec.splitlines():
            c = [x.strip() for x in line.strip("|").split("|")]
            if len(c) == 5 and c[0] == card and c[1].isdigit():
                n += int(c[1])
                w += int(c[1]) * int(c[2].rstrip("%")) / 100
    return int(n), (100 * w / n if n else 0.0)


def pct(t: tuple[int, int, int]) -> float:
    return 100 * (t[0] + t[2] / 2) / max(1, sum(t))


# ---------------------------------------------------------------------------
# 5. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline")
    ap.add_argument("cardsfolder")
    ap.add_argument("queue")
    ap.add_argument("--flex", nargs=4, required=True, help='the 4 main-deck lines to replace, e.g. "1 Card A"')
    ap.add_argument("--profile", required=True,
                    help="our AI profile name (a file in the private repo's engine/ai_profiles/)")
    ap.add_argument("--games", type=int, default=50)
    ap.add_argument("--shards", type=int, default=1)   # one job at a time anyway (max-parallel 1)
    ap.add_argument("--private", default="../mtg-private-list", help="local clone of MTG-Private-List")
    args = ap.parse_args()

    # Safety: the deck name becomes a folder name, so allow only simple names
    if not re.fullmatch(r"[a-z0-9_]+", args.baseline):
        sys.exit("baseline must be a simple deck name like my_deck")

    private = Path(args.private).resolve()
    home = private / "decks" / args.baseline            # this deck's one folder
    results = home / "results"
    (home / "experiments").mkdir(parents=True, exist_ok=True)
    sh(["git", "pull", "-q"], check=False, cwd=private)

    base_text = (home / "deck.txt").read_text()
    base_table = read_table(results / f"{args.baseline}_Hardest_prio_{args.profile}.md")
    opps = sorted(base_table)
    out = results / f"{args.baseline}_card_tests.md"
    base_overall = pct(tuple(map(sum, zip(*base_table.values()))))
    if not out.exists():
        out.write_text(
            f"# Card tests for {args.baseline}\n\n"
            f"Each candidate goes in as **4 copies** in place of: {', '.join(args.flex)}. "
            f"Game 1, {args.games} games per matchup, Hardest AI, AI helpers on, our profile {args.profile}. "
            f"Baseline (0 copies): **{base_overall:.0f}%**. "
            "All 8 decks combined is about ±5 points by luck.\n\n"
            "| Card | Overall | Change | Cast in | Win % when cast | " + " | ".join(o.split("_")[0] for o in opps) + " |\n"
            "|---|---:|---:|---:|---:|" + "---:|" * len(opps) + "\n")

    for card in [ln.strip() for ln in Path(args.queue).read_text().splitlines() if ln.strip()]:
        # 5a. Write the test deck into the private repo
        name = f"exp_{slug(args.baseline)[:6]}_{slug(card)[:24]}"
        deck_file = home / "experiments" / f"{name}.txt"
        deck_file.write_text(build_variant(base_text, args.flex, card))

        # 5b. Check it is Standard-legal (in a temporary folder, outside both repos)
        tmp = Path(f"/tmp/cq_{name}"); sh(["rm", "-rf", str(tmp), str(tmp) + "_c"]); tmp.mkdir()
        sh(["cp", str(deck_file), str(tmp)])
        sh([sys.executable, "scripts/convert_decks.py", args.cardsfolder, str(tmp), str(tmp) + "_c", "--standard"])

        # 5c. Save it privately, then run the test
        push_private(private, f"Card test: 4 {card} in {args.baseline}")
        print(f"[{time.strftime('%H:%M')}] testing {card} ...", flush=True)
        run_test(name, args.games, args.shards, args.profile)
        time.sleep(10)
        sh(["git", "pull", "-q"], cwd=private)          # the workflow saved the results there

        # 5d. Add the result row and save it privately
        table = read_table(results / f"{name}_Hardest_prio_{args.profile}.md")
        overall = pct(tuple(map(sum, zip(*table.values()))))
        n, wr = card_stats(results, name, args.profile, card)
        cells = " | ".join(f"{pct(table[o]):.0f}%" if o in table else "-" for o in opps)
        with out.open("a") as fh:
            fh.write(f"| {card} | {overall:.0f}% | {overall - base_overall:+.0f} | {n} | {wr:.0f}% | {cells} |\n")
        push_private(private, f"Card test result: {card}")
        print(f"[{time.strftime('%H:%M')}] {card}: {overall:.0f}% ({overall - base_overall:+.0f}), "
              f"cast in {n} games, {wr:.0f}% when cast", flush=True)


if __name__ == "__main__":
    main()
