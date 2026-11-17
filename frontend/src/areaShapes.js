// An area made of several shapes, on the front end: the shapes themselves,
// the area text that carries them in the page's address and to the API,
// every check the API makes (with the API's own messages), their sizes and
// their hexagons. Each part copies its counterpart in backend/api/area.py
// (read_area_shapes, check_shape_sizes, area_cells and the functions they
// use), and a back end test (backend/tests/test_area_shapes_js.py) runs this
// file with node to check the two still agree, message for message.
//
// A shape is { ring, isBox, box }: ring is its corners as [lng, lat] in
// degrees, in the order given, not closed (the first corner is not repeated
// at the end, and no corner comes twice in a row); isBox says whether it
// was given as a box; box is { west, south, east, north } for a box, else
// null. A box's ring runs west-south, east-south, east-north, west-north.
//
// The area text: shapes separated by ";", "b:west,south,east,north" for a
// box and "p:lng,lat,lng,lat,..." for a polygon (its first point not
// repeated at the end; a repeated one is dropped). An old link's
// "west,south,east,north" with no prefix is one box.

import { POLYGON_TO_CELLS_FLAGS, getHexagonAreaAvg, polygonToCellsExperimental } from 'h3-js'

import { EARTH_RADIUS_KM, MAX_AREA_HEXAGONS, MIN_AREA_HEXAGONS, boxAreaKm2 } from './area.js'

// The most points a whole area may have, every shape together (a box counts
// as its 4 corners): the same as MAX_AREA_POINTS in backend/api/area.py.
// It depends on how long a GBIF link gbif.org accepts, and may change.
export const MAX_AREA_POINTS = 250

// How the area text separates its shapes and names each kind of shape.
const SHAPE_SEPARATOR = ';'
const BOX_PREFIX = 'b:'
const POLYGON_PREFIX = 'p:'

// A number of degrees as the area text writes it: an optional sign, then
// digits with an optional decimal part. The same pattern as DEGREES in
// area.py (ASCII digits only, no exponent, no "inf" or "nan").
const DEGREES = /^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/

// How much of a value a message quotes back, at most.
const QUOTED_LENGTH = 30

// The box's edges, in the order the area text gives them.
const BOX_EDGES = ['west', 'south', 'east', 'north']

// Two corners joined by an edge may not be this many degrees of longitude
// apart or more (see MAX_EDGE_LONGITUDE_SPAN in area.py).
const MAX_EDGE_LONGITUDE_SPAN = 180

// How many decimals of a degree the page writes a shape's corners with:
// four is about 11 m (as the box always had).
const AREA_DECIMALS = 4

// A request the API would refuse, with the API's message.
export class AreaProblem extends Error {}

// --- Python's ways with text, so the messages match exactly -------------

// The characters Python's str.strip() takes off the ends (str.isspace).
const PYTHON_SPACE = '\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0        '
  + '        　'

// Returns text with white space taken off both ends, as Python's strip()
// does (JavaScript's trim() takes a slightly different set of characters).
function pyStrip(text) {
  let start = 0
  let end = text.length
  while (start < end && PYTHON_SPACE.includes(text[start])) {
    start += 1
  }
  while (end > start && PYTHON_SPACE.includes(text[end - 1])) {
    end -= 1
  }
  return text.slice(start, end)
}

// The characters Python's repr() writes as an escape (str.isprintable is
// false for them): control, format, surrogate, private use, unassigned,
// line and paragraph separators, and spaces other than " ".
const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u

// Returns text as Python's repr() writes a string: in single quotes (double
// quotes when it holds a single quote and no double quote), with the quote,
// backslash and characters that are not printable escaped.
function pyRepr(text) {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'"
  let out = ''
  for (const character of text) {
    const code = character.codePointAt(0)
    if (character === quote || character === '\\') {
      out += `\\${character}`
    } else if (character === '\t') {
      out += '\\t'
    } else if (character === '\n') {
      out += '\\n'
    } else if (character === '\r') {
      out += '\\r'
    } else if (character !== ' ' && NOT_PRINTABLE.test(character)) {
      const hex = code.toString(16)
      out += code < 0x100 ? `\\x${hex.padStart(2, '0')}`
        : code < 0x10000 ? `\\u${hex.padStart(4, '0')}` : `\\U${hex.padStart(8, '0')}`
    } else {
      out += character
    }
  }
  return quote + out + quote
}

