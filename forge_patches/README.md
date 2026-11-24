# forge_patches/ — our fixes to Forge's AI

The workflow downloads the newest Forge, applies every `*.patch` file here
(in file-name order), then builds it. Patched builds are cached separately:
the cache key includes a hash of these files.

| Patch | What it fixes |
|---|---|
| `01_empower_ai.patch` | Adds AI logic for **Empower** (e.g. "Empower Jace 1"). Without it Forge used "can't play" for Empower, so the AI never cast spells with Empower in them (Countersculpt, Jace's Machinations) and never used Empower loyalty abilities (Jace, Reality Sculptor's +1). |
| `02_behold_any_card.patch` | **Behold** costs: the AI only looked for *creatures* to behold, so with 2+ valid non-creature cards (e.g. two Jaces for Countersculpt) it couldn't pay. Now any valid card works (behold-and-exile gives up the least valuable one). |
| `03_log_opening_hand.patch` | Logs each player's kept opening hand ("Mulligan: <player> opening hand: A ; B ; ...") so `analyze_logs.py` can report win rate by opening hand. Logging only, no change to play. |
| `04_hand_disruption_first.patch` | New AI profile setting `PRIORITIZE_HAND_DISRUPTION` (off by default). When on: cast hand disruption (Duress, exile-from-hand spells...) on our own turn before anything else, also in main phase 1, and only hold mana for instants when no hand disruption can be cast. |
| `05_remember_opponent_hand.patch` | New AI profile setting `REMEMBER_OPPONENT_HAND` (off by default). When on: cards seen in an opponent's hand (Duress, exile-from-hand spells, any reveal) are remembered until they leave it; the look-ahead keeps them in the opponent's hand instead of reshuffling them; Duress and exile-from-hand spells take the card our own hand can't answer; and no more Duress into a known hand with nothing to take. |
| `06_bo3_sideboard_matches.patch` | **Best-of-3 with sideboarding** in simulation mode. New option `-sb <deck1> <deck2>`: from game 2 of every match each player switches to that deck. With `-m 3`, `-n` is now the number of matches, a match stops after 3 games even with draws, and each match prints a `Match Result:` line. (Forge's own AI sideboarding is off in simulations, so our written plans are used instead.) |
| `07_counter_cheap_if_no_answer.patch` | New AI profile setting `COUNTER_CHEAP_IF_NO_ANSWER` (off by default). When on: a cheap creature/equipment/other permanent (below `MIN_SPELL_CMC_TO_COUNTER`) is countered if no removal in our hand (spell or enters-the-battlefield trigger, e.g. Seam Rip) could handle it later. (06 is used by the bo3-test branch.) |
| `08_life_or_discard.patch` | New AI profile setting `LIFE_OR_DISCARD_SMART` (off by default). "Pay N life or discard" spells (Bitter Triumph): early game (turns 1-4) with 10+ life left after paying, pay life; otherwise discard a spare card (spare land, or a spell that isn't a threat or an answer); with only key cards, pay life only if we survive the turn; else discard the least valuable card. Ward: a target's "Ward - pay N life" is counted in the life math. |
| `09_avoid_unpayable_ward.patch` | New AI profile setting `AVOID_UNPAYABLE_WARD` (off by default). While our AI decides what to target, an opponent's warded permanent whose ward we can't or shouldn't pay (mana for spell + ward; life cost + ward life leaves us under 4; other ward costs by Forge's usual check) counts as not targetable, so the AI picks the next best target instead of not casting. Only during the decision (never when a spell resolves), and not for mandatory triggers. |
| `10_blight_only_when_spent.patch` | New AI profile setting `BLIGHT_ONLY_WHEN_SPENT` (off by default). Optional blight costs (Requiting Hex): only pay when a creature of ours has already done its job - it is blocking a non-trampler and would die in combat anyway (or is a token) - and put the -1/-1 counter on that creature. Otherwise cast without the blight. |
| `11_smart_discard.patch` | New AI profile setting `SMART_DISCARD` (off by default). When an effect makes us discard from our own hand (Seasoned Cryomancer): keep the lands we still need (we want lands = most expensive spell in hand, 4-6), pitch extra lands first, then spells we can't cast soon and second copies of legends already in play; keep threats and answers; prefer nonland cards when that stuns attackers. |
| `12_sweep_when_free.patch` | One-shot damage sweepers (Pyroclasm, Slagstorm, Fire Magic...): cast them when they kill 2+ of the opponent's creatures and none of ours. Forge's default needed ~2 creatures' worth of value even when we lose nothing, so it held Pyroclasm until turn 5-6, when the creatures were too big. Applies to both players. |
| `15_log_charm_targets.patch` | **Log only.** Modal spells (charms and other choose-one spells) keep their targets on the chosen mode, so Forge logged them with no targets and our removal report never counted them. Now their targets are logged. No change to how games are played. |
| `16_bounce_own_permanents.patch` | Bounce effects that may return **our own** permanents ("return up to one target ... you control"): Forge's AI always dropped its own permanents as targets unless they were about to die. A card script can now list what the AI should return in `AIBounceOwn$`, best first, tiers separated by `;` (used by our Nurturing Pixie fix: replay a used-up The Naked Time). Cards without `AIBounceOwn$` play exactly as before. |
| `17_removal_timing.patch` | New AI profile settings `REMOVAL_KILL_THREATS_EARLY` and `REMOVAL_INSTANT_ON_THEIR_TURN` (off by default). Forge often holds removal with a dice roll, hoping for a bigger target, so control decks let early creatures hit them for turns. With the first on: kill an opponent's creature that can deal damage (or a strong one) as soon as we can, and let removal that pays its victim's controller (Get Lost's Map tokens, Erode's land) hit any creature with power 2+ (or any creature while our life is in danger) - Forge's guard only allowed those on big creatures. With the second also on: instant removal waits for the opponent's turn (when the creature attacks, or their end step) unless our life is in danger. Used by `ai_profiles/MetaControl.ai`. |
| `18_wipe_timing.patch` | New AI profile setting `WIPE_SAVE_FOR_SEVERAL` (off by default). Creature board wipes (Day of Judgment, Deadly Cover-Up): don't spend the wipe on a lone creature that a spot removal spell in hand can answer (unless our life is in danger); with 2+ opponent creatures that out-value ours and an attack that would take a third of our life or more, wipe now. Used by `ai_profiles/MetaControl.ai`. |
| `20_sacrifice_payoffs.patch` | New AI profile setting `SAC_PAYOFF_SMART` (off by default; new file `SacPayoff.java`). Forge's Hardest profile only pays "sacrifice another creature or artifact" costs with cards marked SacMe, so sacrifice decks (Jund Biotech) almost never used their free outlets. With the setting on, a token is sacrificed when the ability draws a card, when our "whenever you sacrifice" payoffs would deal lethal damage, or for a spare Treasure at the opponent's end step. Only tokens, never real cards. Used by `ai_profiles/MetaSacrifice.ai`. |
| `19_log_positions.patch` | **Log only.** Step 1 of the new AI brain: at the start of every turn, logs one "Position" line per player with board numbers (life, cards in hand, lands, creatures, power, evasive power, planeswalkers...). `scripts/positions.py` turns these into training data for the learned scorekeeper (saved on analytics runs to `results/positions/`). No change to how games are played. |

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
