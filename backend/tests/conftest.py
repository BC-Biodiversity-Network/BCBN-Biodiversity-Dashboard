"""
conftest.py

Shared test set-up: writes a tiny copy of the API's four data files into a
temporary folder, so the tests never need the real (large, uncommitted)
data.

The test data:
    species AAAA  Turdus migratorius, 5 records (2 of them with no year),
                  with a BCSEE status
    species BBBB  Salix scouleriana, 3 records at one point, with old key
                  OLD1 merged into it, no image, not in the BCSEE file
    species CCCC  Bombus (Pyrobombus) vosnesenskii, a name with a
                  subgenus; its image has no creator, so it is not shown
    species DDDD  Carex test, with an ND-licensed image from a site other
                  than iNaturalist
    BCSEE row ZZZZ  a status for a species with no records in BC, so no
                    card (it must not be counted)

BCSEE_WITH_NO_STATUS_SQL is the BCSEE file as it would be if it also
listed species whose BCSEE entries have no shown status: it adds CCCC,
Not Reviewed.
"""

import json

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings

# The card table. Every column the API reads is here, with the types the
# pipeline writes.
CARD_SQL = """
    -- Written as 49.1, the coordinates would be DECIMAL; the real file has
    -- DOUBLE.
    SELECT * REPLACE (min_lat::DOUBLE AS min_lat, max_lat::DOUBLE AS max_lat,
                      min_lon::DOUBLE AS min_lon, max_lon::DOUBLE AS max_lon)
    FROM (VALUES
        ('AAAA', 'Turdus migratorius Linnaeus, 1766', 'Turdus migratorius',
         'Linnaeus, 1766', 'Catalogue of Life, via GBIF',
         'COL26.6 XR (ChecklistBank 315557)', 'Turdus migratorius',
         'Animalia', 'Chordata', 'Aves', 'Passeriformes', 'Turdidae', 'Turdus',
         5::BIGINT, 2::BIGINT, 2020, 2020, 1, 49.1, 49.2, -123.2, -123.1,
         'https://inaturalist-open-data.s3.amazonaws.com/photos/1/original.jpg',
         'https://www.inaturalist.org/photos/1', 'A. Person', 'A. Person',
         'CC_BY_4_0', 2, 'test', 'AAAA',
         [{'datasetkey': 'd1', 'title': 'Test dataset', 'records': 5::BIGINT}],
         NULL::VARCHAR[], 0::BIGINT),
        ('BBBB', 'Salix scouleriana Barratt ex Hook.', 'Salix scouleriana',
         'Barratt ex Hook.', 'Catalogue of Life, via GBIF',
         'COL26.6 XR (ChecklistBank 315557)', 'Salix scouleriana',
         'Plantae', 'Tracheophyta', 'Magnoliopsida', 'Malpighiales', 'Salicaceae',
         'Salix', 3::BIGINT, 0::BIGINT, 2019, 2019, 1, 50.0, 50.0, -122.0, -122.0,
         NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
         [{'datasetkey': 'd1', 'title': 'Test dataset', 'records': 3::BIGINT}],
         ['OLD1'], 1::BIGINT),
        ('CCCC', 'Bombus (Pyrobombus) vosnesenskii Radoszkowski, 1862',
         'Bombus (Pyrobombus) vosnesenskii', 'Radoszkowski, 1862',
         'Catalogue of Life, via GBIF', 'COL26.6 XR (ChecklistBank 315557)',
         'Bombus vosnesenskii', 'Animalia', 'Arthropoda', 'Insecta',
         'Hymenoptera', 'Apidae', 'Bombus', 1::BIGINT, 0::BIGINT, 2021, 2021, 1,
         49.3, 49.3, -123.0, -123.0,
         'https://caos.boldsystems.org/api/objects/x.jpg', 'https://portal.boldsystems.org/record/X',
         NULL, NULL, 'CC_BY_NC_SA_4_0', 6, 'media', 'CCCC',
         [{'datasetkey': 'd1', 'title': 'Test dataset', 'records': 1::BIGINT}],
         NULL::VARCHAR[], 0::BIGINT),
        ('DDDD', 'Carex test L.', 'Carex test', 'L.',
         'Catalogue of Life, via GBIF', 'COL26.6 XR (ChecklistBank 315557)',
         'Carex test', 'Plantae', 'Tracheophyta', 'Liliopsida', 'Poales',
         'Cyperaceae', 'Carex', 1::BIGINT, 0::BIGINT, 2018, 2018, 1,
         54.0, 54.0, -128.0, -128.0,
         'https://images.example.org/carex/original.jpg', 'https://herbarium.example.org/specimen/9',
         'B. Collector', 'Example Herbarium', 'CC_BY_NC_ND_4_0', 7, 'media', 'DDDD',
         [{'datasetkey': 'd2', 'title': 'Herbarium', 'records': 1::BIGINT}],
         NULL::VARCHAR[], 0::BIGINT)
    ) AS t(specieskey, scientificname, col_name, col_authorship, name_source,
           col_release, species, kingdom, phylum, class, "order", family, genus,
           record_count, records_no_year, first_year, last_year, n_datasets,
           min_lat, max_lat, min_lon, max_lon,
           image_identifier, image_references, image_creator, image_rightsholder,
           image_license_code, image_license_rank, image_license_source,
           image_specieskey, top_datasets, merged_keys, merged_record_count)
"""

