"""
run_request.py
--------------
Checks one test request against our testing rules and turns it into the
settings for the "Simulate matchups" workflow.

Why this exists: a Claude session that can push files but has no GitHub login
can still ask for a test. It saves the request's settings in the PRIVATE repo
(run_queue/<id>.json, so deck names never appear here) and pushes the request
id to run_requests/go.txt in this repo. The workflow run_request.yml then runs
this script and starts the test.

  Input:  private/run_queue/<id>.json, for example
          {"ref": "bo3-test", "games": 100, "ours": "esper_control",
           "opponent": "boros_dwarves", "ai": "Hardest", "priorities": true,
           "analytics": true, "our_profile": "EsperControl", "bo3": true,
           "sideboard": false, "shards": 8}
  Output: (1) "::add-mask::" lines, so GitHub hides our deck/profile names in the log
          (2) dispatch.txt: the branch on line 1, then one "name=value" per line

How to run (Python 3.10+, no extra packages):
    python3 scripts/run_request.py private/run_queue/<id>.json dispatch.txt
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 2. The settings a request may contain, and what each one must look like
# ---------------------------------------------------------------------------
NAME = re.compile(r"^[a-z0-9_]+$")                 # deck / opponent names
PROFILE = re.compile(r"^[A-Za-z0-9_]*$")           # AI profile name (may be empty)
BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")         # a branch of this repo
AI_LEVELS = {"Default", "Hard", "Hardest"}
BOOLS = ("priorities", "analytics", "bo3", "sideboard")


def fail(message: str) -> None:
    """Stop with an error that GitHub shows in red (no private names in it)."""
    print(f"::error::{message}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 3. Check the request and build the workflow settings
# ---------------------------------------------------------------------------
def build(request: dict) -> tuple[str, list[str]]:
    allowed = {"ref", "games", "ours", "opponent", "ai", "our_profile", "shards", *BOOLS}
    unknown = set(request) - allowed
    if unknown:
        fail(f"Unknown setting(s) in the request: {', '.join(sorted(unknown))}")

    ref = str(request.get("ref", "main"))
    if not BRANCH.match(ref) or ".." in ref:
        fail("ref must be a branch name of this repo")

    ours = str(request.get("ours", ""))
    if not NAME.match(ours):
        fail("ours must be a simple deck name")

    # Rule (user, 2026-11-11): ONE opponent per test while tuning
    opponent = str(request.get("opponent", ""))
    if not NAME.match(opponent) or opponent == "all":
        fail("Pick ONE opponent (a file name in decks/); 'all' and lists are not allowed")
    if not Path(f"decks/{opponent}.txt").is_file():
        fail("opponent is not a deck in decks/")

    games = request.get("games", 100)
    if not isinstance(games, int) or not 1 <= games <= 500:
        fail("games must be a whole number from 1 to 500")

    shards = request.get("shards", 8)
    if not isinstance(shards, int) or not 1 <= shards <= 8:
        fail("shards must be a whole number from 1 to 8")

    ai = str(request.get("ai", "Hardest"))
    if ai not in AI_LEVELS:
        fail("ai must be Default, Hard or Hardest")

    profile = str(request.get("our_profile", ""))
    if not PROFILE.match(profile):
        fail("our_profile must be letters, numbers and _ only")

    settings = [f"games={games}", f"ours={ours}", f"opponent={opponent}", f"ai={ai}",
                f"our_profile={profile}", f"shards={shards}"]
    for key in BOOLS:
        value = request.get(key, False)
        if not isinstance(value, bool):
            fail(f"{key} must be true or false")
        # 'bo3' only exists on branches that have best-of-3; send it only when on
        if key == "bo3" and not value:
            continue
        settings.append(f"{key}={'true' if value else 'false'}")

    # Hide the private names in this job's log before anything else prints them
    print(f"::add-mask::{ours}")
    if profile:
        print(f"::add-mask::{profile}")
    return ref, settings


# ---------------------------------------------------------------------------
# 4. Main program
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    request_file, out_file = Path(sys.argv[1]), Path(sys.argv[2])
    if not request_file.is_file():
        fail("No settings file for this request id in the private repo's run_queue/")
    try:
        request = json.loads(request_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        fail("The request's settings file is not valid JSON")
    if not isinstance(request, dict):
        fail("The request's settings file must be a JSON object")
    ref, settings = build(request)
    out_file.write_text("\n".join([ref, *settings]) + "\n", encoding="utf-8")
    print(f"Request checked: branch {ref}, {len(settings)} settings")


if __name__ == "__main__":
    main()
