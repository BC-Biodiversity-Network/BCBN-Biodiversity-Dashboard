# DataONE harvest

`harvest_dataone.py` downloads every latest-version metadata record from
DataONE's public search index (`formatType:METADATA AND -obsoletedBy:*`,
about 1.25 million records), with no BC filter. Later steps (removing old
versions, the BC sieve, the LLM filters) read the local files instead of
asking DataONE again.

The script keeps the fields exactly as DataONE returns them: nothing is
cleaned or renamed, and a field a record does not have is stored as null.
The only column it adds is `harvest_date`, the day the page was fetched.
One exception: DataONE sometimes sends a box edge as the text `"NaN"`. The
script stores that as a numeric NaN, which keeps it apart from a missing
edge (null), and lists each one (page, id, field, original value) in
`nan_box_values.csv` in the harvest folder. Any other unexpected value
stops the harvest.
The fields are listed in `FIELDS` at the top of the script.

## Rules for talking to DataONE

- Only the public search API, `https://cn.dataone.org/cn/v2/query/solr/`.
- At most one request per second.
- User-Agent: `BCBN-Dashboard (BC Biodiversity Network, UBC; songyanf@student.ubc.ca)`.
- At most 200 requests per harvest folder, counting every run. The count
  comes from the folder's `requests_log.csv`. A full harvest needs about
  127 requests.
- Any answer other than 200 stops the run at once, with no retry. If
  DataONE refuses (403, 429, or 503 with Retry-After), tell Lucia before
  doing anything else.

## Running it

Run from `backend/`, with a new folder under `data/dataone/` for each
harvest:

    ../.venv/bin/python pipeline/dataone/harvest_dataone.py --outdir data/dataone/harvest_YYYY_MM_DD

Before it writes anything, the script runs `git check-ignore` on the folder
and stops if git does not ignore it. Everything under `data/` is ignored.

It fetches 10,000 records per request, sorted by id, using Solr's
cursorMark paging. Each page is saved as `pages/page_NNNNN.parquet` as
soon as it arrives. `progress.json` records the query, the fields, and
each page's cursor and next cursor. After the last page, the script joins
every page into `dataone_full_harvest.parquet` and prints how many ids
appear more than once. They are kept, not removed. A full run takes about
5 to 10 minutes.

For a test, `--max-pages N` stops after N new pages and `--request-limit N`
lowers the limit, for example:

    ../.venv/bin/python pipeline/dataone/harvest_dataone.py --outdir data/dataone/harvest_test --max-pages 2 --request-limit 3

## Resuming

If a run stops part way, run the same command with the same `--outdir`.
It starts from the next cursor saved in `progress.json`. A page that
arrived but was not recorded in `progress.json` is simply fetched again.

Resume on the same day if you can. The cursor is the last id fetched, so
it still works later, but DataONE changes every day: a record that gets a
new version between two pages can be missed or saved twice. The script
notes any change in the total number of matching records. After a long
gap, start again in a new folder.

The script refuses to continue a folder whose `progress.json` was made
with a different query, field list, page size or sort.

## Documents of records with several boxes

DataONE's search index keeps only the **first** box of a record in its four
box fields. It also keeps the centre of **every** box in `geohash_9`.
`fetch_multibox_documents.py` downloads the original metadata document of
every latest record with more than one `geohash_9` value, and reads every
box from it. No text rule is used to choose these records.

The script builds its own list from `dataone_full_harvest_versions.parquet`
(written by `mark_versions.py`) and saves it as `dataone_multibox_all.csv`
next to that file. On 2026-11-18 the list held 5,328 records. Each
document is downloaded once from
`https://cn.dataone.org/cn/v2/object/<url-encoded id>` and saved exactly as
received, gzipped, in `originals/`. The file name is a hash of the id.
`originals/index.csv` gives the id, file, HTTP status, bytes, formatId,
seconds and time of each document.

The script follows the same rules for talking to DataONE as the harvest:
- at most one request per second, with the BCBN User-Agent;
- 403, 429 or any 5xx stops it at once;
- a 404 is logged and it goes on, but more than 20 404s stop it;
- at most 5,400 requests for the folder, counted from
  `originals/requests_log.csv`;
- before writing anything, it checks with `git check-ignore` that the
  folder is ignored by git.

Run from `backend/`. The full download takes about 2 hours, so run it in a
terminal with `caffeinate`:

    caffeinate -i ../.venv/bin/python pipeline/dataone/fetch_multibox_documents.py 2>&1 | tee -a data/dataone/harvest_2026_11_18/originals/run.log

- **Progress:** it prints a line every 50 documents. From another
  terminal, `wc -l data/dataone/harvest_2026_11_18/originals/index.csv`
  counts the documents done (minus 1 for the header).
- **Resume:** run the same command again. It skips every id already in
  `originals/index.csv`, both saved documents and 404s.
- **Check without downloading:** `--check` builds the list and says
  whether the request limit still covers it. It sends no requests.

Then read the boxes; this makes no requests:

    ../.venv/bin/python pipeline/dataone/fetch_multibox_documents.py --parse

This writes `originals_boxes.parquet`, one row per box: id, format, box
number in document order, north, south, east, west, and the path where the
box sits in the document.

- **EML:** every `boundingCoordinates` inside a `geographicCoverage`.
- **ISO:** every `EX_GeographicBoundingBox`.
- **Values that are not numbers:** the number is left empty, the text is
  kept in `<edge>_text`, and the row is flagged.
- **Other formats, or documents that cannot be read:** listed in
  `originals/parse_log.csv`.
- **Check:** it prints how many documents have as many boxes as
  `geohash_9` values.