# Records per species, year and hexagon: 10 records, 2 with no year.
HEX_SQL = """
    SELECT * FROM (VALUES
        ('AAAA', 2020, '842800bffffffff', '852800b3fffffff', '862800b37ffffff',
         '872800b30ffffff', 3::BIGINT),
        ('AAAA', NULL, '842800bffffffff', '852800b3fffffff', '862800b37ffffff',
         '872800b30ffffff', 2::BIGINT),
        ('BBBB', 2019, '8428c9bffffffff', '8528c9b7fffffff', '8628c9b47ffffff',
         '8728c9b40ffffff', 3::BIGINT),
        ('CCCC', 2021, '842800bffffffff', '852800b3fffffff', '862800b37ffffff',
         '872800b30ffffff', 1::BIGINT),
        ('DDDD', 2018, '8428c9bffffffff', '8528c9b7fffffff', '8628c9b47ffffff',
         '8728c9b40ffffff', 1::BIGINT)
    ) AS t(specieskey, year, h3_r4, h3_r5, h3_r6, h3_r7, occurrences)
"""

# BCSEE statuses: one for a species with a card, one for a species without.
BCSEE_SQL = """
    SELECT * FROM (VALUES
        ('AAAA', 'Turdus migratorius', 'Yellow: Turdus migratorius [American Robin]',
         false, false, ['Yellow'], ['ABPBJ20170'],
         [{'status': 'Yellow', 'scientific_label': 'Turdus migratorius',
           'name_source': 'GBIF', 'common_label': 'American Robin',
           'is_species_level': true, 'is_gbif_synonym': false,
           'matched_species_keys': ['AAAA'], 'element_codes': ['ABPBJ20170']}],
         ['AAAA'], 'BC Species and Ecosystems Explorer (BCSEE)',
         '2026-05-05', '2026-09-26'),
        ('ZZZZ', 'Nowhere inbc', 'Red: Nowhere inbc [Absent Thing]',
         false, false, ['Red'], ['XXXXX00000'],
         [{'status': 'Red', 'scientific_label': 'Nowhere inbc',
           'name_source': 'GBIF', 'common_label': 'Absent Thing',
           'is_species_level': true, 'is_gbif_synonym': false,
           'matched_species_keys': ['ZZZZ'], 'element_codes': ['XXXXX00000']}],
         ['ZZZZ'], 'BC Species and Ecosystems Explorer (BCSEE)',
         '2026-05-05', '2026-09-26')
    ) AS t(gbif_species_key, species_name, status_display, has_multiple,
           has_separate_bcsee_species, statuses, element_codes, entries,
           matched_species_keys, status_source, status_source_date,
           status_pulled_on)
"""

# The BCSEE file as it would be if it also listed species with no shown
# status: CCCC is in BCSEE, but only as Not Reviewed.
BCSEE_WITH_NO_STATUS_SQL = f"""
    {BCSEE_SQL}
    UNION ALL
    SELECT 'CCCC', 'Bombus vosnesenskii', 'Not Reviewed: Bombus vosnesenskii',
           false, false, []::VARCHAR[], ['IIHYM24000'],
           [{{'status': 'Not Reviewed', 'scientific_label': 'Bombus vosnesenskii',
              'name_source': 'GBIF', 'common_label': 'Yellow-faced Bumble Bee',
              'is_species_level': true, 'is_gbif_synonym': false,
              'matched_species_keys': ['CCCC'], 'element_codes': ['IIHYM24000']}}],
           ['CCCC'], 'BC Species and Ecosystems Explorer (BCSEE)',
           '2026-05-05', '2026-09-26'
"""

DOWNLOAD_INFO = {
    "download_key": "0000000-000000000000000",
    "doi": "10.15468/dl.test",
    "doi_url": "https://doi.org/10.15468/dl.test",
    "doi_source": "gbif_download_api",
    "date": "2026-10-04T03:10:39Z",
}


def write_parquet(query, path):
    """Write the rows of a SQL query to a parquet file."""
    con = duckdb.connect()
    con.execute(f"COPY ({query}) TO '{path}' (FORMAT PARQUET)")
    con.close()


def write_test_data(folder, card_sql=CARD_SQL, hex_sql=HEX_SQL, bcsee_sql=BCSEE_SQL,
                    download_info=DOWNLOAD_INFO):
    """Write the four data files into folder and return folder. A test can
    pass its own version of any one of them to try a broken file."""
    write_parquet(card_sql, folder / "species_card_gbif.parquet")
    write_parquet(hex_sql, folder / "species_hex.parquet")
    write_parquet(bcsee_sql, folder / "bcsee_status_by_gbif_species.parquet")
    with open(folder / "download_info.json", "w", encoding="utf-8") as handle:
        json.dump(download_info, handle)
    return folder


@pytest.fixture
def data_dir(tmp_path):
    """A temporary folder holding the test data files."""
    return write_test_data(tmp_path)


@pytest.fixture
def settings(data_dir):
    """Settings pointing at the test data, with the sensitive setting empty."""
    return Settings(data_dir=data_dir, sensitive_max_res=None)


@pytest.fixture
def client(settings):
    """A test client for the API on the test data. Using it in a with
    block runs the start-up, which opens the data."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
