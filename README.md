# mtg-forge-sim

Tests Magic: The Gathering Standard decks against the current meta using the
[Forge](https://github.com/Card-Forge/forge) engine's AI, on GitHub Actions.

This public repo holds only the **test engine**. The decks being tested,
their sideboard plans, our sim-only card fixes, our AI profiles and all
results live in a separate private repo. Workflow artifacts are encrypted,
and results, card names and our deck's name are never shown in the public
run logs, step names or artifact names.

GitHub Actions is free for public repos on standard runners, so runs here
use no plan minutes. (The private repo has no workflows on purpose.)

## Folders

| Folder | What's in it |
|---|---|
| `.github/workflows/simulate.yml` | The test workflow (build Forge, play games, save results privately) |
| `decks/` | The meta opponents (public meta lists) |
| `scripts/` | Python scripts: run games, summarize, analyze logs, AI audit, card queue |
| `plans/ai_priorities.json` | Which opponent cards the AI should target first |
| `session_tools/` | Local helper scripts and public card pool data |

## How to run a simulation

1. Open the **Actions** tab, click **Simulate matchups**, then **Run workflow**.
2. Fill in:
   - **ours**: the deck's name in the private repo (a folder in `decks/`, or an experiment file name)
   - **games**: games per matchup
   - **sideboard**: tick to play post-sideboard games using the deck's `plans.json`
   - **opponent**: one deck from `decks/`, several separated by commas, or `all`
   - **analytics**: tick for card performance, game length and turning-point reports
   - **ai**: `Default` (fast), `Hard` or `Hardest` (thinks ahead)
   - **priorities**: tick to use `plans/ai_priorities.json` and the private repo's card fixes (`engine/card_overrides/`)
   - **our_profile**: an AI profile from the private repo's `engine/ai_profiles/` (name without `.ai`) for our deck only
   - **shards**: split each matchup over several jobs to finish faster
3. Results are saved to the private repo, in `decks/<deck>/results/`.

## Setup (secrets)

- `PRIVATE_REPO_TOKEN` (required): a fine-grained token with Contents read
  and write on the private repo only.
- `ARTIFACT_KEY` (optional): a separate password for encrypting artifacts.
  Without it, the token is used as the password.

## Luck margins

At 25 games a matchup can swing about ±20 points by luck, at 50 games about
±14, and at 100 games about ±10. Confirm a "winner" with a second run.
