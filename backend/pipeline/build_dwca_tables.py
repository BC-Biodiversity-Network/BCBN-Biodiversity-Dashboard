"""
build_dwca_tables.py

Turn a GBIF Darwin Core Archive download (the zip GBIF emails you) into the
tables the dashboard reads: occurrences, media, datasets and one image per
species.

Input:  <download key>.zip   (a GBIF "Darwin Core Archive" download:
                              occurrence.txt, multimedia.txt, dataset/*.xml,
                              metadata.xml)
Output, all in --outdir:
    bc_occurrence.parquet     one row per record. The same filters and exact
                              BC boundary clip as build_bc_clean.py, the 48
                              bc_clean columns that exist in a download (same
                              names and types as bc_clean), plus `references`
                              and the H3 cell at resolutions 4-7.
    bc_media.parquet          one row per media item (image, sound, video) of
                              a record kept in bc_occurrence, with a short
                              license_code parsed from the license value.
    bc_datasets.parquet       one row per dataset in the archive: title,
                              citation, license, DOI, publisher and how many
                              records it has in bc_occurrence.
    bc_species_image.parquet  one openly licensed image per species.
    download_info.json        download key, DOI, date and query.

How the occurrence table is built:
  1. occurrence.txt is copied to a temporary parquet with the bc_clean column
     names and types. The ";"-joined fields become lists, as in bc_clean, and
     the dates become timestamps the same way the GBIF snapshot does it.
     (bc_clean's lastinterpreted is 9999-12-31 on every row, a placeholder
     from the snapshot; here it is the real date from the download.)
  2. That file is passed to build_bc_clean.build_clean(), so the filters and
     the boundary clip are the exact same code that makes bc_clean.
  3. The H3 cells are computed straight from the coordinates, the same way
     build_hex_aggregates.py bins records, once per resolution.

DuckDB cannot read inside a zip, so occurrence.txt and multimedia.txt are
extracted to a temporary folder first (--workdir picks where; a full BC
download needs tens of GB there).

Run:
    python build_dwca_tables.py --zip ~/bcbn/data/0009659-260928105237408.zip --outdir ~/bcbn/data/dwca
"""

import argparse
import json
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import requests

from build_bc_clean import (
    GEO_ISSUES,
    build_clean,
    get_bc_geometry,
    write_boundary_parquet,
)


# The 48 bc_clean columns that a download contains, in bc_clean's order, with
# bc_clean's types. "LIST" means a ";"-joined field in the download that
# bc_clean holds as VARCHAR[]. bc_clean's other two columns,
# verbatimscientificnameauthorship and publishingorgkey, are not in a
# Darwin Core Archive download.
OCC_COLUMNS = [
    ("gbifid", "VARCHAR"),
    ("datasetkey", "VARCHAR"),
    ("occurrenceid", "VARCHAR"),
    ("kingdom", "VARCHAR"),
    ("phylum", "VARCHAR"),
    ("class", "VARCHAR"),
    ("order", "VARCHAR"),
    ("family", "VARCHAR"),
    ("genus", "VARCHAR"),
    ("species", "VARCHAR"),
    ("infraspecificepithet", "VARCHAR"),
    ("taxonrank", "VARCHAR"),
    ("scientificname", "VARCHAR"),
    ("verbatimscientificname", "VARCHAR"),
    ("countrycode", "VARCHAR"),
    ("locality", "VARCHAR"),
    ("stateprovince", "VARCHAR"),
    ("occurrencestatus", "VARCHAR"),
    ("individualcount", "INTEGER"),
    ("decimallatitude", "DOUBLE"),
    ("decimallongitude", "DOUBLE"),
    ("coordinateuncertaintyinmeters", "DOUBLE"),
    ("coordinateprecision", "DOUBLE"),
    ("elevation", "DOUBLE"),
    ("elevationaccuracy", "DOUBLE"),
    ("depth", "DOUBLE"),
    ("depthaccuracy", "DOUBLE"),
    ("eventdate", "TIMESTAMP"),
    ("day", "INTEGER"),
    ("month", "INTEGER"),
    ("year", "INTEGER"),
    ("taxonkey", "VARCHAR"),
    ("specieskey", "VARCHAR"),
    ("basisofrecord", "VARCHAR"),
    ("institutioncode", "VARCHAR"),
    ("collectioncode", "VARCHAR"),
    ("catalognumber", "VARCHAR"),
    ("recordnumber", "VARCHAR"),
    ("identifiedby", "LIST"),
    ("dateidentified", "TIMESTAMP"),
    ("license", "VARCHAR"),
    ("rightsholder", "VARCHAR"),
    ("recordedby", "LIST"),
    ("typestatus", "LIST"),
    ("establishmentmeans", "VARCHAR"),
    ("lastinterpreted", "TIMESTAMP"),
    ("mediatype", "LIST"),
    ("issue", "LIST"),
]

