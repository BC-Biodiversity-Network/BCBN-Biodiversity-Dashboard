// The words the Datasets page uses for a Lunaris dataset: licences, dates,
// and what the map draws and says about its location. All
// plain functions, so they can be tested without a page or a map.
//
// Everything here treats text from the records as plain text. Some licence
// names and abstracts hold HTML; it is read with DOMParser, which never runs
// scripts or loads images, and only its text is kept. Nothing from a record
// is ever put on the page as HTML.

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December']

// Writes an issued date (YYYY-MM-DD) in words, for example "5 June 2020".
// Anything else is returned as it is, and an empty date as null.
export function issuedLabel(issued) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(issued ?? '')
  if (!match) {
    return issued || null
  }
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`
}

// Shortens text to at most length characters, cut at a space, with "..."
// when cut.
export function shorten(text, length) {
  if (text.length <= length) {
    return text
  }
  // One character more than allowed, so a cut that falls just at the end
  // of a word keeps that word.
  const cut = text.slice(0, length + 1).replace(/\s+\S*$/, '')
  return `${(cut || text.slice(0, length)).replace(/[\s,;:.]+$/, '')}...`
}

// True if text looks like it holds HTML tags.
export function looksLikeHtml(text) {
  return /<\/?[a-z][^>]*>?/i.test(text ?? '')
}

// Reads text that may hold HTML into an inert document (nothing in it runs
// or loads), or returns null when there is no DOMParser.
function parseHtml(html) {
  if (typeof DOMParser === 'undefined') {
    return null
  }
  return new DOMParser().parseFromString(html, 'text/html')
}

// Turns text that may hold HTML into plain text: tags removed, entities such
// as &amp; read, and runs of spaces and line breaks made single spaces.
export function plainText(text) {
  if (!text) {
    return ''
  }
  let result = text
  if (looksLikeHtml(text)) {
    // A space before each block tag (paragraph, heading, list item, line
    // break...), so words on either side of one are not run together.
    const spaced = text.replace(/<(\/?)(p|h[1-6]|li|ul|ol|br|hr|div|tr|td|blockquote)\b/gi, ' <$1$2')
    const doc = parseHtml(spaced)
    result = doc ? doc.body.textContent : spaced.replace(/<[^>]*>?/g, ' ')
  }
  return result.replace(/\s+/g, ' ').trim()
}

// True for a web address that can be linked (http or https only, so a
// javascript: or other address from a record is never made a link).
export function isWebLink(uri) {
  return /^https?:\/\/[^\s'"<>]+$/i.test(uri ?? '')
}

// The DataCite rights URIs that give an access right, not a licence.
const ACCESS_RIGHTS_PREFIX = 'info:eu-repo/semantics/'
const ACCESS_RIGHTS = {
  openAccess: 'Open access',
  restrictedAccess: 'Restricted access',
  embargoedAccess: 'Embargoed access',
  closedAccess: 'Closed access',
}

// Licences longer than this are shortened, with a way to see the full text.
export const LICENCE_NAME_LENGTH = 90

// A readable name for a licence given only as a URI: the SPDX id
// (https://spdx.org/licenses/CC0-1.0.html gives "CC0-1.0"), else the URI.
function nameFromUri(uri) {
  const spdx = /^https?:\/\/spdx\.org\/licenses\/([^/]+?)(\.html)?$/i.exec(uri)
  return spdx ? spdx[1] : uri
}

// Some catalogues (city open data hubs on ArcGIS, among them Kelowna,
// Victoria, Prince George and Abbotsford) send HTML split in two: the name
// holds the start, for example "<a href='", and the URI holds the rest,
// tags and all. This joins them back and returns { name, uri } read from
// the first link in them (or, with no link, the text, linked when it is a
// web address), or null when the entry is not split that way.
function repairSplitLink(name, uri) {
  if (!looksLikeHtml(name) || !/[<>'"]/.test(uri)) {
    return null
  }
  const joined = name + uri
  const link = parseHtml(joined)?.querySelector('a[href]')
  if (link) {
    return { name: plainText(link.textContent), uri: link.getAttribute('href').trim() }
  }
  const text = plainText(joined)
  return { name: text, uri: isWebLink(text) ? text : '' }
}

// Reads one rights entry from the API ({ name, uri }, exactly as harvested)
// into what the page shows. An access right gives { kind: 'access', label }.
// A licence gives { kind: 'licence', name, fullText, uri }: name is short
// (a heading in the HTML if it has one, else the text, shortened), fullText
// is the whole text when it is longer than name (else null), and uri is a
// web address to link, or null.
export function readLicence(entry) {
  let name = entry?.name ?? ''
  let uri = (entry?.uri ?? '').trim()

  if (!name.trim() && uri.startsWith(ACCESS_RIGHTS_PREFIX)) {
    const right = uri.slice(ACCESS_RIGHTS_PREFIX.length)
    return { kind: 'access', label: ACCESS_RIGHTS[right] ?? `Access: ${right}` }
  }

  const repaired = repairSplitLink(name, uri)
  if (repaired) {
    name = repaired.name
    uri = repaired.uri
  }

  let heading = ''
  if (looksLikeHtml(name)) {
    const doc = parseHtml(name)
    heading = plainText(doc?.querySelector('h1, h2, h3, h4')?.textContent ?? '')
  }
  const fullText = plainText(name)
  const link = isWebLink(uri) ? uri : null
  let short = heading || fullText
  if (!short) {
    short = uri ? nameFromUri(uri) : 'Unnamed licence'
  }
  short = shorten(short, LICENCE_NAME_LENGTH)

  return {
    kind: 'licence',
    name: short,
    fullText: fullText && fullText !== short ? fullText : null,
    uri: link,
  }
}

// Splits a dataset's rights entries into its licences and its access
// rights, each read with readLicence.
export function readLicences(entries) {
  const read = (entries ?? []).map(readLicence)
  return {
    licences: read.filter((entry) => entry.kind === 'licence'),
    accessRights: read.filter((entry) => entry.kind === 'access').map((entry) => entry.label),
  }
}

// How long the licence label of a list item is, at most.
const LIST_LICENCE_LENGTH = 60

// The short licence label for a list item: the first licence's name, cut to
// LIST_LICENCE_LENGTH characters, with how many more there are; or "No
// licence given".
export function licenceListLabel(entries) {
  const { licences } = readLicences(entries)
  if (licences.length === 0) {
    return 'No licence given'
  }
  const more = licences.length > 1 ? ` and ${licences.length - 1} more` : ''
  return shorten(licences[0].name, LIST_LICENCE_LENGTH) + more
}

// --- Location ---------------------------------------------------------------

// BC's bounding rectangle and margin, the same as step 1 of the BC filter
// (BcShape.rectangle of bc_boundary_gbif.wkt, and BC_RECTANGLE_MARGIN in
// backend/pipeline/lunaris/bc_filter_rules.py), and the Canada-wide extent
// and margin of the export (CANADA_DEFAULT_BOX and CANADA_DEFAULT_MARGIN in
// export_lunaris_datasets.py). The export flags a dataset when any of its
// boxes matches; the page needs to know which box, so it checks each box
// again with the same numbers. A test checks these give the export's flags.
const BC_RECTANGLE = { w: -139.12771, s: 48.20636, e: -114.02091, n: 60.02025 }
const BC_RECTANGLE_MARGIN = 1.0
const CANADA_DEFAULT_BOX = { w: -141.0, e: -52.6, s: 41.7, n: 83.1 }
const CANADA_DEFAULT_MARGIN = 0.5
// A box covering at least this share of the whole longitude by latitude
// range covers most of the world (MOST_OF_WORLD in the export).
const MOST_OF_WORLD = 0.5

// True for a box whose west edge is in the eastern half of the world and
// east edge in the western half: it crosses the 180 degree line.
function crosses180(box) {
  return box.w > 0 && box.e < 0
}

// The box with swapped edges put back (south below north, west left of
// east), as step 1 does before comparing it with a standard box; null for a
// box that crosses the 180 degree line, which is never a standard box.
function straightened(box) {
  if (crosses180(box)) {
    return null
  }
  return {
    w: Math.min(box.w, box.e), e: Math.max(box.w, box.e),
    s: Math.min(box.s, box.n), n: Math.max(box.s, box.n),
  }
}

// True if every edge of box is within margin degrees of the same edge of
// standard.
function matches(box, standard, margin) {
  return box !== null && ['w', 'e', 's', 'n'].every((edge) => Math.abs(box[edge] - standard[edge]) <= margin)
}

// Why one harvested box ({ w, e, s, n }) is not drawn, or null if it is
// drawn: 'bc_rectangle' or 'canada_default' for a standard box (checked
// first, so a standard box with swapped edges is still called standard),
// then 'swapped', 'zero_width' or 'most_of_world', as the export's flags.
export function boxIssue(box) {
  const straight = straightened(box)
  if (matches(straight, BC_RECTANGLE, BC_RECTANGLE_MARGIN)) {
    return 'bc_rectangle'
  }
  if (matches(straight, CANADA_DEFAULT_BOX, CANADA_DEFAULT_MARGIN)) {
    return 'canada_default'
  }
  if (box.s > box.n || (box.w > box.e && !crosses180(box))) {
    return 'swapped'
  }
  if (box.w === box.e || box.s === box.n) {
    return 'zero_width'
  }
  const width = crosses180(box) ? (((box.e - box.w) % 360) + 360) % 360 : Math.abs(box.e - box.w)
  if ((width / 360) * (Math.abs(box.n - box.s) / 180) >= MOST_OF_WORLD) {
    return 'most_of_world'
  }
  return null
}

// A number of degrees as written in the record, without trailing zeros.
function degrees(value) {
  return String(Number(value.toFixed(5)))
}

// A box written out as harvested, for the note about a box that is not drawn.
export function boxAsWritten(box) {
  return `west ${degrees(box.w)}, east ${degrees(box.e)}, south ${degrees(box.s)}, north ${degrees(box.n)}`
}

// Why a box with an issue is not drawn, in words.
const NOT_DRAWN_BECAUSE = {
  swapped: 'its edges are the wrong way round (south above north, or west beyond east)',
  zero_width: 'it has no width or no height, so it is a line or a single point',
  most_of_world: 'it covers most of the world, so it does not show where the data is',
}

// How a looked-up place relates to BC, in words, from the kind of the
// GeoNames match the export picked (a match in BC is preferred), or null for
// a place in BC.
const PLACE_NOTES = {
  outside_bc: 'not in BC',
  contains_bc: 'a large area that includes BC',
  spans_bc_border: 'crosses the BC border',
  ocean_no_country: 'at sea, not in BC',
}

// What one looked-up place is called on the page: its name, with a note in
// brackets when it is not in BC or could not be placed.
export function placeLabel(place) {
  if (place.latitude == null || place.longitude == null) {
    return `${place.name} (could not be placed on the map)`
  }
  return PLACE_NOTES[place.kind] ? `${place.name} (${PLACE_NOTES[place.kind]})` : place.name
}

// The items of a list once each, in order.
function once(items) {
  return [...new Set(items)]
}

// Works out what the map draws for a dataset and what the page says about
// where its location comes from. Returns { boxes, points, places, notes,
// geonames }:
//   boxes   the harvested boxes to draw (those with no issue)
//   points  the harvested points to draw
//   places  the looked-up places to draw, only when nothing from the
//           record itself is drawn: each has coordinates and is not a large
//           area (large_area, worked out by the export from the GeoNames
//           feature class and code)
//   notes   the sentences to show, in order
//   geonames  true when looked-up places are shown, so the GeoNames
//           attribution must be shown with them
// Places pass 1 marked as an author's institution are never drawn or named.
export function describeLocation(dataset) {
  const source = dataset.source_name || 'the source catalogue'
  const boxes = dataset.boxes ?? []
  const points = dataset.points ?? []
  const notes = []

  const drawn = boxes.filter((box) => boxIssue(box) === null)
  if (drawn.length === 1) {
    notes.push(`Extent: from the ${source} record.`)
  } else if (drawn.length > 1) {
    notes.push(`Extent: ${drawn.length} boxes from the ${source} record.`)
  }
  const issues = boxes.map(boxIssue)
  if (issues.includes('bc_rectangle')) {
    notes.push(`Extent: whole of BC (standard box used by ${source}, not the dataset's real extent). Not drawn.`)
  }
  if (issues.includes('canada_default')) {
    notes.push(`Extent: all of Canada (standard box used by ${source}). Not drawn.`)
  }
  boxes.forEach((box, index) => {
    const because = NOT_DRAWN_BECAUSE[issues[index]]
    if (because) {
      notes.push(`Box in the record: ${boxAsWritten(box)}. Not drawn, because ${because}.`)
    }
  })

  if (points.length === 1) {
    notes.push('Location: a point from the record.')
  } else if (points.length > 1) {
    notes.push(`Location: ${points.length} points from the record.`)
  }

  // Places our lookup found are only used when the record itself gives
  // nothing that can be drawn, and never an author's institution.
  const looked = drawn.length === 0 && points.length === 0
    ? (dataset.looked_up_places ?? []).filter((place) => place.role !== 'affiliation')
    : []
  const small = looked.filter((place) => !place.large_area)
  const large = looked.filter((place) => place.large_area)
  const places = small.filter((place) => place.latitude != null && place.longitude != null)
  if (small.length > 0) {
    notes.push(`Places named in the record: ${once(small.map(placeLabel)).join(', ')} ` +
      '(located by BCBN using GeoNames).')
  }
  if (large.length > 0) {
    notes.push(`${small.length > 0 ? 'Other places named' : 'Places named in the record'}: ` +
      `${once(large.map((place) => place.name)).join(', ')} (large areas, not drawn on the map).`)
  }

  if (boxes.length === 0 && points.length === 0 && looked.length === 0) {
    notes.push('No location given in the record.')
  }

  return { boxes: drawn, points, places, notes, geonames: looked.length > 0 }
}