// Returns a value for a message as area.py's quoted() does: stripped, cut
// to QUOTED_LENGTH characters (with "...") when longer, in Python's quotes.
function quoted(text) {
  const characters = [...pyStrip(text)]
  const kept = characters.length <= QUOTED_LENGTH ? characters.join('')
    : `${characters.slice(0, QUOTED_LENGTH).join('')}...`
  return pyRepr(kept)
}

// Returns quoted(text) without its quote marks, as area.py writes a number
// inside a sentence (quoted(text)[1:-1]).
function bare(text) {
  return quoted(text).slice(1, -1)
}

// Returns a number as a whole number over a power of two, exactly:
// { num: BigInt, shift } with value = num / 2^shift (shift as small as
// possible). Doubling a float is exact, so nothing is rounded. Only for
// finite numbers (the checks refuse the others first).
function exactParts(value) {
  if (!Number.isFinite(value)) {
    throw new RangeError(`not a finite number: ${value}`)
  }
  let num = value
  let shift = 0
  while (!Number.isInteger(num)) {
    num *= 2
    shift += 1
  }
  return { num: BigInt(num), shift }
}

// Writes a number with a fixed number of decimals and commas between
// thousands, exactly as Python's format(value, ",.Nf") does: from the
// number's exact value, a half rounded to the even digit.
export function pyFixed(value, decimals) {
  const { num, shift } = exactParts(Math.abs(value))
  const scaled = num * 10n ** BigInt(decimals)
  const denominator = 1n << BigInt(shift)
  let whole = scaled / denominator
  const twice = 2n * (scaled % denominator)
  if (twice > denominator || (twice === denominator && whole % 2n === 1n)) {
    whole += 1n
  }
  const digits = whole.toString().padStart(decimals + 1, '0')
  const integer = digits.slice(0, digits.length - decimals)
  const fraction = digits.slice(digits.length - decimals)
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  // Python keeps the minus sign of a negative number even when it rounds
  // to zero ("-0.0").
  const sign = value < 0 || Object.is(value, -0) ? '-' : ''
  return `${sign}${grouped}${decimals ? `.${fraction}` : ''}`
}

// Writes a whole number with commas between thousands, as Python's
// format(value, ",") does.
function pyInt(value) {
  return pyFixed(value, 0)
}

// --- Reading the area text ---------------------------------------------------

// How a message names a shape: "The box" or "The polygon" when the area has
// one shape, else "Shape 2 (a box)" and so on, counting from 1.
export function shapeName(number, count, isBox) {
  const kind = isBox ? 'box' : 'polygon'
  return count === 1 ? `The ${kind}` : `Shape ${number} (a ${kind})`
}

// Reads one number of a shape as the number every later step uses (-0 read
// as 0), or throws an AreaProblem when it is empty or not a plain number of
// degrees.
function readDegrees(name, text) {
  const stripped = pyStrip(text)
  if (!DEGREES.test(stripped)) {
    if (!stripped) {
      throw new AreaProblem(`${name} has an empty value where a number of degrees should be.`)
    }
    const hint = stripped.includes(':') ? ' (separate shapes with ";")' : ''
    throw new AreaProblem(`${name} has ${quoted(stripped)} where a number of degrees should be${hint}.`)
  }
  return Number(stripped) + 0
}

// Throws an AreaProblem when a longitude (limit 180) or latitude (limit 90)
// is outside -limit to limit degrees; what says which number it is.
function checkRange(name, what, value, text, limit) {
  if (!(value >= -limit && value <= limit)) {
    throw new AreaProblem(`${name}: its ${what} is ${bare(text)}, outside -${limit} to ${limit} degrees.`)
  }
}