# Extra download columns added after the bc_clean ones. `publisher` is only
# kept long enough to build bc_datasets; it is not written to bc_occurrence.
EXTRA_COLUMNS = ["references"]
STAGING_ONLY_COLUMNS = ["publisher"]

# H3 resolutions stored on every record: the province (4), regional (5),
# local (6) and closest (7) map tiers of build_hex_aggregates.py.
H3_RESOLUTIONS = [4, 5, 6, 7]

# Columns of bc_media, in order. Names on the right are multimedia.txt's.
MEDIA_COLUMNS = [
    ("type", "type"),
    ("format", "format"),
    ("identifier", "identifier"),
    ("references", "references"),
    ("title", "title"),
    ("creator", "creator"),
    ("rightsholder", "rightsHolder"),
    ("license", "license"),
    ("created", "created"),
]

# A GBIF download key looks like 0009659-260928105237408.
DOWNLOAD_KEY_PATTERN = re.compile(r"^\d{7}-\d{15}$")

# A download DOI, e.g. 10.15468/dl.abc123.
DOWNLOAD_DOI_PATTERN = re.compile(r"10\.15468/dl\.[a-z0-9]+", re.IGNORECASE)

GBIF_DOWNLOAD_API = "https://api.gbif.org/v1/occurrence/download/"


def download_key_from_zip(zip_path):
    """Return the GBIF download key from the zip's file name, or stop if
    the name does not look like one."""
    key = os.path.basename(zip_path)
    if key.lower().endswith(".zip"):
        key = key[:-4]
    if not DOWNLOAD_KEY_PATTERN.match(key):
        raise ValueError(
            f"Zip name {os.path.basename(zip_path)!r} is not a GBIF download "
            f"key (expected something like 0009659-260928105237408.zip)"
        )
    return key


def extract_member(archive, member, out_dir):
    """Extract one file from the archive into out_dir and return its path,
    or None if the archive does not contain it."""
    if member not in archive.namelist():
        return None
    return archive.extract(member, out_dir)


def read_header(txt_path):
    """Return the column names on the first line of a tab-separated file."""
    with open(txt_path, encoding="utf-8") as f:
        return f.readline().rstrip("\r\n").split("\t")


def connect():
    """Open a duckdb connection with the h3 and spatial extensions loaded,
    and a macro that turns GBIF's date text into a timestamp."""
    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    con.execute("INSTALL spatial; LOAD spatial;")
    # GBIF writes dates as text in several shapes: "1934", "1965-09",
    # "2020-09-10T09:05:43Z", or a range like "1988-07-04/1988-07-09". The
    # GBIF snapshot, and so bc_clean, stores the start of a range, without
    # the "Z", with a missing month or day filled in as 01. Checked against
    # bc_clean on the 175,347 records this test download shares with it.
    con.execute(
        """
        CREATE MACRO gbif_timestamp(txt) AS (
            CASE length(rtrim(split_part(txt, '/', 1), 'Z'))
                WHEN 4 THEN rtrim(split_part(txt, '/', 1), 'Z') || '-01-01'
                WHEN 7 THEN rtrim(split_part(txt, '/', 1), 'Z') || '-01'
                ELSE rtrim(split_part(txt, '/', 1), 'Z')
            END
        )::TIMESTAMP
        """
    )
    return con


def column_sql(name, sql_type, source):
    """Return the SQL that turns one occurrence.txt text column into the
    bc_clean column `name` of type `sql_type`."""
    src = f'"{source}"'
    if sql_type == "LIST":
        # GBIF joins list items with a bare ";". A "; " with a space is part
        # of one item, e.g. the single recordedBy "Robert Forsyth; Tammy
        # Forsyth", which bc_clean also holds as one item. So "; " is hidden
        # behind a placeholder (chr(1)) while splitting, then put back.
        # string_split keeps NULL as NULL, which matches bc_clean, where an
        # empty list field is NULL.
        expr = (
            f"list_transform(string_split(replace({src}, '; ', chr(1)), ';'), "
            f"item -> replace(item, chr(1), '; '))"
        )
    elif sql_type == "TIMESTAMP":
        expr = f"gbif_timestamp({src})"
    elif sql_type == "VARCHAR":
        expr = src
    else:
        # A plain CAST (not TRY_CAST) so a malformed number stops the run
        # instead of quietly turning into NULL.
        expr = f"CAST({src} AS {sql_type})"
    return f'{expr} AS "{name}"'


