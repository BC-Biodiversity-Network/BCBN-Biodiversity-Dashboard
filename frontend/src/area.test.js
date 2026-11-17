import { cellToChildren, cellToParent, getHexagonAreaAvg } from 'h3-js'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { areaExportUrl, fetchArea } from './api'
import {
  EARTH_RADIUS_KM, MAX_AREA_HEXAGONS, MIN_AREA_HEXAGONS, boxAreaKm2, boxCells, boxFromCorners, groupOutline,
  minimumAreaKm2, selectionTest,
} from './area'
import { boxShapeOf, pyFixed, readAreaText, shapesProblem } from './areaShapes'
import { AREA_GBIF_NOTE, GBIF_CHECKLIST_KEY, boxWkt, gbifBoxSearchUrl } from './gbifLinks'
import { AREA_LIST, NO_LIST, isPlainMap, mapSearch, readArea, readAreaPanelOpen, readList } from './route'

afterEach(() => {
  vi.unstubAllGlobals()
})

// A box around Kamloops, and the phylum Chordata.
const KAMLOOPS = { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }
const CHORDATA = { rank: 'phylum', name: 'Chordata', path: [
  { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' },
] }

// The options that choose a page of a list, which a download never has.
const PAGING = ['offset', 'limit']

// The query of an address as [name, value] pairs, in order.
function queryOf(url) {
  return [...new URL(url, 'http://site.example').searchParams]
}

// A box from south to north whose area is areaKm2 (its width worked out
// from the area formula).
function boxWithArea(areaKm2, south = 49, north = 49.5, west = -125) {
  const radians = areaKm2 / (EARTH_RADIUS_KM ** 2 * (Math.sin((north * Math.PI) / 180) - Math.sin((south * Math.PI) / 180)))
  return { west, south, east: west + (radians * 180) / Math.PI, north }
}

describe('a box drawn on the map', () => {
  it('is the same box whichever way it was dragged, rounded to 4 decimals', () => {
    const box = { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }
    expect(boxFromCorners([-120.600004, 50.8], [-120.1, 50.549996])).toEqual(box)
    expect(boxFromCorners([-120.1, 50.55], [-120.6, 50.8])).toEqual(box)
  })

  it('is measured on H3\'s sphere, as the API measures it', () => {
    expect(boxAreaKm2({ west: -180, south: -90, east: 180, north: 90 })).toBeCloseTo(4 * Math.PI * EARTH_RADIUS_KM ** 2, 3)
    expect(boxAreaKm2(KAMLOOPS)).toBeCloseTo(979.4, 1)
    expect(minimumAreaKm2(6)).toBe(MIN_AREA_HEXAGONS * getHexagonAreaAvg(6, 'km2'))
    expect([MIN_AREA_HEXAGONS, MAX_AREA_HEXAGONS]).toEqual([4, 200000])
  })
})

describe('a box too small or too big', () => {
  // The checks are the API's, with its words (shapesProblem in
  // areaShapes.js); the old ones of this file are gone.
  it.each([4, 5, 6, 7])('is too small under 4 hexagons (resolution %i), saying its size and the minimum', (res) => {
    const smallest = minimumAreaKm2(res)
    const problem = shapesProblem([boxShapeOf(boxWithArea(smallest * 0.99))], res)
    expect(problem).toBe(`The box is about ${pyFixed(smallest * 0.99, 1)} km², smaller than the minimum of `
      + `${pyFixed(smallest, 1)} km² for hexagons of about ${pyFixed(getHexagonAreaAvg(res, 'km2'), 1)} km² (4 times `
      + 'their average area). Draw a larger box.')
    expect(problem).not.toMatch(/resolution/i)
    expect(shapesProblem([boxShapeOf(boxWithArea(smallest * 1.01))], res)).toBeNull()
  })

  it('is too big over 200,000 hexagons, saying about how many, before any hexagon is found', () => {
    const allOfBc = boxShapeOf({ west: -139.1, south: 48.2, east: -114, north: 60 })
    const problem = shapesProblem([allOfBc], 7)
    expect(problem).toMatch(/^The box touches about 4\d\d,\d\d\d hexagons of about 5\.2 km², more than the 200,000/)
    expect(problem).not.toMatch(/resolution/i)
    expect(problem).toContain('Draw a smaller box or use larger hexagons.')
    expect(shapesProblem([allOfBc], 6)).toBeNull()
  })
})

describe('the hexagons under a box', () => {
  it('are every hexagon that overlaps it, even by a corner, as the API counts them', () => {
    expect(boxCells(KAMLOOPS, 6)).toHaveLength(45)
    expect(boxCells(KAMLOOPS, 7)).toHaveLength(249)
    expect(boxCells(KAMLOOPS, 4)).toHaveLength(3)
    // The test data's hexagon A (39.81 N, 125.20 W) under a small box.
    expect(boxCells({ west: -125.25, south: 39.78, east: -125.15, north: 39.85 }, 7)).toContain('872800b30ffffff')
    // A box much smaller than one hexagon still touches at least one.
    expect(boxCells({ west: -120.30, south: 50.60, east: -120.29, north: 50.61 }, 4).length).toBeGreaterThan(0)
  })

  it('are sorted, so the same box always gives the same list', () => {
    const cells = boxCells(KAMLOOPS, 6)
    expect([...cells].sort()).toEqual(cells)
  })
})

describe('the selected group of hexagons on the map', () => {
  it('has one outline around the outside of the whole group, not one per hexagon', () => {
    const cells = boxCells(KAMLOOPS, 6)
    const outline = groupOutline(cells)
    expect(outline.type).toBe('Feature')
    expect(outline.geometry.type).toBe('MultiPolygon')
    // One shape with one ring (no holes), closed, longitude first.
    expect(outline.geometry.coordinates).toHaveLength(1)
    expect(outline.geometry.coordinates[0]).toHaveLength(1)
    const ring = outline.geometry.coordinates[0][0]
    expect(ring[0]).toEqual(ring[ring.length - 1])
    expect(ring.every(([lng, lat]) => lng < -119 && lng > -121.5 && lat > 50 && lat < 51.5)).toBe(true)
    // Far fewer points than the hexagons' own corners: only the outside.
    expect(ring.length).toBeLessThan(cells.length * 6)
  })

  it('tells which hexagons are in it, at its own resolution, finer ones and coarser ones', () => {
    const cells = boxCells(KAMLOOPS, 6)
    const inside = selectionTest(cells, 6)
    expect(cells.every(inside)).toBe(true)
    expect(inside('862800b37ffffff')).toBe(false)
    // At resolution 7, the hexagons inside a selected one are selected.
    expect(cellToChildren(cells[0], 7).every(inside)).toBe(true)
    expect(cellToChildren('862800b37ffffff', 7).some(inside)).toBe(false)
    // At resolution 4 and 5, a hexagon is selected only when every hexagon
    // inside it is: the map never keeps bright land the area does not count.
    const chosen = new Set(cells)
    for (const res of [4, 5]) {
      const parents = [...new Set(cells.map((cell) => cellToParent(cell, res)))]
      for (const parent of parents) {
        expect(inside(parent)).toBe(cellToChildren(parent, 6).every((child) => chosen.has(child)))
      }
      if (res === 4) {
        expect(parents.some(inside)).toBe(false)
      }
    }
    expect(inside('842800bffffffff')).toBe(false)
    const whole = cellToChildren('852800b3fffffff', 6)
    const wholeTest = selectionTest([...whole, ...cells], 6)
    expect(wholeTest('852800b3fffffff')).toBe(true)
    expect(selectionTest(whole.slice(1), 6)('852800b3fffffff')).toBe(false)
  })
})

describe('the area in the address', () => {
  // The box around Kamloops as an area of one shape, and with a second box.
  const ONE = { shapes: [boxShapeOf(KAMLOOPS)], res: 6 }
  const TWO_TEXT = 'b:-120.6,50.55,-120.1,50.8;b:-123,53.8,-122.5,54'

  it('comes back from the address as it went in, with its list options under area_', () => {
    const list = { q: 'thrush', sort: 'english', status: ['red', 'none'], page: 2 }
    const search = mapSearch({ rank: CHORDATA, area: ONE, areaList: list, years: { from: 2000, to: 2020 } })
    expect(search).toBe('?rank=phylum&kingdom=Animalia&phylum=Chordata&area=b:-120.6,50.55,-120.1,50.8&area_res=6'
      + '&area_q=thrush&area_sort=english&area_status=red&area_status=none&area_page=2&year_from=2000&year_to=2020')
    expect(readArea(search)).toEqual(ONE)
    expect(readList(search, AREA_LIST)).toEqual(list)
    expect(isPlainMap(search.replace(/rank=[^&]*&kingdom=Animalia&phylum=Chordata&/, ''))).toBe(false)
  })

  it('writes several shapes as readable area text, and reads them back', () => {
    const two = { shapes: readAreaText(TWO_TEXT), res: 6 }
    const search = mapSearch({ area: two })
    expect(search).toBe(`?area=${TWO_TEXT}&area_res=6`)
    expect(readArea(search)).toEqual(two)
    // Written the way URLSearchParams writes it, it reads the same.
    expect(readArea(`?${new URLSearchParams({ area: TWO_TEXT, area_res: '6' })}`)).toEqual(two)
  })

  it('opens an old link (area=west,south,east,north) as one box', () => {
    expect(readArea('?area=-120.6,50.55,-120.1,50.8&area_res=6')).toEqual(ONE)
  })

  it('leaves the defaults out, keeps an area with a hexagon, and its panel closed when asked', () => {
    const seven = { ...ONE, res: 7 }
    expect(mapSearch({ area: seven, areaList: NO_LIST })).toBe('?area=b:-120.6,50.55,-120.1,50.8&area_res=7')
    const both = mapSearch({ cell: '872800b30ffffff', area: seven })
    expect(both).toBe('?cell=872800b30ffffff&area=b:-120.6,50.55,-120.1,50.8&area_res=7')
    expect(readArea(both)).toEqual(seven)
    const closed = mapSearch({ area: seven, areaPanelOpen: false })
    expect(closed).toBe('?area=b:-120.6,50.55,-120.1,50.8&area_res=7&area_panel=closed')
    expect(readAreaPanelOpen(closed)).toBe(false)
    expect(readAreaPanelOpen('?area=b:-120.6,50.55,-120.1,50.8&area_res=7')).toBe(true)
    // A species' card keeps the area in the address (not shown on its map).
    expect(mapSearch({ species: '59PMB', area: seven }))
      .toBe('?species=59PMB&area=b:-120.6,50.55,-120.1,50.8&area_res=7')
  })

  it('reads an address without area_res as Auto, and writes Auto without it', () => {
    expect(readArea('?area=b:-120.6,50.55,-120.1,50.8')).toEqual({ ...ONE, res: null })
    expect(readArea('?area=-120.6,50.55,-120.1,50.8')).toEqual({ ...ONE, res: null })
    expect(mapSearch({ area: { ...ONE, res: null } })).toBe('?area=b:-120.6,50.55,-120.1,50.8')
  })

  it.each([
    '?area=-120.6,50.55,-120.1&area_res=6', '?area=-120.6,50.55,-120.1,50.8&area_res=3',
    '?area=-120.1,50.55,-120.6,50.8&area_res=6', '?area=-120.6,50.8,-120.1,50.55&area_res=6',
    '?area=-200,50.55,-120.1,50.8&area_res=6', '?area=a,50.55,-120.1,50.8&area_res=6', '?area=,50.55,-120.1,50.8&area_res=6',
    '?area=b:0,0,1,1;&area_res=6', '?area=p:0,0,1,1,1,0,0,1&area_res=6', '?area=x:0,0,1,1&area_res=6',
  ])('is not read from an address that cannot hold one (%s)', (search) => {
    expect(readArea(search)).toBeNull()
  })
})

describe('the area requests', () => {
  // A pretend fetch that answers {} and records the addresses asked for.
  function recordUrls() {
    const urls = []
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      urls.push(url)
      return { ok: true, status: 200, json: async () => ({}) }
    }))
    return urls
  }

  it('ask for the area, its resolution and the list\'s options, the page only for the list', async () => {
    const urls = recordUrls()
    const area = { shapes: [boxShapeOf(KAMLOOPS)], res: 6 }
    const options = { years: { from: 2000, to: 2020 }, q: ' thrush ', status: ['red'], sort: 'english', rank: CHORDATA }
    await fetchArea(area, { ...options, offset: 50, limit: 50 })
    expect(urls[0]).toBe('/api/area?offset=50&limit=50&area=b%3A-120.6%2C50.55%2C-120.1%2C50.8&res=6'
      + '&rank=phylum&kingdom=Animalia&phylum=Chordata&year_from=2000&year_to=2020&q=thrush&status=red&sort=english')
    const exportUrl = areaExportUrl(area, { ...options, offset: 50, limit: 50 })
    expect(exportUrl.split('?')[0]).toBe('/api/export/area')
    expect(queryOf(exportUrl)).toEqual(queryOf(urls[0]).filter(([name]) => !PAGING.includes(name)))
  })

  it('never ask for a species (the API refuses it for an area)', () => {
    expect(areaExportUrl({ shapes: [boxShapeOf(KAMLOOPS)], res: 6 }, { species: '59PMB' }))
      .toBe('/api/export/area?area=b%3A-120.6%2C50.55%2C-120.1%2C50.8&res=6')
  })
})

