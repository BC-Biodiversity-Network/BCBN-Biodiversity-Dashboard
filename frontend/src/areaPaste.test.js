import { getHexagonAreaAvg } from 'h3-js'
import { describe, expect, it } from 'vitest'

import { PASTE_EXAMPLE, PASTE_MAX_CHARACTERS, addPastedShapes, readPastedRings } from './areaPaste'
import { areaText, boxShapeOf, pyFixed, readAreaText, shapeAreaKm2, shapesProblem } from './areaShapes'

// The example's ring, as read (not closed).
const EXAMPLE_RING = [[-121, 50], [-119.2, 50], [-119, 51], [-120.4, 51.2]]

// Two polygons near Kamloops and Prince George, each big enough at every
// hexagon size, as GeoJSON rings (closed).
const KAMLOOPS = [[-121, 50], [-119.2, 50], [-119, 51], [-120.4, 51.2], [-121, 50]]
const PRINCE_GEORGE = [[-123.5, 53.5], [-122, 53.5], [-122, 54.5], [-123.5, 54.5], [-123.5, 53.5]]

// A GeoJSON text of a value.
function geojson(value) {
  return JSON.stringify(value)
}

// What addPastedShapes says when it refuses text (into an empty area, at
// resolution 6 unless given).
function refused(text, existing = [], res = 6) {
  const result = addPastedShapes(text, existing, res)
  expect(result.shapes).toBeUndefined()
  return result.problem
}

describe('reading WKT', () => {
  it('reads a POLYGON, closed or not, in any case and spacing, with or without a height', () => {
    expect(readPastedRings(PASTE_EXAMPLE)).toEqual([EXAMPLE_RING])
    expect(readPastedRings('POLYGON((-121 50,-119.2 50,-119 51,-120.4 51.2))')).toEqual([EXAMPLE_RING])
    expect(readPastedRings('  polygon (( -121 50 , -119.2 50 , -119 51 , -120.4 51.2 , -121 50 ))\n'))
      .toEqual([EXAMPLE_RING])
    expect(readPastedRings('POLYGON Z ((-121 50 3, -119.2 50 3, -119 51 3, -120.4 51.2 3, -121 50 3))'))
      .toEqual([EXAMPLE_RING])
    expect(readPastedRings('SRID=4326;POLYGON((-121 50, -119.2 50, -119 51, -120.4 51.2, -121 50))'))
      .toEqual([EXAMPLE_RING])
    expect(readPastedRings('POLYGON((-1.21e2 5e1, -119.2 50, -119 51, -120.4 51.2))')).toEqual([EXAMPLE_RING])
  })

  it('reads a MULTIPOLYGON as several shapes, in order', () => {
    const text = 'MULTIPOLYGON(((-121 50, -119.2 50, -119 51, -120.4 51.2, -121 50)), '
      + '((-123.5 53.5, -122 53.5, -122 54.5, -123.5 54.5)))'
    expect(readPastedRings(text)).toEqual([EXAMPLE_RING, PRINCE_GEORGE.slice(0, 4)])
  })

  it.each([
    ['POINT(-121 50)', 'Only polygons can be added; this is a POINT.'],
    ['LINESTRING(-121 50, -119 51)', 'Only polygons can be added; this is a LINESTRING.'],
    ['MULTILINESTRING((-121 50, -119 51))', 'Only polygons can be added; this is a MULTILINESTRING.'],
    ['GEOMETRYCOLLECTION(POLYGON((0 0, 1 0, 1 1, 0 0)))', 'Only polygons can be added; this is a GEOMETRYCOLLECTION.'],
    ['POLYGON EMPTY', 'The pasted POLYGON is empty.'],
    ['POLYGON((-121 50, -119.2 50, -119 51)', 'This is not WKT that can be read: ")" was expected at character 38, '
      + 'but there is the end of the text.'],
    ['POLYGON((-121 50, -119.2, -119 51))', 'This is not WKT that can be read: a point (longitude, then latitude) '
      + 'was expected at character 25, but there is ", -119 51))".'],
    ['POLYGON((-121 50, -119.2 50, -119 51)) extra', 'This is not WKT that can be read: nothing more was expected at '
      + 'character 40, but there is "extra".'],
    ['SHAPE((0 0))', 'This is not WKT that can be read: "SHAPE" is not a kind of geometry.'],
    ['hello there', 'This is not WKT or GeoJSON that can be read. Paste a POLYGON or MULTIPOLYGON in WKT, or a '
      + 'GeoJSON Polygon, MultiPolygon, Feature or FeatureCollection.'],
    ['SRID=3005;POLYGON((1 1, 2 1, 2 2, 1 1))', 'The WKT says its coordinates are in SRID=3005, not longitude and '
      + 'latitude (WGS 84, SRID=4326). Paste longitude and latitude in degrees.'],
  ])('refuses %j', (text, message) => {
    expect(refused(text)).toBe(message)
  })
})

