# forge_patches/ — our fixes to Forge's AI

The workflow downloads the newest Forge, applies every `*.patch` file here
(in file-name order), then builds it. Patched builds are cached separately:
the cache key includes a hash of these files.

| Patch | What it fixes |
|---|---|
| `01_empower_ai.patch` | Adds AI logic for **Empower** (e.g. "Empower Jace 1"). Without it Forge used "can't play" for Empower, so the AI never cast spells with Empower in them (Countersculpt, Jace's Machinations) and never used Empower loyalty abilities (Jace, Reality Sculptor's +1). |
| `02_behold_any_card.patch` | **Behold** costs: the AI only looked for *creatures* to behold, so with 2+ valid non-creature cards (e.g. two Jaces for Countersculpt) it couldn't pay. Now any valid card works (behold-and-exile gives up the least valuable one). |
| `03_log_opening_hand.patch` | Logs each player's kept opening hand ("Mulligan: <player> opening hand: A ; B ; ...") so `analyze_logs.py` can report win rate by opening hand. Logging only, no change to play. |
| `04_hand_disruption_first.patch` | New AI profile setting `PRIORITIZE_HAND_DISRUPTION` (off by default). When on: cast hand disruption (Duress, Soul Search...) on our own turn before anything else, also in main phase 1, and only hold mana for instants when no hand disruption can be cast. |

## If a patch stops applying

Forge changes every day. If the build stops with "no longer applies to the
latest Forge":
1. Clone Forge: `git clone --depth 1 https://github.com/Card-Forge/forge`
2. Make the same change by hand in the files the patch touches.
3. Save a new patch: `git -C forge diff > forge_patches/<same name>.patch`
   (for new files, run `git -C forge add -N <file>` first so they're included).

## Making a new patch

1. Change the Forge source in a local clone.
2. `git add -N` any new files, then `git diff > forge_patches/NN_short_name.patch`.
3. Add a row to the table above.

Long term: send these fixes to the Forge project
(https://github.com/Card-Forge/forge) so we no longer need to keep patches.
