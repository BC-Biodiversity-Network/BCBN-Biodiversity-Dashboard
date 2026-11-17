import { describe, expect, it } from 'vitest'

import { boxCells } from './area'
import { addPastedShapes } from './areaPaste'
import { boxShapeOf, readAreaText, shapesCells, shapesProblem } from './areaShapes'
import { AREA_SIZES, autoSize, pickedSizeProblem, sizeHint, sizeKm, sizeLabel, sizedArea } from './areaSize'

// Boxes around Kamloops (about 979 km²), most of BC, and one far too small.
const KAMLOOPS = boxShapeOf({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 })
const BC = boxShapeOf({ west: -139.1, south: 48.2, east: -114, north: 60 })
const TINY = boxShapeOf({ west: -120.3, south: 50.6, east: -120.29, north: 50.61 })

describe('Auto', () => {
  it('chooses the smallest hexagons at which every shape passes', () => {
    expect(AREA_SIZES).toEqual([7, 6, 5, 4])
    expect(autoSize([KAMLOOPS])).toEqual({ res: 7, cells: boxCells(KAMLOOPS.box, 7) })
    // Most of BC is over 200,000 hexagons at the smallest size.
    expect(shapesProblem([BC], 7)).not.toBeNull()
    expect(autoSize([BC]).res).toBe(6)
    // The whole area moves to the size its largest need sets.
    const both = autoSize([KAMLOOPS, BC])
    expect(both.res).toBe(6)
    expect(both.cells).toEqual(shapesCells([KAMLOOPS, BC], 6).cells)
  })

  it('moves up a size when the shapes only fit together at a larger one', () => {
    // Two boxes, each under 200,000 hexagons at the smallest size, over it together.
    const shapes = readAreaText('b:-130,50,-120,59;b:-120,50,-110,59')
    expect(shapesProblem([shapes[0]], 7)).toBeNull()
    expect(shapesProblem(shapes, 7)).toMatch(/^The area's shapes together/)
    expect(autoSize(shapes).res).toBe(6)
  })

  it("refuses with the smallest size's message when a shape is too small even there", () => {
    expect(autoSize([KAMLOOPS, TINY])).toEqual({ problem: shapesProblem([KAMLOOPS, TINY], 7) })
    expect(autoSize([TINY]).problem).toMatch(/^The box is about .* minimum of 20\.6 km²/)
  })

  it("refuses with the largest size's message when the area is too big even there", () => {
    // Most of the world, in two halves: over 200,000 hexagons even at the largest size.
    const halves = readAreaText('b:-179,-80,0,80;b:0.5,-80,179.5,80')
    expect(autoSize(halves)).toEqual({ problem: shapesProblem(halves, 4) })
    expect(autoSize(halves).problem).toMatch(/^The area's shapes together touch at least .* about 1,770\.3 km²/)
  })
})

describe('a size picked', () => {
  it("refuses with the API's message and, for a shape too small, a hint", () => {
    expect(pickedSizeProblem([KAMLOOPS], 6)).toBeNull()
    expect(pickedSizeProblem([KAMLOOPS], 4)).toBe(`${shapesProblem([KAMLOOPS], 4)} Or choose smaller hexagons.`)
    // Too big: the API's message already says to use larger hexagons.
    expect(pickedSizeProblem([BC], 7)).toBe(shapesProblem([BC], 7))
    expect(sizeHint([TINY], 7)).toBe('')
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
