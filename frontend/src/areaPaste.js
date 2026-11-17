// Shapes pasted into the "Create an area of interest" panel's Paste tab:
// WKT (POLYGON, MULTIPOLYGON) or GeoJSON (Polygon, MultiPolygon, a Feature
// with one of these, or a FeatureCollection of them), longitude then
// latitude in degrees (WGS 84). Each polygon becomes one polygon shape of
// the area (see areaShapes.js), its corners rounded as drawn corners are.
//
// All or nothing: addPastedShapes either gives the whole area with every
// pasted shape added, or says why not and adds none. It first reads the
// text (this file: what kind of geometry, holes, the coordinate system,
// numbers, ranges), then checks the area as the API would (areaTextProblem
// and shapesProblem in areaShapes.js: crossing edges, at least 3 points, the
// 180th meridian, 250 points in all, each shape's size and the joined
// hexagons), with the API's own messages. Shapes are numbered as they will
// be in the area: after the shapes already in it.

import { areaText, areaTextProblem, readAreaText, roundCorner, shapeName, shapesProblem } from './areaShapes.js'

// The longest text read, in characters. 250 points (the most an area can
// have) take about 10,000 characters even with 15 decimals and GeoJSON's
// brackets; this leaves room for a Feature's own properties, and keeps
// reading quick (well under a tenth of a second) however the text is made.
export const PASTE_MAX_CHARACTERS = 200000

// A small example inside BC, around Kamloops and the north of the Okanagan
// (about 14,000 km², big enough at every hexagon size the map shows).
export const PASTE_EXAMPLE = 'POLYGON((-121 50, -119.2 50, -119 51, -120.4 51.2, -121 50))'

// The line under the text box saying what it takes.
export const PASTE_HELP = 'Polygons as WKT or GeoJSON, longitude then latitude (WGS 84). Several at once are '
  + 'fine (MULTIPOLYGON, FeatureCollection); holes are not supported yet.'

// The GeoJSON coordinate systems that mean longitude and latitude on WGS 84.
const WGS84_NAMES = new Set([
  'urn:ogc:def:crs:OGC:1.3:CRS84', 'urn:ogc:def:crs:OGC::CRS84', 'CRS84', 'OGC:CRS84',
  'EPSG:4326', 'urn:ogc:def:crs:EPSG::4326', 'urn:ogc:def:crs:EPSG:4326', 'http://www.opengis.net/def/crs/OGC/1.3/CRS84',
])

// The WKT geometry types that are not polygons, to say what was pasted.
const OTHER_WKT_TYPES = new Set(['POINT', 'LINESTRING', 'MULTIPOINT', 'MULTILINESTRING', 'GEOMETRYCOLLECTION',
  'CIRCULARSTRING', 'COMPOUNDCURVE', 'CURVEPOLYGON', 'MULTICURVE', 'MULTISURFACE', 'TRIANGLE', 'TIN',
  'POLYHEDRALSURFACE'])

// A paste that cannot be added, with a message for the visitor.
export class PasteProblem extends Error {}

// --- WKT ----------------------------------------------------------------------------