// Splits the area text into [isBox, the text after the prefix] pairs, in
// order. An old link's text (one part, no ":", not starting with a letter)
// is one box. Throws an AreaProblem for an empty text, an empty shape or a
// shape with no known prefix.
function splitShapes(text) {
  if (text === null || text === undefined || !pyStrip(String(text))) {
    throw new AreaProblem('Give the area as area=..., one or more shapes separated by ";": '
      + 'b:west,south,east,north for a box, or p:lng,lat,lng,lat,... for a polygon.')
  }
  const parts = pyStrip(String(text)).split(SHAPE_SEPARATOR).map(pyStrip)
  if (parts.length === 1 && !parts[0].includes(':') && !/^\p{L}/u.test(parts[0])) {
    // An old link (area=west,south,east,north): one box.
    return [[true, parts[0]]]
  }
  return parts.map((part, index) => {
    const where = parts.length === 1 ? 'The area' : `Shape ${index + 1}`
    if (!part) {
      throw new AreaProblem(`${where} is empty: remove the extra ";".`)
    }
    if (part.startsWith(BOX_PREFIX)) {
      return [true, part.slice(BOX_PREFIX.length)]
    }
    if (part.startsWith(POLYGON_PREFIX)) {
      return [false, part.slice(POLYGON_PREFIX.length)]
    }
    const prefix = part.includes(':') ? `${part.split(':')[0]}:` : null
    const start = prefix ? `starts with ${quoted(prefix)}, which is not a kind of shape`
      : 'does not say what kind of shape it is'
    throw new AreaProblem(`${where} ${start}: start it with b: for a box or p: for a polygon.`)
  })
}

// Reads every number of a shape's text (after its prefix), or throws an
// AreaProblem with the message empty when the text is empty. Returns
// [the texts, the numbers].
function readNumbers(name, body, empty) {
  if (!pyStrip(body)) {
    throw new AreaProblem(empty)
  }
  const texts = body.split(',')
  return [texts, texts.map((text) => readDegrees(name, text))]
}

// The error for a shape with two joined corners 180 or more degrees of
// longitude apart.
function acrossMeridian(name) {
  return new AreaProblem(`${name} has two joined corners 180 or more degrees of longitude apart, so it would be `
    + 'read as crossing the 180th meridian. An area across that meridian is not taken.')
}

// Reads a box shape from its text "west,south,east,north", with the API's
// checks: 4 readable numbers in range, west less than east, south less than
// north, and less than 180 degrees of longitude across.
function readBoxShape(name, body) {
  const [texts, values] = readNumbers(name, body, `${name} is empty: give it as b:west,south,east,north.`)
  if (values.length !== 4) {
    throw new AreaProblem(`${name} needs 4 numbers, west,south,east,north; it has ${values.length}.`)
  }
  BOX_EDGES.forEach((edge, index) => {
    checkRange(name, `${edge} edge`, values[index], texts[index], edge === 'west' || edge === 'east' ? 180 : 90)
  })
  const [west, south, east, north] = values
  if (west >= east) {
    throw new AreaProblem(`${name}: its west edge (${bare(texts[0])}) must be less than its east edge `
      + `(${bare(texts[2])}).`)
  }
  if (south >= north) {
    throw new AreaProblem(`${name}: its south edge (${bare(texts[1])}) must be less than its north edge `
      + `(${bare(texts[3])}).`)
  }
  if (east - west >= MAX_EDGE_LONGITUDE_SPAN) {
    throw acrossMeridian(name)
  }
  return boxShapeOf({ west, south, east, north })
}

// A box ({ west, south, east, north }) as a shape.
export function boxShapeOf(box) {
  const { west, south, east, north } = box
  return { ring: [[west, south], [east, south], [east, north], [west, north]], isBox: true, box: { ...box } }
}