describe("the area's GBIF link", () => {
  it('searches the box as a closed, counter-clockwise polygon, every species, for the years', () => {
    const url = new URL(gbifBoxSearchUrl({ box: KAMLOOPS, years: [2000, 2020] }))
    expect(url.origin + url.pathname).toBe('https://www.gbif.org/occurrence/search')
    expect(url.searchParams.get('checklistKey')).toBe(GBIF_CHECKLIST_KEY)
    expect(url.searchParams.has('taxonKey')).toBe(false)
    expect(url.searchParams.get('occurrenceStatus')).toBe('PRESENT')
    expect(url.searchParams.get('year')).toBe('2000,2020')
    const wkt = url.searchParams.get('geometry')
    expect(wkt).toBe(boxWkt(KAMLOOPS))
    const points = wkt.slice('POLYGON(('.length, -2).split(',').map((pair) => pair.split(' ').map(Number))
    expect(points).toHaveLength(5)
    expect(points[0]).toEqual(points[4])
    let twiceArea = 0
    for (let i = 0; i < 4; i++) {
      twiceArea += points[i][0] * points[i + 1][1] - points[i + 1][0] * points[i][1]
    }
    expect(twiceArea).toBeGreaterThan(0)
    expect(new URL(gbifBoxSearchUrl({ box: KAMLOOPS })).searchParams.has('year')).toBe(false)
  })

  it('says what GBIF counts', () => {
    expect(AREA_GBIF_NOTE).toBe('GBIF searches only the box you drew. Our numbers include every hexagon the box '
      + 'touches, so GBIF usually shows fewer records.')
  })
})