describe('reading GeoJSON', () => {
  const polygon = { type: 'Polygon', coordinates: [KAMLOOPS] }

  it('reads a Polygon, a MultiPolygon, a Feature and a FeatureCollection', () => {
    expect(readPastedRings(geojson(polygon))).toEqual([EXAMPLE_RING])
    expect(readPastedRings(geojson({ type: 'Polygon', coordinates: [KAMLOOPS.slice(0, 4)] }))).toEqual([EXAMPLE_RING])
    expect(readPastedRings(geojson({ type: 'MultiPolygon', coordinates: [[KAMLOOPS], [PRINCE_GEORGE]] })))
      .toEqual([EXAMPLE_RING, PRINCE_GEORGE.slice(0, 4)])
    expect(readPastedRings(geojson({ type: 'Feature', properties: { name: 'x' }, geometry: polygon })))
      .toEqual([EXAMPLE_RING])
    const collection = { type: 'FeatureCollection', features: [
      { type: 'Feature', properties: {}, geometry: polygon },
      { type: 'Feature', properties: {}, geometry: { type: 'MultiPolygon', coordinates: [[PRINCE_GEORGE]] } },
    ] }
    expect(readPastedRings(geojson(collection))).toEqual([EXAMPLE_RING, PRINCE_GEORGE.slice(0, 4)])
    // Heights are read and left out; a crs naming WGS 84 is fine.
    expect(readPastedRings(geojson({ type: 'Polygon', coordinates: [KAMLOOPS.map(([x, y]) => [x, y, 120])],
      crs: { type: 'name', properties: { name: 'urn:ogc:def:crs:OGC:1.3:CRS84' } } }))).toEqual([EXAMPLE_RING])
  })

  it.each([
    ['{"type": "Polygon", "coordinates": [[[0, 0], [1, 0]]', 'This is not valid GeoJSON: the JSON cannot be read '
      + '(look for a missing bracket, comma or quote).'],
    ['[1, 2]', 'This is not GeoJSON: paste a Polygon, MultiPolygon, Feature or FeatureCollection.'],
    [geojson({ type: 'Point', coordinates: [-121, 50] }), 'Only polygons can be added; this is a Point.'],
    [geojson({ type: 'LineString', coordinates: [[-121, 50], [-119, 51]] }),
      'Only polygons can be added; this is a LineString.'],
    [geojson({ type: 'Feature', geometry: null }), 'This Feature has no geometry.'],
    [geojson({ type: 'FeatureCollection', features: [] }), 'The pasted FeatureCollection has no features.'],
    [geojson({ type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Polygon',
      coordinates: [KAMLOOPS] } }, { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] } }] }),
    'Only polygons can be added; Feature 2 is a Point.'],
    [geojson({ type: 'Polygon', coordinates: [KAMLOOPS],
      crs: { type: 'name', properties: { name: 'urn:ogc:def:crs:EPSG::3005' } } }),
    'The GeoJSON says its coordinates are in "urn:ogc:def:crs:EPSG::3005", not longitude and latitude (WGS 84). '
      + 'Paste longitude and latitude in degrees.'],
    [geojson({ hello: 1 }), 'This is not a GeoJSON geometry (it has no "type").'],
  ])('refuses %s', (text, message) => {
    expect(refused(text)).toBe(message)
  })
})