// Reads a polygon's points from its text "lng,lat,lng,lat,...", checking
// the numbers are readable, come in pairs and are in range: [{ number as
// given, lng, lat }]. A last point that repeats the first is dropped.
function readPolygonPoints(name, body) {
  const [texts, values] = readNumbers(name, body, `${name} is empty: give it as p:lng,lat,lng,lat,...`)
  if (values.length % 2) {
    throw new AreaProblem(`${name} needs its numbers in pairs, longitude then latitude; it has `
      + `${values.length} ${values.length === 1 ? 'number' : 'numbers'}.`)
  }
  const points = []
  for (let index = 0; index < values.length; index += 2) {
    const number = index / 2 + 1
    checkRange(name, `point ${number}'s longitude`, values[index], texts[index], 180)
    checkRange(name, `point ${number}'s latitude`, values[index + 1], texts[index + 1], 90)
    points.push({ number, lng: values[index], lat: values[index + 1] })
  }
  if (points.length > 1 && samePoint(points[points.length - 1], points[0])) {
    // The first point repeated at the end: the ring is closed by whoever
    // uses it, so the copy is dropped.
    points.pop()
  }
  return points
}

// Whether two points are the same corner.
function samePoint(a, b) {
  return a.lng === b.lng && a.lat === b.lat
}

// --- Edges that cross or touch, checked exactly -------------------------------

// The corners [lng, lat] as whole-number pairs (BigInt), all multiplied by
// the same power of two, so the checks compare exactly the numbers kept
// (as exact_corners in area.py does).
function exactCorners(corners) {
  const parts = corners.flatMap(([lng, lat]) => [exactParts(lng), exactParts(lat)])
  const shift = Math.max(...parts.map((part) => part.shift))
  const numbers = parts.map((part) => part.num << BigInt(shift - part.shift))
  const result = []
  for (let index = 0; index < numbers.length; index += 2) {
    result.push([numbers[index], numbers[index + 1]])
  }
  return result
}

// Which way c lies from the line a to b: positive to the left, negative to
// the right, 0 on the line.
function orientation(a, b, c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}

// The smaller and the larger of two BigInts.
function minBig(a, b) {
  return a < b ? a : b
}
function maxBig(a, b) {
  return a > b ? a : b
}

// Whether c, already known to be on the line through a and b, lies between
// them (ends included).
function onSegment(a, b, c) {
  return minBig(a[0], b[0]) <= c[0] && c[0] <= maxBig(a[0], b[0])
    && minBig(a[1], b[1]) <= c[1] && c[1] <= maxBig(a[1], b[1])
}

// Whether the segment a-b and the segment c-d have any point in common:
// crossing, touching at an end, or lying along each other.
function segmentsMeet(a, b, c, d) {
  const o1 = orientation(a, b, c)
  const o2 = orientation(a, b, d)
  const o3 = orientation(c, d, a)
  const o4 = orientation(c, d, b)
  if (((o1 > 0n && o2 < 0n) || (o1 < 0n && o2 > 0n)) && ((o3 > 0n && o4 < 0n) || (o3 < 0n && o4 > 0n))) {
    return true
  }
  return (o1 === 0n && onSegment(a, b, c)) || (o2 === 0n && onSegment(a, b, d))
    || (o3 === 0n && onSegment(c, d, a)) || (o4 === 0n && onSegment(c, d, b))
}

// Whether two neighbouring edges a-b and b-d, which share the corner b,
// meet anywhere else: only when one doubles back along the other.
function neighboursMeet(a, b, d) {
  return (orientation(a, b, d) === 0n && onSegment(a, b, d)) || (orientation(b, d, a) === 0n && onSegment(b, d, a))
}

