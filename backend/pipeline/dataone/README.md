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
