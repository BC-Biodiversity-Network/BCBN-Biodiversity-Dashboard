import { cellToBoundary } from 'h3-js'

// BC's boundary, the polygon our GBIF download used, read as plain text
// exactly as it is in the file (see geo/README.md for where it comes from).
import BC_BOUNDARY_WKT from './geo/bc_boundary_gbif.wkt?raw'

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

// The note that goes under every link to GBIF's records, so nobody is
// surprised when GBIF's count is not ours.
export const GBIF_COUNT_NOTE = 'GBIF shows its current data, without the cleaning of our snapshot, so its count '
  + 'may differ a little from ours.'

// BC's boundary as the link sends it: the file's text, unchanged.
export { BC_BOUNDARY_WKT }

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

// The address of a GBIF occurrence search inside a hexagon, PRESENT records
// only (as in our download), for a year range ([from, to], both included)
// or every year (null). With keys (a card's own key and every key merged
// into it) it finds only that species; with none (the all-species hexagon
// panel's link) every species in the hexagon. The parameters are those of
// GBIF's occurrence search: checklistKey, taxonKey (once per key),
// geometry, occurrenceStatus and year ("from,to").
export function gbifCellSearchUrl({ keys = [], cell, years = null }) {
  return gbifSearchUrl({ keys, geometry: cellWkt(cell), years })
}

// The note beside a GBIF link for a list of species (a hexagon's), when the
// list has filters the link cannot carry, or null when it has none. The
// link carries the place and the years; a rank (rank, { rank, name }, or
// null) cannot go yet, as the site has no GBIF key for a rank, and neither
// can a name filter or a BC status filter (list, { q, status }), which are
// our own. place says where GBIF searches ("hexagon").
export function gbifLeftOutNote({ place, rank = null, list = null }) {
  const leftOut = []
  if (rank) {
    leftOut.push(`${rank.rank} ${rank.name}`)
  }
  if (list?.q) {
    leftOut.push('name filter')
  }
  if (list?.status?.length) {
    leftOut.push('BC status filter')
  }
  if (leftOut.length === 0) {
    return null
  }
  const named = leftOut.length === 1
    ? leftOut[0]
    : `${leftOut.slice(0, -1).join(', ')} and ${leftOut[leftOut.length - 1]}`
  return `GBIF shows every species in the ${place}: the link leaves out this list's ${named}.`
}

// The address of a GBIF occurrence search for some species keys (a card's
// own key and every key merged into it) anywhere in BC: inside the polygon
// our GBIF download used, PRESENT records only, every year. The same
// parameters as the hexagon's link, with BC's boundary as the geometry.
export function gbifBcSearchUrl({ keys }) {
  return gbifSearchUrl({ keys, geometry: BC_BOUNDARY_WKT })
}

// The note under the area panel's GBIF link: the link searches the box
// itself, while our numbers count every hexagon the box touches.
export const AREA_GBIF_NOTE = 'GBIF searches only the box you drew. Our numbers include every hexagon the box '
  + 'touches, so GBIF usually shows fewer records.'

// A box ({ west, south, east, north }) as WKT for GBIF: a closed 5-point
// polygon running counter-clockwise (west-south, east-south, east-north,
// west-north, west-south), longitude first, six decimals, the same text as
// box_wkt in backend/api/gbif_links.py.
export function boxWkt({ west, south, east, north }) {
  const corners = [[west, south], [east, south], [east, north], [west, north], [west, south]]
  const points = corners.map(([lng, lat]) => `${lng.toFixed(DECIMALS)} ${lat.toFixed(DECIMALS)}`)
  return `POLYGON((${points.join(',')}))`
}

// The address of a GBIF occurrence search inside a box drawn on the map,
// PRESENT records only, for a year range ([from, to]) or every year (null).
// With no keys (the area panel's link) it finds every species in the box;
// with keys, only those species (as gbif_box_search_url in
// backend/api/gbif_links.py writes each species' link).
export function gbifBoxSearchUrl({ keys = [], box, years = null }) {
  return gbifSearchUrl({ keys, geometry: boxWkt(box), years })
}

// Writes a GBIF occurrence search address, the one place both links are
// made: the Catalogue of Life checklist, one taxonKey per species key, the
// area as WKT, PRESENT records only, and the years ([from, to]) when given.
function gbifSearchUrl({ keys, geometry, years = null }) {
  const params = new URLSearchParams({ checklistKey: GBIF_CHECKLIST_KEY })
  for (const key of keys) {
    params.append('taxonKey', key)
  }
  params.set('geometry', geometry)
  params.set('occurrenceStatus', 'PRESENT')
  if (years) {
    params.set('year', `${years[0]},${years[1]}`)
  }
  return `${GBIF_OCCURRENCE_SEARCH}?${params}`
}
