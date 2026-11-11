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
| `05_remember_opponent_hand.patch` | New AI profile setting `REMEMBER_OPPONENT_HAND` (off by default). When on: cards seen in an opponent's hand (Duress, Soul Search, any reveal) are remembered until they leave it; the look-ahead keeps them in the opponent's hand instead of reshuffling them; Duress / Soul Search take the card our own hand can't answer; and no more Duress into a known hand with nothing to take. |
| `07_counter_cheap_if_no_answer.patch` | New AI profile setting `COUNTER_CHEAP_IF_NO_ANSWER` (off by default). When on: a cheap creature/equipment/other permanent (below `MIN_SPELL_CMC_TO_COUNTER`) is countered if no removal in our hand (spell or enters-the-battlefield trigger, e.g. Seam Rip) could handle it later. (06 is used by the bo3-test branch.) |
| `08_life_or_discard.patch` | New AI profile setting `LIFE_OR_DISCARD_SMART` (off by default). "Pay N life or discard" spells (Bitter Triumph): early game (turns 1-4) with 10+ life left after paying, pay life; otherwise discard a spare card (spare land, or a spell that isn't a threat or an answer); with only key cards, pay life only if we survive the turn; else discard the least valuable card. Ward: a target's "Ward - pay N life" is counted in the life math. |
| `09_avoid_unpayable_ward.patch` | New AI profile setting `AVOID_UNPAYABLE_WARD` (off by default). While our AI decides what to target, an opponent's warded permanent whose ward we can't or shouldn't pay (mana for spell + ward; life cost + ward life leaves us under 4; other ward costs by Forge's usual check) counts as not targetable, so the AI picks the next best target instead of not casting. Only during the decision (never when a spell resolves), and not for mandatory triggers. |
| `10_blight_only_when_spent.patch` | New AI profile setting `BLIGHT_ONLY_WHEN_SPENT` (off by default). Optional blight costs (Requiting Hex): only pay when a creature of ours has already done its job - it is blocking a non-trampler and would die in combat anyway (or is a token) - and put the -1/-1 counter on that creature. Otherwise cast without the blight. |

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