def stage_occurrence(con, occ_txt, out_path):
    """Copy occurrence.txt to a parquet with bc_clean's column names and
    types, plus references, the H3 cells and publisher. Returns the number
    of rows."""
    header = read_header(occ_txt)
    by_lower = {h.lower(): h for h in header}
    wanted = [name for name, _ in OCC_COLUMNS] + EXTRA_COLUMNS + STAGING_ONLY_COLUMNS
    missing = [name for name in wanted if name not in by_lower]
    if missing:
        raise RuntimeError(f"occurrence.txt is missing columns: {missing}")

    select = [column_sql(name, t, by_lower[name]) for name, t in OCC_COLUMNS]
    select += [f'"{by_lower[name]}" AS "{name}"' for name in EXTRA_COLUMNS]
    # Each resolution is binned straight from the coordinates, as in
    # build_hex_aggregates.py (never rolled up from a finer cell). Records
    # without coordinates get NULL and are removed by the filters anyway.
    for r in H3_RESOLUTIONS:
        select.append(
            f"h3_h3_to_string(h3_latlng_to_cell("
            f"CAST(\"{by_lower['decimallatitude']}\" AS DOUBLE), "
            f"CAST(\"{by_lower['decimallongitude']}\" AS DOUBLE), {r})) AS h3_r{r}"
        )
    select += [f'"{by_lower[name]}" AS "{name}"' for name in STAGING_ONLY_COLUMNS]

    # GBIF's text files are tab-separated with no quoting at all, so quote
    # and escape are switched off: a stray " inside a locality must not be
    # read as the start of a quoted field. Everything is read as text and
    # converted above. Empty fields come in as NULL.
    con.execute(
        f"""
        COPY (
            SELECT {", ".join(select)}
            FROM read_csv('{occ_txt}', delim='\t', header=true, quote='',
                          escape='', all_varchar=true)
        ) TO '{out_path}' (FORMAT PARQUET)
        """
    )
    return con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]


def removal_breakdown(con, staged_path, boundary_path):
    """Count the staged records by the first filter of build_bc_clean.py
    that removes them, or 'kept'.

    This is a report only: the records are actually filtered by
    build_clean(). The 'kept' count is checked against its result, so this
    breakdown cannot drift from the real filter without the run stopping.
    The NULL cases are listed separately because build_clean's SQL drops a
    row when a condition is NULL, not only when it is false.
    """
    geo_issues_sql = "[" + ", ".join(f"'{issue}'" for issue in GEO_ISSUES) + "]"
    return con.execute(
        f"""
        SELECT reason, count(*) AS n
        FROM (
            SELECT CASE
                WHEN occ.decimallatitude IS NULL OR occ.decimallongitude IS NULL
                    THEN 'no coordinates'
                WHEN NOT ST_Contains(bc.geom, ST_Point(occ.decimallongitude, occ.decimallatitude))
                    THEN 'outside the exact BC boundary'
                WHEN occ.occurrencestatus IS DISTINCT FROM 'PRESENT'
                    THEN 'occurrencestatus not PRESENT'
                WHEN occ.basisofrecord IS NULL
                    THEN 'basisofrecord missing'
                WHEN occ.basisofrecord IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN')
                    THEN 'fossil or living specimen'
                WHEN occ.species IS NULL
                    THEN 'no species-level id'
                WHEN list_has_any(coalesce(occ.issue, []::VARCHAR[]), {geo_issues_sql})
                    THEN 'coordinate-quality issue'
                ELSE 'kept'
            END AS reason
            FROM read_parquet('{staged_path}') AS occ
            CROSS JOIN (
                SELECT ST_GeomFromText(geom_wkt) AS geom
                FROM read_parquet('{boundary_path}')
            ) AS bc
        )
        GROUP BY reason
        ORDER BY n DESC
        """
    ).fetchall()


