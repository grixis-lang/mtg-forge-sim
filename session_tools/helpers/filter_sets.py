"""
filter_sets.py
--------------
Pulls every card from a chosen list of Magic: The Gathering sets out of the
big MTGJSON "cards" CSV file and saves TWO files:

  1. <output>.csv         -> every printing (showcase, borderless, etc.)
  2. <output>_unique.csv  -> one row per card name (duplicates removed)

How to run (Python 3.10+ and pandas required):
    pip install pandas
    python filter_sets.py <path-to-big-cards-csv> <output-csv>

Example:
    python filter_sets.py cards.csv standard_sets_cards.csv
"""

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
import sys                # lets us read the file paths typed on the command line
from pathlib import Path  # modern, safe way to work with file paths
import pandas as pd       # the library that reads and filters the CSV


# ---------------------------------------------------------------------------
# 2. The sets we want, as {set code: set name}
#    The CSV only stores the short set code (e.g. "WOE"), so this mapping
#    lets us add a readable set name column to the output.
#    The ORDER here matters: when a card is in several sets, the unique file
#    keeps the printing from the set listed first.
# ---------------------------------------------------------------------------
SETS_WANTED = {
    "WOE": "Wilds of Eldraine",
    "LCI": "The Lost Caverns of Ixalan",
    "MKM": "Murders at Karlov Manor",
    "OTJ": "Outlaws of Thunder Junction",
    "BLB": "Bloomburrow",
    "DSK": "Duskmourn: House of Horror",
    "FDN": "Magic: The Gathering Foundations",
    "DFT": "Aetherdrift",
    "TDM": "Tarkir: Dragonstorm",
    "FIN": "FINAL FANTASY",
    "SPM": "Marvel's Spider-Man",
    "TLA": "Avatar: The Last Airbender",
    "EOE": "Edge of Eternities",
    "ECL": "Lorwyn Eclipsed",
    "TMT": "Teenage Mutant Ninja Turtles",
    "MSH": "Marvel Super Heroes",
    "SOS": "Secrets of Strixhaven",
    "HOB": "The Hobbit",
    "FRA": "Reality Fracture",
    "TRK": "Star Trek",
}

# ---------------------------------------------------------------------------
# 3. The columns to keep. The original file has 82 columns; these are the
#    ones most people actually need. Add or remove names here as you like.
# ---------------------------------------------------------------------------
COLUMNS_TO_KEEP = [
    "setCode", "number", "name", "manaCost", "manaValue", "colors",
    "colorIdentity", "type", "rarity", "text", "power", "toughness",
    "loyalty", "keywords", "artist", "finishes", "frameEffects",
    "promoTypes", "edhrecRank", "uuid",
]


def make_unique(all_printings: pd.DataFrame) -> pd.DataFrame:
    """
    Turn the 'every printing' table into one row per card name.

    - Keeps the FIRST printing of each card. Because the table is already
      sorted by set order and then collector number, that is the regular
      (non-showcase) version from the earliest set in SETS_WANTED.
    - Adds 'allSets' (every set code the card appears in) and
      'printingCount' (how many printings were merged into this row).
    """
    # For each card name, collect the set codes it appears in (no repeats,
    # kept in set-list order) and count its printings.
    summary = (
        all_printings.groupby("name", sort=False)
        .agg(
            allSets=("setCode", lambda s: ", ".join(dict.fromkeys(s))),
            printingCount=("setCode", "size"),
        )
        .reset_index()
    )

    # Keep only the first row for each name, then attach the summary columns
    unique = all_printings.drop_duplicates(subset="name", keep="first")
    unique = unique.merge(summary, on="name", how="left")

    # Columns specific to one printing are misleading once rows are merged,
    # so drop them from the unique file.
    printing_only = ["number", "artist", "finishes", "frameEffects",
                     "promoTypes", "uuid"]
    unique = unique.drop(columns=[c for c in printing_only if c in unique.columns])

    # Rename setCode/setName so it's clear they mean "first set seen in"
    return unique.rename(columns={"setCode": "firstSetCode",
                                  "setName": "firstSetName"})


def main() -> None:
    # -----------------------------------------------------------------------
    # 4. Read and check the command-line arguments
    # -----------------------------------------------------------------------
    if len(sys.argv) != 3:
        print("Usage: python filter_sets.py <input-csv> <output-csv>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    # Second file name: same as output, with "_unique" added before ".csv"
    unique_path = output_path.with_name(f"{output_path.stem}_unique{output_path.suffix}")

    # Security/safety: make sure the input exists and is a real file,
    # and never overwrite the input file by accident.
    if not input_path.is_file():
        print(f"Input file not found: {input_path}")
        sys.exit(1)
    if input_path.resolve() in (output_path.resolve(), unique_path.resolve()):
        print("Output file must be different from the input file.")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # 5. Load the big CSV
    #    dtype=str reads everything as text, so values like card number
    #    "001" or "12★" are never changed into numbers by mistake.
    # -----------------------------------------------------------------------
    print(f"Reading {input_path} ...")
    cards = pd.read_csv(input_path, dtype=str, keep_default_na=False)

    # -----------------------------------------------------------------------
    # 6. Keep only rows whose set code is in our list
    # -----------------------------------------------------------------------
    filtered = cards[cards["setCode"].isin(SETS_WANTED.keys())].copy()

    # Keep only the columns we care about (skip any that are missing)
    keep = [c for c in COLUMNS_TO_KEEP if c in filtered.columns]
    filtered = filtered[keep]

    # Add a readable set name right after the set code
    filtered.insert(1, "setName", filtered["setCode"].map(SETS_WANTED))

    # -----------------------------------------------------------------------
    # 7. Sort: by the order of the set list above, then by collector number
    # -----------------------------------------------------------------------
    set_order = {code: i for i, code in enumerate(SETS_WANTED)}
    filtered["_set_order"] = filtered["setCode"].map(set_order)
    # Pull the leading digits out of numbers like "12a" or "301★" for sorting
    filtered["_num"] = pd.to_numeric(
        filtered["number"].str.extract(r"(\d+)")[0], errors="coerce"
    )
    filtered = filtered.sort_values(["_set_order", "_num", "number"])
    filtered = filtered.drop(columns=["_set_order", "_num"])

    # -----------------------------------------------------------------------
    # 8. Build the unique (no duplicates) version
    # -----------------------------------------------------------------------
    unique = make_unique(filtered)

    # -----------------------------------------------------------------------
    # 9. Save both files (utf-8-sig so Excel shows symbols like ★ correctly)
    # -----------------------------------------------------------------------
    filtered.to_csv(output_path, index=False, encoding="utf-8-sig")
    unique.to_csv(unique_path, index=False, encoding="utf-8-sig")

    # -----------------------------------------------------------------------
    # 10. Print a summary
    # -----------------------------------------------------------------------
    print(f"\nSaved {len(filtered):,} printings to {output_path}")
    print(f"Saved {len(unique):,} unique cards to {unique_path}\n")
    print(f"{'Code':<5} {'Set':<34} {'Printings':>9} {'Unique cards':>13}")
    for code, name in SETS_WANTED.items():
        rows = filtered[filtered["setCode"] == code]
        flag = "  <-- NOT FOUND" if rows.empty else ""
        print(f"{code:<5} {name:<34} {len(rows):>9} {rows['name'].nunique():>13}{flag}")

    in_many = (unique["allSets"].str.contains(",")).sum()
    print(f"\nCards that appear in more than one of these sets: {in_many:,}")


# Run main() only when this file is run directly (not when imported)
if __name__ == "__main__":
    main()