describe('checking each pasted polygon', () => {
  it.each([
    ['POLYGON((-121 50, -119 50, -119 51, -121 51), (-120.5 50.2, -120 50.2, -120 50.5, -120.5 50.2))',
      'The polygon has a hole; holes are not supported yet. Paste its outer edge only.'],
    [geojson({ type: 'Polygon', coordinates: [] }), 'The polygon has no points.'],
    [geojson({ type: 'Polygon', coordinates: [[[-121, 50], ['a', 50], [-119, 51]]] }),
      "The polygon: its point 2 is not a pair of numbers (longitude, latitude)."],
    ['POLYGON((-121 50, -119 50, -119 95))', 'The polygon: its point 3 (-119, 95) is outside -180 to 180 longitude '
      + 'or -90 to 90 latitude. Paste longitude then latitude, in degrees (WGS 84).'],
    // BC Albers metres instead of degrees.
    ['POLYGON((1200000 500000, 1300000 500000, 1300000 600000))', 'The polygon: its point 1 (1200000, 500000) is '
      + 'outside -180 to 180 longitude or -90 to 90 latitude. Paste longitude then latitude, in degrees (WGS 84).'],
    // Latitude first by mistake.
    ['POLYGON((50 -121, 50 -119, 51 -119))', 'The polygon: its point 1 (50, -121) is outside -180 to 180 '
      + 'longitude or -90 to 90 latitude. Paste longitude then latitude, in degrees (WGS 84).'],
    // Edges that cross (a bow-tie), with the API's message.
    ['POLYGON((-121 50, -119 51, -119 50, -121 51))', 'The polygon crosses or touches itself: the edge from point 1 '
      + 'to point 2 meets the edge from point 3 to point 4. Draw it so that no two edges cross or touch.'],
    ['POLYGON((-121 50, -119 50))', 'The polygon has 2 different points; a polygon needs at least 3.'],
    ['POLYGON((170 50, -170 50, -170 51))', 'The polygon has two joined corners 180 or more degrees of longitude '
      + 'apart, so it would be read as crossing the 180th meridian. An area across that meridian is not taken.'],
  ])('refuses %s', (text, message) => {
    expect(refused(text)).toBe(message)
  })

  it('names the shape by its place in the area after the shapes already there', () => {
    const existing = [boxShapeOf({ west: -125, south: 49, east: -124, north: 50 })]
    const text = 'MULTIPOLYGON(((-121 50, -119 50, -119 51, -121 51)), ((-123 53, -122 53, -122 54), (-122.8 53.2, '
      + '-122.5 53.2, -122.5 53.5)))'
    expect(refused(text, existing)).toBe('Shape 3 (a polygon) has a hole; holes are not supported yet. Paste its '
      + 'outer edge only.')
  })

  it('refuses a shape too small, an area too big, and more than 250 points, as the API does', () => {
    const tiny = 'POLYGON((-121 50, -120.99 50, -120.99 50.01, -121 50))'
    const tinyShape = readAreaText('p:-121,50,-120.99,50,-120.99,50.01')
    expect(refused(tiny)).toBe(shapesProblem(tinyShape, 6))
    expect(refused(tiny)).toMatch(/^The polygon is about .* smaller than the minimum of 144\.5 km²/)
    // Two big polygons at the finest hexagons: each under the maximum on its
    // own, over it together.
    const halves = 'MULTIPOLYGON(((-130 50, -120 50, -120 59, -130 59)), ((-120 50, -110 50, -110 59, -120 59)))'
    expect(refused(halves, [], 7)).toMatch(/^The area's shapes together touch at least .* more than the 200,000/)
    const many = 'POLYGON((' + Array.from({ length: 251 }, (_, i) => {
      const angle = (2 * Math.PI * i) / 251
      return `${(-121 + Math.cos(angle)).toFixed(4)} ${(50 + Math.sin(angle)).toFixed(4)}`
    }).join(', ') + '))'
    expect(refused(many)).toBe('The area has 251 points, more than the 250 an area can have (a box counts as 4). '
      + 'Use fewer points or fewer shapes.')
  })

  it('refuses an empty paste and one too long to read', () => {
    expect(refused('   ')).toBe('Paste a WKT or GeoJSON polygon first.')
    expect(PASTE_MAX_CHARACTERS).toBe(200000)
    const long = `POLYGON((${'0 0, '.repeat(60000)}0 0))`
    expect(refused(long)).toBe(`The pasted text has ${long.length.toLocaleString('en-US')} characters, more than `
      + 'the 200,000 that can be read. Paste fewer or simpler shapes.')
  })
})

describe('adding the pasted shapes to the area', () => {
  it('adds every polygon as a polygon shape, rounded to 4 decimals, after the shapes already there', () => {
    const existing = [boxShapeOf({ west: -125, south: 49, east: -124, north: 50 })]
    const text = 'MULTIPOLYGON(((-121.000049 50, -119.2 50, -119 51, -120.4 51.2, -121.000049 50)), '
      + '((-123.5 53.5, -122 53.5, -122 54.5, -123.5 54.5)))'
    const { shapes, problem } = addPastedShapes(text, existing, 6)
    expect(problem).toBeUndefined()
    expect(areaText(shapes)).toBe('b:-125,49,-124,50;p:-121,50,-119.2,50,-119,51,-120.4,51.2;'
      + 'p:-123.5,53.5,-122,53.5,-122,54.5,-123.5,54.5')
    expect(shapes.map((shape) => shape.isBox)).toEqual([true, false, false])
  })

  it('keeps a pasted rectangle a polygon', () => {
    const { shapes } = addPastedShapes(geojson({ type: 'Polygon', coordinates: [PRINCE_GEORGE] }), [], 6)
    expect(shapes).toEqual([{ ring: PRINCE_GEORGE.slice(0, 4), isBox: false, box: null }])
  })

  it('adds nothing when any part is refused', () => {
    const existing = [boxShapeOf({ west: -125, south: 49, east: -124, north: 50 })]
    // The first polygon is fine, the second has a hole: neither is added.
    const result = addPastedShapes(geojson({ type: 'MultiPolygon', coordinates: [[KAMLOOPS],
      [PRINCE_GEORGE, [[-123, 54], [-122.5, 54], [-122.5, 54.2], [-123, 54]]]] }), existing, 6)
    expect(result).toEqual({ problem: 'Shape 3 (a polygon) has a hole; holes are not supported yet. Paste its outer '
      + 'edge only.' })
  })

  it('checks sizes at the hexagon size given', () => {
    // About 14,000 km²: fine at every size, from about 1,770 km² hexagons down.
    const area = shapeAreaKm2(readAreaText('p:-121,50,-119.2,50,-119,51,-120.4,51.2')[0])
    expect(area).toBeGreaterThan(4 * getHexagonAreaAvg(4, 'km2'))
    for (const res of [4, 5, 6, 7]) {
      expect(addPastedShapes(PASTE_EXAMPLE, [], res).problem).toBeUndefined()
    }
    expect(pyFixed(area, 0)).toMatch(/^1[34],\d{3}$/)
  })
})
