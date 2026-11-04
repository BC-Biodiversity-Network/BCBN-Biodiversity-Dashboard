import { cellToBoundary } from 'h3-js'

// Links to GBIF's own occurrence search, so a visitor can see the records
// behind a number on the site.

// GBIF's occurrence search page.
export const GBIF_OCCURRENCE_SEARCH = 'https://www.gbif.org/occurrence/search'

// The Catalogue of Life checklist in GBIF's registry. Our species keys are
// ids in it (the download was made with this checklistKey; see
// backend/pipeline/gbif/request_gbif_download.py), so a search by our keys
// must name it: without it GBIF reads them as keys of its old backbone and
// finds nothing.
export const GBIF_CHECKLIST_KEY = '7ddf754f-d193-4cc9-b351-99906754a03b'

// How many decimals of a degree the hexagon's corners keep in the link:
// six is about 0.1 m, far finer than any hexagon.
const DECIMALS = 6

// A hexagon's outline as WKT, the way GBIF takes a geometry: longitude then
// latitude, the ring closed (the first corner repeated at the end) and
// counter-clockwise, as GBIF asks for polygons. h3-js gives the corners in
// that order already.
export function cellWkt(cell) {
  const ring = cellToBoundary(cell, true)
  const corners = ring.map(([lng, lat]) => `${lng.toFixed(DECIMALS)} ${lat.toFixed(DECIMALS)}`)
  return `POLYGON((${corners.join(',')}))`
}

// The address of a GBIF occurrence search for some species keys (a card's
// own key and every key merged into it) inside a hexagon, PRESENT records
// only (as in our download), for a year range ([from, to], both included)
// or every year (null). The parameters are those of GBIF's occurrence
// search: checklistKey, taxonKey (once per key), geometry, occurrenceStatus
// and year ("from,to").
export function gbifCellSearchUrl({ keys, cell, years = null }) {
  const params = new URLSearchParams({ checklistKey: GBIF_CHECKLIST_KEY })
  for (const key of keys) {
    params.append('taxonKey', key)
  }
  params.set('geometry', cellWkt(cell))
  params.set('occurrenceStatus', 'PRESENT')
  if (years) {
    params.set('year', `${years[0]},${years[1]}`)
  }
  return `${GBIF_OCCURRENCE_SEARCH}?${params}`
}
