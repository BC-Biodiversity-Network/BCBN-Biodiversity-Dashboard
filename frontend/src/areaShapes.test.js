import { getHexagonAreaAvg } from 'h3-js'
import { describe, expect, it } from 'vitest'

import { boxAreaKm2, boxCells } from './area'
import {
  MAX_AREA_POINTS, areaText, areaTextProblem, boxShapeOf, pointWouldCross, polygonAreaKm2, pyFixed, readAreaText,
  ringCrosses, roundCorner, shapeAreaKm2, shapeCells, shapeSizeProblem, shapesCells, shapesProblem,
} from './areaShapes'
import { readArea } from './route'

// A polygon of this many points around a circle (never crossing itself),
// as area text with 4 decimals.
function circle(points, lng = -123, lat = 49, radius = 0.5) {
  const numbers = []
  for (let i = 0; i < points; i += 1) {
    const angle = (2 * Math.PI * i) / points
    numbers.push((lng + radius * Math.cos(angle)).toFixed(4), (lat + radius * Math.sin(angle)).toFixed(4))
  }
  return `p:${numbers.join(',')}`
}

describe('reading the area text', () => {
  it('reads a box, an old link and a polygon', () => {
    const box = { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }
    expect(readAreaText('b:-120.6,50.55,-120.1,50.8')).toEqual([boxShapeOf(box)])
    expect(readAreaText('-120.6,50.55,-120.1,50.8')).toEqual([boxShapeOf(box)])
    expect(readAreaText('b:-120.6,50.55,-120.1,50.8')[0].ring).toEqual(
      [[-120.6, 50.55], [-120.1, 50.55], [-120.1, 50.8], [-120.6, 50.8]])
    expect(readAreaText('p:-123,49,-122,49,-122.5,50')).toEqual(
      [{ ring: [[-123, 49], [-122, 49], [-122.5, 50]], isBox: false, box: null }])
  })

  it('drops a repeated closing point and a point given twice in a row', () => {
    const open = readAreaText('p:-123,49,-122,49,-122.5,50')
    expect(readAreaText('p:-123,49,-122,49,-122.5,50,-123,49')).toEqual(open)
    expect(readAreaText('p:-123,49,-122,49,-122,49,-122.5,50')).toEqual(open)
  })

  it('reads several shapes in order, and -0 as 0', () => {
    const shapes = readAreaText('b:-121,50,-120,51;p:-120.5,50.5,-119,50.5,-119.5,52')
    expect(shapes.map((shape) => shape.isBox)).toEqual([true, false])
    expect(Object.is(readAreaText('p:-0,0,1,0,1,1')[0].ring[0][0], 0)).toBe(true)
  })

  it.each([
    ['', 'Give the area as area=..., one or more shapes separated by ";": b:west,south,east,north for a box, or '
      + 'p:lng,lat,lng,lat,... for a polygon.'],
    ['b:0,0,1,1;;b:2,2,3,3', 'Shape 2 is empty: remove the extra ";".'],
    ['x:0,0,1,1', "The area starts with 'x:', which is not a kind of shape: start it with b: for a box or p: for "
      + 'a polygon.'],
    ['p0,0,1,0,1,1', 'The area does not say what kind of shape it is: start it with b: for a box or p: for a '
      + 'polygon.'],
    ['b:', 'The box is empty: give it as b:west,south,east,north.'],
    ['b:0,0,1,abc', "The box has 'abc' where a number of degrees should be."],
    ['b:0,0,1e1,1', "The box has '1e1' where a number of degrees should be."],
    ['p:0,0,1,0,1,1,', 'The polygon has an empty value where a number of degrees should be.'],
    ['b:0,0,1,1 p:2,2,3,2,3,3', "The box has '1 p:2' where a number of degrees should be (separate shapes "
      + 'with ";").'],
    ['b:0,0,1', 'The box needs 4 numbers, west,south,east,north; it has 3.'],
    ['p:1', 'The polygon needs its numbers in pairs, longitude then latitude; it has 1 number.'],
    ['b:-180.5,0,1,1', 'The box: its west edge is -180.5, outside -180 to 180 degrees.'],
    ['p:0,0,1,95,1,1', "The polygon: its point 2's latitude is 95, outside -90 to 90 degrees."],
    ['b:2,0,1,1', 'The box: its west edge (2) must be less than its east edge (1).'],
    ['b:0,2,1,1', 'The box: its south edge (2) must be less than its north edge (1).'],
    ['p:0,0,1,1,0,0', 'The polygon has 2 different points; a polygon needs at least 3.'],
    ['p:0,0,1,1,1,0,0,1', 'The polygon crosses or touches itself: the edge from point 1 to point 2 meets the edge '
      + 'from point 3 to point 4. Draw it so that no two edges cross or touch.'],
    ['p:0,0,0,1,1,0,1,1', 'The polygon crosses or touches itself: the edge from point 2 to point 3 meets the edge '
      + 'from point 4 to point 1. Draw it so that no two edges cross or touch.'],
    ['b:-179,0,179,1', 'The box has two joined corners 180 or more degrees of longitude apart, so it would be read '
      + 'as crossing the 180th meridian. An area across that meridian is not taken.'],
    ['b:0,0,1,1;p:179,0,-179,0,-179,1', 'Shape 2 (a polygon) has two joined corners 180 or more degrees of '
      + 'longitude apart, so it would be read as crossing the 180th meridian. An area across that meridian is '
      + 'not taken.'],
  ])('refuses %j with the API\'s message', (text, message) => {
    expect(areaTextProblem(text)).toBe(message)
  })

  it('counts at most 250 points, a box as 4', () => {
    expect(MAX_AREA_POINTS).toBe(250)
    expect(readAreaText(circle(250))[0].ring).toHaveLength(250)
    expect(readAreaText(`${circle(246)};b:0,0,1,1`)).toHaveLength(2)
    expect(areaTextProblem(`${circle(247)};b:0,0,1,1`)).toBe('The area has 251 points, more than the 250 an area '
      + 'can have (a box counts as 4). Use fewer points or fewer shapes.')
  })

  it('checks crossings on the numbers kept, exactly', () => {
    expect(areaTextProblem('p:0,0,4,0,4,4,2,0.0001,0,4')).toBe(null)
    expect(areaTextProblem('p:0,0,4,0,4,4,2,0,0,4')).toContain('touches itself')
    // Point 4 is 1e-20 above edge 1-2 as written, but on it once read.
    expect(areaTextProblem('p:0,10,4,10,4,14,2,10.00000000000000000001,0,14')).toContain('touches itself')
  })
})

