"""
run_matchup.py
--------------
Plays one matchup (our deck vs one opponent) with Forge's AI and saves the
result as a small JSON file. GitHub Actions runs this once per opponent.

How to run (Python 3.10+, Java 17+, a built Forge):
    python scripts/run_matchup.py --jar <forge jar> --forge-gui <forge/forge-gui>
        --decks <forge_decks dir> --ours grixis_walkers --opponent boros_dragons
        --games 20 --out results/boros_dragons.json

Forge prints one line per game, for example:
    Game Result: Game 3 ended in 41234 ms. Ai(1)-grixis_walkers has won!
    Game Result: Game 4 ended in a Draw! Took 120000 ms.
This script counts those lines.

--bo3 plays real best-of-3 MATCHES instead (needs our Forge patch 03):
--games is then the number of matches. Game 1 uses both main decks; from game 2
each side switches to its sideboarded deck (--ours-sb / --opponent-sb), and the
loser of the previous game goes first. Forge prints one line per match:
    Match Result: Match 3 | Ai(1)-grixis_walkers 2 | Ai(2)-boros_dragons 1 | draws 0

--parallel N splits the games into N batches and runs N copies of Forge at
the same time (each uses one CPU core). Keep N at 2 on GitHub's 4-core
machines: the Hardest AI thinks on a time budget, so crowding the CPU would make
it play weaker. If a copy of Forge crashes, the games it didn't finish are
played again (up to 2 more times) and the error is saved in the result.
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import argparse             # reads the --options above
import json                 # saves results in a simple, readable format
import re                   # finds the "Game Result" lines in Forge's output
import subprocess           # starts Forge (a Java program) from Python
import sys
import time
from pathlib import Path

# Matches: "Game Result: Game 3 ended in 41234 ms. Ai(1)-grixis_walkers has won!"
WIN_LINE = re.compile(r"Game Result: Game (\d+) ended in \d+ ms\. Ai\((\d)\)-(.+?) has won!")
DRAW_LINE = re.compile(r"Game Result: Game (\d+) ended in a Draw!")
# Matches: "Match Result: Match 3 | Ai(1)-ours 2 | Ai(2)-them 1 | draws 0" (best-of-3 runs)
MATCH_LINE = re.compile(r"Match Result: Match (\d+) \| Ai\(1\)-(.+?) (\d+) \| Ai\(2\)-(.+?) (\d+) \| draws (\d+)")


# ---------------------------------------------------------------------------
# 2. Read the command-line options
# ---------------------------------------------------------------------------
def get_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one Forge AI-vs-AI matchup")
    p.add_argument("--jar", required=True, help="Forge desktop jar-with-dependencies")
    p.add_argument("--forge-gui", required=True, help="Forge's forge-gui folder (has res/)")
    p.add_argument("--decks", required=True, help="folder of converted .dck files")
    p.add_argument("--ours", required=True, help="our deck name, without .dck")
    p.add_argument("--opponent", required=True, help="opponent deck name, without .dck")
    p.add_argument("--games", type=int, default=20, help="number of games to play")
    p.add_argument("--game-timeout", type=int, default=180,
                   help="seconds before a single slow game is called a draw")
    p.add_argument("--out", required=True, help="where to save the JSON result")
    p.add_argument("--ai", default="Default",
                   help="Forge AI level for both players: Default, Hard or Hardest "
                        "(Hard/Hardest search ahead before each decision; slower but stronger)")
    p.add_argument("--log-out", default=None,
                   help="save Forge's full game log here (turns off quiet mode) "
                        "for scripts/analyze_logs.py")
    p.add_argument("--our-ai", default="",
                   help="a different AI profile for OUR deck only (e.g. MyProfile, a file in "
                        "Forge's res/ai folder); the opponent keeps --ai")
    p.add_argument("--parallel", type=int, default=1,
                   help="run this many copies of Forge at once, splitting the games "
                        "between them (use the number of CPU cores, e.g. 4)")
    p.add_argument("--private", action="store_true",
                   help="public repo: don't print results or game text in the job log "
                        "(they are saved to --out only, which the workflow encrypts)")
    p.add_argument("--report-as", default=None,
                   help="name to show in results (e.g. our deck's name when playing "
                        "its sideboarded copy); defaults to --ours")
    p.add_argument("--opponent-deck", default=None,
                   help="the opponent's .dck to actually load (e.g. its sideboarded copy "
                        "'<opponent>__sb__<style>'); results still use --opponent as the name")
    p.add_argument("--bo3", action="store_true",
                   help="play best-of-3 matches: --games is the number of MATCHES, "
                        "and both sides switch to their sideboarded decks after game 1")
    p.add_argument("--ours-sb", default=None,
                   help="best-of-3: our sideboarded .dck for games 2-3 (default: our main deck)")
    p.add_argument("--opponent-sb", default=None,
                   help="best-of-3: the opponent's sideboarded .dck for games 2-3 (default: its main deck)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# 3. Run Forge (one or several copies at once) and return everything it printed
# ---------------------------------------------------------------------------
def forge_command(args: argparse.Namespace, games: int, memory: str) -> list[str]:
    decks_dir = str(Path(args.decks).resolve())
    command = [
        "java", f"-Xmx{memory}",
        "-jar", str(Path(args.jar).resolve()),
        "sim",                                   # Forge's simulation mode
        # the opponent's sideboarded copy is loaded when given, else its main deck
        "-d", f"{args.ours}.dck", f"{args.opponent_deck or args.opponent}.dck",
        "-D", decks_dir,                         # folder the .dck files are in
        "-n", str(games),                        # number of games (matches with --bo3)
        "-c", str(args.game_timeout),            # per-game time limit
    ]
    if args.bo3:
        # best of 3; from game 2 each side plays its sideboarded deck (Forge patch 03)
        command += ["-m", "3",
                    "-sb", f"{args.ours_sb or args.ours}.dck",
                    f"{args.opponent_sb or args.opponent_deck or args.opponent}.dck"]
    if not args.log_out:
        command.append("-q")                     # quiet: results only, no full logs
    if args.our_ai:
        # player 1 (our deck) uses our own profile, player 2 keeps the normal level
        command += ["-a", args.our_ai, args.ai or "Default"]
    elif args.ai and args.ai != "Default":
        command += ["-a", args.ai, args.ai]      # same AI level for both players
    # Forge turns its Monte Carlo search OFF in simulations unless asked:
    # "on" for Hard, "max" (desktop strength) for Hardest. Our own profiles are
    # built on Hardest, so they get "max" too.
    if args.ai == "Hardest" or args.our_ai:
        command += ["-mc", "max"]
    elif args.ai == "Hard":
        command += ["-mc", "on"]
    return command


def run_batches(args: argparse.Namespace, batches: list[int]) -> list[tuple[int, str, int]]:
    """Start one copy of Forge per batch at the same time.
    Returns (games asked for, everything it printed, exit code) for each batch."""
    memory = "4g" if len(batches) == 1 else "3g"     # keep total memory within the machine's limit
    per_unit = 3 if args.bo3 else 1                  # a match is up to 3 games
    overall_limit = max(batches) * per_unit * args.game_timeout + 600   # so a stuck run can't run forever
    # Each copy of Forge creates its settings and cache folders on start-up (in a
    # fixed order, ending with ~/.cache/forge/pics/planechase). Two copies doing
    # that at the same moment can make one of them quit ("cannot create profile
    # directory"). So: start the first copy, wait until its last folder exists,
    # then start the others.
    last_folder = Path.home() / ".cache" / "forge" / "pics" / "planechase"
    running = []
    for i, games in enumerate(batches):
        if i == 1:
            for _ in range(120):                     # wait up to 60 seconds
                if last_folder.is_dir():
                    break
                time.sleep(0.5)
            time.sleep(1)
        command = forge_command(args, games, memory)
        unit = "matches" if args.bo3 else "games"
        if args.private:
            # public log: don't show the command, its deck names would describe our deck
            print(f"Running batch {i + 1}/{len(batches)} ({games} {unit})", flush=True)
        else:
            print(f"Running batch {i + 1}/{len(batches)} ({games} {unit}):", " ".join(command), flush=True)
        running.append((games, subprocess.Popen(command, cwd=args.forge_gui,   # Forge looks for res/ here
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
    finished = []
    for i, (games, proc) in enumerate(running):
        try:
            out, err = proc.communicate(timeout=overall_limit)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            err += "\n[run_matchup] batch hit the overall time limit and was stopped"
        finished.append((games, out + "\n" + err, proc.returncode))
    return finished


def is_end_line(line: str, bo3: bool) -> bool:
    """True for the line that closes one unit: a game, or a whole match with --bo3."""
    if bo3:
        return bool(MATCH_LINE.search(line))
    return bool(WIN_LINE.search(line) or DRAW_LINE.search(line))


def games_in(output: str, bo3: bool = False) -> int:
    """How many finished games (or matches with --bo3) a batch's output contains."""
    return sum(1 for line in output.splitlines() if is_end_line(line, bo3))


