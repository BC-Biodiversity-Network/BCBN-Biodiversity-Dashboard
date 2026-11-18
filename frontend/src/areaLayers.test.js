import { cellToChildren, cellToParent } from 'h3-js'
import { describe, expect, it, vi } from 'vitest'

// The hexagon layer needs a graphics card; this stand-in keeps its props.
vi.mock('@deck.gl/geo-layers', () => ({
  H3HexagonLayer: class {
    constructor(props) {
      this.props = props
      this.id = props.id
    }
  },
}))

import { boxCells, selectionTest } from './area'
import {
  EMPTY_AREA_FILL, areaHexagonLayer, areaId, areaLegendNote, emptyCells, hexagonSizeLines, mapIsCoarser, mapSelection,
  rowsInArea, shapesBounds,
} from './areaLayers'
import { boxShapeOf, readAreaText } from './areaShapes'
import { COLOUR_SCHEMES, hexagonColour } from './colours'

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

  it('has its own hexagons drawn only while the map\'s are larger', () => {
    expect([4, 5, 6, 7].map((mapRes) => mapIsCoarser(AREA, mapRes))).toEqual([true, true, false, false])
    expect(mapIsCoarser(null, 4)).toBe(false)
  })

  it('keeps the map rows in its hexagons, then lists its empty ones', () => {
    const cellSet = new Set(AREA.cells)
    const rows = [
      { h3Cell: AREA.cells[3], occurrences: 5, distinctSpecies: 2 },
      { h3Cell: '862800b37ffffff', occurrences: 9, distinctSpecies: 9 },
    ]
    const inside = rowsInArea(rows, cellSet)
    expect(inside).toEqual([rows[0]])
    const empty = emptyCells(AREA.cells, inside)
    expect(empty).toHaveLength(44)
    expect(empty[0]).toEqual({ h3Cell: AREA.cells[0], empty: true })
    expect(empty.some((row) => row.h3Cell === AREA.cells[3])).toBe(false)
  })
})

describe('the map hexagons kept bright', () => {
  const baseTest = selectionTest(AREA.cells, AREA.res)
  const inside = AREA.cells[0]

  it('fades every larger map hexagon once the area\'s own are here, keeping the rule at its size and finer', () => {
    const { test, key } = mapSelection(AREA, baseTest, true)
    expect(key).toBe(areaId(AREA))
    expect(test(inside)).toBe(true)
    expect(test('862800b37ffffff')).toBe(false)
    expect(cellToChildren(inside, 7).every(test)).toBe(true)
    // A resolution 5 hexagon wholly inside a big area is faded all the same.
    const big = { ...AREA, cells: cellToChildren(cellToParent(inside, 5), 6) }
    const bigTest = mapSelection(big, selectionTest(big.cells, 6), true).test
    expect(selectionTest(big.cells, 6)(cellToParent(inside, 5))).toBe(true)
    expect(bigTest(cellToParent(inside, 5))).toBe(false)
  })

  it('keeps the old rule, under another key, while the area\'s own are not here yet', () => {
    const { test, key } = mapSelection(AREA, baseTest, false)
    expect(key).toBe(`${areaId(AREA)}|waiting`)
    expect(test).toBe(baseTest)
  })
})

describe('the layer of the area\'s own hexagons', () => {
  const ROW = { h3Cell: AREA.cells[0], occurrences: 40, distinctSpecies: 30 }
  const EMPTY = { h3Cell: AREA.cells[1], empty: true }
  const OWN = { drawn: true, rows: [ROW, EMPTY], layerKey: 'overview' }

  it('colours them on the map\'s scale, the empty ones plain, under the labels, pickable', () => {
    const layer = areaHexagonLayer(OWN, AREA, 'records', 0.5, 'labels')
    expect(layer.id).toBe('area-hexagons-overview-6:b:-120.6,50.55,-120.1,50.8')
    expect(layer.props.getFillColor(ROW)).toEqual(hexagonColour(ROW, COLOUR_SCHEMES.records))
    expect(layer.props.getFillColor(EMPTY)).toEqual(EMPTY_AREA_FILL)
    expect(layer.props).toMatchObject({
      data: [ROW, EMPTY], pickable: true, beforeId: 'labels', stroked: true, getLineColor: [255, 255, 255, 128],
    })
    expect(layer.props.updateTriggers.getFillColor).toEqual(['records'])
    expect(areaHexagonLayer(OWN, AREA, 'species', 0, 'labels').props.stroked).toBe(false)
  })

  it('is not there when they are not drawn', () => {
    expect(areaHexagonLayer({ ...OWN, drawn: false }, AREA, 'species', 1, 'labels')).toBeNull()
    expect(areaHexagonLayer(null, AREA, 'species', 1, 'labels')).toBeNull()
  })
})

describe('the size label and the legend', () => {
  it('give one line for one size and two for two', () => {
    expect(hexagonSizeLines(4, null)).toEqual(['Hexagons ~52 km'])
    expect(hexagonSizeLines(6, AREA)).toEqual(['Hexagons ~7 km'])
    expect(hexagonSizeLines(4, { ...AREA, res: 7 })).toEqual(['Map: hexagons ~52 km', 'Area: hexagons ~3 km'])
    expect(hexagonSizeLines(7, AREA)).toEqual(['Map: hexagons ~3 km', 'Area: hexagons ~7 km'])
  })

  it('say the area\'s hexagons look paler only while they are drawn', () => {
    expect(areaLegendNote({ drawn: true }, { ...AREA, res: 7 }))
      .toBe('Inside the area: hexagons of about 3 km, on the same scale, so they look paler.')
    expect(areaLegendNote({ drawn: false }, AREA)).toBeNull()
    expect(areaLegendNote({ drawn: true }, null)).toBeNull()
  })
})