describe('writing the area text', () => {
  it('writes boxes and polygons with 4 decimals, and reads them back the same', () => {
    const shapes = readAreaText('b:-120.6,50.55,-120.1,50.8;p:-121.12345,50,-120.8,50,-120.9,50.2')
    const text = areaText(shapes)
    expect(text).toBe('b:-120.6,50.55,-120.1,50.8;p:-121.1235,50,-120.8,50,-120.9,50.2')
    const rounded = readAreaText(text)
    expect(readAreaText(areaText(rounded))).toEqual(rounded)
    expect(areaText([boxShapeOf({ west: -0, south: 0, east: 1, north: 1 })])).toBe('b:0,0,1,1')
  })

  it('rounds a drawn corner to 4 decimals', () => {
    expect(roundCorner([-123.456789, 49.000049])).toEqual([-123.4568, 49])
  })

  it('reads the area from the page address, old and new', () => {
    expect(readArea('?area=-120.6,50.55,-120.1,50.8&area_res=6')).toEqual(
      { shapes: readAreaText('b:-120.6,50.55,-120.1,50.8'), res: 6 })
    const search = `?${new URLSearchParams({ area: 'b:-121,50,-120,51;p:-120.5,50.5,-119,50.5,-119.5,52',
      area_res: '7' })}`
    expect(readArea(search).shapes).toHaveLength(2)
    expect(readArea('?area=p:0,0,1,1,1,0,0,1&area_res=6')).toBe(null)
    expect(readArea('?area=b:0,0,1,1&area_res=9')).toBe(null)
    expect(readArea('?area_res=6')).toBe(null)
  })
})

describe('drawing a polygon point by point', () => {
  const drawn = [[0, 0], [4, 0], [4, 4]]

  it('says when the next point would make the edges cross or touch', () => {
    expect(pointWouldCross([], [1, 1])).toBe(false)
    expect(pointWouldCross([[0, 0]], [1, 1])).toBe(false)
    expect(pointWouldCross(drawn, [0, 4])).toBe(false)
    // Crossing the first edge.
    expect(pointWouldCross(drawn, [2, -1])).toBe(true)
    // Touching the first edge at a point that is not a corner.
    expect(pointWouldCross(drawn, [2, 0])).toBe(true)
    // Going back along the edge before.
    expect(pointWouldCross(drawn, [4, 2])).toBe(true)
    // Back to an earlier corner that is not the last one.
    expect(pointWouldCross([[0, 0], [4, 0], [4, 4], [2, 6]], [4, 0])).toBe(true)
    // The same as the last corner adds no edge.
    expect(pointWouldCross(drawn, [4, 4])).toBe(false)
  })

  it('says whether the polygon can be closed', () => {
    expect(ringCrosses([[0, 0], [4, 0], [4, 4], [0, 4]])).toBe(false)
    expect(ringCrosses([[0, 0], [0, 1], [1, 0], [1, 1]])).toBe(true)
  })
})