def finished_part(output: str, bo3: bool = False) -> str:
    """The output up to the end of the last finished game (or match with --bo3).
    A crashed copy can stop in the middle; that half is dropped so it can't get
    mixed into the next one when the logs are joined."""
    lines = output.splitlines()
    last = max((i for i, ln in enumerate(lines) if is_end_line(ln, bo3)), default=-1)
    return "\n".join(lines[:last + 1])


def problem_text(output: str, code: int, asked: int, got: int) -> str:
    """A short description of why a batch stopped early (last error lines from Forge)."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    errors = [ln for ln in lines if any(w in ln for w in ("Exception", "Error", "error:", "Caused by", "[run_matchup]"))]
    tail = (errors[-3:] if errors else lines[-3:])
    return f"exit code {code}, {got} of {asked} games: " + " | ".join(t[:200] for t in tail)


def run_forge(args: argparse.Namespace) -> tuple[str, list[str], int]:
    """Plays all the games. If a copy of Forge stops early (a crash), the
    games it didn't finish are played again, up to 2 more times.
    Returns (all output, problems seen, games that had to be replayed)."""
    # Split the games as evenly as possible, e.g. 50 games / 2 copies = 25+25
    copies = max(1, min(args.parallel, args.games))
    batches = [args.games // copies + (1 if i < args.games % copies else 0) for i in range(copies)]
    outputs, problems, replayed = [], [], 0
    for attempt in range(3):
        missing = 0
        for asked, output, code in run_batches(args, batches):
            got = games_in(output, args.bo3)
            outputs.append(finished_part(output, args.bo3))
            if got < asked:
                missing += asked - got
                problems.append(f"attempt {attempt + 1}: " + problem_text(output, code, asked, got))
        if missing == 0:
            break
        replayed += missing
        print(f"{missing} games did not finish; playing them again", flush=True)
        copies = max(1, min(args.parallel, missing))
        batches = [missing // copies + (1 if i < missing % copies else 0) for i in range(copies)]
    # Each batch's log is complete games, so they can simply be joined
    return "\n".join(outputs), problems, replayed


# ---------------------------------------------------------------------------
# 4. Count wins, losses and draws from Forge's output
# ---------------------------------------------------------------------------
def count_results(output: str, ours: str) -> dict:
    wins = losses = draws = 0
    for line in output.splitlines():
        win = WIN_LINE.search(line)
        if win:
            winner_name = win.group(3).strip()
            if winner_name == ours:
                wins += 1
            else:
                losses += 1
        elif DRAW_LINE.search(line):
            draws += 1
    return {"wins": wins, "losses": losses, "draws": draws}


def count_bo3(output: str, ours: str) -> dict:
    """Best-of-3: wins/losses/draws are MATCHES. Also counts game 1 (main decks)
    and games 2-3 (both sideboarded) separately, from our side."""
    match = {"wins": 0, "losses": 0, "draws": 0}
    game1 = {"wins": 0, "losses": 0, "draws": 0}
    later = {"wins": 0, "losses": 0, "draws": 0}
    for line in output.splitlines():
        win, draw, m = WIN_LINE.search(line), DRAW_LINE.search(line), MATCH_LINE.search(line)
        if win or draw:
            number = int((win or draw).group(1))          # game number inside its match
            bucket = game1 if number == 1 else later
            if draw:
                bucket["draws"] += 1
            elif win.group(3).strip() == ours:
                bucket["wins"] += 1
            else:
                bucket["losses"] += 1
        elif m:
            # player 1 is always our deck (-d ours opponent)
            mine, theirs = int(m.group(3)), int(m.group(5))
            key = "wins" if mine > theirs else "losses" if theirs > mine else "draws"
            match[key] += 1
    return {**match, "bo3": True, "game1": game1, "games_2_3": later}


# ---------------------------------------------------------------------------
# 5. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    args = get_args()
    start = time.time()
    output, problems, replayed = run_forge(args)
    result = count_bo3(output, args.ours) if args.bo3 else count_results(output, args.ours)
    if args.log_out:
        log_path = Path(args.log_out)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
        print(f"Full game log saved to {log_path} ({len(output):,} characters)")
    played = result["wins"] + result["losses"] + result["draws"]   # games, or matches with --bo3

    result.update({
        "ours": args.report_as or args.ours,
        "opponent": args.opponent,
        # True when the opponent played a sideboarded copy (games 2-3 against us)
        "opponent_sideboarded": bool(args.opponent_deck and args.opponent_deck != args.opponent),
        "games_requested": args.games,
        "games_played": played,
        "ai": args.ai,
        "our_ai": args.our_ai or args.ai,
        "replayed_games": replayed,      # games played again after a Forge crash
        "problems": problems,            # what went wrong, if anything
        "minutes": round((time.time() - start) / 60, 1),
    })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.private:
        # Only a harmless count goes in the public log; the real result stays in the file
        print(f"Finished: {played} of {args.games} games recorded")
    else:
        print(json.dumps(result, indent=2))

    # If no games were recorded, show the end of Forge's output to help debugging,
    # and fail this job so the problem is visible in GitHub.
    if played == 0:
        print("\nNo game results found.")
        if args.private:
            # Forge's output shows our cards, so in private mode show only error lines
            errors = [l for l in output.splitlines() if re.search(r"(?i)exception|error|fatal", l)]
            print("Error lines from Forge:\n" + "\n".join(errors[-30:]))
        else:
            print("Last part of Forge's output:\n")
            print(output[-4000:])
        sys.exit(1)


if __name__ == "__main__":
    main()
