import { cellToChildren, cellToParent, getHexagonAreaAvg } from 'h3-js'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { areaExportUrl, fetchArea } from './api'
import {
  EARTH_RADIUS_KM, MAX_AREA_HEXAGONS, MIN_AREA_HEXAGONS, areaProblem, boxAreaKm2, boxCells, boxFromCorners,
  boxSizeProblem, groupOutline, minimumAreaKm2, selectionTest,
} from './area'
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
  it.each([4, 5, 6, 7])('is too small under 4 hexagons (resolution %i), saying its size and the minimum', (res) => {
    const smallest = minimumAreaKm2(res)
    const problem = areaProblem(boxWithArea(smallest * 0.99), res)
    expect(problem).toContain(`smaller than the minimum of ${smallest.toLocaleString('en-US', {
      minimumFractionDigits: 1, maximumFractionDigits: 1 })} km²`)
    expect(problem).toContain(`This box is about ${(smallest * 0.99).toLocaleString('en-US', {
      minimumFractionDigits: 1, maximumFractionDigits: 1 })} km²`)
    expect(problem).toContain(`for hexagons of this size (4 hexagons of about ${getHexagonAreaAvg(res, 'km2').toLocaleString('en-US', {
      minimumFractionDigits: 1, maximumFractionDigits: 1 })} km²)`)
    expect(problem).not.toMatch(/resolution/i)
    expect(areaProblem(boxWithArea(smallest * 1.01), res)).toBeNull()
  })

  it('is too big over 200,000 hexagons, saying about how many, before any hexagon is found', () => {
    const allOfBc = { west: -139.1, south: 48.2, east: -114, north: 60 }
    const problem = boxSizeProblem(allOfBc, 7)
    expect(problem).toMatch(/^This box touches about 4\d\d,\d\d\d hexagons of about 5\.2 km², more than the 200,000/)
    expect(problem).not.toMatch(/resolution/i)
    expect(problem).toContain('zoom out first for larger hexagons')
    expect(areaProblem(allOfBc, 6)).toBeNull()
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
    // At resolution 4 and 5, the hexagons holding a selected one are.
    expect(inside(cellToParent(cells[0], 5))).toBe(true)
    expect(inside(cellToParent(cells[0], 4))).toBe(true)
    expect(inside('842800bffffffff')).toBe(false)
  })
})

describe('the area in the address', () => {
  it('comes back from the address as it went in, with its list options under area_', () => {
    const area = { box: KAMLOOPS, res: 6 }
    const list = { q: 'thrush', sort: 'english', status: ['red', 'none'], page: 2 }
    const search = mapSearch({ rank: CHORDATA, area, areaList: list, years: { from: 2000, to: 2020 } })
    expect(search).toBe('?rank=phylum&kingdom=Animalia&phylum=Chordata&area=-120.6,50.55,-120.1,50.8&area_res=6'
      + '&area_q=thrush&area_sort=english&area_status=red&area_status=none&area_page=2&year_from=2000&year_to=2020')
    expect(readArea(search)).toEqual(area)
    expect(readList(search, AREA_LIST)).toEqual(list)
    expect(isPlainMap(search.replace(/rank=[^&]*&kingdom=Animalia&phylum=Chordata&/, ''))).toBe(false)
  })

  it('leaves the defaults out, keeps an area with a hexagon, and its panel closed when asked', () => {
    expect(mapSearch({ area: { box: KAMLOOPS, res: 7 }, areaList: NO_LIST }))
      .toBe('?area=-120.6,50.55,-120.1,50.8&area_res=7')
    const both = mapSearch({ cell: '872800b30ffffff', area: { box: KAMLOOPS, res: 7 } })
    expect(both).toBe('?cell=872800b30ffffff&area=-120.6,50.55,-120.1,50.8&area_res=7')
    expect(readArea(both)).toEqual({ box: KAMLOOPS, res: 7 })
    const closed = mapSearch({ area: { box: KAMLOOPS, res: 7 }, areaPanelOpen: false })
    expect(closed).toBe('?area=-120.6,50.55,-120.1,50.8&area_res=7&area_panel=closed')
    expect(readAreaPanelOpen(closed)).toBe(false)
    expect(readAreaPanelOpen('?area=-120.6,50.55,-120.1,50.8&area_res=7')).toBe(true)
    // A species' card keeps the area in the address (not shown on its map).
    expect(mapSearch({ species: '59PMB', area: { box: KAMLOOPS, res: 7 } }))
      .toBe('?species=59PMB&area=-120.6,50.55,-120.1,50.8&area_res=7')
  })

  it.each([
    '?area=-120.6,50.55,-120.1&area_res=6', '?area=-120.6,50.55,-120.1,50.8', '?area=-120.6,50.55,-120.1,50.8&area_res=3',
    '?area=-120.1,50.55,-120.6,50.8&area_res=6', '?area=-120.6,50.8,-120.1,50.55&area_res=6',
    '?area=-200,50.55,-120.1,50.8&area_res=6', '?area=a,50.55,-120.1,50.8&area_res=6', '?area=,50.55,-120.1,50.8&area_res=6',
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

  it('ask for the box, its resolution and the list\'s options, the page only for the list', async () => {
    const urls = recordUrls()
    const area = { box: KAMLOOPS, res: 6 }
    const options = { years: { from: 2000, to: 2020 }, q: ' thrush ', status: ['red'], sort: 'english', rank: CHORDATA }
    await fetchArea(area, { ...options, offset: 50, limit: 50 })
    expect(urls[0]).toBe('/api/area?offset=50&limit=50&west=-120.6&south=50.55&east=-120.1&north=50.8&res=6'
      + '&rank=phylum&kingdom=Animalia&phylum=Chordata&year_from=2000&year_to=2020&q=thrush&status=red&sort=english')
    const exportUrl = areaExportUrl(area, { ...options, offset: 50, limit: 50 })
    expect(exportUrl.split('?')[0]).toBe('/api/export/area')
    expect(queryOf(exportUrl)).toEqual(queryOf(urls[0]).filter(([name]) => !PAGING.includes(name)))
  })

  it('never ask for a species (the API refuses it for an area)', () => {
    expect(areaExportUrl({ box: KAMLOOPS, res: 6 }, { species: '59PMB' }))
      .toBe('/api/export/area?west=-120.6&south=50.55&east=-120.1&north=50.8&res=6')
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
