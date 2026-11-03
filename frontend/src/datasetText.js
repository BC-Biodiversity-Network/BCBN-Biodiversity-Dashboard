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

// How many creators are named before the rest are counted ("and 4 others").
const CREATORS_SHOWN = 3

// The creators line of the Details table: the names exactly as harvested,
// joined with "; ", up to CREATORS_SHOWN of them, then "and N others" ("and
// 1 other") for the rest. null when the record has none.
export function creatorsLabel(creators) {
  const names = creators ?? []
  if (names.length === 0) {
    return null
  }
  const shown = names.slice(0, CREATORS_SHOWN)
  const rest = names.length - shown.length
  return rest > 0 ? [...shown, `and ${rest} ${rest === 1 ? 'other' : 'others'}`].join('; ') : shown.join('; ')
}

// The start of a DOI written as a web address (the export's link for a
// record whose identifier is a DOI).
const DOI_LINK = 'https://doi.org/'

// The identifier of the Details table, as text: "doi:10.5683/SP3/GZQXVC" for
// a DOI, the landing page's address otherwise. null when there is no link
// that can be followed.
export function identifierLabel(link, linkType) {
  if (!isWebLink(link)) {
    return null
  }
  return linkType === 'doi' && link.startsWith(DOI_LINK) ? `doi:${link.slice(DOI_LINK.length)}` : link
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

// The text a few catalogues put in place of a licence when there is none
// (2 records in October 2026: <rights>None</rights>). With no link, it is
// read as no licence; the export keeps it as harvested.
const NO_LICENCE_PLACEHOLDER = 'None'

// Reads one rights entry from the API ({ name, uri }, exactly as harvested)
// into what the page shows. An access right gives { kind: 'access', label }.
// The placeholder "None" with no link gives { kind: 'none' }: no licence.
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
  if (name === NO_LICENCE_PLACEHOLDER && !uri) {
    return { kind: 'none' }
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
// rights, each read with readLicence. An entry that is no licence (the
// "None" placeholder) is in neither.
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
// LIST_LICENCE_LENGTH characters, with how many more there are; when the
// record has none, the first of the licences BCBN found at the original
// catalogue (checkedEntries) instead; or "No licence in the record".
export function licenceListLabel(entries, checkedEntries = null) {
  let { licences } = readLicences(entries)
  if (licences.length === 0) {
    licences = readLicences(checkedEntries).licences
  }
  if (licences.length === 0) {
    return 'No licence in the record'
  }
  const more = licences.length > 1 ? ` and ${licences.length - 1} more` : ''
  return shorten(licences[0].name, LIST_LICENCE_LENGTH) + more
}

// --- What BCBN checked at the source -----------------------------------------

// The source check's access values (the API's dataset_source_checks), in
// plain words.
const CHECKED_ACCESS_WORDS = {
  open: 'open access',
  restricted: 'restricted access',
  on_request: 'available on request',
  embargoed: 'embargoed',
  not_downloadable: 'not downloadable',
  government_only: 'downloadable by government users only',
  government_bceid_only: 'downloadable by government and business BCeID users only',
  named_users_only: 'downloadable by named users only',
  ubc_only: 'open to UBC users only',
  limited: 'limited access',
  no_files: 'no files in the record',
  some_files_locked: 'some files locked',
}

// The checked access in plain words, with its date when it has one ("some
// files locked until 1 February 2027"), or null when access was not
// checked.
export function checkedAccessLabel(check) {
  if (!check?.access) {
    return null
  }
  const words = CHECKED_ACCESS_WORDS[check.access] ?? check.access.replaceAll('_', ' ')
  return check.access_until ? `${words} until ${issuedLabel(check.access_until)}` : words
}

// Text with its first letter in capitals ("open access" gives "Open access").
export function capitalised(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text
}

// --- Location ---------------------------------------------------------------

// BC's bounding rectangle and margin, the same as step 1 of the BC filter
// (BcShape.rectangle of bc_boundary_gbif.wkt, and BC_RECTANGLE_MARGIN in
// backend/pipeline/lunaris/bc_filter_rules.py), and the Canada-wide extent
// and margin of the export (CANADA_EXTENT_BOX and CANADA_EXTENT_MARGIN in
// export_lunaris_datasets.py). The export flags a dataset when any of its
// boxes matches; the page needs to know which box, so it checks each box
// again with the same numbers. A test checks these give the export's flags.
const BC_RECTANGLE = { w: -139.12771, s: 48.20636, e: -114.02091, n: 60.02025 }
const BC_RECTANGLE_MARGIN = 1.0
const CANADA_EXTENT_BOX = { w: -141.0, e: -52.6, s: 41.7, n: 83.1 }
const CANADA_EXTENT_MARGIN = 0.5
// A box covering at least this share of the whole longitude by latitude
// range covers most of the world (MOST_OF_WORLD in the export).
const MOST_OF_WORLD = 0.5

// True for a box whose west edge is in the eastern half of the world and
// east edge in the western half: it crosses the 180 degree line.
function crosses180(box) {
  return box.w > 0 && box.e < 0
}

// The box with swapped edges put back (south below north, west left of
// east), as step 1 does before comparing it with BC's rectangle; null for a
// box that crosses the 180 degree line, which never matches BC or Canada.
export function straightened(box) {
  if (crosses180(box)) {
    return null
  }
  return {
    w: Math.min(box.w, box.e), e: Math.max(box.w, box.e),
    s: Math.min(box.s, box.n), n: Math.max(box.s, box.n),
  }
}

// True if every edge of box is within margin degrees of the same edge of
// target.
function matches(box, target, margin) {
  return box !== null && ['w', 'e', 's', 'n'].every((edge) => Math.abs(box[edge] - target[edge]) <= margin)
}

// What kind of box one harvested box ({ w, e, s, n }) is, as the export's
// flags say: 'matches_bc_rectangle' or 'matches_canada_extent' (drawn
// dashed; checked first, so such a box with swapped edges still counts),
// 'swapped', 'zero_width' or 'most_of_world' (not drawn), or null for any
// other box (drawn solid).
export function boxKind(box) {
  const straight = straightened(box)
  if (matches(straight, BC_RECTANGLE, BC_RECTANGLE_MARGIN)) {
    return 'matches_bc_rectangle'
  }
  if (matches(straight, CANADA_EXTENT_BOX, CANADA_EXTENT_MARGIN)) {
    return 'matches_canada_extent'
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

// The most decimals a box or point number is shown with; the number as
// harvested is in the tooltip.
const DEGREE_DECIMALS = 4

// A number of degrees as shown: at most DEGREE_DECIMALS decimals, trailing
// zeros dropped (-128.61694335937497 is -128.6169; 35.0 is 35).
export function shownDegrees(value) {
  return String(Number(value.toFixed(DEGREE_DECIMALS)))
}

// A number of degrees exactly as harvested, for the tooltip: no rounding,
// only trailing zeros gone.
export function harvestedDegrees(value) {
  return String(value)
}

// The numbers of a box or a point, each with its name, in the order they are
// written: [['west', -150], ['east', -110], ['south', 35], ['north', 65]] or
// [['latitude', 51.6], ['longitude', -128.1]].
export function coordinateFields(item, kind) {
  return kind === 'box'
    ? [['west', item.w], ['east', item.e], ['south', item.s], ['north', item.n]]
    : [['latitude', item.lat], ['longitude', item.lon]]
}

// How many boxes or points a note lists before "and N more points".
export const SHOWN_COORDINATES = 3

// A note that lists boxes or points: { lead, kind ('box' or 'point'),
// items, brackets, tail }. It reads: lead, a space, the items (each in
// brackets when brackets is true), then tail. Notes like this are objects,
// not text, so the page can put the full number in a tooltip on each
// number and fold a long list.
function coordinatesNote(lead, kind, items, tail, brackets = true) {
  return { lead, kind, items, brackets, tail }
}

// What a coordinates note shows, folded (all false) or not: { items, before,
// more, foldable }. items are the boxes or points shown: the first
// SHOWN_COORDINATES when folded. before[i] is the text before items[i]:
// nothing, then ", ", and " and " before the last unless more follow. more is
// " and 24 more points" when folded, else "". foldable is true when there
// are more than SHOWN_COORDINATES items.
export function shownCoordinates(note, all = false) {
  const extra = note.items.length - SHOWN_COORDINATES
  const folded = !all && extra > 0
  const items = folded ? note.items.slice(0, SHOWN_COORDINATES) : note.items
  const before = items.map((_, i) => {
    if (i === 0) {
      return ''
    }
    return !folded && i === items.length - 1 ? ' and ' : ', '
  })
  const noun = note.kind === 'box' ? ['box', 'boxes'] : ['point', 'points']
  const more = folded ? ` and ${extra} more ${extra === 1 ? noun[0] : noun[1]}` : ''
  return { items, before, more, foldable: extra > 0 }
}

// One box or point written out as shown: "west -150, east -110, south 35,
// north 65", in brackets when the note asks for them.
function coordinatesText(item, kind, brackets) {
  const text = coordinateFields(item, kind).map(([name, value]) => `${name} ${shownDegrees(value)}`).join(', ')
  return brackets ? `(${text})` : text
}

// A note as plain text, as the page shows it (numbers rounded), folded
// unless all is true. Text notes are returned as they are.
export function noteText(note, all = false) {
  if (typeof note === 'string') {
    return note
  }
  const { items, before, more } = shownCoordinates(note, all)
  const list = items.map((item, i) => before[i] + coordinatesText(item, note.kind, note.brackets)).join('')
  return `${note.lead} ${list}${more}${note.tail}`
}

// Why a box with an issue is not drawn, in words.
const NOT_DRAWN_BECAUSE = {
  swapped: 'its edges are the wrong way round (south above north, or west beyond east)',
  zero_width: 'it has no width or no height, so it is a line or a single point',
  most_of_world: 'it covers most of the world, so it does not show where the data is',
}

// The items of a list once each, in order.
function once(items) {
  return [...new Set(items)]
}

// The items in words: "A and B", "A, B and C".
function listed(items) {
  return items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

// The note for the boxes (as harvested) that match BC's rectangle or the
// Canada-wide extent: what they cover, with their numbers, and, when other
// records of the same catalogue have exactly the same box (sharedBy, the
// export's box_shared_by), how many. Facts only: some datasets really do
// cover all of BC or Canada.
function matchingBoxNote(kind, boxes, source, sharedBy) {
  const [extent, area] = kind === 'matches_bc_rectangle' ? ['whole of BC', 'BC'] : ['all of Canada', 'Canada']
  const shared = sharedBy > 1
    ? `; ${source} uses this same box for ${sharedBy.toLocaleString()} of its records in Lunaris, so it may ` +
      "not show where this dataset's data actually are"
    : ''
  return coordinatesNote(`Extent: ${extent}`, 'box', boxes, `. The box covers all of ${area}${shared}.`)
}

// Works out what the map draws for a dataset and what the page says about
// where its location comes from. Returns { boxes, matchingBoxes, points,
// notes }:
//   boxes   the harvested boxes drawn with a solid line (any box that is
//           not one of the kinds below)
//   matchingBoxes  the harvested boxes that match BC's rectangle or the
//           Canada-wide extent, drawn with a dashed line (swapped edges put
//           back)
//   points  the harvested points to draw
//   notes   the sentences to show, in order: text, or for a sentence that
//           lists boxes or points, a coordinates note (see coordinatesNote
//           and noteText)
// The map draws only the record's own boxes and points. The place names our
// lookup found are listed as text only, while the lookup is redone (it
// picked each GeoNames match without the record's context, and some were
// wrong, such as the Columbia Basin placed in Illinois).
// Places pass 1 marked as an author's institution are never drawn or named.
export function describeLocation(dataset) {
  const source = dataset.source_name || 'the source catalogue'
  const boxes = dataset.boxes ?? []
  const points = dataset.points ?? []
  const notes = []

  const kinds = boxes.map(boxKind)
  const drawn = boxes.filter((box, index) => kinds[index] === null)
  if (drawn.length === 1) {
    notes.push(coordinatesNote(`Extent: from the ${source} record`, 'box', drawn, '.'))
  } else if (drawn.length > 1) {
    notes.push(coordinatesNote(`Extent: ${drawn.length} boxes from the ${source} record:`, 'box', drawn, '.'))
  }
  const matchingBoxes = []
  for (const kind of ['matches_bc_rectangle', 'matches_canada_extent']) {
    const indexes = kinds.flatMap((k, index) => (k === kind ? [index] : []))
    if (indexes.length > 0) {
      matchingBoxes.push(...indexes.map((index) => straightened(boxes[index])))
      const sharedBy = Math.max(...indexes.map((index) => dataset.box_shared_by?.[index] ?? 1))
      notes.push(matchingBoxNote(kind, indexes.map((index) => boxes[index]), source, sharedBy))
    }
  }
  boxes.forEach((box, index) => {
    const because = NOT_DRAWN_BECAUSE[kinds[index]]
    if (because) {
      notes.push(coordinatesNote('Box in the record:', 'box', [box], `. Not drawn, because ${because}.`, false))
    }
  })

  if (points.length === 1) {
    notes.push(coordinatesNote('Location: a point from the record', 'point', points, '.'))
  } else if (points.length > 1) {
    notes.push(coordinatesNote(`Location: ${points.length} points from the record:`, 'point', points, '.'))
  }

  // The place names our lookup found are listed only when the record itself
  // gives no solid box and no point, and never an author's institution.
  const named = drawn.length === 0 && points.length === 0
    ? once((dataset.looked_up_places ?? []).filter((place) => place.role !== 'affiliation')
      .map((place) => place.name))
    : []
  if (named.length === 1) {
    notes.push(`${named[0]} (place named in the record; BCBN's lookup is being redone, so it is not drawn).`)
  } else if (named.length > 1) {
    notes.push(`${listed(named)} (places named in the record; BCBN's lookup is being redone, so they are not ` +
      'drawn).')
  }

  if (boxes.length === 0 && points.length === 0 && named.length === 0) {
    notes.push('No location given in the record.')
  }

  return { boxes: drawn, matchingBoxes, points, notes }
}