def write_occurrence(con, kept_path, out_path):
    """Write bc_occurrence.parquet from the filtered records, leaving out
    the staging-only columns."""
    columns = (
        [name for name, _ in OCC_COLUMNS]
        + EXTRA_COLUMNS
        + [f"h3_r{r}" for r in H3_RESOLUTIONS]
    )
    select = ", ".join(f'"{c}"' for c in columns)
    con.execute(
        f"""COPY (SELECT {select} FROM read_parquet('{kept_path}'))
            TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"""
    )


def stage_multimedia(txt_path, out_path):
    """Copy multimedia.txt to parquet with every column as text, adding
    file_row, the line's position in the file (0 = first data line).
    Returns the number of rows.

    Read with pyarrow's streaming reader, which hands the lines over in
    file order, so file_row is exactly the file order that media_order is
    based on.
    """
    header = read_header(txt_path)
    parse_options = pacsv.ParseOptions(
        delimiter="\t", quote_char=False, escape_char=False
    )
    # Only an empty field is NULL. pyarrow would otherwise also read text
    # like "NA" or "null" (which could be a real title) as NULL.
    convert_options = pacsv.ConvertOptions(
        column_types={c: pa.string() for c in header},
        null_values=[""],
        strings_can_be_null=True,
    )
    reader = pacsv.open_csv(
        txt_path,
        read_options=pacsv.ReadOptions(block_size=64 << 20),
        parse_options=parse_options,
        convert_options=convert_options,
    )

    schema = pa.schema([(c, pa.string()) for c in header] + [("file_row", pa.int64())])
    n_rows = 0
    with pq.ParquetWriter(out_path, schema) as writer:
        for batch in reader:
            file_row = pa.array(range(n_rows, n_rows + batch.num_rows), pa.int64())
            table = pa.Table.from_batches([batch]).append_column("file_row", file_row)
            writer.write_table(table)
            n_rows += batch.num_rows
    return n_rows


def write_empty_multimedia(out_path):
    """Write an empty staged multimedia file, for an archive that has no
    multimedia.txt."""
    names = ["gbifID"] + [source for _, source in MEDIA_COLUMNS] + ["file_row"]
    types = [pa.string()] * (len(names) - 1) + [pa.int64()]
    pq.write_table(pa.schema(list(zip(names, types))).empty_table(), out_path)


def parse_license_code(value):
    """Return a short standard code (CC0_1_0, CC_BY_4_0, CC_BY_NC_SA_3_0,
    PDM_1_0, ...) for a license value, or None if it can't be read.

    Reads Creative Commons URLs ("http://creativecommons.org/licenses/by/4.0/"),
    short forms anywhere in free text ("Anna Feng (cc-by-sa)",
    "CC BY-NC 4.0") and spelled-out names ("Creative Commons Attribution
    NonCommercial 4.0"). When the value names no version, as in
    "(cc-by-sa)", the code has no version either (CC_BY_SA), rather than
    guessing one.
    """
    if not value:
        return None
    text = value.strip().lower()

    # CC0 and the Public Domain Mark have a single version, 1.0.
    if "publicdomain/zero" in text or re.search(r"\bcc[\s_-]?0\b", text):
        return "CC0_1_0"
    if "publicdomain/mark" in text:
        return "PDM_1_0"

    url = re.search(r"creativecommons\.org/licenses/([a-z-]+)/(\d+(?:\.\d+)?)", text)
    short = re.search(
        r"\bcc[\s_-]+(by(?:[\s_-]+(?:nc|sa|nd))*)\b(?:[\s_-]+v?(\d+(?:\.\d+)?))?", text
    )
    if url:
        parts = url.group(1).split("-")
        version = url.group(2)
    elif short:
        parts = re.split(r"[\s_-]+", short.group(1))
        version = short.group(2)
    elif "creative commons" in text and "attribution" in text:
        parts = ["by"]
        if re.search(r"non[\s-]?commercial", text):
            parts.append("nc")
        if re.search(r"share[\s-]?alike", text):
            parts.append("sa")
        if re.search(r"no[\s-]?deriv", text):
            parts.append("nd")
        found = re.search(r"\b(\d\.\d)\b", text)
        version = found.group(1) if found else None
    else:
        return None

    # A real CC license is BY plus any of NC, SA, ND, never both SA and ND.
    if parts[0] != "by" or not set(parts[1:]) <= {"nc", "sa", "nd"}:
        return None
    if "sa" in parts and "nd" in parts:
        return None

    # Write the parts in the standard order (BY_NC_SA, not BY_SA_NC).
    code = "CC_" + "_".join(["BY"] + [p.upper() for p in ("nc", "sa", "nd") if p in parts])
    if version:
        if "." not in version:
            version += ".0"
        code += "_" + version.replace(".", "_")
    return code