// A WKT number: a sign, digits with a decimal part, and an exponent. It is
// "sticky" (y): it reads at a given place in the text, without copying the
// rest of the text for every number.
const WKT_NUMBER = /[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?/y

// Reads WKT text into polygons, each a list of rings of [lng, lat] (a
// third or fourth number, height or measure, is read and left out).
// Throws a PasteProblem for anything else.
function readWkt(text) {
  let rest = text
  const srid = /^SRID=(\d+)\s*;\s*/i.exec(rest)
  if (srid) {
    if (srid[1] !== '4326') {
      throw new PasteProblem(`The WKT says its coordinates are in SRID=${srid[1]}, not longitude and latitude `
        + '(WGS 84, SRID=4326). Paste longitude and latitude in degrees.')
    }
    rest = rest.slice(srid[0].length)
  }
  const head = /^([A-Za-z]+)\s*(?:ZM|Z|M)?\s*(?=\(|EMPTY\b|$)/i.exec(rest)
  if (!head) {
    throw new PasteProblem('This is not WKT or GeoJSON that can be read. Paste a POLYGON or MULTIPOLYGON in WKT, '
      + 'or a GeoJSON Polygon, MultiPolygon, Feature or FeatureCollection.')
  }
  const kind = head[1].toUpperCase()
  if (kind !== 'POLYGON' && kind !== 'MULTIPOLYGON') {
    if (OTHER_WKT_TYPES.has(kind)) {
      throw new PasteProblem(`Only polygons can be added; this is a ${kind}.`)
    }
    throw new PasteProblem(`This is not WKT that can be read: "${head[1]}" is not a kind of geometry.`)
  }
  let at = head[0].length
  if (/^EMPTY\b/i.test(rest.slice(at))) {
    throw new PasteProblem(`The pasted ${kind} is empty.`)
  }

  // Small readers over rest from position at.
  function skipSpace() {
    while (at < rest.length && /\s/.test(rest[at])) {
      at += 1
    }
  }
  function fail(what) {
    const found = at < rest.length ? `"${rest.slice(at, at + 12)}"` : 'the end of the text'
    throw new PasteProblem(`This is not WKT that can be read: ${what} was expected at character ${at + 1}, `
      + `but there is ${found}.`)
  }
  function expect(character) {
    skipSpace()
    if (rest[at] !== character) {
      fail(`"${character}"`)
    }
    at += 1
  }
  function next(character) {
    skipSpace()
    if (rest[at] === character) {
      at += 1
      return true
    }
    return false
  }
  function point() {
    const numbers = []
    for (;;) {
      skipSpace()
      WKT_NUMBER.lastIndex = at
      const match = WKT_NUMBER.exec(rest)
      if (!match) {
        break
      }
      numbers.push(Number(match[0]))
      at += match[0].length
    }
    if (numbers.length < 2 || numbers.length > 4) {
      fail('a point (longitude, then latitude)')
    }
    return [numbers[0], numbers[1]]
  }
  function ring() {
    expect('(')
    const points = [point()]
    while (next(',')) {
      points.push(point())
    }
    expect(')')
    return points
  }
  function polygon() {
    expect('(')
    const rings = [ring()]
    while (next(',')) {
      rings.push(ring())
    }
    expect(')')
    return rings
  }

  let polygons
  if (kind === 'POLYGON') {
    polygons = [polygon()]
  } else {
    expect('(')
    polygons = [polygon()]
    while (next(',')) {
      polygons.push(polygon())
    }
    expect(')')
  }
  skipSpace()
  if (at < rest.length) {
    fail('nothing more')
  }
  return polygons
}

// --- GeoJSON ------------------------------------------------------------------------

// Checks a GeoJSON "crs" (the old way GeoJSON named a coordinate system):
// throws a PasteProblem unless it names longitude and latitude on WGS 84.
function checkCrs(crs) {
  if (crs === undefined || crs === null) {
    return
  }
  const name = crs?.type === 'name' ? crs.properties?.name : null
  if (!WGS84_NAMES.has(name)) {
    throw new PasteProblem(`The GeoJSON says its coordinates are in ${name ? `"${name}"` : 'another system'}, not `
      + 'longitude and latitude (WGS 84). Paste longitude and latitude in degrees.')
  }
}

// Reads a GeoJSON geometry (Polygon or MultiPolygon) into polygons, each a
// list of rings; what names the geometry for a message ("This" or
// "Feature 2"). Throws a PasteProblem for anything else.
function geojsonPolygons(geometry, what) {
  if (!geometry || typeof geometry !== 'object') {
    throw new PasteProblem(`${what} has no geometry.`)
  }
  const { type, coordinates } = geometry
  if (type === 'Polygon') {
    return [coordinates]
  }
  if (type === 'MultiPolygon') {
    if (!Array.isArray(coordinates)) {
      throw new PasteProblem(`${what} is a MultiPolygon with no coordinates.`)
    }
    return coordinates
  }
  if (typeof type === 'string') {
    throw new PasteProblem(`Only polygons can be added; ${what === 'This' ? 'this' : what} is a ${type}.`)
  }
  throw new PasteProblem(`${what} is not a GeoJSON geometry (it has no "type").`)
}

// Reads GeoJSON text into polygons, each a list of rings (not yet checked).
// Throws a PasteProblem for text that is not GeoJSON polygons.
function readGeojson(text) {
  let value
  try {
    value = JSON.parse(text)
  } catch {
    throw new PasteProblem('This is not valid GeoJSON: the JSON cannot be read (look for a missing bracket, comma '
      + 'or quote).')
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new PasteProblem('This is not GeoJSON: paste a Polygon, MultiPolygon, Feature or FeatureCollection.')
  }
  checkCrs(value.crs)
  if (value.type === 'FeatureCollection') {
    if (!Array.isArray(value.features) || value.features.length === 0) {
      throw new PasteProblem('The pasted FeatureCollection has no features.')
    }
    return value.features.flatMap((feature, index) => {
      const what = `Feature ${index + 1}`
      if (feature?.type !== 'Feature') {
        throw new PasteProblem(`${what} of the FeatureCollection is not a Feature.`)
      }
      checkCrs(feature.crs)
      return geojsonPolygons(feature.geometry, what)
    })
  }
  if (value.type === 'Feature') {
    return geojsonPolygons(value.geometry, 'This Feature')
  }
  return geojsonPolygons(value, 'This')
}

// --- From polygons to shapes ----------------------------------------------------------

// Checks one pasted polygon (a list of rings of positions) and returns its
// outer ring as [lng, lat] corners, the first corner not repeated at the
// end. name is how the messages name it, as areaShapes.js names shapes
// ("The polygon", "Shape 2 (a polygon)"). Throws a PasteProblem for a hole,
// a ring with no points, a point that is not a pair of numbers, or a point
// outside -180 to 180 and -90 to 90.
function polygonRing(rings, name) {
  if (!Array.isArray(rings) || rings.length === 0 || !Array.isArray(rings[0]) || rings[0].length === 0) {
    throw new PasteProblem(`${name} has no points.`)
  }
  if (rings.length > 1) {
    throw new PasteProblem(`${name} has a hole; holes are not supported yet. Paste its outer edge only.`)
  }
  const ring = rings[0].map((position, index) => {
    if (!Array.isArray(position) || position.length < 2
        || !position.slice(0, 2).every((value) => typeof value === 'number' && Number.isFinite(value))) {
      throw new PasteProblem(`${name}: its point ${index + 1} is not a pair of numbers (longitude, latitude).`)
    }
    const [lng, lat] = position
    if (lng < -180 || lng > 180 || lat < -90 || lat > 90) {
      throw new PasteProblem(`${name}: its point ${index + 1} (${lng}, ${lat}) is outside -180 to 180 longitude `
        + 'or -90 to 90 latitude. Paste longitude then latitude, in degrees (WGS 84).')
    }
    return [lng, lat]
  })
  const [first] = ring
  const last = ring[ring.length - 1]
  if (ring.length > 1 && first[0] === last[0] && first[1] === last[1]) {
    // The ring was closed by repeating its first point: drop the copy.
    ring.pop()
  }
  return ring
}

// Reads pasted text into the outer rings of its polygons, in order, each a
// list of [lng, lat] corners (not closed, not yet rounded). firstNumber is
// the place in the area the first of them will take, so the messages name
// them as the area will ("Shape 3 (a polygon)", or "The polygon" when it is
// the area's only shape). Throws a PasteProblem with a message for the
// visitor.
export function readPastedRings(text, firstNumber = 1) {
  const trimmed = (text ?? '').trim()
  if (!trimmed) {
    throw new PasteProblem('Paste a WKT or GeoJSON polygon first.')
  }
  if (trimmed.length > PASTE_MAX_CHARACTERS) {
    throw new PasteProblem(`The pasted text has ${trimmed.length.toLocaleString('en-US')} characters, more than `
      + `the ${PASTE_MAX_CHARACTERS.toLocaleString('en-US')} that can be read. Paste fewer or simpler shapes.`)
  }
  const polygons = trimmed.startsWith('{') || trimmed.startsWith('[') ? readGeojson(trimmed) : readWkt(trimmed)
  const count = firstNumber - 1 + polygons.length
  return polygons.map((rings, index) => polygonRing(rings, shapeName(firstNumber + index, count, false)))
}

// Adds the shapes of a pasted text to an area: existing is the area's
// shapes now (none for no area) and res the hexagon size to check them at
// (the area's, or the map's when the area is empty). Returns { shapes } with
// every shape of the area after the paste, read back from its area text so
// the page holds exactly what the API will read, or { problem } with the
// reason nothing was added.
export function addPastedShapes(text, existing, res) {
  try {
    const rings = readPastedRings(text, existing.length + 1)
    const pasted = rings.map((ring) => ({ ring: ring.map(roundCorner), isBox: false, box: null }))
    const written = areaText([...existing, ...pasted])
    const problem = areaTextProblem(written)
    if (problem) {
      return { problem }
    }
    const shapes = readAreaText(written)
    const sizeProblem = shapesProblem(shapes, res)
    return sizeProblem ? { problem: sizeProblem } : { shapes }
  } catch (error) {
    if (error instanceof PasteProblem) {
      return { problem: error.message }
    }
    throw error
  }
}