// The first pair of edges of a closed ring (exact corners, the last joined
// back to the first) that meet anywhere but at the corner two neighbouring
// edges share, as [i, j] (edge i runs from corner i to corner i + 1), or
// null. The same pairs in the same order as first_crossing in area.py.
function firstCrossing(corners) {
  const count = corners.length
  const edges = corners.map((corner, index) => [corner, corners[(index + 1) % count]])
  const boxes = edges.map(([a, b]) => [minBig(a[0], b[0]), maxBig(a[0], b[0]), minBig(a[1], b[1]), maxBig(a[1], b[1])])
  for (let i = 0; i < count; i += 1) {
    const [a, b] = edges[i]
    for (let j = i + 1; j < count; j += 1) {
      const boxI = boxes[i]
      const boxJ = boxes[j]
      if (boxI[1] < boxJ[0] || boxJ[1] < boxI[0] || boxI[3] < boxJ[2] || boxJ[3] < boxI[2]) {
        continue
      }
      const [c, d] = edges[j]
      if (j === i + 1) {
        // Neighbours sharing b (= c).
        if (neighboursMeet(a, b, d)) {
          return [i, j]
        }
      } else if (i === 0 && j === count - 1) {
        // Neighbours sharing a (= d), the ring's closing corner.
        if (neighboursMeet(c, a, b)) {
          return [i, j]
        }
      } else if (segmentsMeet(a, b, c, d)) {
        return [i, j]
      }
    }
  }
  return null
}

// Whether a polygon's ring of [lng, lat] corners (not closed, already
// checked) crosses or touches itself, the closing edge included.
export function ringCrosses(ring) {
  return firstCrossing(exactCorners(ring)) !== null
}

// Whether adding one more corner (point, [lng, lat]) to a polygon being
// drawn (ring, its corners so far in order, not closed, with no crossing
// yet) would make the new edge, from the last corner to point, cross or
// touch an earlier edge, or run back along the edge before it. A point
// equal to the last corner adds no edge (it would be dropped), so it does
// not cross. Whether the polygon can then be closed is ringCrosses on the
// whole ring.
export function pointWouldCross(ring, point) {
  if (ring.length === 0) {
    return false
  }
  const last = ring[ring.length - 1]
  if (last[0] === point[0] && last[1] === point[1]) {
    return false
  }
  const corners = exactCorners([...ring, point])
  const count = corners.length
  const a = corners[count - 2]
  const b = corners[count - 1]
  if (count >= 3 && neighboursMeet(corners[count - 3], a, b)) {
    return true
  }
  for (let index = 0; index < count - 3; index += 1) {
    if (segmentsMeet(corners[index], corners[index + 1], a, b)) {
      return true
    }
  }
  return false
}

// Names an edge of a polygon by the points it joins, numbered as given.
function edgeText(points, index) {
  const start = points[index].number
  const end = points[(index + 1) % points.length].number
  return `the edge from point ${start} to point ${end}`
}

// Makes a polygon shape from its points (readPolygonPoints), dropping a
// point given twice in a row, with the API's checks: at least 3 different
// points, nothing across the 180th meridian, no edges that cross or touch.
function readPolygonShape(name, points) {
  const kept = []
  for (const point of points) {
    if (!kept.length || !samePoint(kept[kept.length - 1], point)) {
      kept.push(point)
    }
  }
  if (kept.length > 1 && samePoint(kept[kept.length - 1], kept[0])) {
    kept.pop()
  }
  const different = new Set(points.map((point) => `${point.lng} ${point.lat}`)).size
  if (different < 3) {
    throw new AreaProblem(`${name} has ${different} different ${different === 1 ? 'point' : 'points'}; a polygon `
      + 'needs at least 3.')
  }
  kept.forEach((point, index) => {
    const following = kept[(index + 1) % kept.length]
    if (Math.abs(point.lng - following.lng) >= MAX_EDGE_LONGITUDE_SPAN) {
      throw acrossMeridian(name)
    }
  })
  const crossing = firstCrossing(exactCorners(kept.map((point) => [point.lng, point.lat])))
  if (crossing) {
    const [i, j] = crossing
    throw new AreaProblem(`${name} crosses or touches itself: ${edgeText(kept, i)} meets ${edgeText(kept, j)}. `
      + 'Draw it so that no two edges cross or touch.')
  }
  return { ring: kept.map((point) => [point.lng, point.lat]), isBox: false, box: null }
}