describe('sizes and hexagons', () => {
  const average = getHexagonAreaAvg(7, 'km2')

  it('uses the box formula for a box and the spherical formula for a polygon', () => {
    const box = { west: -125, south: 49, east: -124, north: 49.5 }
    expect(shapeAreaKm2(boxShapeOf(box))).toBe(boxAreaKm2(box))
    expect(polygonAreaKm2(boxShapeOf(box).ring)).toBeCloseTo(boxAreaKm2(box), 6)
    expect(polygonAreaKm2([...boxShapeOf(box).ring].reverse())).toBeCloseTo(boxAreaKm2(box), 6)
    // An L shape is the sum of its two boxes.
    const lShape = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 3], [0, 3]]
    expect(polygonAreaKm2(lShape)).toBeCloseTo(boxAreaKm2({ west: 0, south: 0, east: 2, north: 1 })
      + boxAreaKm2({ west: 0, south: 1, east: 1, north: 3 }), 3)
  })

  it('refuses a shape too small or too big, naming it as the API does', () => {
    const tiny = 'p:-125.20,39.80,-125.19,39.80,-125.195,39.81'
    const area = polygonAreaKm2(readAreaText(tiny)[0].ring)
    const expected = `is about ${pyFixed(area, 1)} km², smaller than the minimum of 20.6 km². Draw a larger polygon.`
    expect(shapeSizeProblem(readAreaText(tiny), 7)).toBe(`The polygon ${expected}`)
    expect(shapeSizeProblem(readAreaText(`b:-125.25,39.78,-125.15,39.85;${tiny}`), 7))
      .toBe(`Shape 2 (a polygon) ${expected}`)
    const bc = readAreaText('p:-139,48,-114,48,-114,60,-139,60')
    expect(shapeSizeProblem(bc, 7)).toBe(`The polygon touches about ${pyFixed(polygonAreaKm2(bc[0].ring) / average, 0)}`
      + ` hexagons of about ${pyFixed(average, 1)} km², more than the 200,000 an area search can count. Draw a `
      + 'smaller polygon or use larger hexagons.')
    expect(shapeSizeProblem(readAreaText('b:-125.25,39.78,-125.15,39.85'), 7)).toBe(null)
  })

  it('joins the hexagons of several shapes, each once, sorted', () => {
    const shapes = readAreaText('b:-125.25,39.78,-125.15,39.85;b:-125.22,39.78,-125.12,39.85')
    const first = shapeCells(shapes[0], 7)
    const second = shapeCells(shapes[1], 7)
    const joined = shapesCells(shapes, 7)
    expect(joined.problem).toBe(null)
    expect(joined.cells).toEqual([...new Set([...first, ...second])].sort())
    expect(joined.cells.length).toBeLessThan(first.length + second.length)
    // A box's hexagons are boxCells' hexagons.
    expect([...shapeCells(shapes[0], 7)].sort()).toEqual(boxCells(shapes[0].box, 7))
    expect(shapesProblem(shapes, 7)).toBe(null)
  })

  it('stops when the joined hexagons pass the maximum', () => {
    // Two boxes, each under the maximum on its own, over it together.
    const shapes = readAreaText('b:-130,50,-120,59;b:-120,50,-110,59')
    expect(shapeSizeProblem(shapes, 7)).toBe(null)
    const { problem, cells } = shapesCells(shapes, 7)
    expect(problem).toBe(`The area's shapes together touch at least ${pyFixed(cells.length, 0)} hexagons of about `
      + `${pyFixed(average, 1)} km², more than the 200,000 an area search can count. Use smaller or fewer shapes, or `
      + 'larger hexagons.')
    expect(cells.length).toBeGreaterThan(200000)
  }, 20000)
})

describe("Python's number format", () => {
  it.each([
    [1234.56, 1, '1,234.6'], [0.25, 1, '0.2'], [0.75, 1, '0.8'], [2.5, 0, '2'], [3.5, 0, '4'],
    [1234567, 0, '1,234,567'], [-0.04, 1, '-0.0'], [5.16129, 1, '5.2'], [0, 1, '0.0'],
  ])('writes %d with %d decimals as %j', (value, decimals, written) => {
    expect(pyFixed(value, decimals)).toBe(written)
  })
})
