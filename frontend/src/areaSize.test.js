import { describe, expect, it } from 'vitest'

import { boxCells } from './area'
import { addPastedShapes } from './areaPaste'
import { boxShapeOf, readAreaText, shapesCells, shapesProblem } from './areaShapes'
import {
  AREA_SIZES, AUTO_PREFERRED, AUTO_SMALL_SHAPE_KM2, autoSize, autoSizes, pickedSizeProblem, sizeKm, sizeLabel,
  sizedArea,
} from './areaSize'

// Boxes around Kamloops (about 979 km²), most of BC, one of about 75 km²
// (small: Auto starts at about 3 km), one just over (about 155 km²) and one
// just under (about 141 km²) Auto's line for a small shape (144.5 km²), and
// one far too small (under the minimum of 20.6 km²).
const KAMLOOPS = boxShapeOf({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 })
const BC = boxShapeOf({ west: -139.1, south: 48.2, east: -114, north: 60 })
const SMALL = boxShapeOf({ west: -120.3, south: 50.6, east: -120.18, north: 50.68 })
const JUST_OVER = boxShapeOf({ west: -120.3, south: 50.6, east: -120.12, north: 50.71 })
const JUST_UNDER = boxShapeOf({ west: -120.3, south: 50.6, east: -120.12, north: 50.7 })
const TINY = boxShapeOf({ west: -120.3, south: 50.6, east: -120.29, north: 50.61 })

describe('Auto', () => {
  it('prefers about 7 km', () => {
    expect(AREA_SIZES).toEqual([7, 6, 5, 4])
    expect(AUTO_PREFERRED).toBe(6)
    expect(autoSize([KAMLOOPS])).toEqual({ res: 6, cells: boxCells(KAMLOOPS.box, 6) })
    // Most of BC fits at about 7 km too (70,747 hexagons), with Kamloops.
    expect(autoSize([BC]).res).toBe(6)
    const both = autoSize([KAMLOOPS, BC])
    expect(both.res).toBe(6)
    expect(both.cells).toEqual(shapesCells([KAMLOOPS, BC], 6).cells)
  })

  it('starts at about 3 km for a small shape, for the whole area, as before the fixed minimum', () => {
    expect(AUTO_SMALL_SHAPE_KM2).toBeCloseTo(144.5, 1)
    expect(autoSizes([JUST_OVER])).toEqual([6, 5, 4])
    expect(autoSize([JUST_OVER]).res).toBe(6)
    expect(autoSizes([JUST_UNDER])).toEqual([7, 6, 5, 4])
    // A small shape now passes at about 7 km too; Auto still prefers 3 km.
    expect(shapesProblem([JUST_UNDER], 6)).toBeNull()
    expect(autoSize([JUST_UNDER]).res).toBe(7)
    expect(autoSize([SMALL]).res).toBe(7)
    const both = autoSize([KAMLOOPS, SMALL])
    expect(both.res).toBe(7)
    expect(both.cells).toEqual(shapesCells([KAMLOOPS, SMALL], 7).cells)
  })

  it('moves up from about 7 km when the shapes only fit together at a larger size', () => {
    // Two boxes, each under 200,000 hexagons at about 7 km, over it together.
    const shapes = readAreaText('b:-140,45,-110,62;b:-110,45,-80,62')
    expect(shapesProblem([shapes[0]], 6)).toBeNull()
    expect(shapesProblem(shapes, 6)).toMatch(/^The area's shapes together/)
    expect(autoSize(shapes).res).toBe(5)
  })

  it('moves up from about 3 km for a small shape when the area is too big there', () => {
    // A small box beside most of BC: over 200,000 hexagons at about 3 km.
    expect(shapesProblem([SMALL, BC], 7)).toMatch(/touches about|together touch/)
    const both = autoSize([SMALL, BC])
    expect(both.res).toBe(6)
    expect(both.cells).toEqual(shapesCells([SMALL, BC], 6).cells)
  })

  it('refuses with the same message at every size when a shape is under the minimum', () => {
    for (const res of AREA_SIZES) {
      expect(shapesProblem([KAMLOOPS, TINY], res)).toBe(shapesProblem([KAMLOOPS, TINY], 7))
    }
    expect(autoSize([KAMLOOPS, TINY])).toEqual({ problem: shapesProblem([KAMLOOPS, TINY], 4) })
    expect(autoSize([TINY]).problem).toMatch(/^The box is about .* km², smaller than the minimum of 20\.6 km²\. Draw a larger box\.$/)
  })

  it("refuses with the largest size's message when the area is too big even there", () => {
    // Most of the world, in two halves: over 200,000 hexagons even at the largest size.
    const halves = readAreaText('b:-179,-80,0,80;b:0.5,-80,179.5,80')
    expect(autoSize(halves)).toEqual({ problem: shapesProblem(halves, 4) })
    expect(autoSize(halves).problem).toMatch(/^The area's shapes together touch at least .* about 1,770\.3 km²/)
  })
})

describe('a size picked', () => {
  it("refuses with the API's message alone, and only for an area too big", () => {
    // Kamloops passes at every size now, even with hexagons of about 1,770 km².
    for (const res of AREA_SIZES) {
      expect(pickedSizeProblem([KAMLOOPS], res)).toBeNull()
    }
    // Too big: the API's message already says to use larger hexagons.
    expect(pickedSizeProblem([BC], 7)).toBe(shapesProblem([BC], 7))
    expect(pickedSizeProblem([TINY], 6)).toBe(shapesProblem([TINY], 6))
    expect(pickedSizeProblem([TINY], 6)).not.toContain('Or choose')
  })

  it('is worked out at that size, or by Auto with none', () => {
    expect(sizedArea([KAMLOOPS], 6)).toEqual({ res: 6, cells: boxCells(KAMLOOPS.box, 6) })
    expect(sizedArea([KAMLOOPS], null)).toEqual(autoSize([KAMLOOPS]))
  })

  it('is named as the map names its sizes', () => {
    expect(AREA_SIZES.map(sizeKm)).toEqual([3, 7, 20, 52])
    expect(sizeLabel(4)).toBe('About 52 km')
  })
})

describe('a paste', () => {
  it('leaves the sizes to the page when asked (res null)', () => {
    const tiny = 'POLYGON((-121 50, -120.99 50, -120.99 50.01, -121 50))'
    expect(addPastedShapes(tiny, [], null).shapes).toHaveLength(1)
    expect(addPastedShapes(tiny, [], 6).problem).toMatch(/smaller than the minimum/)
  })
})
