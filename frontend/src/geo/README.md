# Geography the front end builds in

This folder is not called data/ because .gitignore leaves out every
folder of that name.

## bc_boundary_gbif.wkt

BC's boundary as one WKT polygon (266 points, counter-clockwise, 5,533
characters), used as the `geometry` of the species card's link "See this
species' BC records on GBIF" (`gbifBcSearchUrl` in `../gbifLinks.js`).

- Written by `backend/pipeline/gbif/make_gbif_boundary.py` (BC's ABMS
  boundary pushed out 2 km and simplified), saved on 2026-10-03 as
  `~/Desktop/BCBN/Results/bc_boundary_gbif.wkt`.
- The polygon our GBIF download used: download `0009771-260928105237408`
  (DOI 10.15468/dl.ge9ypx, 4 October 2026), requested with
  `request_gbif_download.py --wkt` on that file.
- Copied here unchanged on 2026-11-09. SHA-1
  `07b56f163b7ada86023a415c9d007bbb4cfff125`, the same as the Results
  file; `../gbifLinks.test.js` checks it. Do not edit it by hand: the link
  must use the same polygon as the download. When the download is redone
  with a new polygon, copy the new file here and update this note and the
  checksum in the test.
- GBIF's own record of the download (`predicate` in `download_info.json`)
  writes 4 of the 266 points 0.00001 degrees (about 1 m) differently, as
  GBIF rounds them when it stores the polygon; this file is what was sent.
- The GBIF API refuses this polygon in a GET address (it allows about
  3,500 characters, tested 2026-11-09), but www.gbif.org opens the link
  (tested by hand: 170,767 records for Sockeye Salmon, 49JFH).