// Reads an area text into its shapes, in order, with every check the API
// makes (read_area_shapes in area.py), or throws an AreaProblem with the
// API's message. The point count comes before the crossing checks.
export function readAreaText(text) {
  const parts = splitShapes(text)
  const read = parts.map(([isBox, body], index) => {
    const name = shapeName(index + 1, parts.length, isBox)
    return [name, isBox ? readBoxShape(name, body) : readPolygonPoints(name, body)]
  })
  const total = read.reduce((sum, [, shape]) => sum + (Array.isArray(shape) ? shape.length : 4), 0)
  if (total > MAX_AREA_POINTS) {
    throw new AreaProblem(`The area has ${pyInt(total)} points, more than the ${pyInt(MAX_AREA_POINTS)} an area `
      + 'can have (a box counts as 4). Use fewer points or fewer shapes.')
  }
  return read.map(([name, shape]) => (Array.isArray(shape) ? readPolygonShape(name, shape) : shape))
}

// What is wrong with an area text, with the API's message, or null when
// nothing is.
export function areaTextProblem(text) {
  try {
    readAreaText(text)
    return null
  } catch (error) {
    if (error instanceof AreaProblem) {
      return error.message
    }
    throw error
  }
}

// --- Writing the area text --------------------------------------------------

// Writes a number of degrees for the area text: rounded to AREA_DECIMALS,
// without trailing zeros, as the page has always written a box's edges
// ("-120.6", "50.55"); -0 is written "0".
function degreesText(value) {
  return String(Number(value.toFixed(AREA_DECIMALS)) + 0)
}

// Writes shapes as the area text the API reads: "b:west,south,east,north"
// for a box and "p:lng,lat,..." for a polygon, separated by ";". The page's
// shapes already have 4 decimals (corners are rounded as they are drawn),
// so reading what this writes gives the same shapes back.
export function areaText(shapes) {
  return shapes.map((shape) => {
    if (shape.isBox) {
      const { west, south, east, north } = shape.box
      return `${BOX_PREFIX}${[west, south, east, north].map(degreesText).join(',')}`
    }
    return `${POLYGON_PREFIX}${shape.ring.flatMap(([lng, lat]) => [degreesText(lng), degreesText(lat)]).join(',')}`
  }).join(SHAPE_SEPARATOR)
}

// Rounds a corner [lng, lat] to AREA_DECIMALS, as the page keeps every
// corner it draws.
export function roundCorner([lng, lat]) {
  return [Number(lng.toFixed(AREA_DECIMALS)) + 0, Number(lat.toFixed(AREA_DECIMALS)) + 0]
}

// --- Sizes ------------------------------------------------------------------------

// Turns degrees into radians the way Python's math.radians does (times
// pi / 180), so the areas match the API's to the last digit as far as the
// sine functions allow.
function radians(degrees) {
  return degrees * (Math.PI / 180)
}

// A polygon's area in km² on H3's sphere, from its ring of [lng, lat]
// corners (not closed; either way round): the spherical polygon formula
// turf uses (@turf/area's ringArea, from Chamberlain and Duquette, "Some
// Algorithms for Polygons on a Sphere", JPL Publication 07-03, 2007), with
// H3's radius, added up in the same order as polygon_area_km2 in area.py.
export function polygonAreaKm2(ring) {
  let total = 0
  for (let index = 0; index < ring.length; index += 1) {
    const before = ring[(index - 1 + ring.length) % ring.length]
    const corner = ring[index]
    const after = ring[(index + 1) % ring.length]
    total += (radians(after[0]) - radians(before[0])) * Math.sin(radians(corner[1]))
  }
  return (Math.abs(total) * EARTH_RADIUS_KM ** 2) / 2
}