def build_media(con, staged_media_path, occurrence_path, out_path):
    """Write bc_media.parquet: the media items of records kept in
    bc_occurrence, numbered within each record in file order, with a
    license_code."""
    # The license values repeat a lot, so each distinct value is parsed
    # once in Python and joined back on.
    licenses = [
        row[0]
        for row in con.execute(
            f"""SELECT DISTINCT license FROM read_parquet('{staged_media_path}')
                WHERE license IS NOT NULL"""
        ).fetchall()
    ]
    license_table = pa.table(
        {
            "license": pa.array(licenses, pa.string()),
            "license_code": pa.array([parse_license_code(v) for v in licenses], pa.string()),
        }
    )
    con.register("license_codes", license_table)

    renamed = ", ".join(f'"{source}" AS "{name}"' for name, source in MEDIA_COLUMNS)
    # Every output column is named explicitly: duckdb names are not case
    # sensitive, so without an alias "gbifID" would keep its original case.
    select = ", ".join(f'm."{name}" AS "{name}"' for name, _ in MEDIA_COLUMNS)
    # media_order is numbered over all of a record's items before any are
    # dropped. Only whole records are dropped, so this is the same as
    # numbering after.
    con.execute(
        f"""
        COPY (
            SELECT m.gbifid AS gbifid, m.media_order AS media_order, {select},
                   lc.license_code AS license_code
            FROM (
                SELECT "gbifID" AS gbifid, file_row, {renamed},
                       CAST(row_number() OVER (PARTITION BY "gbifID" ORDER BY file_row)
                            AS INTEGER) AS media_order
                FROM read_parquet('{staged_media_path}')
            ) AS m
            LEFT JOIN license_codes AS lc ON lc.license = m.license
            WHERE m.gbifid IN (SELECT gbifid FROM read_parquet('{occurrence_path}'))
            ORDER BY m.file_row
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    con.unregister("license_codes")


def clean_text(element):
    """Return an XML element's text with runs of whitespace collapsed to
    one space, or None if the element is missing or empty."""
    if element is None:
        return None
    text = " ".join("".join(element.itertext()).split())
    return text or None


def normalise_doi(value):
    """Return a bare DOI ("10.15468/qqawta") from a DOI written as
    "doi:...", "https://doi.org/..." or bare, or None if value isn't one."""
    if not value:
        return None
    found = re.search(r"\b(10\.\d{4,9}/\S+)", value.strip())
    return found.group(1).rstrip(".") if found else None


def read_dataset_eml(xml_bytes):
    """Return the datasetkey, title, citation, license and DOI from one
    dataset/*.xml (EML) file of the archive."""
    root = ET.fromstring(xml_bytes)
    dataset = root.find("dataset")

    # The license is in <licensed> on most datasets; older metadata only
    # links it from the <intellectualRights> text.
    license_value = None
    licensed = dataset.find("licensed")
    if licensed is not None:
        license_value = clean_text(licensed.find("identifier")) or clean_text(
            licensed.find("url")
        )
    if license_value is None:
        rights = dataset.find("intellectualRights")
        if rights is not None:
            link = rights.find(".//ulink")
            license_value = link.get("url") if link is not None else clean_text(rights)

    citation = clean_text(root.find("additionalMetadata/metadata/gbif/citation"))

    # The DOI is one of the <alternateIdentifier>s. If none is a DOI, fall
    # back to the doi.org link in the citation.
    doi = None
    for alt in dataset.findall("alternateIdentifier"):
        doi = normalise_doi(clean_text(alt))
        if doi:
            break
    if doi is None and citation:
        found = re.search(r"doi\.org/(10\.\S+)", citation)
        doi = normalise_doi(found.group(1)) if found else None

    return {
        "datasetkey": root.get("packageId"),
        "title": clean_text(dataset.find("title")),
        "citation": citation,
        "license": license_value,
        "doi": doi,
    }


def build_datasets(con, archive, staged_path, occurrence_path, out_path):
    """Write bc_datasets.parquet: one row per dataset in the archive's
    dataset/ folder (plus any dataset that has records but no metadata
    file), with its publisher and record count in bc_occurrence.

    Returns a list of (datasetkey, publishers) for datasets whose records
    name more than one publisher, so the summary can report them.
    """
    emls = [
        read_dataset_eml(archive.read(name))
        for name in archive.namelist()
        if name.startswith("dataset/") and name.endswith(".xml")
    ]
    con.register("emls", pa.Table.from_pylist(
        emls,
        schema=pa.schema([(c, pa.string()) for c in
                          ["datasetkey", "title", "citation", "license", "doi"]]),
    ))

    # Publisher is taken from all of a dataset's records in the download,
    # before filtering, so datasets with nothing left in BC still get one.
    # A dataset should have a single publisher; if not, the most common one
    # is kept and the dataset is reported.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE publishers AS
        SELECT datasetkey, publisher, count(*) AS n
        FROM read_parquet('{staged_path}')
        GROUP BY ALL
        """
    )
    con.execute(
        f"""
        COPY (
            WITH pub AS (
                SELECT datasetkey, publisher FROM publishers
                WHERE publisher IS NOT NULL
                QUALIFY row_number() OVER (
                    PARTITION BY datasetkey ORDER BY n DESC, publisher) = 1
            ),
            counts AS (
                SELECT datasetkey, count(*) AS bc_record_count
                FROM read_parquet('{occurrence_path}')
                GROUP BY datasetkey
            ),
            keys AS (
                SELECT datasetkey FROM emls
                UNION
                SELECT DISTINCT datasetkey FROM publishers
            )
            SELECT k.datasetkey, e.title, e.citation, e.license, e.doi,
                   pub.publisher,
                   coalesce(c.bc_record_count, 0)::BIGINT AS bc_record_count
            FROM keys AS k
            LEFT JOIN emls AS e USING (datasetkey)
            LEFT JOIN pub USING (datasetkey)
            LEFT JOIN counts AS c USING (datasetkey)
            ORDER BY bc_record_count DESC, k.datasetkey
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    con.unregister("emls")

    return con.execute(
        """SELECT datasetkey, list(publisher ORDER BY n DESC)
           FROM publishers WHERE publisher IS NOT NULL
           GROUP BY datasetkey HAVING count(*) > 1"""
    ).fetchall()


# The licenses an image may have to be chosen as a species image, best
# first. Any version counts, including no stated version.
SPECIES_IMAGE_LICENSES_SQL = """
    CASE
        WHEN regexp_full_match(m.license_code, 'CC0(_[0-9]+_[0-9]+)?') THEN 1
        WHEN regexp_full_match(m.license_code, 'CC_BY(_[0-9]+_[0-9]+)?') THEN 2
        WHEN regexp_full_match(m.license_code, 'CC_BY_NC(_[0-9]+_[0-9]+)?') THEN 3
    END
"""


def build_species_image(con, occurrence_path, media_path, out_path):
    """Write bc_species_image.parquet: for each specieskey, one StillImage
    licensed CC0, CC BY or CC BY-NC.

    Prefers CC0, then CC BY, then CC BY-NC; among those, the record with
    the most recent eventdate (records with no date last), then the lowest
    gbifid and media_order.
    """
    con.execute(
        f"""
        COPY (
            SELECT specieskey, species, gbifid, media_order, identifier,
                   "references", creator, rightsholder, license_code, datasetkey
            FROM (
                SELECT o.specieskey, o.species, m.gbifid, m.media_order,
                       m.identifier, m."references", m.creator, m.rightsholder,
                       m.license_code, o.datasetkey, o.eventdate,
                       {SPECIES_IMAGE_LICENSES_SQL} AS license_rank
                FROM read_parquet('{media_path}') AS m
                JOIN read_parquet('{occurrence_path}') AS o USING (gbifid)
                WHERE m.type = 'StillImage' AND o.specieskey IS NOT NULL
            )
            WHERE license_rank IS NOT NULL
            QUALIFY row_number() OVER (
                PARTITION BY specieskey
                ORDER BY license_rank, eventdate DESC NULLS LAST,
                         CAST(gbifid AS BIGINT), media_order
            ) = 1
            ORDER BY species
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def species_without_image(con, occurrence_path, media_path, species_image_path):
    """Return how many species with records got no image, split by why:
    no StillImage at all, or only StillImages with other (or no)
    licenses."""
    return con.execute(
        f"""
        WITH species AS (
            SELECT DISTINCT specieskey FROM read_parquet('{occurrence_path}')
            WHERE specieskey IS NOT NULL
        ),
        has_still AS (
            SELECT DISTINCT o.specieskey
            FROM read_parquet('{media_path}') AS m
            JOIN read_parquet('{occurrence_path}') AS o USING (gbifid)
            WHERE m.type = 'StillImage'
        )
        SELECT
            CASE WHEN h.specieskey IS NULL THEN 'no images at all'
                 ELSE 'only images with other or no licenses' END AS reason,
            count(*)
        FROM species AS s
        LEFT JOIN has_still AS h USING (specieskey)
        WHERE s.specieskey NOT IN (
            SELECT specieskey FROM read_parquet('{species_image_path}'))
        GROUP BY reason
        ORDER BY reason
        """
    ).fetchall()


def read_download_metadata(archive):
    """Return the archive date, human-readable query and any download DOI
    found in the archive's metadata.xml, citations.txt and rights.txt."""
    root = ET.fromstring(archive.read("metadata.xml"))
    date = clean_text(root.find("additionalMetadata/metadata/gbif/dateStamp"))

    # The query is the JSON-like block in the abstract, between "matching
    # the query:" and the list of datasets.
    query = None
    abstract = "".join(root.find("dataset/abstract").itertext())
    found = re.search(r"matching the query:\s*(\{.*?\n\})", abstract, re.DOTALL)
    if found:
        try:
            query = json.loads(found.group(1))
        except json.JSONDecodeError:
            query = found.group(1)

    doi = None
    for name in ("metadata.xml", "citations.txt", "rights.txt"):
        if name in archive.namelist():
            match = DOWNLOAD_DOI_PATTERN.search(archive.read(name).decode("utf-8", "replace"))
            if match:
                doi = match.group(0).lower()
                break
    return date, query, doi


def fetch_download_api(key):
    """Return GBIF's download API record for this key, or None if the API
    can't be reached."""
    try:
        resp = requests.get(GBIF_DOWNLOAD_API + key, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as err:
        print(f"WARNING: could not read the GBIF download API for {key}: {err}")
        return None


def write_download_info(archive, key, zip_path, out_path):
    """Write download_info.json: the download key, DOI, date and query.
    The DOI comes from the archive if it has one, otherwise from the GBIF
    download API. Returns the dict written."""
    date, query, doi = read_download_metadata(archive)
    doi_source = "archive" if doi else None

    api = fetch_download_api(key)
    if doi is None and api and api.get("doi"):
        doi = api["doi"].lower()
        doi_source = "gbif_download_api"

    info = {
        "download_key": key,
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}" if doi else None,
        "doi_source": doi_source,
        # When GBIF built the archive, from metadata.xml.
        "date": date,
        # The query as GBIF wrote it in metadata.xml.
        "query": query,
        # The machine-readable query and other details, from the API.
        "predicate": (api or {}).get("request", {}).get("predicate"),
        "created": (api or {}).get("created"),
        "total_records": (api or {}).get("totalRecords"),
        "license": (api or {}).get("license"),
        "source_zip": os.path.basename(zip_path),
    }
    if doi:
        info["citation"] = (
            f"GBIF.org ({(date or '')[:10]}) GBIF Occurrence Download "
            f"https://doi.org/{doi}"
        )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return info


def count(con, path):
    """Return the number of rows in a parquet file."""
    return con.execute(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]


def print_media_licenses(con, media_path):
    """Print how many media items have each license_code, and the original
    license values that could not be read."""
    print("\nbc_media license_code counts:")
    for code, n in con.execute(
        f"""SELECT coalesce(license_code, '(empty)'), count(*)
            FROM read_parquet('{media_path}') GROUP BY 1 ORDER BY 2 DESC"""
    ).fetchall():
        print(f"  {code:<20} {n:>10,}")

    unread = con.execute(
        f"""SELECT license, count(*) FROM read_parquet('{media_path}')
            WHERE license IS NOT NULL AND license_code IS NULL
            GROUP BY 1 ORDER BY 2 DESC"""
    ).fetchall()
    print(f"License values that could not be read ({len(unread)}):")
    for value, n in unread:
        print(f"  {n:>6,}  {value}")


def main():
    """Build every table from one GBIF Darwin Core Archive download."""
    parser = argparse.ArgumentParser(
        description="Build the BC occurrence, media, dataset and species image "
        "tables from a GBIF Darwin Core Archive download."
    )
    parser.add_argument("--zip", required=True, help="The GBIF download zip.")
    parser.add_argument("--outdir", required=True, help="Directory to write the tables into.")
    parser.add_argument(
        "--workdir",
        default=None,
        help="Where to put the temporary extracted files (default: the system "
        "temp folder).",
    )
    args = parser.parse_args()

    key = download_key_from_zip(args.zip)
    os.makedirs(args.outdir, exist_ok=True)
    occurrence_path = os.path.join(args.outdir, "bc_occurrence.parquet")
    media_path = os.path.join(args.outdir, "bc_media.parquet")
    datasets_path = os.path.join(args.outdir, "bc_datasets.parquet")
    species_image_path = os.path.join(args.outdir, "bc_species_image.parquet")
    info_path = os.path.join(args.outdir, "download_info.json")

    print(f"Download: {key}")
    print(f"Zip:      {args.zip}")
    print(f"Output:   {args.outdir}\n")
    t_all = time.time()

    geometry = get_bc_geometry()
    con = connect()

    with zipfile.ZipFile(args.zip) as archive, \
            tempfile.TemporaryDirectory(dir=args.workdir) as tmpdir:
        print("\nExtracting occurrence.txt and multimedia.txt...")
        occ_txt = extract_member(archive, "occurrence.txt", tmpdir)
        if occ_txt is None:
            raise RuntimeError("The archive has no occurrence.txt")
        media_txt = extract_member(archive, "multimedia.txt", tmpdir)

        boundary_path = os.path.join(tmpdir, "bc_boundary.parquet")
        staged_path = os.path.join(tmpdir, "occurrence_staged.parquet")
        kept_path = os.path.join(tmpdir, "occurrence_kept.parquet")
        staged_media_path = os.path.join(tmpdir, "multimedia_staged.parquet")

        write_boundary_parquet(con, geometry, boundary_path)

        print("Converting occurrence.txt to bc_clean's columns and types...")
        n_staged = stage_occurrence(con, occ_txt, staged_path)
        print(f"Records in the download: {n_staged:,}")

        # The real filter: build_bc_clean's own function.
        n_kept = build_clean(staged_path, boundary_path, kept_path)
        breakdown = removal_breakdown(con, staged_path, boundary_path)
        kept_in_breakdown = dict(breakdown).get("kept", 0)
        if kept_in_breakdown != n_kept:
            raise RuntimeError(
                f"Removal breakdown says {kept_in_breakdown:,} kept but "
                f"build_clean kept {n_kept:,}; the breakdown no longer matches "
                f"build_bc_clean.py's filter"
            )
        write_occurrence(con, kept_path, occurrence_path)

        print("Building bc_media...")
        if media_txt is None:
            print("The archive has no multimedia.txt; bc_media will be empty.")
            write_empty_multimedia(staged_media_path)
            n_media_all = 0
        else:
            n_media_all = stage_multimedia(media_txt, staged_media_path)
        build_media(con, staged_media_path, occurrence_path, media_path)

        print("Building bc_datasets...")
        multi_publisher = build_datasets(
            con, archive, staged_path, occurrence_path, datasets_path
        )

        print("Building bc_species_image...")
        build_species_image(con, occurrence_path, media_path, species_image_path)

        print("Writing download_info.json...")
        info = write_download_info(archive, key, args.zip, info_path)

    # Summary.
    n_occ = count(con, occurrence_path)
    n_media = count(con, media_path)
    n_datasets = count(con, datasets_path)
    n_species_image = count(con, species_image_path)
    n_species = con.execute(
        f"""SELECT count(DISTINCT specieskey) FROM read_parquet('{occurrence_path}')"""
    ).fetchone()[0]

    print("\nRecords by the first filter that removes them:")
    for reason, n in breakdown:
        print(f"  {reason:<42} {n:>10,}")
    print(f"  {'removed in total':<42} {n_staged - n_occ:>10,}")

    print(f"\nbc_occurrence:    {n_occ:>10,} records")
    print(f"bc_media:         {n_media:>10,} items "
          f"(of {n_media_all:,} in multimedia.txt)")
    print(f"bc_datasets:      {n_datasets:>10,} datasets")
    print(f"bc_species_image: {n_species_image:>10,} species "
          f"(of {n_species:,} species with records)")
    for reason, n in species_without_image(
        con, occurrence_path, media_path, species_image_path
    ):
        print(f"  no image, {reason}: {n:,}")

    print_media_licenses(con, media_path)

    for datasetkey, publishers in multi_publisher:
        print(f"WARNING: dataset {datasetkey} has several publishers: {publishers}")

    print(f"\nDOI: {info['doi']} (from {info['doi_source']})")
    print(f"\nDone in {time.time() - t_all:.1f}s")
    con.close()


if __name__ == "__main__":
    main()
