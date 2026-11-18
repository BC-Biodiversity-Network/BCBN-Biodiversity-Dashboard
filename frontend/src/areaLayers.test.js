import { describe, expect, it } from 'vitest'

import { boxCells } from './area'
import { areaId, areaLegendNote, hexagonSizeText, lockedResolution, shapesBounds } from './areaLayers'
import { boxShapeOf, readAreaText } from './areaShapes'

// A box around Kamloops at resolution 6 (45 hexagons), as an area.
const KAMLOOPS = { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }
const AREA = { shapes: [boxShapeOf(KAMLOOPS)], res: 6, cells: boxCells(KAMLOOPS, 6) }

describe('an area on the map', () => {
  it('is named by its size and its area text', () => {
    expect(areaId(AREA)).toBe('6:b:-120.6,50.55,-120.1,50.8')
  })

  it('fits its shapes, not its hexagons', () => {
    expect(shapesBounds(AREA.shapes)).toEqual([[-120.6, 50.55], [-120.1, 50.8]])
    const two = readAreaText('b:-120.6,50.55,-120.1,50.8;p:-121,50,-119.2,50,-119,51,-120.4,51.2')
    expect(shapesBounds(two)).toEqual([[-121, 50], [-119, 51.2]])
  })

  it("locks the map to its size, except on a single species' map", () => {
    expect(lockedResolution(AREA, false)).toBe(6)
    expect(lockedResolution({ ...AREA, res: 7 }, false)).toBe(7)
    expect(lockedResolution(AREA, true)).toBeNull()
    expect(lockedResolution(null, false)).toBeNull()
  })
})

describe('the size label and the legend', () => {
  it("name the area's size while the map is locked to it", () => {
    expect(hexagonSizeText(4, false)).toBe('Hexagons ~52 km')
    expect(hexagonSizeText(6, true)).toBe("Hexagons ~7 km (the area's size)")
    expect(hexagonSizeText(7, true)).toBe("Hexagons ~3 km (the area's size)")
  })

  it('say why zooming no longer changes the size, only while locked', () => {
    expect(areaLegendNote(6)).toBe('While an area is shown, the whole map uses its hexagon size, about 7 km, at every '
      + 'zoom. Clear the area to go back.')
    expect(areaLegendNote(5)).toContain('about 20 km')
    expect(areaLegendNote(null)).toBeNull()
  })
})