// A shape's area in km²: the box formula for a box (boxAreaKm2 in area.js,
// as before shapes came in), the polygon formula for a polygon.
export function shapeAreaKm2(shape) {
  return shape.isBox ? boxAreaKm2(shape.box) : polygonAreaKm2(shape.ring)
}

// The average hexagon's area at a resolution, in km².
function average(res) {
  return getHexagonAreaAvg(res, 'km2')
}

// The message for a shape (name, kind) over MAX_AREA_HEXAGONS hexagons
// (count, about), as too_many_hexagons in area.py writes it.
function tooManyText(count, res, name, kind) {
  return `${name} touches about ${pyFixed(count, 0)} hexagons of about ${pyFixed(average(res), 1)} km², more than `
    + `the ${pyInt(MAX_AREA_HEXAGONS)} an area search can count. Draw a smaller ${kind} or use larger hexagons.`
}

// What is wrong with the shapes' sizes at a resolution, or null: each shape
// must cover at least MIN_AREA_HEXAGONS average hexagons and, judging by its
// area, touch at most MAX_AREA_HEXAGONS hexagons on its own (check_shape_sizes
// in area.py, the same checks, order and messages). No hexagon is found.
export function shapeSizeProblem(shapes, res) {
  const smallest = MIN_AREA_HEXAGONS * average(res)
  for (let index = 0; index < shapes.length; index += 1) {
    const shape = shapes[index]
    const name = shapeName(index + 1, shapes.length, shape.isBox)
    const kind = shape.isBox ? 'box' : 'polygon'
    const area = shapeAreaKm2(shape)
    if (area < smallest) {
      return `${name} is about ${pyFixed(area, 1)} km², smaller than the minimum of ${pyFixed(smallest, 1)} km² `
        + `for hexagons of about ${pyFixed(average(res), 1)} km² (${MIN_AREA_HEXAGONS} times their average area). `
        + `Draw a larger ${kind}.`
    }
    if (area / average(res) > MAX_AREA_HEXAGONS) {
      return tooManyText(area / average(res), res, name, kind)
    }
  }
  return null
}

// The hexagons at resolution res that overlap one shape, even by a corner:
// h3-js's overlap search, the same as the API's (shape_cells in area.py).
export function shapeCells(shape, res) {
  return polygonToCellsExperimental(shape.ring.map(([lng, lat]) => [lat, lng]), res,
    POLYGON_TO_CELLS_FLAGS.containmentOverlapping)
}

// The hexagons of an area: every hexagon at resolution res that overlaps
// any of the shapes, each once, sorted, as { cells, problem }. Shapes are
// added one at a time and the search stops as soon as the joined set passes
// MAX_AREA_HEXAGONS, with the API's message as problem (area_cells in
// area.py); problem is null otherwise.
export function shapesCells(shapes, res) {
  const cells = new Set()
  for (const shape of shapes) {
    for (const cell of shapeCells(shape, res)) {
      cells.add(cell)
    }
    if (cells.size > MAX_AREA_HEXAGONS) {
      const problem = shapes.length === 1
        ? tooManyText(cells.size, res, shapeName(1, 1, shape.isBox), shape.isBox ? 'box' : 'polygon')
        : `The area's shapes together touch at least ${pyInt(cells.size)} hexagons of about `
          + `${pyFixed(average(res), 1)} km², more than the ${pyInt(MAX_AREA_HEXAGONS)} an area search can count. `
          + 'Use smaller or fewer shapes, or larger hexagons.'
      return { cells: [...cells].sort(), problem }
    }
  }
  return { cells: [...cells].sort(), problem: null }
}

// What is wrong with an area of shapes at a resolution, with the API's
// message, or null: the sizes first (no hexagon found), then the joined
// hexagons against the maximum, as the API checks them.
export function shapesProblem(shapes, res) {
  return shapeSizeProblem(shapes, res) ?? shapesCells(shapes, res).problem
}
