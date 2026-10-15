"""
species.py

Builds the species card for GET /api/species/{key}.

Where each part comes from:
    name, taxonomy, record counts, extent, image, top datasets
        species_card_gbif.parquet, one row per species after the key
        merge (so every count is by specieskey after the merge)
    conservation status and English name
        bcsee_status_by_gbif_species.parquet, joined on gbif_species_key

A key that was merged into another (listed in a card's merged_keys) gets
the kept key's card, with merged_from saying which key was asked for.

The BCSEE block's state is one of:
    listed              BCSEE gives the species at least one shown status
                        (Red, Blue, Yellow, Exotic)
    in_bcsee_no_status  the species has BCSEE entries, none with a shown
                        status (Not Reviewed, Unknown, ...)
    not_in_bcsee        the species has no BCSEE entry at all
    unknown             the species has no row in the BCSEE file, and the
                        file leaves out species without a shown status, so
                        it cannot say which of the two cases above applies.
                        This is what the current file gives for every
                        species without a shown status.
Every state carries the BCSEE source, its date and the date it was pulled,
so "not in BCSEE" is always "not in BCSEE as of that export".
"""

from api.db import SHOWN_STATUSES
from api.images import card_image

# The card columns the endpoint reads, in the order the query returns them.
CARD_COLUMNS = [
    "specieskey", "scientificname", "col_name", "col_authorship", "name_source",
    "col_release", "kingdom", "phylum", "class", "order", "family", "genus",
    "record_count", "records_no_year", "first_year", "last_year", "n_datasets",
    "min_lat", "max_lat", "min_lon", "max_lon",
    "image_identifier", "image_references", "image_creator", "image_rightsholder",
    "image_license_code", "top_datasets", "merged_keys", "merged_record_count",
]

# The BCSEE columns the endpoint reads. bcsee_key is NULL when the species
# has no BCSEE row.
BCSEE_COLUMNS = ["bcsee_key", "statuses", "entries", "has_separate_bcsee_species"]

# One query for the card and its BCSEE row. "order" is quoted because it
# is a SQL word.
SPECIES_QUERY = f"""
    SELECT {", ".join(f'c."{column}"' for column in CARD_COLUMNS)},
           b.gbif_species_key AS bcsee_key, b.statuses, b.entries,
           b.has_separate_bcsee_species
    FROM species_card AS c
    LEFT JOIN bcsee_status AS b ON b.gbif_species_key = c.specieskey
    WHERE c.specieskey = ?
"""


def resolve_key(key, merged_keys):
    """Return (card_key, merged_from) for a requested key: the key whose
    card to show, and the old key that was asked for if it was merged
    (otherwise None). merged_keys is the old-key-to-kept-key dict made
    when the API starts."""
    if key in merged_keys:
        return merged_keys[key], key
    return key, None


def read_species_row(cursor, card_key):
    """Return the card and BCSEE columns for one key as a dict, or None if
    there is no card with that key."""
    row = cursor.execute(SPECIES_QUERY, [card_key]).fetchone()
    if row is None:
        return None
    return dict(zip(CARD_COLUMNS + BCSEE_COLUMNS, row))


def bcsee_state(row, includes_without_status):
    """Work out the BCSEE state of a species (see the top of this file):
    listed, in_bcsee_no_status, not_in_bcsee or unknown."""
    if row["bcsee_key"] is None:
        return "not_in_bcsee" if includes_without_status else "unknown"
    if any(status in SHOWN_STATUSES for status in row["statuses"] or []):
        return "listed"
    return "in_bcsee_no_status"


def english_name(entries):
    """Return the species' English name from BCSEE: the common name of its
    species-level BCSEE entry. Subspecies and population names ("White
    Sturgeon (Upper Kootenay River Population)") are not names of the
    species, so a species with no species-level entry has none (None)."""
    for entry in entries or []:
        if entry["is_species_level"] and (entry["common_label"] or "").strip():
            return entry["common_label"].strip()
    return None


def bcsee_block(row, includes_without_status, source):
    """Build the conservation status block. source holds status_source,
    status_source_date and status_pulled_on, from /api/meta.

    Each entry repeats the source and dates, so no status can ever be shown
    without them. scientific_label_source says where the entry's scientific
    label came from ("GBIF", or "BCSEE" when GBIF has no entry at that
    rank); it is called name_source in the BCSEE file, renamed here so it
    is not mixed up with the card's own name_source."""
    entries = [
        {
            "status": entry["status"],
            "scientific_label": entry["scientific_label"],
            "scientific_label_source": entry["name_source"],
            "common_label": entry["common_label"] or None,
            "is_species_level": entry["is_species_level"],
            "is_gbif_synonym": entry["is_gbif_synonym"],
            "element_codes": entry["element_codes"],
            **source,
        }
        for entry in row["entries"] or []
    ]

    return {
        "state": bcsee_state(row, includes_without_status),
        **source,
        "statuses": row["statuses"] or [],
        "has_separate_bcsee_species": bool(row["has_separate_bcsee_species"]),
        "entries": entries,
    }


def build_species_card(data, key):
    """Return the species card for a requested key, or None if the key is
    neither a card key nor a key merged into one. data is the ApiData."""
    card_key, merged_from = resolve_key(key, data.merged_keys)
    row = read_species_row(data.cursor(), card_key)
    if row is None:
        return None

    statuses = data.meta["statuses"]
    source = {
        "status_source": statuses["status_source"],
        "status_source_date": statuses["status_source_date"],
        "status_pulled_on": statuses["status_pulled_on"],
    }

    return {
        "specieskey": row["specieskey"],
        "requested_key": key,
        "merged_from": merged_from,
        "name": {
            "col_name": row["col_name"],
            "col_authorship": row["col_authorship"],
            "scientificname": row["scientificname"],
            "name_source": row["name_source"],
            "col_release": row["col_release"],
        },
        "english_name": english_name(row["entries"]),
        "english_name_source": source["status_source"],
        "taxonomy": {rank: row[rank] for rank in
                     ("kingdom", "phylum", "class", "order", "family", "genus")},
        "records": {
            "record_count": row["record_count"],
            "records_no_year": row["records_no_year"],
            "first_year": row["first_year"],
            "last_year": row["last_year"],
            "n_datasets": row["n_datasets"],
            "merged_keys": row["merged_keys"] or [],
            "merged_record_count": row["merged_record_count"],
        },
        # The furthest records north, south, east and west, as stored. Not
        # coarsened for any species: sensitive-species handling is not
        # decided yet, and the API refuses to start if it is asked for.
        "extent": {edge: row[edge] for edge in
                   ("min_lat", "max_lat", "min_lon", "max_lon")},
        "image": card_image(row),
        "top_datasets": row["top_datasets"] or [],
        "bcsee": bcsee_block(row, data.bcsee_includes_without_status, source),
    }
